"""Tests for the REST extractor."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from libs.extractors.base import SourceConfig
from libs.extractors.rest import (
    _SUPPORTED_METHODS,
    DiscoveredEndpoint,
    EndpointClassification,
    EndpointClassifier,
    RESTExtractor,
    _ObservedEndpoint,
)
from libs.ir.models import RiskLevel, SourceType


class RecordingClassifier(EndpointClassifier):
    """Classifier double that records discovery input."""

    def __init__(self) -> None:
        self.seen_endpoints: list[DiscoveredEndpoint] = []

    def classify(
        self,
        *,
        base_url: str,
        endpoints: list[DiscoveredEndpoint],
    ) -> list[EndpointClassification]:
        assert base_url == "https://api.example.com"
        self.seen_endpoints = list(endpoints)
        return [
            EndpointClassification(
                path="/users/{user_id}",
                method="GET",
                name="Get User",
                description="Fetch a user by identifier.",
                confidence=0.92,
                tags=("users", "read"),
            ),
            EndpointClassification(
                path="/orders",
                method="POST",
                name="Create Order",
                description="Create a new order.",
                confidence=0.9,
                tags=("orders", "write"),
            ),
        ]


class RelativePathClassifier(EndpointClassifier):
    """Classifier double that emits a path relative to the discovery base path."""

    def classify(
        self,
        *,
        base_url: str,
        endpoints: list[DiscoveredEndpoint],
    ) -> list[EndpointClassification]:
        assert base_url == "https://api.example.com/catalog"
        assert any(
            endpoint.path == "/catalog/products/{product_id}?view=detail" for endpoint in endpoints
        )
        return [
            EndpointClassification(
                path="/products/{product_id}?view=detail",
                method="GET",
                name="Get Product",
                description="Fetch a catalog product relative to the discovery base path.",
                confidence=0.93,
                tags=("products", "read"),
            )
        ]


def _build_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        routes: dict[tuple[str, str], httpx.Response] = {
            (
                "GET",
                "https://api.example.com",
            ): httpx.Response(
                200,
                text=(
                    '<html><body><a href="/users/{user_id}?verbose=true">User</a>'
                    '<form action="/orders" method="post"></form></body></html>'
                ),
                headers={"content-type": "text/html"},
                request=request,
            ),
            (
                "GET",
                "https://api.example.com/catalog",
            ): httpx.Response(
                200,
                text=(
                    "<html><body>"
                    '<a href="/catalog/products/{product_id}?view=detail">Product</a>'
                    "</body></html>"
                ),
                headers={"content-type": "text/html"},
                request=request,
            ),
            (
                "GET",
                "https://api.example.com/users/%7Buser_id%7D?verbose=true",
            ): httpx.Response(
                200,
                json={"links": ["/users/{user_id}/orders"]},
                request=request,
            ),
            (
                "OPTIONS",
                "https://api.example.com/users/%7Buser_id%7D?verbose=true",
            ): httpx.Response(200, headers={"allow": "GET"}, request=request),
            (
                "OPTIONS",
                "https://api.example.com/orders",
            ): httpx.Response(200, headers={"allow": "POST"}, request=request),
            (
                "OPTIONS",
                "https://api.example.com/users/%7Buser_id%7D/orders",
            ): httpx.Response(200, headers={"allow": "GET"}, request=request),
            (
                "GET",
                "https://api.example.com/users/%7Buser_id%7D/orders",
            ): httpx.Response(200, json={"items": []}, request=request),
            (
                "GET",
                "https://api.example.com/catalog/products/%7Bproduct_id%7D?view=detail",
            ): httpx.Response(200, json={"id": "sku-1"}, request=request),
            (
                "OPTIONS",
                "https://api.example.com/catalog/products/%7Bproduct_id%7D?view=detail",
            ): httpx.Response(200, headers={"allow": "GET"}, request=request),
        }
        return routes.get(
            (request.method, str(request.url)),
            httpx.Response(404, request=request),
        )

    return httpx.MockTransport(handler)


def test_discovers_rest_endpoints_and_uses_classifier_output() -> None:
    classifier = RecordingClassifier()
    client = httpx.Client(transport=_build_transport(), follow_redirects=True)
    extractor = RESTExtractor(client=client, classifier=classifier)

    try:
        service_ir = extractor.extract(SourceConfig(url="https://api.example.com"))
    finally:
        extractor.close()

    discovered_paths = {endpoint.path for endpoint in classifier.seen_endpoints}
    assert discovered_paths >= {"/users/{user_id}?verbose=true", "/orders"}
    assert service_ir.protocol == "rest"
    assert {operation.id for operation in service_ir.operations} == {
        "get_users_user_id",
        "post_orders",
    }

    get_user = next(
        operation for operation in service_ir.operations if operation.id == "get_users_user_id"
    )
    assert get_user.source is SourceType.llm
    assert get_user.risk.risk_level is RiskLevel.safe
    param_names = {param.name for param in get_user.params}
    assert param_names == {"user_id"}

    create_order = next(
        operation for operation in service_ir.operations if operation.id == "post_orders"
    )
    assert create_order.source is SourceType.llm
    assert create_order.risk.risk_level is RiskLevel.cautious
    assert any(param.name == "payload" for param in create_order.params)
    assert create_order.body_param_name == "payload"


def test_default_classifier_derives_risk_from_discovered_methods() -> None:
    client = httpx.Client(transport=_build_transport(), follow_redirects=True)
    extractor = RESTExtractor(client=client)

    try:
        service_ir = extractor.extract(SourceConfig(url="https://api.example.com"))
    finally:
        extractor.close()

    operation_by_id = {operation.id: operation for operation in service_ir.operations}
    assert operation_by_id["get_users_user_id"].risk.risk_level is RiskLevel.safe
    assert operation_by_id["post_orders"].risk.risk_level is RiskLevel.cautious
    assert operation_by_id["post_orders"].source is SourceType.extractor


def _build_catalog_item_transport() -> httpx.MockTransport:
    """Mock transport reproducing the live llm-proof-http REST discovery layout.

    The catalog root links to ``/rest/catalog/items/{item_id}?view=detail``, and
    that item endpoint returns JSON body values like ``"Puzzle Box"``, ``"active"``,
    ``"games"``, and ``"detail"`` that must NOT be promoted to discovered endpoints.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        routes: dict[tuple[str, str], httpx.Response] = {
            (
                "GET",
                "https://mock.example.com/rest/catalog",
            ): httpx.Response(
                200,
                text=(
                    "<html><body>"
                    '<a href="/rest/catalog/items/{item_id}?view=detail">Item Detail</a>'
                    "</body></html>"
                ),
                headers={"content-type": "text/html"},
                request=request,
            ),
            (
                "GET",
                "https://mock.example.com/rest/catalog/items/%7Bitem_id%7D?view=detail",
            ): httpx.Response(
                200,
                json={
                    "item_id": "{item_id}",
                    "view": "detail",
                    "name": "Puzzle Box",
                    "status": "active",
                    "category": "games",
                },
                request=request,
            ),
            (
                "OPTIONS",
                "https://mock.example.com/rest/catalog/items/%7Bitem_id%7D?view=detail",
            ): httpx.Response(200, headers={"allow": "GET, OPTIONS"}, request=request),
        }
        return routes.get(
            (request.method, str(request.url)),
            httpx.Response(404, request=request),
        )

    return httpx.MockTransport(handler)


