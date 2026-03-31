"""REST schema and parameter extraction helpers.

All functions are standalone (no LLM calls — extractor purity).
"""

from __future__ import annotations

from urllib.parse import parse_qsl, unquote, urlparse

from libs.extractors.rest_probing import (
    _PATH_PARAM_PATTERN,
    DiscoveredEndpoint,
    _normalize_classification_path,
)
from libs.ir.models import Param, SourceType


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
