"""REST extractor with discovery and classifier-assisted endpoint normalization."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from html import unescape
from typing import Any, Protocol
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse

import httpx

from libs.extractors.base import SourceConfig
from libs.extractors.llm_seed_mutation import (
    SeedMutationLLMClient,
    generate_seed_candidates,
)
from libs.ir.models import (
    AuthConfig,
    AuthType,
    Operation,
    PaginationConfig,
    Param,
    ResponseStrategy,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)

logger = logging.getLogger(__name__)

_HTML_LINK_PATTERN = re.compile(r"""href=["']([^"'#]+)["']""", re.IGNORECASE)
_HTML_FORM_PATTERN = re.compile(
    r"""<form[^>]*action=["']([^"'#]+)["'][^>]*?(?:method=["']([^"']+)["'])?""",
    re.IGNORECASE,
)
_PATH_PARAM_PATTERN = re.compile(r"{([^{}]+)}")
_SUPPORTED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_MAX_INFERENCE_PASSES = 3
_STATIC_ASSET_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".css",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".map",
    ".mjs",
    ".png",
    ".svg",
    ".ttf",
    ".webp",
    ".woff",
    ".woff2",
}
_JSON_SERVER_MARKERS = (
    "<title>json server</title>",
    "you're successfully running json server",
    "congrats!",
)


@dataclass(frozen=True)
class DiscoveredEndpoint:
    """Observed endpoint candidate before classifier normalization."""

    path: str
    absolute_url: str
    methods: tuple[str, ...]
    discovery_sources: tuple[str, ...]
    confidence: float


@dataclass(frozen=True)
class EndpointClassification:
    """Normalized endpoint description returned by a classifier."""

    path: str
    method: str
    name: str
    description: str
    confidence: float
    tags: tuple[str, ...] = ()


class EndpointClassifier(Protocol):
    """Classifier interface for discovery output."""

    def classify(
        self,
        *,
        base_url: str,
        endpoints: list[DiscoveredEndpoint],
    ) -> list[EndpointClassification]: ...


@dataclass
class _ObservedEndpoint:
    path: str
    absolute_url: str
    methods: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)
    confidence: float = 0.0

    def freeze(self) -> DiscoveredEndpoint:
        resolved_methods = tuple(sorted(self.methods)) or ("GET",)
        return DiscoveredEndpoint(
            path=self.path,
            absolute_url=self.absolute_url,
            methods=resolved_methods,
            discovery_sources=tuple(sorted(self.sources)),
            confidence=self.confidence or 0.5,
        )


class HeuristicRESTClassifier:
    """Fallback classifier when no LLM-backed classifier is supplied."""

    def classify(
        self,
        *,
        base_url: str,
        endpoints: list[DiscoveredEndpoint],
    ) -> list[EndpointClassification]:
        del base_url

        classifications: list[EndpointClassification] = []
        for endpoint in endpoints:
            for method in endpoint.methods:
                method_name = method.upper()
                if method_name not in _SUPPORTED_METHODS:
                    continue
                classifications.append(
                    EndpointClassification(
                        path=endpoint.path,
                        method=method_name,
                        name=_default_operation_name(method_name, endpoint.path),
                        description=(
                            f"Discovered REST endpoint {endpoint.path} using {method_name}."
                        ),
                        confidence=endpoint.confidence,
                        tags=("rest", "discovered"),
                    )
                )
        return classifications


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

        base_path = _normalized_base_path(source.url)
        path_param_defaults = _path_param_defaults_by_operation_path(
            discovered_endpoints,
            base_path=base_path,
        )
        operations = [
            self._classification_to_operation(
                classification,
                base_path=base_path,
                path_param_defaults=path_param_defaults.get(
                    _normalize_classification_path(classification.path, base_path=base_path),
                    {},
                ),
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
            base_url=_runtime_base_url(source.url),
            auth=AuthConfig(type=AuthType.none),
            operations=operations,
            metadata={
                "discovered_paths": [endpoint.path for endpoint in discovered_endpoints],
                "classifier": self._classifier.__class__.__name__,
                "base_path": urlparse(source.url).path or "/",
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
                response = self._client.get(current_url, headers=headers)
            except httpx.HTTPError:
                continue
            if response.status_code >= 400:
                continue

            discovered_relations = self._bootstrap_json_server(
                current_url=current_url,
                response=response,
                observed=observed,
                auth_headers=headers,
            )
            for parent_name, child_names in discovered_relations.items():
                json_server_relations.setdefault(parent_name, set()).update(child_names)

            self._bootstrap_current_json_entrypoint(
                current_url=current_url,
                response=response,
                observed=observed,
                auth_headers=headers,
            )

            for path, source_name in self._extract_candidate_paths(base_url, response):
                candidate_url = urljoin(base_url, path)
                normalized_path = self._normalize_path(candidate_url)
                endpoint = observed.setdefault(
                    normalized_path,
                    _ObservedEndpoint(path=normalized_path, absolute_url=candidate_url),
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
            self._probe_allowed_methods(endpoint)

        # Phase 2: Iterative URI-based resource hierarchy inference.
        # When we discover a collection endpoint like /api/users, probe
        # /api/users/{id} via OPTIONS.  When a detail endpoint like
        # /api/users/{id} is discovered, probe common sub-resource patterns.
        # Running iteratively lets depth-2+ paths (e.g. /users/{id}/posts)
        # be discovered from inferred depth-1 endpoints.
        for _pass in range(_MAX_INFERENCE_PASSES):
            inferred = self._infer_sub_resources(
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

    def _bootstrap_current_json_entrypoint(
        self,
        *,
        current_url: str,
        response: httpx.Response,
        observed: dict[str, _ObservedEndpoint],
        auth_headers: dict[str, str],
    ) -> None:
        content_type = response.headers.get("content-type", "").lower()
        if "json" not in content_type:
            return

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            logger.debug("JSON parse failed for %s", current_url)
            return

        collection_items = _extract_collection_items(payload)
        if collection_items is None:
            return

        collection_path = self._normalize_path(current_url)
        self._register_endpoint(
            observed,
            path=collection_path,
            absolute_url=current_url,
            methods={"GET"},
            source="json_entrypoint",
            confidence=0.96,
        )

        sample_id = _sample_resource_id(collection_items)
        if sample_id is None:
            return

        param_name = _resource_param_name_from_path(collection_path)
        detail_path = f"{collection_path.rstrip('/')}/{{{param_name}}}"
        detail_url = _join_relative_url(current_url, str(sample_id))
        self._probe_and_register(
            detail_path,
            detail_url,
            observed,
            source="json_entrypoint",
            auth_headers=auth_headers,
        )

    def _bootstrap_json_server(
        self,
        *,
        current_url: str,
        response: httpx.Response,
        observed: dict[str, _ObservedEndpoint],
        auth_headers: dict[str, str],
    ) -> dict[str, set[str]]:
        content_type = response.headers.get("content-type", "").lower()
        if "html" not in content_type:
            return {}
        if not _looks_like_json_server_html(response.text):
            return {}

        db_url = _join_relative_url(current_url, "db")
        try:
            db_response = self._client.get(db_url, headers=auth_headers)
        except httpx.HTTPError:
            return {}
        if db_response.status_code >= 400:
            return {}

        try:
            payload = db_response.json()
        except (json.JSONDecodeError, ValueError):
            logger.debug("JSON parse failed for json-server db endpoint %s", db_url)
            return {}
        if not _looks_like_json_server_db_payload(payload):
            return {}

        for resource_name, resource_value in payload.items():
            if not _looks_like_json_server_resource(resource_name, resource_value):
                continue

            collection_url = _join_relative_url(current_url, resource_name)
            collection_path = self._normalize_path(collection_url)
            self._register_endpoint(
                observed,
                path=collection_path,
                absolute_url=collection_url,
                methods=_json_server_collection_methods(resource_value),
                source="json_server_db",
                confidence=0.98,
            )

            sample_id = _sample_resource_id(resource_value)
            if sample_id is None:
                continue

            singular = resource_name[:-1] if resource_name.endswith("s") else resource_name
            param_name = f"{_slugify(singular).replace('-', '_')}_id"
            detail_path = f"{collection_path.rstrip('/')}/{{{param_name}}}"
            detail_url = _join_relative_url(collection_url, str(sample_id))
            self._register_endpoint(
                observed,
                path=detail_path,
                absolute_url=detail_url,
                methods={"GET", "PUT", "PATCH", "DELETE"},
                source="json_server_db",
                confidence=0.97,
            )

        return _infer_json_server_relations(payload)

    def _infer_sub_resources(
        self,
        base_url: str,
        observed: dict[str, _ObservedEndpoint],
        *,
        json_server_relations: dict[str, set[str]] | None = None,
        auth_headers: dict[str, str] | None = None,
    ) -> dict[str, _ObservedEndpoint]:
        """Synthesize and probe sub-resource paths from discovered endpoints.

        Uses URI path structure to infer likely child resources:
        - Collection ``/api/X`` → probe ``/api/X/{id}``
        - Detail ``/api/X/{id}`` → probe ``/api/X/{id}/Y`` for common sub-resources

        Only endpoints confirmed via OPTIONS or GET (2XX response) are added.
        """
        inferred: dict[str, _ObservedEndpoint] = {}
        probed: set[str] = set()
        relation_hints = json_server_relations or {}
        headers = auth_headers or {}

        for path in list(observed.keys()):
            endpoint = observed[path]
            clean = path.split("?", 1)[0].rstrip("/")
            if not clean:
                continue

            # If this looks like a collection (no path param at the leaf),
            # probe for a detail endpoint with {id}.
            leaf = clean.rsplit("/", 1)[-1] if "/" in clean else clean
            if not (leaf.startswith("{") and leaf.endswith("}")):
                # Use resource-specific param name to avoid duplicate {id}
                # when depth-2+ paths are inferred iteratively.
                singular = leaf[:-1] if leaf.endswith("s") and len(leaf) > 2 else leaf
                param_name = f"{singular}_id"
                candidate = f"{clean}/{{{param_name}}}"
                candidate_url = urljoin(base_url, candidate)
                if candidate not in observed and candidate not in probed:
                    probed.add(candidate)
                    self._probe_and_register(
                        candidate,
                        candidate_url,
                        inferred,
                        source="inferred",
                        auth_headers=headers,
                    )

            # If this looks like a detail endpoint (has {param} leaf),
            # probe common sub-resource names.
            if leaf.startswith("{") and leaf.endswith("}"):
                segments = [segment for segment in clean.split("/") if segment]
                parent = clean.rsplit("/", 1)[0] if "/" in clean else ""
                # Infer common sub-resource names from the parent.
                parent_leaf = parent.rsplit("/", 1)[-1] if parent else ""
                if "json_server_db" in endpoint.sources:
                    sub_candidates = (
                        sorted(relation_hints.get(parent_leaf, ())) if len(segments) == 2 else []
                    )
                else:
                    sub_candidates = _common_sub_resources(parent_leaf)
                for sub_name in sub_candidates:
                    candidate = f"{clean}/{sub_name}"
                    candidate_url = urljoin(base_url, candidate)
                    if candidate not in observed and candidate not in probed:
                        probed.add(candidate)
                        self._probe_and_register(
                            candidate,
                            candidate_url,
                            inferred,
                            source="inferred",
                            auth_headers=headers,
                        )

        return inferred

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
            self._probe_and_register(
                candidate.path,
                candidate_url,
                llm_inferred,
                source="llm_seed",
            )

        return llm_inferred

    def _head_probe(
        self,
        absolute_url: str,
        *,
        auth_headers: dict[str, str] | None = None,
    ) -> set[str]:
        """Lightweight HEAD probe; returns {'GET'} if successful, else empty."""
        try:
            response = self._client.head(absolute_url, headers=auth_headers or {})
            if response.status_code < 400:
                return {"GET"}  # HEAD success implies GET works
        except httpx.HTTPError:
            pass
        return set()

    def _probe_and_register(
        self,
        path: str,
        absolute_url: str,
        target: dict[str, _ObservedEndpoint],
        *,
        source: str = "inferred",
        auth_headers: dict[str, str] | None = None,
    ) -> None:
        """Probe a candidate URL via OPTIONS and optionally GET, registering it if valid."""
        methods: set[str] = set()
        headers = auth_headers or {}

        # Phase 1: Try OPTIONS
        try:
            response = self._client.options(absolute_url, headers=headers)
            if response.status_code < 400:
                allow_header = response.headers.get("allow", "").strip()
                if allow_header == "*":
                    methods = set(_SUPPORTED_METHODS)
                else:
                    methods = {
                        m.strip().upper()
                        for m in allow_header.split(",")
                        if m.strip().upper() in _SUPPORTED_METHODS
                    }
            elif response.status_code == 405:
                # Endpoint exists but OPTIONS not allowed — try HEAD
                methods = self._head_probe(absolute_url, auth_headers=headers)
        except httpx.HTTPError:
            pass

        # Phase 2: HEAD fallback if OPTIONS produced nothing
        if not methods:
            methods = self._head_probe(absolute_url, auth_headers=headers)

        # Phase 3: GET fallback with Content-Type validation
        if not methods:
            try:
                response = self._client.get(absolute_url, headers=headers)
                if response.status_code < 400:
                    ct = response.headers.get("content-type", "").lower()
                    if any(t in ct for t in ("json", "html", "xml", "text")):
                        methods.add("GET")
            except httpx.HTTPError:
                pass

        if methods:
            endpoint = _ObservedEndpoint(
                path=path,
                absolute_url=absolute_url,
                methods=methods,
                sources={source, "options"} if len(methods) > 1 else {source},
                confidence=0.85,
            )
            target[path] = endpoint

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
        endpoint = target.setdefault(
            path,
            _ObservedEndpoint(path=path, absolute_url=absolute_url),
        )
        endpoint.absolute_url = absolute_url
        endpoint.methods.update(methods)
        endpoint.sources.add(source)
        endpoint.confidence = max(endpoint.confidence, confidence)

    def _auth_headers(self, source: SourceConfig) -> dict[str, str]:
        if source.auth_header:
            return {"Authorization": source.auth_header}
        if source.auth_token:
            return {"Authorization": f"Bearer {source.auth_token}"}
        return {}

    def _extract_candidate_paths(
        self,
        base_url: str,
        response: httpx.Response,
    ) -> list[tuple[str, str]]:
        content_type = response.headers.get("content-type", "").lower()
        if "html" in content_type:
            return self._extract_from_html(base_url, response.text)
        if "json" in content_type:
            try:
                return self._extract_from_json(base_url, response.json())
            except (json.JSONDecodeError, ValueError, KeyError, TypeError):
                logger.debug("JSON candidate extraction failed for %s", base_url)
                return self._extract_from_html(base_url, response.text)
        return self._extract_from_html(base_url, response.text)

    def _extract_from_html(self, base_url: str, body: str) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []
        for href in _HTML_LINK_PATTERN.findall(body):
            normalized = self._normalize_candidate(base_url, unescape(href))
            if normalized is not None:
                candidates.append((normalized, "link"))
        for action, method in _HTML_FORM_PATTERN.findall(body):
            normalized = self._normalize_candidate(base_url, unescape(action))
            if normalized is None:
                continue
            candidates.append((normalized, "form" if method.upper() == "POST" else "link"))
        return candidates

    def _extract_from_json(self, base_url: str, payload: Any) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []
        for value, parent_key in _walk_json_link_candidates(payload):
            if not _is_path_like(value, parent_key=parent_key):
                continue
            normalized = self._normalize_candidate(base_url, value)
            if normalized is not None:
                candidates.append((normalized, "json"))
        return candidates

    def _normalize_candidate(self, base_url: str, candidate: str) -> str | None:
        if not candidate:
            return None
        absolute = urljoin(base_url, candidate)
        parsed_absolute = urlparse(absolute)
        if parsed_absolute.netloc != urlparse(base_url).netloc:
            return None
        if _is_static_asset_path(parsed_absolute.path):
            return None
        return absolute

    def _normalize_path(self, absolute_url: str) -> str:
        parsed = urlparse(absolute_url)
        path = unquote(parsed.path or "/")
        if parsed.query:
            path = f"{path}?{unquote(parsed.query)}"
        return path

    def _probe_allowed_methods(
        self,
        endpoint: _ObservedEndpoint,
        *,
        auth_headers: dict[str, str] | None = None,
    ) -> None:
        headers = auth_headers or {}
        try:
            response = self._client.options(endpoint.absolute_url, headers=headers)
        except httpx.HTTPError:
            return

        if response.status_code == 405:
            # OPTIONS not allowed, but endpoint exists — try HEAD
            head_methods = self._head_probe(endpoint.absolute_url)
            if head_methods:
                endpoint.methods.update(head_methods)
                endpoint.sources.add("head")
            return

        if response.status_code >= 400:
            return
        allow_header = response.headers.get("allow", "").strip()
        if allow_header == "*":
            allowed_methods = set(_SUPPORTED_METHODS)
        else:
            allowed_methods = {
                method.strip().upper()
                for method in allow_header.split(",")
                if method.strip().upper() in _SUPPORTED_METHODS
            }
        if allowed_methods:
            # OPTIONS is authoritative — replace speculative methods
            # (e.g. GET added from BFS link discovery) with the server's
            # declared Allow set.
            endpoint.methods = allowed_methods
            endpoint.sources.add("options")
            endpoint.confidence = max(endpoint.confidence, 0.9)

    def _infer_pagination_from_response(
        self,
        endpoint: DiscoveredEndpoint,
        method: str,
    ) -> PaginationConfig | None:
        """Infer pagination style from discovered endpoint's observed response shape.

        Since DiscoveredEndpoint does not carry response body keys, inference
        is based on query-parameter names present in the endpoint path.
        """
        if method.upper() != "GET":
            return None

        parsed = urlparse(endpoint.path)
        query_param_names = {k.lower() for k, _ in parse_qsl(parsed.query, keep_blank_values=True)}

        # Cursor-style hints
        cursor_hints = {"next", "cursor", "page_token"}
        if query_param_names & cursor_hints:
            cursor_param = (
                "cursor"
                if "cursor" in query_param_names
                else next(iter(sorted(query_param_names & cursor_hints)))
            )
            return PaginationConfig(
                style="cursor",
                page_param=cursor_param,
                size_param="page_size",
            )

        # Page-style hints
        page_hints = {"page", "total_pages", "current_page"}
        if query_param_names & page_hints:
            page_param = (
                "page"
                if "page" in query_param_names
                else next(iter(sorted(query_param_names & page_hints)))
            )
            size_param = next(
                (p for p in sorted(query_param_names) if p in {"per_page", "page_size", "size"}),
                "page_size",
            )
            return PaginationConfig(
                style="page",
                page_param=page_param,
                size_param=size_param,
            )

        # Offset-style hints
        offset_hints = {"offset", "limit"}
        if query_param_names & offset_hints:
            return PaginationConfig(
                style="offset",
                page_param="offset" if "offset" in query_param_names else "page",
                size_param="limit" if "limit" in query_param_names else "page_size",
            )

        return None

    def _classification_to_operation(
        self,
        classification: EndpointClassification,
        *,
        base_path: str,
        path_param_defaults: dict[str, str] | None = None,
    ) -> Operation:
        method = classification.method.upper()
        normalized_path = _normalize_classification_path(
            classification.path,
            base_path=base_path,
        )
        path = normalized_path.split("?", 1)[0]
        params = _params_from_path_and_query(normalized_path)
        if path_param_defaults:
            params = [
                param.model_copy(update={"default": path_param_defaults[param.name]})
                if param.name in path_param_defaults and param.default is None
                else param
                for param in params
            ]
        body_param_name: str | None = None
        if method in {"POST", "PUT", "PATCH"} and not any(
            param.name == "payload" for param in params
        ):
            params.append(
                Param(
                    name="payload",
                    type="object",
                    required=False,
                    description="Request body payload for the discovered REST endpoint.",
                    source=SourceType.extractor,
                    confidence=classification.confidence,
                )
            )
            body_param_name = "payload"

        pagination_endpoint = DiscoveredEndpoint(
            path=normalized_path,
            absolute_url="",
            methods=(method,),
            discovery_sources=(),
            confidence=classification.confidence,
        )
        pagination = self._infer_pagination_from_response(pagination_endpoint, method)

        return Operation(
            id=_operation_id(method, path),
            name=classification.name,
            description=classification.description,
            method=method,
            path=path,
            params=params,
            body_param_name=body_param_name,
            response_strategy=ResponseStrategy(pagination=pagination),
            risk=RiskMetadata(
                writes_state=method in {"POST", "PUT", "PATCH", "DELETE"},
                destructive=method == "DELETE",
                external_side_effect=method in {"POST", "PUT", "PATCH", "DELETE"},
                idempotent=method in {"GET", "PUT", "DELETE"},
                risk_level=_risk_for_method(method),
                confidence=classification.confidence,
                source=self._operation_source(),
            ),
            tags=list(classification.tags),
            source=self._operation_source(),
            confidence=classification.confidence,
            enabled=True,
        )

    def _operation_source(self) -> SourceType:
        if isinstance(self._classifier, HeuristicRESTClassifier):
            return SourceType.extractor
        return SourceType.llm

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
# Sub-resource inference heuristics
# ---------------------------------------------------------------------------

# Common sub-resource patterns for REST API resource groups.
# Maps parent collection name → list of likely child resource names.
_SUB_RESOURCE_HINTS: dict[str, list[str]] = {
    "users": ["posts", "comments", "settings", "orders", "notifications"],
    "products": ["reviews", "images", "variants", "inventory"],
    "orders": ["items", "payments", "status", "shipments"],
    "categories": ["products", "items"],
    "inventory": ["adjustments", "history"],
    "notifications": ["acknowledge", "read"],
    "reports": ["status", "download"],
    "webhooks": ["test", "events", "logs"],
}

# Fallback sub-resource names tried for any collection not in the hints map.
_DEFAULT_SUB_RESOURCES = [
    "items",
    "details",
    "status",
    "history",
    "settings",
    "comments",
]


def _common_sub_resources(parent_name: str) -> list[str]:
    """Return plausible sub-resource names for a given parent collection.

    Uses a heuristic lookup table plus a small set of universal fallbacks.
    """
    normalized = parent_name.lower().rstrip("s") + "s"  # simple pluralize
    hints = _SUB_RESOURCE_HINTS.get(
        normalized,
        _SUB_RESOURCE_HINTS.get(parent_name.lower(), []),
    )
    if hints:
        return hints
    return _DEFAULT_SUB_RESOURCES


def _deduplicate_concrete_paths(
    observed: dict[str, _ObservedEndpoint],
) -> dict[str, _ObservedEndpoint]:
    """Remove paths subsumed by more general template paths.

    A template path subsumes a concrete or less-general template path when:

    1. It has **more** template parameters (e.g. ``/users/{id}/posts/{id}``
       subsumes ``/users/usr-1/posts/{id}``)
    2. Its regex matches the subsumed path segment-by-segment.

    Methods, sources, and confidence from subsumed paths are merged into
    the most general template.
    """

    def _template_count(path: str) -> int:
        return len(_PATH_PARAM_PATTERN.findall(path.split("?", 1)[0]))

    # Build regex matchers for every template-containing path.
    template_regexes: list[tuple[str, re.Pattern[str], int]] = []
    for path in observed:
        clean = path.split("?", 1)[0]
        tc = _template_count(path)
        if tc == 0:
            continue
        parts = clean.split("/")
        regex_parts = []
        for part in parts:
            if part.startswith("{") and part.endswith("}"):
                regex_parts.append("[^/]+")
            else:
                regex_parts.append(re.escape(part))
        pattern = "/".join(regex_parts)
        template_regexes.append((path, re.compile(f"^{pattern}$"), tc))

    if not template_regexes:
        return observed

    # Sort most-general first (highest template param count).
    template_regexes.sort(key=lambda x: -x[2])

    # Map each path to its most general subsuming template (if any).
    subsumed_by: dict[str, str] = {}
    for path in observed:
        clean = path.split("?", 1)[0]
        my_count = _template_count(path)
        for tpath, regex, t_count in template_regexes:
            if tpath == path:
                continue
            if t_count <= my_count:
                break  # Sorted descending — no more general templates remain.
            if regex.fullmatch(clean):
                subsumed_by[path] = tpath
                break

    if not subsumed_by:
        return observed

    merged: dict[str, _ObservedEndpoint] = {}
    for path, endpoint in observed.items():
        if path in subsumed_by:
            target = subsumed_by[path]
            tmpl = merged.setdefault(
                target,
                _ObservedEndpoint(
                    path=observed[target].path,
                    absolute_url=observed[target].absolute_url,
                    methods=set(observed[target].methods),
                    sources=set(observed[target].sources),
                    confidence=observed[target].confidence,
                ),
            )
            tmpl.methods.update(endpoint.methods)
            tmpl.sources.update(endpoint.sources)
            tmpl.confidence = max(tmpl.confidence, endpoint.confidence)
        else:
            merged.setdefault(
                path,
                _ObservedEndpoint(
                    path=endpoint.path,
                    absolute_url=endpoint.absolute_url,
                    methods=set(endpoint.methods),
                    sources=set(endpoint.sources),
                    confidence=endpoint.confidence,
                ),
            )

    return merged


_SIBLING_COALESCE_THRESHOLD = 3


def _looks_like_value_segment(segment: str) -> bool:
    """Return True if a path segment looks like a specific value rather than an endpoint name."""
    if " " in segment:
        return True
    if segment.startswith("{") and segment.endswith("}"):
        return False
    if re.fullmatch(r"\d+", segment):
        return True
    if re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        segment,
        re.IGNORECASE,
    ):
        return True
    return False


def _coalesce_sibling_endpoints(
    observed: dict[str, _ObservedEndpoint],
) -> dict[str, _ObservedEndpoint]:
    """Merge sibling leaf endpoints into a single template when evidence suggests they are values.

    Groups endpoints by parent path. When a group has >= threshold siblings
    and any of them look like values (spaces, numeric IDs, UUIDs), the group is
    collapsed into a single ``{id}`` template under that parent.
    """
    groups: dict[str, list[str]] = {}
    for path in observed:
        clean = path.split("?", 1)[0].rstrip("/")
        parent = clean.rsplit("/", 1)[0] if "/" in clean.lstrip("/") else ""
        groups.setdefault(parent, []).append(path)

    merged: dict[str, _ObservedEndpoint] = {}
    coalesced_parents: set[str] = set()

    for parent, paths in groups.items():
        if len(paths) < _SIBLING_COALESCE_THRESHOLD:
            has_value = any(
                _looks_like_value_segment(p.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1])
                for p in paths
            )
            if not has_value:
                for p in paths:
                    merged[p] = observed[p]
                continue

        has_value = any(
            _looks_like_value_segment(p.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1])
            for p in paths
        )
        if not has_value:
            for p in paths:
                merged[p] = observed[p]
            continue

        template_path = f"{parent}/{{id}}" if parent else "/{id}"
        existing_template = None
        non_value_paths: list[str] = []
        for p in paths:
            leaf = p.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
            if leaf.startswith("{") and leaf.endswith("}"):
                existing_template = p
            elif not _looks_like_value_segment(leaf):
                non_value_paths.append(p)

        if existing_template:
            template_path = existing_template
        else:
            query_suffix = _shared_query_suffix(paths)
            if query_suffix:
                template_path = f"{template_path}{query_suffix}"

        best = max(
            (observed[p] for p in paths),
            key=lambda ep: ep.confidence,
        )

        coalesced = _ObservedEndpoint(
            path=template_path,
            absolute_url=best.absolute_url,
            methods=set().union(*(observed[p].methods for p in paths)),
            sources=set().union(*(observed[p].sources for p in paths)),
            confidence=best.confidence,
        )
        merged[template_path] = coalesced
        coalesced_parents.add(parent)

        for p in non_value_paths:
            merged[p] = observed[p]

    return merged


_LINK_LIKE_JSON_KEYS = {
    "endpoint",
    "endpoints",
    "href",
    "link",
    "links",
    "location",
    "next",
    "path",
    "previous",
    "prev",
    "related",
    "self",
    "uri",
    "url",
}


def _is_path_like(value: str, *, parent_key: str | None = None) -> bool:
    """Return True only if the string looks like a URL or path, not a plain value."""
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.startswith(("/", "./", "../", "http://", "https://")):
        return True
    if "://" in stripped:
        return True

    parsed = urlparse(stripped)
    if parsed.scheme or parsed.netloc:
        return False
    if " " in stripped:
        return False

    path = parsed.path
    if not path or path.startswith("#"):
        return False

    if _is_link_like_json_key(parent_key):
        return "/" in path or bool(parsed.query)

    segments = [segment for segment in path.split("/") if segment]
    if len(segments) >= 3:
        return True
    if parsed.query and len(segments) >= 1:
        return True
    return False


def _walk_json_link_candidates(
    value: Any,
    *,
    parent_key: str | None = None,
) -> Iterable[tuple[str, str | None]]:
    if isinstance(value, str):
        yield value, parent_key
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            yield from _walk_json_link_candidates(nested, parent_key=str(key))
        return
    if isinstance(value, list):
        for nested in value:
            yield from _walk_json_link_candidates(nested, parent_key=parent_key)


def _is_link_like_json_key(parent_key: str | None) -> bool:
    if parent_key is None:
        return False
    normalized = parent_key.strip().lower().replace("-", "_")
    if normalized in _LINK_LIKE_JSON_KEYS:
        return True
    return normalized.endswith(("_endpoint", "_href", "_link", "_links", "_path", "_uri", "_url"))


def _is_static_asset_path(path: str) -> bool:
    candidate = path.rsplit("/", 1)[-1].lower()
    if "." not in candidate:
        return False
    extension = f".{candidate.rsplit('.', 1)[-1]}"
    return extension in _STATIC_ASSET_EXTENSIONS


def _looks_like_json_server_html(body: str) -> bool:
    lowered = body.lower()
    return all(marker in lowered for marker in _JSON_SERVER_MARKERS)


def _looks_like_json_server_db_payload(payload: Any) -> bool:
    if not isinstance(payload, dict) or not payload:
        return False
    return any(isinstance(value, (list, dict)) for value in payload.values())


def _looks_like_json_server_resource(name: Any, value: Any) -> bool:
    if not isinstance(name, str) or not name.strip():
        return False
    if name.startswith("__"):
        return False
    return isinstance(value, (list, dict))


def _json_server_collection_methods(resource_value: Any) -> set[str]:
    if isinstance(resource_value, list):
        return {"GET", "POST"}
    if isinstance(resource_value, dict):
        return {"GET", "PUT", "PATCH"}
    return {"GET"}


def _extract_collection_items(payload: Any) -> list[Any] | None:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return None

    for key in ("data", "items", "records", "results", "value"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return None


def _sample_resource_id(resource_value: Any) -> str | int | None:
    if not isinstance(resource_value, list):
        return None
    for item in resource_value:
        if not isinstance(item, dict):
            continue
        sample_id = item.get("id")
        if isinstance(sample_id, (str, int)):
            return sample_id
    return None


def _join_relative_url(base_url: str, relative_path: str) -> str:
    normalized_base = base_url if base_url.endswith("/") else f"{base_url}/"
    normalized_relative = relative_path.lstrip("/")
    return urljoin(normalized_base, normalized_relative)


def _resource_param_name_from_path(path: str) -> str:
    clean = path.split("?", 1)[0].rstrip("/")
    leaf = clean.rsplit("/", 1)[-1] if clean else "resource"
    singular = leaf[:-1] if leaf.endswith("s") and len(leaf) > 2 else leaf
    normalized = _slugify(singular).replace("-", "_")
    return f"{normalized or 'resource'}_id"


def _path_param_defaults_by_operation_path(
    endpoints: list[DiscoveredEndpoint],
    *,
    base_path: str,
) -> dict[str, dict[str, str]]:
    defaults_by_path: dict[str, dict[str, str]] = {}
    for endpoint in endpoints:
        defaults = _path_param_defaults_from_endpoint(endpoint, base_path=base_path)
        if not defaults:
            continue
        normalized_path = _normalize_classification_path(endpoint.path, base_path=base_path)
        defaults_by_path.setdefault(normalized_path, defaults)
    return defaults_by_path


def _path_param_defaults_from_endpoint(
    endpoint: DiscoveredEndpoint,
    *,
    base_path: str,
) -> dict[str, str]:
    del base_path
    template_segments = [segment for segment in urlparse(endpoint.path).path.split("/") if segment]
    concrete_segments = [
        unquote(segment) for segment in urlparse(endpoint.absolute_url).path.split("/") if segment
    ]
    if len(template_segments) != len(concrete_segments):
        return {}

    defaults: dict[str, str] = {}
    for template_segment, concrete_segment in zip(template_segments, concrete_segments):
        if not (template_segment.startswith("{") and template_segment.endswith("}")):
            continue
        param_name = template_segment[1:-1].strip()
        if not param_name:
            continue
        defaults[param_name] = concrete_segment
    return defaults


def _infer_json_server_relations(payload: Any) -> dict[str, set[str]]:
    if not isinstance(payload, dict):
        return {}

    resources = {
        name: value
        for name, value in payload.items()
        if _looks_like_json_server_resource(name, value)
    }
    resource_names = set(resources)
    relations: dict[str, set[str]] = {}

    for child_name, child_value in resources.items():
        if not isinstance(child_value, list):
            continue
        for foreign_key in _json_server_foreign_keys(child_value):
            for parent_name in _json_server_parent_candidates(foreign_key, resource_names):
                if parent_name == child_name:
                    continue
                relations.setdefault(parent_name, set()).add(child_name)

    return relations


def _json_server_foreign_keys(resource_items: list[Any]) -> set[str]:
    foreign_keys: set[str] = set()
    for item in resource_items:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if key == "id":
                continue
            if _looks_like_foreign_key_field(key) and isinstance(value, (str, int)):
                foreign_keys.add(key)
    return foreign_keys


def _looks_like_foreign_key_field(field_name: str) -> bool:
    normalized = field_name.strip()
    lowered = normalized.lower()
    return lowered.endswith("_id") or (lowered.endswith("id") and lowered != "id")


def _json_server_parent_candidates(
    field_name: str,
    resource_names: set[str],
) -> list[str]:
    base_name = field_name.strip()
    lowered = base_name.lower()
    if lowered.endswith("_id"):
        base_name = base_name[:-3]
    elif lowered.endswith("id") and lowered != "id":
        base_name = base_name[:-2]
    else:
        return []

    normalized = _slugify(re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", base_name))
    if not normalized:
        return []

    candidates = [normalized]
    plural = _pluralize_resource_name(normalized)
    if plural != normalized:
        candidates.append(plural)

    return [candidate for candidate in candidates if candidate in resource_names]


def _pluralize_resource_name(name: str) -> str:
    if not name:
        return name
    if name.endswith("y") and len(name) > 1 and name[-2] not in "aeiou":
        return f"{name[:-1]}ies"
    if name.endswith(("s", "x", "z", "ch", "sh")):
        return f"{name}es"
    return f"{name}s"


def _shared_query_suffix(paths: list[str]) -> str:
    queries = [
        tuple(sorted(parse_qsl(urlparse(path).query, keep_blank_values=True))) for path in paths
    ]
    if not queries or any(not query for query in queries):
        return ""

    first_query = queries[0]
    if any(query != first_query for query in queries[1:]):
        return ""

    encoded = urlencode(list(first_query), doseq=True)
    return f"?{encoded}" if encoded else ""


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


def _runtime_base_url(url: str) -> str:
    return f"{_origin(url)}{_normalized_base_path(url)}"


def _normalize_classification_path(path: str, *, base_path: str) -> str:
    parsed = urlparse(path)
    normalized_path = unquote(parsed.path or "/")
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"

    if base_path:
        if normalized_path == base_path:
            normalized_path = "/"
        elif normalized_path.startswith(f"{base_path}/"):
            normalized_path = normalized_path[len(base_path) :]

    if parsed.query:
        return f"{normalized_path}?{unquote(parsed.query)}"
    return normalized_path


def _default_operation_name(method: str, path: str) -> str:
    path_label = path.strip("/").replace("/", " ").replace("{", "").replace("}", "")
    path_label = " ".join(segment for segment in path_label.split() if segment) or "root"
    return f"{method.title()} {path_label.title()}"


def _operation_id(method: str, path: str) -> str:
    path_parts = [segment for segment in re.split(r"[/{}]+", path) if segment]
    slug = "_".join(_slugify(part).replace("-", "_") for part in path_parts) or "root"
    return f"{method.lower()}_{slug}"


def _deduplicate_operation_ids(operations: list[Operation]) -> list[Operation]:
    """Append numeric suffixes to resolve duplicate operation IDs."""
    seen: dict[str, int] = {}
    result: list[Operation] = []
    for op in operations:
        base_id = op.id
        count = seen.get(base_id, 0)
        if count > 0:
            new_id = f"{base_id}_{count}"
            op = op.model_copy(update={"id": new_id})
        seen[base_id] = count + 1
        result.append(op)
    return result


def _params_from_path_and_query(path: str) -> list[Param]:
    parsed = urlparse(path)
    params: list[Param] = []
    for param_name in _PATH_PARAM_PATTERN.findall(parsed.path):
        params.append(
            Param(
                name=param_name,
                type="string",
                required=True,
                description=f"Path parameter extracted from {parsed.path}.",
                source=SourceType.extractor,
                confidence=0.9,
            )
        )
    for query_name, query_value in parse_qsl(parsed.query, keep_blank_values=True):
        params.append(
            Param(
                name=query_name,
                type="string",
                required=False,
                description=f"Query parameter extracted from {parsed.query}.",
                default=query_value if query_value else None,
                source=SourceType.extractor,
                confidence=0.9,
            )
        )
    return params


def _risk_for_method(method: str) -> RiskLevel:
    if method == "GET":
        return RiskLevel.safe
    if method in {"POST", "PUT", "PATCH"}:
        return RiskLevel.cautious
    if method == "DELETE":
        return RiskLevel.dangerous
    return RiskLevel.unknown


def _slugify(value: str) -> str:
    normalized = []
    previous_dash = False
    for char in value.lower():
        if char.isalnum():
            normalized.append(char)
            previous_dash = False
            continue
        if previous_dash:
            continue
        normalized.append("-")
        previous_dash = True
    return "".join(normalized).strip("-") or "rest-service"


__all__ = [
    "DiscoveredEndpoint",
    "EndpointClassification",
    "EndpointClassifier",
    "HeuristicRESTClassifier",
    "RESTExtractor",
]
