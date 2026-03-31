"""REST extractor with discovery and classifier-assisted endpoint normalization."""

from __future__ import annotations

import hashlib
import json
import logging
from urllib.parse import unquote, urljoin, urlparse

import httpx

from libs.extractors.base import SourceConfig
from libs.extractors.llm_seed_mutation import (
    SeedMutationLLMClient,
    generate_seed_candidates,
)
from libs.extractors.rest_classification import (  # noqa: F401
    _SIBLING_COALESCE_THRESHOLD,
    EndpointClassification,
    EndpointClassifier,
    HeuristicRESTClassifier,
    _classification_to_operation,
    _coalesce_sibling_endpoints,
    _deduplicate_concrete_paths,
    _deduplicate_operation_ids,
    _default_operation_name,
    _infer_pagination_from_response,
    _looks_like_value_segment,
    _operation_id,
    _operation_source,
    _risk_for_method,
)
from libs.extractors.rest_probing import (  # noqa: F401
    _DEFAULT_SUB_RESOURCES,
    _HTML_FORM_PATTERN,
    _HTML_LINK_PATTERN,
    _JSON_SERVER_MARKERS,
    _LINK_LIKE_JSON_KEYS,
    _MAX_RETRY_ATTEMPTS,
    _PATH_PARAM_PATTERN,
    _STATIC_ASSET_EXTENSIONS,
    _SUB_RESOURCE_HINTS,
    _SUPPORTED_METHODS,
    DiscoveredEndpoint,
    _bootstrap_collection_registry,
    _bootstrap_current_json_entrypoint,
    _bootstrap_json_server,
    _collection_registry_candidates,
    _common_sub_resources,
    _directus_collection_candidates,
    _extract_candidate_paths,
    _extract_collection_items,
    _extract_from_html,
    _extract_from_json,
    _head_probe,
    _infer_json_server_relations,
    _infer_sub_resources,
    _int_like,
    _is_link_like_json_key,
    _is_path_like,
    _is_static_asset_path,
    _join_relative_url,
    _json_server_collection_methods,
    _json_server_foreign_keys,
    _json_server_parent_candidates,
    _looks_like_foreign_key_field,
    _looks_like_json_server_db_payload,
    _looks_like_json_server_html,
    _looks_like_json_server_resource,
    _normalize_candidate,
    _normalize_classification_path,
    _normalize_path,
    _normalize_same_origin_candidate,
    _ObservedEndpoint,
    _pagination_followup_urls,
    _pagination_followups,
    _pagination_state,
    _pluralize_resource_name,
    _pocketbase_collection_candidates,
    _probe_allowed_methods,
    _probe_and_register,
    _register_endpoint,
    _RegistryCollectionCandidate,
    _request,
    _resource_param_name_from_path,
    _retry_after_seconds,
    _sample_resource_id,
    _set_query_param,
    _shared_query_suffix,
    _slugify,
    _walk_json_link_candidates,
)
from libs.extractors.rest_schema import (  # noqa: F401
    _params_from_path_and_query,
    _path_param_defaults_by_operation_path,
    _path_param_defaults_from_endpoint,
)
from libs.ir.models import (
    AuthConfig,
    AuthType,
    ServiceIR,
)

logger = logging.getLogger(__name__)

_MAX_INFERENCE_PASSES = 3


