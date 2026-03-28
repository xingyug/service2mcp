"""REST extractor with discovery and classifier-assisted endpoint normalization."""

from __future__ import annotations

import hashlib
import json
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

_HTML_LINK_PATTERN = re.compile(r"""href=["']([^"'#]+)["']""", re.IGNORECASE)
_HTML_FORM_PATTERN = re.compile(
    r"""<form[^>]*action=["']([^"'#]+)["'][^>]*?(?:method=["']([^"']+)["'])?""",
    re.IGNORECASE,
)
_PATH_PARAM_PATTERN = re.compile(r"{([^{}]+)}")
_SUPPORTED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_MAX_INFERENCE_PASSES = 3


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

        discovered_endpoints = self._discover(source.url)
        if not discovered_endpoints:
            raise ValueError(f"No REST endpoints discovered from {source.url}")

        classified = self._classifier.classify(
            base_url=source.url,
            endpoints=discovered_endpoints,
        )
        if not classified:
            raise ValueError(f"Classifier returned no REST operations for {source.url}")

        base_path = _normalized_base_path(source.url)
        operations = [
            self._classification_to_operation(
                classification,
                base_path=base_path,
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

    def _discover(self, base_url: str) -> list[DiscoveredEndpoint]:
        observed: dict[str, _ObservedEndpoint] = {}
        queue = [(base_url, 0)]
        visited_pages: set[str] = set()

        while queue and len(visited_pages) < self._max_pages:
            current_url, depth = queue.pop(0)
            if current_url in visited_pages:
                continue
            visited_pages.add(current_url)

            try:
                response = self._client.get(current_url)
            except httpx.HTTPError:
                continue
            if response.status_code >= 400:
                continue

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
            inferred = self._infer_sub_resources(base_url, observed)
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

    def _infer_sub_resources(
        self,
        base_url: str,
        observed: dict[str, _ObservedEndpoint],
    ) -> dict[str, _ObservedEndpoint]:
        """Synthesize and probe sub-resource paths from discovered endpoints.

        Uses URI path structure to infer likely child resources:
        - Collection ``/api/X`` → probe ``/api/X/{id}``
        - Detail ``/api/X/{id}`` → probe ``/api/X/{id}/Y`` for common sub-resources

        Only endpoints confirmed via OPTIONS or GET (2XX response) are added.
        """
        inferred: dict[str, _ObservedEndpoint] = {}
        probed: set[str] = set()

        for path in list(observed.keys()):
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
                    )

            # If this looks like a detail endpoint (has {param} leaf),
            # probe common sub-resource names.
            if leaf.startswith("{") and leaf.endswith("}"):
                parent = clean.rsplit("/", 1)[0] if "/" in clean else ""
                # Infer common sub-resource names from the parent.
                parent_leaf = parent.rsplit("/", 1)[-1] if parent else ""
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

    def _head_probe(self, absolute_url: str) -> set[str]:
        """Lightweight HEAD probe; returns {'GET'} if successful, else empty."""
        try:
            response = self._client.head(absolute_url)
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
    ) -> None:
        """Probe a candidate URL via OPTIONS and optionally GET, registering it if valid."""
        methods: set[str] = set()

        # Phase 1: Try OPTIONS
        try:
            response = self._client.options(absolute_url)
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
                methods = self._head_probe(absolute_url)
        except httpx.HTTPError:
            pass

        # Phase 2: HEAD fallback if OPTIONS produced nothing
        if not methods:
            methods = self._head_probe(absolute_url)

        # Phase 3: GET fallback with Content-Type validation
        if not methods:
            try:
                response = self._client.get(absolute_url)
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
            except Exception:
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
        if urlparse(absolute).netloc != urlparse(base_url).netloc:
            return None
        return absolute

    def _normalize_path(self, absolute_url: str) -> str:
        parsed = urlparse(absolute_url)
        path = unquote(parsed.path or "/")
        if parsed.query:
            path = f"{path}?{unquote(parsed.query)}"
        return path

    def _probe_allowed_methods(self, endpoint: _ObservedEndpoint) -> None:
        try:
            response = self._client.options(endpoint.absolute_url)
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
    ) -> Operation:
        method = classification.method.upper()
        normalized_path = _normalize_classification_path(
            classification.path,
            base_path=base_path,
        )
        path = normalized_path.split("?", 1)[0]
        params = _params_from_path_and_query(normalized_path)
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
