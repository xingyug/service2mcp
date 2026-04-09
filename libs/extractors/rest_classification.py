"""REST endpoint classification, deduplication, and operation mapping.

All functions are standalone (no LLM calls — extractor purity).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlparse

from libs.extractors.rest_probing import (
    _PATH_PARAM_PATTERN,
    _SUPPORTED_METHODS,
    DiscoveredEndpoint,
    _normalize_classification_path,
    _ObservedEndpoint,
    _shared_query_suffix,
    _slugify,
)
from libs.extractors.rest_schema import _params_from_path_and_query
from libs.ir.models import (
    Operation,
    PaginationConfig,
    Param,
    ResponseStrategy,
    RiskLevel,
    RiskMetadata,
    SourceType,
)


@dataclass(frozen=True)
class EndpointClassification:
    """Normalized endpoint description returned by a classifier."""

    path: str
    method: str
    name: str
    description: str
    confidence: float
    tags: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Classifier protocol and heuristic implementation
# ---------------------------------------------------------------------------


class EndpointClassifier(Protocol):
    """Classifier interface for discovery output."""

    def classify(
        self,
        *,
        base_url: str,
        endpoints: list[DiscoveredEndpoint],
    ) -> list[EndpointClassification]: ...


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


# ---------------------------------------------------------------------------
# Operation helpers
# ---------------------------------------------------------------------------


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


def _risk_for_method(method: str) -> RiskLevel:
    if method == "GET":
        return RiskLevel.safe
    if method in {"POST", "PUT", "PATCH"}:
        return RiskLevel.cautious
    if method == "DELETE":
        return RiskLevel.dangerous
    return RiskLevel.unknown


def _operation_source(classifier: Any) -> SourceType:
    """Determine SourceType based on classifier class."""
    if isinstance(classifier, HeuristicRESTClassifier):
        return SourceType.extractor
    return SourceType.llm


def _infer_pagination_from_response(
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
            cursor_param=cursor_param,
            limit_param="page_size",
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
            limit_param=size_param,
        )

    # Offset-style hints
    offset_hints = {"offset", "limit"}
    if query_param_names & offset_hints:
        return PaginationConfig(
            style="offset",
            page_param="offset" if "offset" in query_param_names else "page",
            limit_param="limit" if "limit" in query_param_names else "page_size",
        )

    return None


def _classification_to_operation(
    classification: EndpointClassification,
    *,
    base_path: str,
    path_param_defaults: dict[str, str] | None = None,
    classifier: Any,
) -> Operation:
    """Convert a single classification into an IR Operation."""
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
    if method in {"POST", "PUT", "PATCH"} and not any(param.name == "payload" for param in params):
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
    pagination = _infer_pagination_from_response(pagination_endpoint, method)

    source = _operation_source(classifier)

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
            source=source,
        ),
        tags=list(classification.tags),
        source=source,
        confidence=classification.confidence,
        enabled=True,
    )


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


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