def test_json_body_values_not_promoted_to_endpoints() -> None:
    """Regression: JSON response values like 'active', 'Puzzle Box' must not become endpoints.

    Reproduces the live audit failure from namespace tool-compiler-llm-rest-audit-041525
    where 5 of 6 generated REST tools failed because they pointed at spurious paths
    derived from JSON field values.
    """
    client = httpx.Client(transport=_build_catalog_item_transport(), follow_redirects=True)
    extractor = RESTExtractor(client=client)

    try:
        service_ir = extractor.extract(SourceConfig(url="https://mock.example.com/rest/catalog"))
    finally:
        extractor.close()

    operation_paths = {op.path for op in service_ir.operations}

    # The only legitimate discovered path should be the items endpoint.
    assert "/items/{item_id}" in operation_paths or "/catalog/items/{item_id}" in operation_paths

    # None of the JSON body values should appear as endpoints.
    spurious_paths = {"/active", "/detail", "/games", "/{item_id}", "/Puzzle Box"}
    leaked = operation_paths & spurious_paths
    assert not leaked, f"JSON body values leaked into discovered endpoints: {leaked}"


def test_relative_json_links_are_still_discovered() -> None:
    """Regression: link-like relative JSON paths must still be discovered after B-002."""

    def handler(request: httpx.Request) -> httpx.Response:
        routes = {
            ("GET", "https://api.example.com"): httpx.Response(
                200,
                json={"links": ["users/123/orders"]},
                request=request,
            ),
            ("OPTIONS", "https://api.example.com/users/123/orders"): httpx.Response(
                200,
                headers={"allow": "GET"},
                request=request,
            ),
        }
        return routes.get(
            (request.method, str(request.url)),
            httpx.Response(404, request=request),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    extractor = RESTExtractor(client=client)

    try:
        service_ir = extractor.extract(SourceConfig(url="https://api.example.com"))
    finally:
        extractor.close()

    operation_paths = {op.path for op in service_ir.operations}

    assert "/users/123/orders" in operation_paths


def test_sibling_coalescing_merges_value_like_leaves() -> None:
    """When HTML links produce many sibling paths with value-like segments, coalesce them."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and str(request.url) == "https://items.example.com/shop":
            return httpx.Response(
                200,
                text=(
                    "<html><body>"
                    '<a href="/shop/item/Widget">Widget</a>'
                    '<a href="/shop/item/Gadget">Gadget</a>'
                    '<a href="/shop/item/Puzzle Box">Puzzle Box</a>'
                    "</body></html>"
                ),
                headers={"content-type": "text/html"},
                request=request,
            )
        return httpx.Response(404, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    extractor = RESTExtractor(client=client)

    try:
        service_ir = extractor.extract(SourceConfig(url="https://items.example.com/shop"))
    finally:
        extractor.close()

    operation_paths = {op.path for op in service_ir.operations}

    # "Puzzle Box" has a space → value-like, and there are 3 siblings,
    # so they should coalesce into a single template path.
    assert "/Widget" not in operation_paths
    assert "/Gadget" not in operation_paths
    assert "/Puzzle Box" not in operation_paths
    assert "/item/{id}" in operation_paths or any(
        p is not None and "{" in p for p in operation_paths
    )


def test_sibling_coalescing_preserves_shared_query_defaults() -> None:
    """Coalesced template paths should keep query defaults shared by all siblings."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and str(request.url) == "https://items.example.com/shop":
            return httpx.Response(
                200,
                text=(
                    "<html><body>"
                    '<a href="/shop/item/1?view=detail">One</a>'
                    '<a href="/shop/item/2?view=detail">Two</a>'
                    '<a href="/shop/item/3?view=detail">Three</a>'
                    "</body></html>"
                ),
                headers={"content-type": "text/html"},
                request=request,
            )
        return httpx.Response(404, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    extractor = RESTExtractor(client=client)

    try:
        service_ir = extractor.extract(SourceConfig(url="https://items.example.com/shop"))
    finally:
        extractor.close()

    operation = next(op for op in service_ir.operations if op.path == "/item/{id}")
    params = {param.name: param for param in operation.params}

    assert params["id"].required is True
    assert params["view"].default == "detail"


def test_rebases_classifier_relative_paths_against_discovery_base_path() -> None:
    classifier = RelativePathClassifier()
    client = httpx.Client(transport=_build_transport(), follow_redirects=True)
    extractor = RESTExtractor(client=client, classifier=classifier)

    try:
        service_ir = extractor.extract(SourceConfig(url="https://api.example.com/catalog"))
    finally:
        extractor.close()

    assert service_ir.base_url == "https://api.example.com/catalog"
    assert service_ir.metadata["base_path"] == "/catalog"
    assert service_ir.metadata["discovery_entrypoint"] == "https://api.example.com/catalog"

    operation = service_ir.operations[0]
    assert operation.path == "/products/{product_id}"
    assert operation.id == "get_products_product_id"
    params = {param.name: param for param in operation.params}
    assert params["product_id"].required is True
    assert params["view"].default == "detail"


# ---------------------------------------------------------------------------
# OPTIONS probing hardening tests
# ---------------------------------------------------------------------------


class TestOptionsProbing:
    """Tests for the hardened OPTIONS / HEAD / GET probing logic."""

    @staticmethod
    def _make_extractor(
        handler: Callable[[httpx.Request], httpx.Response],
    ) -> tuple[RESTExtractor, httpx.Client]:
        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport, follow_redirects=True)
        return RESTExtractor(client=client), client

    # 1. HEAD fallback when OPTIONS returns 405
    def test_head_fallback_when_options_fails(self) -> None:
        url = "https://probe.test/api/items"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "OPTIONS":
                return httpx.Response(405, request=request)
            if request.method == "HEAD":
                return httpx.Response(200, request=request)
            return httpx.Response(404, request=request)

        extractor, _ = self._make_extractor(handler)
        target: dict[str, _ObservedEndpoint] = {}
        extractor._probe_and_register("/api/items", url, target)

        assert "/api/items" in target
        assert "GET" in target["/api/items"].methods

    # 2. Allow: * discovers all supported methods
    def test_allow_star_discovers_all_methods(self) -> None:
        url = "https://probe.test/api/items"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "OPTIONS":
                return httpx.Response(200, headers={"allow": "*"}, request=request)
            return httpx.Response(404, request=request)

        extractor, _ = self._make_extractor(handler)
        target: dict[str, _ObservedEndpoint] = {}
        extractor._probe_and_register("/api/items", url, target)

        assert "/api/items" in target
        assert target["/api/items"].methods == set(_SUPPORTED_METHODS)

    # 3. Content-Type validation rejects binary responses
    def test_content_type_validation_rejects_binary(self) -> None:
        url = "https://probe.test/api/download"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method in ("OPTIONS", "HEAD"):
                return httpx.Response(404, request=request)
            if request.method == "GET":
                return httpx.Response(
                    200,
                    headers={"content-type": "application/octet-stream"},
                    request=request,
                )
            return httpx.Response(404, request=request)

        extractor, _ = self._make_extractor(handler)
        target: dict[str, _ObservedEndpoint] = {}
        extractor._probe_and_register("/api/download", url, target)

        assert "/api/download" not in target

    # 4. Content-Type validation accepts JSON
    def test_content_type_validation_accepts_json(self) -> None:
        url = "https://probe.test/api/data"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method in ("OPTIONS", "HEAD"):
                return httpx.Response(404, request=request)
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={"ok": True},
                    request=request,
                )
            return httpx.Response(404, request=request)

        extractor, _ = self._make_extractor(handler)
        target: dict[str, _ObservedEndpoint] = {}
        extractor._probe_and_register("/api/data", url, target)

        assert "/api/data" in target
        assert "GET" in target["/api/data"].methods

    # 5. _probe_allowed_methods falls back to HEAD on 405
    def test_probe_allowed_methods_405_tries_head(self) -> None:
        url = "https://probe.test/api/resource"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "OPTIONS":
                return httpx.Response(405, request=request)
            if request.method == "HEAD":
                return httpx.Response(200, request=request)
            return httpx.Response(404, request=request)

        extractor, _ = self._make_extractor(handler)
        endpoint = _ObservedEndpoint(
            path="/api/resource",
            absolute_url=url,
            methods={"POST"},
            sources={"html"},
            confidence=0.7,
        )
        extractor._probe_allowed_methods(endpoint)

        assert "GET" in endpoint.methods
        assert "POST" in endpoint.methods  # original preserved (405 = non-authoritative)
        assert "head" in endpoint.sources

    # 6. OPTIONS 200 with Allow header replaces speculative methods
    def test_probe_allowed_methods_replaces_speculative_get(self) -> None:
        """When OPTIONS returns 200 with Allow: POST, speculative GET is removed."""
        url = "https://probe.test/api/action"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "OPTIONS":
                return httpx.Response(
                    200,
                    headers={"allow": "POST, OPTIONS"},
                    request=request,
                )
            return httpx.Response(404, request=request)

        extractor, _ = self._make_extractor(handler)
        endpoint = _ObservedEndpoint(
            path="/api/action",
            absolute_url=url,
            methods={"GET"},  # speculative from BFS link discovery
            sources={"json"},
            confidence=0.7,
        )
        extractor._probe_allowed_methods(endpoint)

        assert "POST" in endpoint.methods
        assert "GET" not in endpoint.methods  # speculative GET replaced
        assert "options" in endpoint.sources