class RESTExtractor:
    """Discover REST endpoints from a base URL and produce ServiceIR."""

    protocol_name: str = "rest"

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        classifier: EndpointClassifier | None = None,
        max_pages: int = 8,
        llm_client: SeedMutationLLMClient | None = None,
    ) -> None:
        self._client = client or httpx.Client(follow_redirects=True, timeout=10.0)
        self._owns_client = client is None
        self._classifier = classifier or HeuristicRESTClassifier()
        self._max_pages = max_pages
        self._llm_client = llm_client

    def detect(self, source: SourceConfig) -> float:
        if source.hints.get("protocol") == "rest":
            return 1.0
        if not source.url:
            return 0.0

        parsed = urlparse(source.url)
        if parsed.scheme not in {"http", "https"}:
            return 0.0
        if "openapi" in source.url.lower() or "swagger" in source.url.lower():
            return 0.1
        if "graphql" in source.url.lower():
            return 0.1
        return 0.55

    def extract(self, source: SourceConfig) -> ServiceIR:
        if not source.url:
            raise ValueError("RESTExtractor requires source.url pointing at a REST API base URL")

        discovered_endpoints = self._discover(
            source.url,
            auth_headers=self._auth_headers(source),
        )
        if not discovered_endpoints:
            raise ValueError(f"No REST endpoints discovered from {source.url}")

        classified = self._classifier.classify(
            base_url=source.url,
            endpoints=discovered_endpoints,
        )
        if not classified:
            raise ValueError(f"Classifier returned no REST operations for {source.url}")

        base_path = _discovery_base_path(source.url, discovered_endpoints)
        path_param_defaults = _path_param_defaults_by_operation_path(
            discovered_endpoints,
            base_path=base_path,
        )
        operations = [
            _classification_to_operation(
                classification,
                base_path=base_path,
                path_param_defaults=path_param_defaults.get(
                    _normalize_classification_path(classification.path, base_path=base_path),
                    {},
                ),
                classifier=self._classifier,
            )
            for classification in classified
        ]
        operations = _deduplicate_operation_ids(operations)
        source_hash = self._discovery_hash(source.url, discovered_endpoints, classified)

        return ServiceIR(
            source_url=source.url,
            source_hash=source_hash,
            protocol="rest",
            service_name=_service_name_from_url(source),
            service_description=f"Discovered REST API at {source.url}",
            base_url=_runtime_base_url(source.url, base_path=base_path),
            auth=AuthConfig(type=AuthType.none),
            operations=operations,
            metadata={
                "discovered_paths": [endpoint.path for endpoint in discovered_endpoints],
                "classifier": self._classifier.__class__.__name__,
                "base_path": base_path or "/",
                "discovery_entrypoint": source.url,
                "llm_seed_mutation": self._llm_client is not None,
            },
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _discover(
        self,
        base_url: str,
        *,
        auth_headers: dict[str, str] | None = None,
    ) -> list[DiscoveredEndpoint]:
        observed: dict[str, _ObservedEndpoint] = {}
        json_server_relations: dict[str, set[str]] = {}
        queue = [(base_url, 0)]
        visited_pages: set[str] = set()
        headers = auth_headers or {}

        while queue and len(visited_pages) < self._max_pages:
            current_url, depth = queue.pop(0)
            if current_url in visited_pages:
                continue
            visited_pages.add(current_url)

            try:
                response = _request(self._client, "GET", current_url, headers=headers)
            except httpx.HTTPError:
                continue
            if response.status_code >= 400:
                continue

            discovered_relations = _bootstrap_json_server(
                self._client,
                current_url=current_url,
                response=response,
                observed=observed,
                auth_headers=headers,
            )
            for parent_name, child_names in discovered_relations.items():
                json_server_relations.setdefault(parent_name, set()).update(child_names)

            _bootstrap_current_json_entrypoint(
                self._client,
                current_url=current_url,
                response=response,
                observed=observed,
                auth_headers=headers,
            )
            for candidate in _bootstrap_collection_registry(
                self._client,
                current_url=current_url,
                response=response,
                observed=observed,
            ):
                if depth + 1 < self._max_pages and candidate not in visited_pages:
                    queue.append((candidate, depth + 1))
            for candidate in _pagination_followups(
                self._client,
                current_url=current_url,
                response=response,
            ):
                if depth + 1 < self._max_pages and candidate not in visited_pages:
                    queue.append((candidate, depth + 1))

            for path, source_name in _extract_candidate_paths(base_url, response):
                candidate_url = urljoin(base_url, path)
                normalized = _normalize_path(candidate_url)
                endpoint = observed.setdefault(
                    normalized,
                    _ObservedEndpoint(path=normalized, absolute_url=candidate_url),
                )
                if source_name in {"link", "json"}:
                    endpoint.methods.add("GET")
                    endpoint.confidence = max(
                        endpoint.confidence,
                        0.75 if source_name == "link" else 0.7,
                    )
                    if depth + 1 < self._max_pages and candidate_url not in visited_pages:
                        queue.append((candidate_url, depth + 1))
                elif source_name == "form":
                    endpoint.methods.add("POST")
                    endpoint.confidence = max(endpoint.confidence, 0.85)
                else:
                    endpoint.confidence = max(endpoint.confidence, 0.7)
                endpoint.sources.add(source_name)

        for endpoint in observed.values():
            _probe_allowed_methods(self._client, endpoint)

        # Phase 2: Iterative URI-based resource hierarchy inference.
        # When we discover a collection endpoint like /api/users, probe
        # /api/users/{id} via OPTIONS.  When a detail endpoint like
        # /api/users/{id} is discovered, probe common sub-resource patterns.
        # Running iteratively lets depth-2+ paths (e.g. /users/{id}/posts)
        # be discovered from inferred depth-1 endpoints.
        for _pass in range(_MAX_INFERENCE_PASSES):
            inferred = _infer_sub_resources(
                self._client,
                base_url,
                observed,
                json_server_relations=json_server_relations,
                auth_headers=headers,
            )
            if not inferred:
                break
            observed.update(inferred)

        # Phase 3: LLM-driven seed mutation (opt-in).
        if self._llm_client is not None:
            llm_inferred = self._llm_seed_mutation(base_url, observed)
            observed.update(llm_inferred)

        observed = _deduplicate_concrete_paths(observed)
        observed = _coalesce_sibling_endpoints(observed)

        return [
            endpoint.freeze() for endpoint in sorted(observed.values(), key=lambda item: item.path)
        ]

    def _llm_seed_mutation(
        self,
        base_url: str,
        observed: dict[str, _ObservedEndpoint],
    ) -> dict[str, _ObservedEndpoint]:
        """Use LLM to generate and validate additional endpoint candidates."""
        assert self._llm_client is not None

        discovered_info = [
            {"path": ep.path, "methods": sorted(ep.methods)} for ep in observed.values()
        ]

        candidates = generate_seed_candidates(
            llm_client=self._llm_client,
            base_url=base_url,
            discovered_paths=discovered_info,
        )

        llm_inferred: dict[str, _ObservedEndpoint] = {}
        for candidate in candidates:
            if candidate.path in observed:
                continue
            candidate_url = urljoin(base_url, candidate.path)
            _probe_and_register(
                self._client,
                candidate.path,
                candidate_url,
                llm_inferred,
                source="llm_seed",
            )

        return llm_inferred

    def _auth_headers(self, source: SourceConfig) -> dict[str, str]:
        if source.auth_header:
            return {"Authorization": source.auth_header}
        if source.auth_token:
            return {"Authorization": f"Bearer {source.auth_token}"}
        return {}

    # -- Thin wrappers that delegate to standalone submodule functions ------
    # These preserve the instance-method interface for existing callers.

    def _head_probe(
        self, absolute_url: str, *, auth_headers: dict[str, str] | None = None
    ) -> set[str]:
        return _head_probe(self._client, absolute_url, auth_headers=auth_headers)

    def _probe_and_register(
        self,
        path: str,
        absolute_url: str,
        target: dict[str, _ObservedEndpoint],
        *,
        source: str = "inferred",
        auth_headers: dict[str, str] | None = None,
    ) -> None:
        _probe_and_register(
            self._client, path, absolute_url, target, source=source, auth_headers=auth_headers
        )

    def _probe_allowed_methods(
        self, endpoint: _ObservedEndpoint, *, auth_headers: dict[str, str] | None = None
    ) -> None:
        _probe_allowed_methods(self._client, endpoint, auth_headers=auth_headers)

    def _infer_pagination_from_response(self, endpoint: DiscoveredEndpoint, method: str) -> object:
        return _infer_pagination_from_response(endpoint, method)

    def _infer_sub_resources(
        self,
        base_url: str,
        observed: dict[str, _ObservedEndpoint],
        *,
        json_server_relations: dict[str, set[str]] | None = None,
        auth_headers: dict[str, str] | None = None,
    ) -> dict[str, _ObservedEndpoint]:
        return _infer_sub_resources(
            self._client,
            base_url,
            observed,
            json_server_relations=json_server_relations,
            auth_headers=auth_headers,
        )

    def _extract_candidate_paths(
        self, base_url: str, response: httpx.Response
    ) -> list[tuple[str, str]]:
        return _extract_candidate_paths(base_url, response)

    def _normalize_candidate(self, base_url: str, candidate: str) -> str | None:
        return _normalize_candidate(base_url, candidate)

    def _normalize_path(self, absolute_url: str) -> str:
        return _normalize_path(absolute_url)

    def _extract_from_html(self, base_url: str, body: str) -> list[tuple[str, str]]:
        return _extract_from_html(base_url, body)

    def _register_endpoint(
        self,
        target: dict[str, _ObservedEndpoint],
        *,
        path: str,
        absolute_url: str,
        methods: set[str],
        source: str,
        confidence: float,
    ) -> None:
        _register_endpoint(
            target,
            path=path,
            absolute_url=absolute_url,
            methods=methods,
            source=source,
            confidence=confidence,
        )

    def _bootstrap_json_server(
        self,
        *,
        current_url: str,
        response: httpx.Response,
        observed: dict[str, _ObservedEndpoint],
        auth_headers: dict[str, str],
    ) -> dict[str, set[str]]:
        return _bootstrap_json_server(
            self._client,
            current_url=current_url,
            response=response,
            observed=observed,
            auth_headers=auth_headers,
        )

    def _bootstrap_current_json_entrypoint(
        self,
        *,
        current_url: str,
        response: httpx.Response,
        observed: dict[str, _ObservedEndpoint],
        auth_headers: dict[str, str],
    ) -> None:
        _bootstrap_current_json_entrypoint(
            self._client,
            current_url=current_url,
            response=response,
            observed=observed,
            auth_headers=auth_headers,
        )

    def _classification_to_operation(
        self,
        classification: EndpointClassification,
        *,
        base_path: str,
        path_param_defaults: dict[str, str] | None = None,
    ) -> object:
        return _classification_to_operation(
            classification,
            base_path=base_path,
            path_param_defaults=path_param_defaults,
            classifier=self._classifier,
        )

    def _operation_source(self) -> object:
        return _operation_source(self._classifier)

    def _discovery_hash(
        self,
        base_url: str,
        endpoints: list[DiscoveredEndpoint],
        classified: list[EndpointClassification],
    ) -> str:
        payload = {
            "base_url": base_url,
            "endpoints": [
                {
                    "path": endpoint.path,
                    "methods": endpoint.methods,
                    "sources": endpoint.discovery_sources,
                    "confidence": endpoint.confidence,
                }
                for endpoint in endpoints
            ],
            "classified": [
                {
                    "path": endpoint.path,
                    "method": endpoint.method,
                    "name": endpoint.name,
                    "confidence": endpoint.confidence,
                    "tags": endpoint.tags,
                }
                for endpoint in classified
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# URL utility functions (tightly coupled to the class)
# ---------------------------------------------------------------------------


def _service_name_from_url(source: SourceConfig) -> str:
    if source.hints.get("service_name"):
        return _slugify(source.hints["service_name"])
    if not source.url:
        return "rest-service"
    parsed = urlparse(source.url)
    candidate = parsed.hostname or parsed.path.strip("/") or "rest-service"
    return _slugify(candidate)


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _normalized_base_path(url: str) -> str:
    parsed = urlparse(url)
    raw_path = unquote(parsed.path or "")
    if not raw_path or raw_path == "/":
        return ""
    return raw_path.rstrip("/")


def _runtime_base_url(url: str, *, base_path: str | None = None) -> str:
    resolved_base_path = _normalized_base_path(url) if base_path is None else base_path
    return f"{_origin(url)}{resolved_base_path}"


def _discovery_base_path(url: str, endpoints: list[DiscoveredEndpoint]) -> str:
    entrypoint_path = _normalized_base_path(url)
    if not entrypoint_path:
        return ""
    candidate_paths = [entrypoint_path] + [
        _normalized_base_path(endpoint.path) for endpoint in endpoints
    ]
    return _common_path_prefix(candidate_paths)


def _common_path_prefix(paths: list[str]) -> str:
    normalized_paths = [
        [segment for segment in path.split("/") if segment]
        for path in paths
        if path not in {"", "/"}
    ]
    if not normalized_paths:
        return ""
    prefix = normalized_paths[0]
    for path_segments in normalized_paths[1:]:
        shared: list[str] = []
        for left, right in zip(prefix, path_segments, strict=False):
            if left != right:
                break
            shared.append(left)
        prefix = shared
        if not prefix:
            return ""
    return f"/{'/'.join(prefix)}" if prefix else ""


__all__ = [
    "DiscoveredEndpoint",
    "EndpointClassification",
    "EndpointClassifier",
    "HeuristicRESTClassifier",
    "RESTExtractor",
]
