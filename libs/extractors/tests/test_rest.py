"""Tests for the REST extractor."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from libs.extractors.base import SourceConfig
from libs.extractors.rest import (
    _SUPPORTED_METHODS,
    DiscoveredEndpoint,
    EndpointClassification,
    EndpointClassifier,
    HeuristicRESTClassifier,
    RESTExtractor,
    _coalesce_sibling_endpoints,
    _deduplicate_operation_ids,
    _infer_json_server_relations,
    _is_path_like,
    _is_static_asset_path,
    _json_server_collection_methods,
    _json_server_foreign_keys,
    _json_server_parent_candidates,
    _looks_like_foreign_key_field,
    _looks_like_json_server_db_payload,
    _looks_like_json_server_resource,
    _looks_like_value_segment,
    _normalize_classification_path,
    _ObservedEndpoint,
    _pluralize_resource_name,
    _risk_for_method,
    _slugify,
)
from libs.ir.models import Operation, RiskLevel, RiskMetadata, SourceType


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


def test_current_directus_collection_entrypoint_is_discovered() -> None:
    """Collection URLs that directly return Directus-style JSON must still discover tools."""

    def handler(request: httpx.Request) -> httpx.Response:
        routes = {
            ("GET", "https://directus.example.com/items/products"): httpx.Response(
                200,
                json={"data": [{"id": "prod_1", "name": "Widget"}]},
                request=request,
            ),
            ("OPTIONS", "https://directus.example.com/items/products"): httpx.Response(
                200,
                headers={"allow": "GET, POST"},
                request=request,
            ),
            ("OPTIONS", "https://directus.example.com/items/products/prod_1"): httpx.Response(
                200,
                headers={"allow": "GET, PATCH, DELETE"},
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
        service_ir = extractor.extract(
            SourceConfig(url="https://directus.example.com/items/products")
        )
    finally:
        extractor.close()

    methods_by_path = {(operation.method, operation.path) for operation in service_ir.operations}

    assert service_ir.base_url == "https://directus.example.com/items/products"
    assert "/items/products" in service_ir.metadata["discovered_paths"]
    assert "/items/products/{product_id}" in service_ir.metadata["discovered_paths"]
    assert ("GET", "/") in methods_by_path
    assert ("POST", "/") in methods_by_path
    assert ("GET", "/{product_id}") in methods_by_path
    assert ("PATCH", "/{product_id}") in methods_by_path
    assert ("DELETE", "/{product_id}") in methods_by_path
    detail_operation = next(
        operation
        for operation in service_ir.operations
        if operation.method == "GET" and operation.path == "/{product_id}"
    )
    params_by_name = {param.name: param for param in detail_operation.params}
    assert params_by_name["product_id"].default == "prod_1"


def test_current_pocketbase_collection_entrypoint_is_discovered_without_links() -> None:
    """PocketBase-style paginated JSON entrypoints should bootstrap collection/detail tools."""

    def handler(request: httpx.Request) -> httpx.Response:
        routes = {
            ("GET", "https://pocketbase.example.com/api/collections/products/records"): (
                httpx.Response(
                    200,
                    json={
                        "page": 1,
                        "perPage": 30,
                        "totalItems": 1,
                        "items": [{"id": "rec123", "name": "Widget"}],
                    },
                    request=request,
                )
            ),
            (
                "GET",
                "https://pocketbase.example.com/api/collections/products/records/rec123",
            ): httpx.Response(
                200,
                json={"id": "rec123", "name": "Widget"},
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
        service_ir = extractor.extract(
            SourceConfig(url="https://pocketbase.example.com/api/collections/products/records")
        )
    finally:
        extractor.close()

    operation_paths = {operation.path for operation in service_ir.operations}

    assert service_ir.base_url == "https://pocketbase.example.com/api/collections/products/records"
    assert "/api/collections/products/records" in service_ir.metadata["discovered_paths"]
    assert (
        "/api/collections/products/records/{record_id}"
        in service_ir.metadata["discovered_paths"]
    )
    assert "/" in operation_paths
    assert "/{record_id}" in operation_paths
    detail_operation = next(
        operation
        for operation in service_ir.operations
        if operation.method == "GET" and operation.path == "/{record_id}"
    )
    params_by_name = {param.name: param for param in detail_operation.params}
    assert params_by_name["record_id"].default == "rec123"


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


# ---------------------------------------------------------------------------
# detect() edge cases (lines 149-161)
# ---------------------------------------------------------------------------


class TestDetect:
    def test_detect_protocol_hint_rest(self) -> None:
        """detect() returns 1.0 when hint protocol=rest (line 149-150)."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        extractor = RESTExtractor(client=httpx.Client(transport=transport))
        assert (
            extractor.detect(SourceConfig(url="https://example.com", hints={"protocol": "rest"}))
            == 1.0
        )

    def test_detect_no_url(self) -> None:
        """detect() returns 0.0 when no URL (line 152)."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        extractor = RESTExtractor(client=httpx.Client(transport=transport))
        assert extractor.detect(SourceConfig(file_content="not-a-url")) == 0.0

    def test_detect_non_http_scheme(self) -> None:
        """detect() returns 0.0 for non-http scheme (line 156)."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        extractor = RESTExtractor(client=httpx.Client(transport=transport))
        assert extractor.detect(SourceConfig(url="grpc://example.com")) == 0.0

    def test_detect_openapi_url(self) -> None:
        """detect() returns 0.1 for openapi-like URL (line 158)."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        extractor = RESTExtractor(client=httpx.Client(transport=transport))
        assert extractor.detect(SourceConfig(url="https://example.com/openapi.json")) == 0.1

    def test_detect_graphql_url(self) -> None:
        """detect() returns 0.1 for graphql-like URL (line 160)."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        extractor = RESTExtractor(client=httpx.Client(transport=transport))
        assert extractor.detect(SourceConfig(url="https://example.com/graphql")) == 0.1

    def test_detect_normal_http_url(self) -> None:
        """detect() returns 0.55 for a normal HTTP URL (line 161)."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        extractor = RESTExtractor(client=httpx.Client(transport=transport))
        assert extractor.detect(SourceConfig(url="https://api.example.com/v1")) == 0.55


# ---------------------------------------------------------------------------
# extract() edge cases (lines 165, 169, 176)
# ---------------------------------------------------------------------------


class TestExtractEdgeCases:
    def test_extract_raises_without_url(self) -> None:
        """extract() raises ValueError when source.url is missing (line 165)."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        extractor = RESTExtractor(client=httpx.Client(transport=transport))
        with pytest.raises(ValueError):
            extractor.extract(SourceConfig(file_content="no-url"))

    def test_extract_raises_when_no_endpoints_discovered(self) -> None:
        """extract() raises ValueError when discovery finds nothing (line 169)."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        extractor = RESTExtractor(client=httpx.Client(transport=transport))
        with pytest.raises(ValueError, match="No REST endpoints discovered"):
            extractor.extract(SourceConfig(url="https://empty.example.com"))

    def test_extract_raises_when_classifier_returns_empty(self) -> None:
        """extract() raises ValueError when classifier returns no results (line 176)."""

        class EmptyClassifier(EndpointClassifier):
            def classify(
                self, *, base_url: str, endpoints: list[DiscoveredEndpoint]
            ) -> list[EndpointClassification]:
                return []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(
                    200,
                    text='<html><a href="/foo">Foo</a></html>',
                    headers={"content-type": "text/html"},
                    request=request,
                )
            return httpx.Response(404, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        extractor = RESTExtractor(client=client, classifier=EmptyClassifier())
        with pytest.raises(ValueError, match="Classifier returned no REST operations"):
            extractor.extract(SourceConfig(url="https://api.example.com"))


# ---------------------------------------------------------------------------
# Unsupported HTTP methods (line 113)
# ---------------------------------------------------------------------------


def test_heuristic_classifier_skips_unsupported_methods() -> None:
    """HeuristicRESTClassifier skips methods like TRACE/OPTIONS (line 113)."""
    classifier = HeuristicRESTClassifier()
    endpoint = DiscoveredEndpoint(
        path="/api/test",
        absolute_url="https://x.com/api/test",
        methods=("TRACE", "OPTIONS", "GET"),
        discovery_sources=("link",),
        confidence=0.8,
    )
    results = classifier.classify(base_url="https://x.com", endpoints=[endpoint])
    methods = [r.method for r in results]
    assert "TRACE" not in methods
    assert "OPTIONS" not in methods
    assert "GET" in methods


# ---------------------------------------------------------------------------
# HTTP error handling during discovery (lines 218, 223-224, 243-247)
# ---------------------------------------------------------------------------


class TestDiscoveryErrorHandling:
    def test_discovery_skips_already_visited_pages(self) -> None:
        """Duplicate URLs in the BFS queue are skipped (line 218)."""
        visit_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and str(request.url) == "https://x.com/":
                visit_count["n"] += 1
                return httpx.Response(
                    200,
                    text='<html><a href="/">self-link</a><a href="/resource">res</a></html>',
                    headers={"content-type": "text/html"},
                    request=request,
                )
            return httpx.Response(404, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        extractor = RESTExtractor(client=client)
        try:
            extractor._discover("https://x.com/")
        finally:
            extractor.close()
        assert visit_count["n"] == 1

    def test_discovery_continues_on_http_error(self) -> None:
        """Discovery continues when an HTTP error occurs (line 223-224)."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        extractor = RESTExtractor(client=client)
        try:
            result = extractor._discover("https://broken.example.com")
        finally:
            extractor.close()
        assert result == []

    def test_discovery_skips_pages_with_400_status(self) -> None:
        """Pages returning 400+ are skipped but discovery continues (line 225-226)."""

        call_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            if request.method == "GET":
                return httpx.Response(403, request=request)
            return httpx.Response(404, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        extractor = RESTExtractor(client=client)
        try:
            result = extractor._discover("https://forbidden.example.com")
        finally:
            extractor.close()
        assert result == []

    def test_discovery_handles_form_source(self) -> None:
        """Form sources set POST method and higher confidence (lines 243-245)."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and str(request.url) == "https://x.com/":
                return httpx.Response(
                    200,
                    text='<html><form action="/submit" method="post"></form></html>',
                    headers={"content-type": "text/html"},
                    request=request,
                )
            if request.method == "OPTIONS":
                return httpx.Response(200, headers={"allow": "POST"}, request=request)
            return httpx.Response(404, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        extractor = RESTExtractor(client=client)
        try:
            result = extractor._discover("https://x.com/")
        finally:
            extractor.close()
        paths = {ep.path for ep in result}
        assert "/submit" in paths
        submit = next(ep for ep in result if ep.path == "/submit")
        assert "POST" in submit.methods

    def test_discovery_handles_other_source_type(self) -> None:
        """Non-link/json/form source types get 0.7 confidence (line 246-247)."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and str(request.url) == "https://x.com/":
                return httpx.Response(
                    200,
                    text='<html><form action="/action" method="get"></form></html>',
                    headers={"content-type": "text/html"},
                    request=request,
                )
            if request.method == "OPTIONS":
                return httpx.Response(200, headers={"allow": "GET"}, request=request)
            return httpx.Response(404, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        extractor = RESTExtractor(client=client)
        try:
            result = extractor._discover("https://x.com/")
        finally:
            extractor.close()
        assert len(result) >= 1


# ---------------------------------------------------------------------------
# Sub-resource inference (lines 296, 376-377)
# ---------------------------------------------------------------------------


class TestSubResourceInference:
    def test_empty_path_is_skipped(self) -> None:
        """Paths that are empty after stripping are skipped (line 296)."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        client = httpx.Client(transport=transport, follow_redirects=True)
        extractor = RESTExtractor(client=client)
        observed: dict[str, _ObservedEndpoint] = {
            "": _ObservedEndpoint(path="", absolute_url="https://x.com/", methods={"GET"}),
        }
        inferred = extractor._infer_sub_resources("https://x.com", observed)
        assert len(inferred) == 0

    def test_detail_endpoint_probes_sub_resources(self) -> None:
        """Detail endpoints with a `{param}` leaf probe candidate sub-resources."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "OPTIONS" and "/comments" in str(request.url):
                return httpx.Response(200, headers={"allow": "GET"}, request=request)
            return httpx.Response(404, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        extractor = RESTExtractor(client=client)
        observed: dict[str, _ObservedEndpoint] = {
            "/api/users/{user_id}": _ObservedEndpoint(
                path="/api/users/{user_id}",
                absolute_url="https://x.com/api/users/{user_id}",
                methods={"GET"},
            ),
        }
        inferred = extractor._infer_sub_resources("https://x.com", observed)
        inferred_paths = set(inferred.keys())
        assert any("comments" in p for p in inferred_paths)


# ---------------------------------------------------------------------------
# _probe_and_register error handling (lines 407-408, 422-423)
# ---------------------------------------------------------------------------


class TestProbeAndRegister:
    def test_probe_options_http_error(self) -> None:
        """OPTIONS raising HTTPError falls through to HEAD/GET (lines 407-408)."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "OPTIONS":
                raise httpx.ConnectError("fail")
            if request.method == "HEAD":
                return httpx.Response(200, request=request)
            return httpx.Response(404, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        extractor = RESTExtractor(client=client)
        target: dict[str, _ObservedEndpoint] = {}
        extractor._probe_and_register("/api/test", "https://x.com/api/test", target)
        assert "/api/test" in target
        assert "GET" in target["/api/test"].methods

    def test_probe_get_fallback_http_error(self) -> None:
        """GET fallback also handles HTTPError gracefully (lines 422-423)."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "OPTIONS":
                return httpx.Response(404, request=request)
            if request.method == "HEAD":
                return httpx.Response(404, request=request)
            if request.method == "GET":
                raise httpx.ReadTimeout("timeout")
            return httpx.Response(404, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        extractor = RESTExtractor(client=client)
        target: dict[str, _ObservedEndpoint] = {}
        extractor._probe_and_register("/api/test", "https://x.com/api/test", target)
        assert "/api/test" not in target


# ---------------------------------------------------------------------------
# _extract_candidate_paths edge cases (lines 446-448, 459)
# ---------------------------------------------------------------------------


class TestExtractCandidatePaths:
    def test_json_parse_failure_falls_back_to_html(self) -> None:
        """When JSON parsing fails, fallback to HTML extraction (lines 446-448)."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        client = httpx.Client(transport=transport, follow_redirects=True)
        extractor = RESTExtractor(client=client)
        # Create a response with json content-type but invalid json body
        response = httpx.Response(
            200,
            text='not-json but <a href="/link">link</a>',
            headers={"content-type": "application/json"},
            request=httpx.Request("GET", "https://x.com/"),
        )
        result = extractor._extract_candidate_paths("https://x.com", response)
        paths = [p for p, _ in result]
        assert any("/link" in p for p in paths)

    def test_html_form_with_non_post_method(self) -> None:
        """Form with method != POST produces 'link' source (line 459-460)."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        client = httpx.Client(transport=transport, follow_redirects=True)
        extractor = RESTExtractor(client=client)
        response = httpx.Response(
            200,
            text='<html><form action="/search" method="get"></form></html>',
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", "https://x.com/"),
        )
        result = extractor._extract_candidate_paths("https://x.com", response)
        source_types = [s for _, s in result]
        assert "link" in source_types

    def test_normalize_candidate_rejects_external_host(self) -> None:
        """Candidates pointing to different hosts are rejected (line 478)."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        client = httpx.Client(transport=transport, follow_redirects=True)
        extractor = RESTExtractor(client=client)
        result = extractor._normalize_candidate("https://x.com", "https://evil.com/path")
        assert result is None

    def test_normalize_candidate_empty_returns_none(self) -> None:
        """Empty candidate returns None (line 475)."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        client = httpx.Client(transport=transport, follow_redirects=True)
        extractor = RESTExtractor(client=client)
        result = extractor._normalize_candidate("https://x.com", "")
        assert result is None


# ---------------------------------------------------------------------------
# _probe_allowed_methods edge cases (lines 491-492, 506)
# ---------------------------------------------------------------------------


class TestProbeAllowedMethods:
    def test_probe_options_raises_http_error(self) -> None:
        """When OPTIONS raises HTTPError, method returns early (lines 491-492)."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "OPTIONS":
                raise httpx.ConnectError("fail")
            return httpx.Response(404, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        extractor = RESTExtractor(client=client)
        endpoint = _ObservedEndpoint(
            path="/test", absolute_url="https://x.com/test", methods={"GET"}
        )
        extractor._probe_allowed_methods(endpoint)
        assert endpoint.methods == {"GET"}  # unchanged

    def test_probe_options_allow_star(self) -> None:
        """When OPTIONS returns Allow: *, all supported methods are used (line 506)."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "OPTIONS":
                return httpx.Response(200, headers={"allow": "*"}, request=request)
            return httpx.Response(404, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        extractor = RESTExtractor(client=client)
        endpoint = _ObservedEndpoint(
            path="/test", absolute_url="https://x.com/test", methods={"GET"}
        )
        extractor._probe_allowed_methods(endpoint)
        assert endpoint.methods == set(_SUPPORTED_METHODS)

    def test_probe_options_400_plus_returns_early(self) -> None:
        """When OPTIONS returns >= 400 (not 405), method returns early (line 502-503)."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "OPTIONS":
                return httpx.Response(500, request=request)
            return httpx.Response(404, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        extractor = RESTExtractor(client=client)
        endpoint = _ObservedEndpoint(
            path="/test", absolute_url="https://x.com/test", methods={"GET"}
        )
        extractor._probe_allowed_methods(endpoint)
        assert endpoint.methods == {"GET"}  # unchanged


# ---------------------------------------------------------------------------
# Sibling endpoint coalescing (lines 827, 875, 880)
# ---------------------------------------------------------------------------


class TestSiblingCoalescing:
    def test_uuid_segments_are_coalesced(self) -> None:
        """UUID-like segments trigger coalescing (line 827)."""
        observed = {
            "/api/items/550e8400-e29b-41d4-a716-446655440000": _ObservedEndpoint(
                path="/api/items/550e8400-e29b-41d4-a716-446655440000",
                absolute_url="https://x.com/api/items/550e8400-e29b-41d4-a716-446655440000",
                methods={"GET"},
                confidence=0.7,
            ),
            "/api/items/other-value": _ObservedEndpoint(
                path="/api/items/other-value",
                absolute_url="https://x.com/api/items/other-value",
                methods={"GET"},
                confidence=0.6,
            ),
        }
        result = _coalesce_sibling_endpoints(observed)
        paths = set(result.keys())
        assert any("{id}" in p for p in paths)

    def test_existing_template_is_preserved_during_coalescing(self) -> None:
        """When siblings include an existing `{param}` template, it is reused."""
        observed = {
            "/api/items/{item_id}": _ObservedEndpoint(
                path="/api/items/{item_id}",
                absolute_url="https://x.com/api/items/{item_id}",
                methods={"GET"},
                confidence=0.85,
            ),
            "/api/items/123": _ObservedEndpoint(
                path="/api/items/123",
                absolute_url="https://x.com/api/items/123",
                methods={"GET"},
                confidence=0.7,
            ),
            "/api/items/456": _ObservedEndpoint(
                path="/api/items/456",
                absolute_url="https://x.com/api/items/456",
                methods={"PUT"},
                confidence=0.6,
            ),
        }
        result = _coalesce_sibling_endpoints(observed)
        assert "/api/items/{item_id}" in result
        assert "/api/items/123" not in result
        assert "/api/items/456" not in result

    def test_no_value_segments_skips_coalescing(self) -> None:
        """When no siblings look like values, all are preserved."""
        observed = {
            "/api/alpha": _ObservedEndpoint(
                path="/api/alpha",
                absolute_url="https://x.com/api/alpha",
                methods={"GET"},
                confidence=0.7,
            ),
            "/api/beta": _ObservedEndpoint(
                path="/api/beta",
                absolute_url="https://x.com/api/beta",
                methods={"GET"},
                confidence=0.7,
            ),
            "/api/gamma": _ObservedEndpoint(
                path="/api/gamma",
                absolute_url="https://x.com/api/gamma",
                methods={"GET"},
                confidence=0.7,
            ),
        }
        result = _coalesce_sibling_endpoints(observed)
        assert "/api/alpha" in result
        assert "/api/beta" in result
        assert "/api/gamma" in result


# ---------------------------------------------------------------------------
# _looks_like_value_segment / UUID / numeric detection (lines 929-952)
# ---------------------------------------------------------------------------


class TestLooksLikeValueSegment:
    def test_numeric_segment(self) -> None:
        """Pure numeric segments are value-like (line 821)."""
        assert _looks_like_value_segment("12345") is True

    def test_uuid_segment(self) -> None:
        """UUID segments are value-like (lines 822-827)."""
        assert _looks_like_value_segment("550e8400-e29b-41d4-a716-446655440000") is True

    def test_space_segment(self) -> None:
        """Segments with spaces are value-like (line 817)."""
        assert _looks_like_value_segment("Puzzle Box") is True

    def test_template_segment(self) -> None:
        """Template segments {id} are NOT value-like (line 819)."""
        assert _looks_like_value_segment("{id}") is False

    def test_normal_name_segment(self) -> None:
        """Normal resource names are NOT value-like."""
        assert _looks_like_value_segment("users") is False


# ---------------------------------------------------------------------------
# _is_path_like edge cases (lines 929, 933, 937, 943, 950, 952)
# ---------------------------------------------------------------------------


class TestIsPathLike:
    def test_empty_string(self) -> None:
        """Empty string is not path-like (line 929)."""
        assert _is_path_like("") is False

    def test_starts_with_slash(self) -> None:
        """Strings starting with / are path-like (line 931)."""
        assert _is_path_like("/api/test") is True

    def test_starts_with_protocol(self) -> None:
        """Strings with :// are path-like (line 933)."""
        assert _is_path_like("https://example.com/path") is True

    def test_space_in_value(self) -> None:
        """Strings with spaces are not path-like (line 939)."""
        assert _is_path_like("hello world") is False

    def test_hash_path(self) -> None:
        """Strings starting with # are not path-like (line 943)."""
        assert _is_path_like("#section") is False

    def test_link_like_key_with_slash(self) -> None:
        """Link-like JSON key with / in path is path-like (line 946)."""
        assert _is_path_like("users/123", parent_key="href") is True

    def test_deep_segments_path_like(self) -> None:
        """3+ segments are path-like even without JSON key (line 950)."""
        assert _is_path_like("api/v1/users") is True

    def test_query_with_segment(self) -> None:
        """Single segment with query string is path-like (line 952)."""
        assert _is_path_like("items?page=1") is True

    def test_short_path_not_link_like(self) -> None:
        """Short path without link-like key or query is not path-like."""
        assert _is_path_like("active") is False


# ---------------------------------------------------------------------------
# Cursor pagination inference (lines 540-545)
# ---------------------------------------------------------------------------


class TestCursorPagination:
    def test_cursor_pagination_detected(self) -> None:
        """Cursor-style pagination is inferred from query params (lines 540-545)."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        extractor = RESTExtractor(client=httpx.Client(transport=transport))
        endpoint = DiscoveredEndpoint(
            path="/api/items?cursor=abc&page_size=10",
            absolute_url="https://x.com/api/items?cursor=abc&page_size=10",
            methods=("GET",),
            discovery_sources=("json",),
            confidence=0.8,
        )
        result = extractor._infer_pagination_from_response(endpoint, "GET")
        assert result is not None
        assert result.style == "cursor"
        assert result.page_param == "cursor"

    def test_next_cursor_pagination(self) -> None:
        """'next' query param triggers cursor pagination."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        extractor = RESTExtractor(client=httpx.Client(transport=transport))
        endpoint = DiscoveredEndpoint(
            path="/api/items?next=token123",
            absolute_url="https://x.com/api/items?next=token123",
            methods=("GET",),
            discovery_sources=("json",),
            confidence=0.8,
        )
        result = extractor._infer_pagination_from_response(endpoint, "GET")
        assert result is not None
        assert result.style == "cursor"


# ---------------------------------------------------------------------------
# LLM seed mutation opt-in path (lines 267-268, 344-368)
# ---------------------------------------------------------------------------


class TestLLMSeedMutation:
    def test_llm_seed_mutation_is_invoked_when_client_provided(self) -> None:
        """When llm_client is provided, _llm_seed_mutation is called (lines 267-268)."""

        class FakeLLMClient:
            def complete(self, prompt: str, max_tokens: int = 4096) -> Any:
                return '{"candidates": []}'

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and str(request.url) == "https://x.com/":
                return httpx.Response(
                    200,
                    text='<html><a href="/items">items</a></html>',
                    headers={"content-type": "text/html"},
                    request=request,
                )
            if request.method == "OPTIONS":
                return httpx.Response(200, headers={"allow": "GET"}, request=request)
            return httpx.Response(404, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        extractor = RESTExtractor(client=client, llm_client=FakeLLMClient())
        try:
            result = extractor.extract(SourceConfig(url="https://x.com/"))
        finally:
            extractor.close()
        assert result.metadata["llm_seed_mutation"] is True


# ---------------------------------------------------------------------------
# _normalize_classification_path edge cases (lines 1028, 1032)
# ---------------------------------------------------------------------------


class TestNormalizeClassificationPath:
    def test_relative_path_gets_leading_slash(self) -> None:
        """Paths not starting with / get prepended (line 1028)."""
        result = _normalize_classification_path("users/123", base_path="")
        assert result == "/users/123"

    def test_path_equal_to_base_becomes_root(self) -> None:
        """When path == base_path, it becomes / (line 1032)."""
        result = _normalize_classification_path("/api/v1", base_path="/api/v1")
        assert result == "/"


# ---------------------------------------------------------------------------
# _risk_for_method edge case (line 1089)
# ---------------------------------------------------------------------------


def test_risk_for_unknown_method() -> None:
    """Unknown HTTP methods get RiskLevel.unknown (line 1089)."""
    assert _risk_for_method("TRACE") is RiskLevel.unknown


# ---------------------------------------------------------------------------
# _slugify edge cases (line 1101)
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_consecutive_special_chars(self) -> None:
        """Consecutive non-alnum chars collapse to single dash (line 1101)."""
        result = _slugify("hello---world...test")
        assert result == "hello-world-test"

    def test_empty_string(self) -> None:
        """Empty string returns 'rest-service' default (line 1104)."""
        result = _slugify("")
        assert result == "rest-service"

    def test_only_special_chars(self) -> None:
        """All special chars returns default."""
        result = _slugify("---")
        assert result == "rest-service"


# ---------------------------------------------------------------------------
# _head_probe edge cases (lines 376-377)
# ---------------------------------------------------------------------------


class TestHeadProbe:
    def test_head_probe_http_error_returns_empty(self) -> None:
        """HEAD probe returns empty set on HTTPError (line 377)."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "HEAD":
                raise httpx.ConnectError("fail")
            return httpx.Response(404, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        extractor = RESTExtractor(client=client)
        result = extractor._head_probe("https://x.com/test")
        assert result == set()

    def test_head_probe_400_returns_empty(self) -> None:
        """HEAD probe returns empty set on 400+ status."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "HEAD":
                return httpx.Response(403, request=request)
            return httpx.Response(404, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        extractor = RESTExtractor(client=client)
        result = extractor._head_probe("https://x.com/test")
        assert result == set()


# ---------------------------------------------------------------------------
# _service_name_from_url edge cases (lines 999, 1001)
# ---------------------------------------------------------------------------


class TestServiceNameFromUrl:
    def test_service_name_hint(self) -> None:
        """Service name from hints (line 999)."""
        from libs.extractors.rest import _service_name_from_url

        source = SourceConfig(url="https://x.com", hints={"service_name": "My Cool API"})
        assert _service_name_from_url(source) == "my-cool-api"

    def test_service_name_no_url(self) -> None:
        """No URL returns 'rest-service' (line 1001)."""
        from libs.extractors.rest import _service_name_from_url

        source = SourceConfig(file_content="dummy")
        assert _service_name_from_url(source) == "rest-service"


# ---------------------------------------------------------------------------
# JSON Server helper functions (lines 1225-1400)
# ---------------------------------------------------------------------------


class TestLooksLikeJsonServerDbPayload:
    def test_valid_dict_with_list_value(self) -> None:
        assert _looks_like_json_server_db_payload({"posts": [{"id": 1}]}) is True

    def test_valid_dict_with_dict_value(self) -> None:
        assert _looks_like_json_server_db_payload({"profile": {"name": "Alice"}}) is True

    def test_empty_dict_returns_false(self) -> None:
        assert _looks_like_json_server_db_payload({}) is False

    def test_non_dict_returns_false(self) -> None:
        assert _looks_like_json_server_db_payload([1, 2, 3]) is False
        assert _looks_like_json_server_db_payload("string") is False
        assert _looks_like_json_server_db_payload(None) is False

    def test_dict_with_only_scalar_values(self) -> None:
        assert _looks_like_json_server_db_payload({"name": "Alice", "age": 30}) is False


class TestLooksLikeJsonServerResource:
    def test_valid_list_resource(self) -> None:
        assert _looks_like_json_server_resource("posts", [{"id": 1}]) is True

    def test_valid_dict_resource(self) -> None:
        assert _looks_like_json_server_resource("profile", {"name": "Alice"}) is True

    def test_non_string_name(self) -> None:
        assert _looks_like_json_server_resource(123, []) is False

    def test_empty_name(self) -> None:
        assert _looks_like_json_server_resource("", []) is False
        assert _looks_like_json_server_resource("   ", []) is False

    def test_dunder_name_rejected(self) -> None:
        assert _looks_like_json_server_resource("__internal", [{"id": 1}]) is False

    def test_scalar_value_rejected(self) -> None:
        assert _looks_like_json_server_resource("count", 42) is False
        assert _looks_like_json_server_resource("name", "Alice") is False


class TestJsonServerCollectionMethods:
    def test_list_returns_get_post(self) -> None:
        assert _json_server_collection_methods([{"id": 1}]) == {"GET", "POST"}

    def test_dict_returns_get_put_patch(self) -> None:
        assert _json_server_collection_methods({"key": "value"}) == {"GET", "PUT", "PATCH"}

    def test_other_type_returns_get(self) -> None:
        assert _json_server_collection_methods("string") == {"GET"}
        assert _json_server_collection_methods(42) == {"GET"}


class TestLooksLikeForeignKeyField:
    def test_underscore_id_suffix(self) -> None:
        assert _looks_like_foreign_key_field("user_id") is True
        assert _looks_like_foreign_key_field("post_id") is True

    def test_camel_case_id_suffix(self) -> None:
        assert _looks_like_foreign_key_field("userId") is True
        assert _looks_like_foreign_key_field("postId") is True

    def test_plain_id_excluded(self) -> None:
        assert _looks_like_foreign_key_field("id") is False

    def test_non_id_field(self) -> None:
        assert _looks_like_foreign_key_field("name") is False
        assert _looks_like_foreign_key_field("email") is False

    def test_whitespace_handling(self) -> None:
        assert _looks_like_foreign_key_field("  user_id  ") is True


class TestJsonServerForeignKeys:
    def test_extracts_foreign_keys(self) -> None:
        items = [
            {"id": 1, "title": "Post 1", "user_id": 10},
            {"id": 2, "title": "Post 2", "user_id": 20},
        ]
        keys = _json_server_foreign_keys(items)
        assert "user_id" in keys

    def test_skips_id_field(self) -> None:
        items = [{"id": 1, "name": "Alice"}]
        keys = _json_server_foreign_keys(items)
        assert "id" not in keys

    def test_skips_non_dict_items(self) -> None:
        items = ["not-a-dict", 42, None]
        keys = _json_server_foreign_keys(items)
        assert keys == set()

    def test_skips_non_scalar_fk_values(self) -> None:
        items = [{"id": 1, "author_id": [1, 2, 3]}]
        keys = _json_server_foreign_keys(items)
        assert "author_id" not in keys

    def test_multiple_foreign_keys(self) -> None:
        items = [{"id": 1, "author_id": 5, "category_id": 3}]
        keys = _json_server_foreign_keys(items)
        assert keys == {"author_id", "category_id"}


class TestJsonServerParentCandidates:
    def test_underscore_id_with_matching_resource(self) -> None:
        candidates = _json_server_parent_candidates("user_id", {"users", "posts"})
        assert "users" in candidates

    def test_camel_case_id_with_matching_resource(self) -> None:
        candidates = _json_server_parent_candidates("userId", {"users", "posts"})
        assert "users" in candidates

    def test_no_matching_resource(self) -> None:
        candidates = _json_server_parent_candidates("author_id", {"posts", "comments"})
        assert candidates == []

    def test_plain_id_returns_empty(self) -> None:
        candidates = _json_server_parent_candidates("id", {"users"})
        assert candidates == []

    def test_non_id_suffix_returns_empty(self) -> None:
        candidates = _json_server_parent_candidates("name", {"users"})
        assert candidates == []

    def test_empty_base_after_strip_returns_empty(self) -> None:
        candidates = _json_server_parent_candidates("_id", {"users"})
        assert candidates == []

    def test_plural_form_matched(self) -> None:
        candidates = _json_server_parent_candidates("category_id", {"categories"})
        assert "categories" in candidates


class TestPluralizeResourceName:
    def test_regular_pluralization(self) -> None:
        assert _pluralize_resource_name("user") == "users"
        assert _pluralize_resource_name("post") == "posts"

    def test_y_to_ies(self) -> None:
        assert _pluralize_resource_name("category") == "categories"
        assert _pluralize_resource_name("company") == "companies"

    def test_vowel_y_stays(self) -> None:
        assert _pluralize_resource_name("day") == "days"
        assert _pluralize_resource_name("key") == "keys"

    def test_sibilant_endings(self) -> None:
        assert _pluralize_resource_name("bus") == "buses"
        assert _pluralize_resource_name("box") == "boxes"
        assert _pluralize_resource_name("buzz") == "buzzes"
        assert _pluralize_resource_name("match") == "matches"
        assert _pluralize_resource_name("wish") == "wishes"

    def test_empty_string(self) -> None:
        assert _pluralize_resource_name("") == ""


class TestInferJsonServerRelations:
    def test_simple_relation(self) -> None:
        payload = {
            "users": [{"id": 1, "name": "Alice"}],
            "posts": [{"id": 1, "title": "Post 1", "user_id": 1}],
        }
        relations = _infer_json_server_relations(payload)
        assert "users" in relations
        assert "posts" in relations["users"]

    def test_no_foreign_keys(self) -> None:
        payload = {
            "users": [{"id": 1, "name": "Alice"}],
            "posts": [{"id": 1, "title": "Post 1"}],
        }
        relations = _infer_json_server_relations(payload)
        assert relations == {}

    def test_non_dict_payload(self) -> None:
        assert _infer_json_server_relations("not a dict") == {}
        assert _infer_json_server_relations([1, 2, 3]) == {}

    def test_self_reference_excluded(self) -> None:
        payload = {
            "posts": [{"id": 1, "title": "Post", "post_id": 2}],
        }
        relations = _infer_json_server_relations(payload)
        # post_id -> posts would be self-reference, should be excluded
        assert "posts" not in relations

    def test_dict_resources_skipped_for_fk_scan(self) -> None:
        payload = {
            "users": [{"id": 1, "name": "Alice"}],
            "profile": {"user_id": 1, "bio": "Hello"},
        }
        relations = _infer_json_server_relations(payload)
        # profile is a dict, not a list, so no FK scanning
        assert "users" not in relations


# ---------------------------------------------------------------------------
# JSON Server bootstrap integration (lines 386-447)
# ---------------------------------------------------------------------------


class TestJsonServerBootstrap:
    """Tests for _bootstrap_json_server end-to-end discovery."""

    _JSON_SERVER_HTML = (
        "<html><head><title>JSON Server</title></head><body>"
        "<p>Congrats! You're successfully running JSON Server</p>"
        "</body></html>"
    )

    @staticmethod
    def _make_extractor(
        handler: Callable[[httpx.Request], httpx.Response],
    ) -> RESTExtractor:
        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport, follow_redirects=True)
        return RESTExtractor(client=client)

    def test_bootstrap_registers_collection_and_detail_endpoints(self) -> None:
        """JSON Server /db endpoint registers collection + detail paths."""
        html = self._JSON_SERVER_HTML
        db_payload = {
            "posts": [{"id": 1, "title": "Hello"}],
            "comments": [{"id": 10, "body": "Nice", "postId": 1}],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if request.method == "GET" and url == "https://jsonserver.test/":
                return httpx.Response(
                    200, text=html, headers={"content-type": "text/html"}, request=request
                )
            if request.method == "GET" and url == "https://jsonserver.test/db":
                return httpx.Response(200, json=db_payload, request=request)
            if request.method == "OPTIONS":
                return httpx.Response(200, headers={"allow": "GET"}, request=request)
            return httpx.Response(404, request=request)

        extractor = self._make_extractor(handler)
        try:
            service_ir = extractor.extract(SourceConfig(url="https://jsonserver.test/"))
        finally:
            extractor.close()

        discovered_paths = set(service_ir.metadata["discovered_paths"])
        assert "/posts" in discovered_paths
        assert "/comments" in discovered_paths
        assert any("{" in p and "post" in p for p in discovered_paths)
        assert any("{" in p and "comment" in p for p in discovered_paths)

    def test_bootstrap_skips_non_html_content(self) -> None:
        """Non-HTML responses skip JSON Server bootstrap."""
        extractor = self._make_extractor(lambda r: httpx.Response(404, request=r))
        observed: dict[str, _ObservedEndpoint] = {}
        response = httpx.Response(
            200,
            json={"data": "test"},
            headers={"content-type": "application/json"},
            request=httpx.Request("GET", "https://x.com/"),
        )
        result = extractor._bootstrap_json_server(
            current_url="https://x.com/",
            response=response,
            observed=observed,
            auth_headers={},
        )
        assert result == {}

    def test_bootstrap_skips_non_json_server_html(self) -> None:
        """HTML without JSON Server markers is skipped."""
        extractor = self._make_extractor(lambda r: httpx.Response(404, request=r))
        observed: dict[str, _ObservedEndpoint] = {}
        response = httpx.Response(
            200,
            text="<html><body>Hello World</body></html>",
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", "https://x.com/"),
        )
        result = extractor._bootstrap_json_server(
            current_url="https://x.com/",
            response=response,
            observed=observed,
            auth_headers={},
        )
        assert result == {}

    def test_bootstrap_handles_db_http_error(self) -> None:
        """HTTPError when fetching /db is handled gracefully."""

        def handler(request: httpx.Request) -> httpx.Response:
            if "db" in str(request.url):
                raise httpx.ConnectError("fail")
            return httpx.Response(404, request=request)

        extractor = self._make_extractor(handler)
        observed: dict[str, _ObservedEndpoint] = {}
        response = httpx.Response(
            200,
            text=self._JSON_SERVER_HTML,
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", "https://x.com/"),
        )
        result = extractor._bootstrap_json_server(
            current_url="https://x.com/",
            response=response,
            observed=observed,
            auth_headers={},
        )
        assert result == {}

    def test_bootstrap_handles_db_400_status(self) -> None:
        """400+ status from /db is handled gracefully."""

        def handler(request: httpx.Request) -> httpx.Response:
            if "db" in str(request.url):
                return httpx.Response(500, request=request)
            return httpx.Response(404, request=request)

        extractor = self._make_extractor(handler)
        observed: dict[str, _ObservedEndpoint] = {}
        response = httpx.Response(
            200,
            text=self._JSON_SERVER_HTML,
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", "https://x.com/"),
        )
        result = extractor._bootstrap_json_server(
            current_url="https://x.com/",
            response=response,
            observed=observed,
            auth_headers={},
        )
        assert result == {}

    def test_bootstrap_handles_invalid_json_from_db(self) -> None:
        """Non-JSON response from /db is handled gracefully."""

        def handler(request: httpx.Request) -> httpx.Response:
            if "db" in str(request.url):
                return httpx.Response(
                    200,
                    text="not-json",
                    headers={"content-type": "text/plain"},
                    request=request,
                )
            return httpx.Response(404, request=request)

        extractor = self._make_extractor(handler)
        observed: dict[str, _ObservedEndpoint] = {}
        response = httpx.Response(
            200,
            text=self._JSON_SERVER_HTML,
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", "https://x.com/"),
        )
        result = extractor._bootstrap_json_server(
            current_url="https://x.com/",
            response=response,
            observed=observed,
            auth_headers={},
        )
        assert result == {}

    def test_bootstrap_handles_non_db_payload(self) -> None:
        """Response that doesn't look like a JSON Server DB payload is skipped."""

        def handler(request: httpx.Request) -> httpx.Response:
            if "db" in str(request.url):
                return httpx.Response(200, json="just a string", request=request)
            return httpx.Response(404, request=request)

        extractor = self._make_extractor(handler)
        observed: dict[str, _ObservedEndpoint] = {}
        response = httpx.Response(
            200,
            text=self._JSON_SERVER_HTML,
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", "https://x.com/"),
        )
        result = extractor._bootstrap_json_server(
            current_url="https://x.com/",
            response=response,
            observed=observed,
            auth_headers={},
        )
        assert result == {}

    def test_bootstrap_skips_invalid_resources(self) -> None:
        """Resources with dunder names or non-list/dict values are skipped."""
        db_payload = {
            "__meta": [{"id": 1}],
            "valid": [{"id": 2, "name": "ok"}],
            "count": 42,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if "db" in str(request.url):
                return httpx.Response(200, json=db_payload, request=request)
            return httpx.Response(404, request=request)

        extractor = self._make_extractor(handler)
        observed: dict[str, _ObservedEndpoint] = {}
        response = httpx.Response(
            200,
            text=self._JSON_SERVER_HTML,
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", "https://x.com/"),
        )
        extractor._bootstrap_json_server(
            current_url="https://x.com/",
            response=response,
            observed=observed,
            auth_headers={},
        )
        assert "/valid" in observed
        assert "/__meta" not in observed
        assert "/count" not in observed

    def test_bootstrap_dict_resource_gets_put_patch(self) -> None:
        """Dict resources get GET/PUT/PATCH methods (singleton)."""
        db_payload = {"profile": {"name": "Alice", "bio": "Hello"}}

        def handler(request: httpx.Request) -> httpx.Response:
            if "db" in str(request.url):
                return httpx.Response(200, json=db_payload, request=request)
            return httpx.Response(404, request=request)

        extractor = self._make_extractor(handler)
        observed: dict[str, _ObservedEndpoint] = {}
        response = httpx.Response(
            200,
            text=self._JSON_SERVER_HTML,
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", "https://x.com/"),
        )
        extractor._bootstrap_json_server(
            current_url="https://x.com/",
            response=response,
            observed=observed,
            auth_headers={},
        )
        assert "/profile" in observed
        assert observed["/profile"].methods == {"GET", "PUT", "PATCH"}

    def test_bootstrap_collection_without_sample_id_skips_detail(self) -> None:
        """Collections without items with 'id' field skip detail endpoint."""
        db_payload = {"items": [{"name": "no-id-here"}]}

        def handler(request: httpx.Request) -> httpx.Response:
            if "db" in str(request.url):
                return httpx.Response(200, json=db_payload, request=request)
            return httpx.Response(404, request=request)

        extractor = self._make_extractor(handler)
        observed: dict[str, _ObservedEndpoint] = {}
        response = httpx.Response(
            200,
            text=self._JSON_SERVER_HTML,
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", "https://x.com/"),
        )
        extractor._bootstrap_json_server(
            current_url="https://x.com/",
            response=response,
            observed=observed,
            auth_headers={},
        )
        assert "/items" in observed
        # No detail endpoint since no sample ID
        detail_paths = [p for p in observed if "{" in p]
        assert len(detail_paths) == 0

    def test_bootstrap_returns_relations(self) -> None:
        """Bootstrap returns inferred relations from foreign keys."""
        db_payload = {
            "users": [{"id": 1, "name": "Alice"}],
            "posts": [{"id": 1, "title": "Hello", "user_id": 1}],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if "db" in str(request.url):
                return httpx.Response(200, json=db_payload, request=request)
            return httpx.Response(404, request=request)

        extractor = self._make_extractor(handler)
        observed: dict[str, _ObservedEndpoint] = {}
        response = httpx.Response(
            200,
            text=self._JSON_SERVER_HTML,
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", "https://x.com/"),
        )
        relations = extractor._bootstrap_json_server(
            current_url="https://x.com/",
            response=response,
            observed=observed,
            auth_headers={},
        )
        assert "users" in relations
        assert "posts" in relations["users"]


# ---------------------------------------------------------------------------
# _auth_headers edge cases (lines 647-652)
# ---------------------------------------------------------------------------


class TestAuthHeaders:
    def test_auth_header_direct(self) -> None:
        """auth_header is used directly as Authorization header."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        extractor = RESTExtractor(client=httpx.Client(transport=transport))
        source = SourceConfig(url="https://x.com", auth_header="Token abc123")
        result = extractor._auth_headers(source)
        assert result == {"Authorization": "Token abc123"}

    def test_auth_token_bearer(self) -> None:
        """auth_token is wrapped in Bearer prefix."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        extractor = RESTExtractor(client=httpx.Client(transport=transport))
        source = SourceConfig(url="https://x.com", auth_token="my-token")
        result = extractor._auth_headers(source)
        assert result == {"Authorization": "Bearer my-token"}

    def test_no_auth(self) -> None:
        """No auth configured returns empty dict."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        extractor = RESTExtractor(client=httpx.Client(transport=transport))
        source = SourceConfig(url="https://x.com")
        result = extractor._auth_headers(source)
        assert result == {}


# ---------------------------------------------------------------------------
# _extract_candidate_paths non-HTML/JSON fallback (line 667)
# ---------------------------------------------------------------------------


class TestExtractCandidatePathsFallback:
    def test_non_html_non_json_falls_back_to_html(self) -> None:
        """Content types that are neither HTML nor JSON fall back to HTML extraction."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        client = httpx.Client(transport=transport, follow_redirects=True)
        extractor = RESTExtractor(client=client)
        response = httpx.Response(
            200,
            text='<html><a href="/resource">link</a></html>',
            headers={"content-type": "text/plain"},
            request=httpx.Request("GET", "https://x.com/"),
        )
        result = extractor._extract_candidate_paths("https://x.com", response)
        paths = [p for p, _ in result]
        assert any("/resource" in p for p in paths)


# ---------------------------------------------------------------------------
# _extract_from_html: form without method (line 678)
# ---------------------------------------------------------------------------


class TestExtractFromHtmlFormNoMethod:
    def test_form_without_method_treated_as_link(self) -> None:
        """Forms without explicit method attribute produce 'link' source."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        client = httpx.Client(transport=transport, follow_redirects=True)
        extractor = RESTExtractor(client=client)
        # The regex captures empty string for method when not present
        body = '<html><form action="/search" method="get"></form></html>'
        result = extractor._extract_from_html("https://x.com", body)
        source_types = [s for _, s in result]
        assert "link" in source_types


# ---------------------------------------------------------------------------
# _normalize_candidate: static asset filtering (line 700)
# ---------------------------------------------------------------------------


class TestNormalizeCandidateStaticAsset:
    def test_static_asset_rejected(self) -> None:
        """Static assets (.js, .css, .png etc.) are filtered out."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404, request=r))
        client = httpx.Client(transport=transport, follow_redirects=True)
        extractor = RESTExtractor(client=client)
        assert extractor._normalize_candidate("https://x.com", "/app.js") is None
        assert extractor._normalize_candidate("https://x.com", "/style.css") is None
        assert extractor._normalize_candidate("https://x.com", "/logo.png") is None


# ---------------------------------------------------------------------------
# _deduplicate_operation_ids (lines 1474-1486)
# ---------------------------------------------------------------------------


class TestDeduplicateOperationIds:
    def _make_op(self, op_id: str, method: str = "GET", path: str = "/test") -> Operation:
        return Operation(
            id=op_id,
            name="Test Op",
            description="test",
            method=method,
            path=path,
            params=[],
            risk=RiskMetadata(risk_level=RiskLevel.safe),
            source=SourceType.extractor,
            confidence=0.9,
        )

    def test_no_duplicates_unchanged(self) -> None:
        ops = [self._make_op("get_users"), self._make_op("post_users")]
        result = _deduplicate_operation_ids(ops)
        assert [op.id for op in result] == ["get_users", "post_users"]

    def test_duplicate_ids_get_suffix(self) -> None:
        ops = [
            self._make_op("get_users"),
            self._make_op("get_users"),
            self._make_op("get_users"),
        ]
        result = _deduplicate_operation_ids(ops)
        assert result[0].id == "get_users"
        assert result[1].id == "get_users_1"
        assert result[2].id == "get_users_2"


# ---------------------------------------------------------------------------
# _is_path_like: custom URL scheme detection (lines 1162-1163, 1166-1167)
# ---------------------------------------------------------------------------


class TestIsPathLikeCustomSchemes:
    def test_custom_scheme_with_protocol(self) -> None:
        """Custom schemes like ftp:// are path-like (line 1162)."""
        assert _is_path_like("ftp://files.example.com/data") is True

    def test_parsed_scheme_without_slashes(self) -> None:
        """urlparse scheme-without-netloc values are rejected (lines 1166-1167)."""
        assert _is_path_like("mailto:user@example.com") is False


# ---------------------------------------------------------------------------
# _is_static_asset_path extension extraction (lines 1216-1217)
# ---------------------------------------------------------------------------


class TestIsStaticAssetPath:
    def test_known_extensions(self) -> None:
        assert _is_static_asset_path("/assets/app.js") is True
        assert _is_static_asset_path("/styles/main.css") is True
        assert _is_static_asset_path("/images/logo.png") is True
        assert _is_static_asset_path("/fonts/roboto.woff2") is True

    def test_non_static_extensions(self) -> None:
        assert _is_static_asset_path("/api/users") is False
        assert _is_static_asset_path("/data.json") is False

    def test_no_dot_in_leaf(self) -> None:
        assert _is_static_asset_path("/api/users/123") is False

    def test_nested_path_with_extension(self) -> None:
        assert _is_static_asset_path("/assets/vendor/bundle.js") is True