# ---------------------------------------------------------------------------
# Iterative sub-resource inference tests
# ---------------------------------------------------------------------------


class TestIterativeSubResourceInference:
    """Tests for depth-2+ sub-resource discovery via iterative inference."""

    @staticmethod
    def _make_extractor(
        handler: Callable[[httpx.Request], httpx.Response],
    ) -> tuple[RESTExtractor, httpx.Client]:
        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport, follow_redirects=True)
        return RESTExtractor(client=client, max_pages=10), client

    def test_iterative_inference_discovers_depth2_endpoints(self) -> None:
        """Iterative inference discovers /items/{item_id}/reviews from /items collection."""

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path or "/"
            if request.method == "GET" and path == "/api":
                return httpx.Response(
                    200,
                    json={"links": ["/api/items"]},
                    headers={"content-type": "application/json"},
                    request=request,
                )
            if request.method == "GET" and path == "/api/items":
                return httpx.Response(
                    200,
                    json={"items": [], "links": []},
                    headers={"content-type": "application/json"},
                    request=request,
                )
            if request.method == "OPTIONS":
                # items/{item_id} exists, items/{item_id}/comments exists
                if "items/" in path and "/comments" in path:
                    return httpx.Response(
                        200,
                        headers={"allow": "GET"},
                        request=request,
                    )
                if "items/" in path:
                    return httpx.Response(
                        200,
                        headers={"allow": "GET, PUT, DELETE"},
                        request=request,
                    )
                if path == "/api/items":
                    return httpx.Response(
                        200,
                        headers={"allow": "GET, POST"},
                        request=request,
                    )
            return httpx.Response(404, request=request)

        extractor, _ = self._make_extractor(handler)
        try:
            service_ir = extractor.extract(
                SourceConfig(
                    url="https://api.example.com/api",
                    hints={"protocol": "rest"},
                )
            )
        finally:
            extractor.close()

        op_paths = {op.path for op in service_ir.operations if op.path}
        # Depth-1: /items/{item_id} inferred from /items collection
        assert any("/items/{item_id}" in p for p in op_paths), (
            f"Expected /items/{{item_id}} in {op_paths}"
        )
        # Depth-2: /items/{item_id}/comments inferred from /items/{item_id}
        assert any("comments" in p for p in op_paths), (
            f"Expected comments sub-resource in {op_paths}"
        )

    def test_resource_specific_param_names_avoid_duplicates(self) -> None:
        """Inferred params use resource names (e.g. {item_id}) not generic {id}."""

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path or "/"
            if request.method == "GET" and path == "/api":
                return httpx.Response(
                    200,
                    json={"links": ["/api/users"]},
                    headers={"content-type": "application/json"},
                    request=request,
                )
            if request.method == "GET" and path == "/api/users":
                return httpx.Response(
                    200,
                    json={"items": []},
                    headers={"content-type": "application/json"},
                    request=request,
                )
            if request.method == "OPTIONS":
                return httpx.Response(
                    200,
                    headers={"allow": "GET"},
                    request=request,
                )
            return httpx.Response(404, request=request)

        extractor, _ = self._make_extractor(handler)
        try:
            service_ir = extractor.extract(
                SourceConfig(
                    url="https://api.example.com/api",
                    hints={"protocol": "rest"},
                )
            )
        finally:
            extractor.close()

        # Inferred detail endpoint should use {user_id}, not generic {id}
        op_paths = {op.path for op in service_ir.operations if op.path}
        assert any("{user_id}" in p for p in op_paths), (
            f"Expected {{user_id}} param name in {op_paths}"
        )


# ---------------------------------------------------------------------------
# Concrete path deduplication tests
# ---------------------------------------------------------------------------


class TestDeduplicateConcretePaths:
    """Tests for the _deduplicate_concrete_paths function."""

    def test_concrete_path_merged_into_template(self) -> None:
        """A fully concrete path is merged into a matching template."""
        from libs.extractors.rest import _deduplicate_concrete_paths

        observed = {
            "/api/users/{user_id}": _ObservedEndpoint(
                path="/api/users/{user_id}",
                absolute_url="https://x.com/api/users/{user_id}",
                methods={"GET"},
                sources={"inferred"},
                confidence=0.85,
            ),
            "/api/users/usr-1": _ObservedEndpoint(
                path="/api/users/usr-1",
                absolute_url="https://x.com/api/users/usr-1",
                methods={"GET", "PUT"},
                sources={"json"},
                confidence=0.75,
            ),
        }
        result = _deduplicate_concrete_paths(observed)

        assert "/api/users/usr-1" not in result
        assert "/api/users/{user_id}" in result
        # Methods merged from concrete into template
        assert result["/api/users/{user_id}"].methods >= {"GET", "PUT"}

    def test_partially_concrete_template_merged_into_general(self) -> None:
        """A path with fewer template params merges into one with more."""
        from libs.extractors.rest import _deduplicate_concrete_paths

        observed = {
            "/api/users/{user_id}/posts/{post_id}": _ObservedEndpoint(
                path="/api/users/{user_id}/posts/{post_id}",
                absolute_url="https://x.com/api/users/{user_id}/posts/{post_id}",
                methods={"GET"},
                sources={"inferred"},
                confidence=0.85,
            ),
            "/api/users/usr-1/posts/{post_id}": _ObservedEndpoint(
                path="/api/users/usr-1/posts/{post_id}",
                absolute_url="https://x.com/api/users/usr-1/posts/{post_id}",
                methods={"GET", "DELETE"},
                sources={"json"},
                confidence=0.75,
            ),
        }
        result = _deduplicate_concrete_paths(observed)

        assert "/api/users/usr-1/posts/{post_id}" not in result
        assert "/api/users/{user_id}/posts/{post_id}" in result
        assert result["/api/users/{user_id}/posts/{post_id}"].methods >= {"GET", "DELETE"}

    def test_no_templates_returns_unchanged(self) -> None:
        """When there are no template paths, all paths are kept."""
        from libs.extractors.rest import _deduplicate_concrete_paths

        observed = {
            "/api/users/1": _ObservedEndpoint(
                path="/api/users/1",
                absolute_url="https://x.com/api/users/1",
                methods={"GET"},
                sources={"json"},
                confidence=0.7,
            ),
        }
        result = _deduplicate_concrete_paths(observed)
        assert "/api/users/1" in result


class TestPaginationInference:
    """Tests for RESTExtractor._infer_pagination_from_response."""

    def _make_extractor(self) -> RESTExtractor:
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        return RESTExtractor(client=httpx.Client(transport=transport))

    def test_rest_pagination_offset_limit_params(self) -> None:
        extractor = self._make_extractor()
        endpoint = DiscoveredEndpoint(
            path="/api/items?offset=0&limit=20",
            absolute_url="https://api.example.com/api/items?offset=0&limit=20",
            methods=("GET",),
            discovery_sources=("json",),
            confidence=0.8,
        )
        result = extractor._infer_pagination_from_response(endpoint, "GET")

        assert result is not None
        assert result.style == "offset"
        assert result.page_param == "offset"
        assert result.size_param == "limit"

    def test_rest_pagination_page_params(self) -> None:
        extractor = self._make_extractor()
        endpoint = DiscoveredEndpoint(
            path="/api/items?page=1&per_page=10",
            absolute_url="https://api.example.com/api/items?page=1&per_page=10",
            methods=("GET",),
            discovery_sources=("json",),
            confidence=0.8,
        )
        result = extractor._infer_pagination_from_response(endpoint, "GET")

        assert result is not None
        assert result.style == "page"
        assert result.page_param == "page"
        assert result.size_param == "per_page"

    def test_rest_no_pagination_for_post(self) -> None:
        extractor = self._make_extractor()
        endpoint = DiscoveredEndpoint(
            path="/api/items?offset=0&limit=20",
            absolute_url="https://api.example.com/api/items?offset=0&limit=20",
            methods=("POST",),
            discovery_sources=("form",),
            confidence=0.8,
        )
        result = extractor._infer_pagination_from_response(endpoint, "POST")

        assert result is None


class TestDeduplicateOperationIds:
    """Regression tests for B-005: duplicate operation ID resolution."""

    def test_duplicate_ids_get_numeric_suffix(self) -> None:
        from libs.extractors.rest import _deduplicate_operation_ids
        from libs.ir.models import (
            Operation,
            RiskMetadata,
            SourceType,
        )

        def _make_op(op_id: str, path: str) -> Operation:
            return Operation(
                id=op_id,
                name=op_id,
                description="test",
                method="GET",
                path=path,
                params=[],
                risk=RiskMetadata(
                    writes_state=False,
                    destructive=False,
                    risk_level=RiskLevel.safe,
                    confidence=0.9,
                    source=SourceType.extractor,
                ),
            )

        ops = [
            _make_op("get_comments", "/comments"),
            _make_op("get_comments", "/posts/{post_id}/comments"),
            _make_op("get_users", "/users"),
        ]
        result = _deduplicate_operation_ids(ops)
        ids = [op.id for op in result]
        assert ids == ["get_comments", "get_comments_1", "get_users"]
        assert len(set(ids)) == len(ids)

    def test_no_duplication_leaves_ids_unchanged(self) -> None:
        from libs.extractors.rest import _deduplicate_operation_ids
        from libs.ir.models import Operation, RiskMetadata, SourceType

        def _make_op(op_id: str) -> Operation:
            return Operation(
                id=op_id,
                name=op_id,
                description="test",
                method="GET",
                path=f"/{op_id}",
                params=[],
                risk=RiskMetadata(
                    writes_state=False,
                    destructive=False,
                    risk_level=RiskLevel.safe,
                    confidence=0.9,
                    source=SourceType.extractor,
                ),
            )

        ops = [_make_op("get_a"), _make_op("get_b"), _make_op("get_c")]
        result = _deduplicate_operation_ids(ops)
        ids = [op.id for op in result]
        assert ids == ["get_a", "get_b", "get_c"]
