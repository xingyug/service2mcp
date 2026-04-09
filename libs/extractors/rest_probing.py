"""REST discovery probing, path extraction, and JSON-server helpers.

All functions are standalone (no LLM calls — extractor purity).
Methods formerly on RESTExtractor now accept ``httpx.Client`` as their
first positional argument so the BFS orchestrator in ``rest.py`` can
pass ``self._client`` through.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from html import unescape
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse

import httpx

from libs.extractors.utils import slugify

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HTML_LINK_PATTERN = re.compile(r"""href=["']([^"'#]+)["']""", re.IGNORECASE)
_HTML_FORM_PATTERN = re.compile(
    r"""<form[^>]*action=["']([^"'#]+)["'][^>]*?(?:method=["']([^"']+)["'])?""",
    re.IGNORECASE,
)
_PATH_PARAM_PATTERN = re.compile(r"{([^{}]+)}")
_SUPPORTED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
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

# Sub-resource inference heuristics — maps parent collection name → likely children.
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
_DEFAULT_SUB_RESOURCES = [
    "items",
    "details",
    "status",
    "history",
    "settings",
    "comments",
]

_MAX_RETRY_ATTEMPTS = 3
_DEFAULT_RETRY_AFTER_SECONDS = 1.0
_MAX_BODY_BYTES = 5 * 1024 * 1024  # 5 MB — skip HTML candidate extraction for huge responses


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RegistryCollectionCandidate:
    collection_url: str
    methods: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class CollectionDiscoveryStrategy:
    """Declarative description of a collection-listing API pattern."""

    name: str
    path_suffix: str
    name_fields: list[str]
    type_field: str | None = None
    exclude_prefixes: list[str] = field(default_factory=list)
    valid_types: set[str | None] | None = None
    item_path_template: str = "/{name}"


_COLLECTION_STRATEGIES: list[CollectionDiscoveryStrategy] = [
    CollectionDiscoveryStrategy(
        name="pocketbase",
        path_suffix="/api/collections",
        name_fields=["name"],
        type_field="type",
        exclude_prefixes=["_"],
        valid_types={None, "base", "view"},
        item_path_template="/{name}/records",
    ),
    CollectionDiscoveryStrategy(
        name="directus",
        path_suffix="/collections",
        name_fields=["collection", "name"],
        exclude_prefixes=["directus_"],
        item_path_template="/items/{name}",
    ),
]


@dataclass(frozen=True)
class DiscoveredEndpoint:
    """Observed endpoint candidate before classifier normalization."""

    path: str
    absolute_url: str
    methods: tuple[str, ...]
    discovery_sources: tuple[str, ...]
    confidence: float


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


# ---------------------------------------------------------------------------
# Shared utility
# ---------------------------------------------------------------------------


def _slugify(value: str) -> str:
    return slugify(value, default="rest-service")


# ---------------------------------------------------------------------------
# Path / URL helpers
# ---------------------------------------------------------------------------


def _is_static_asset_path(path: str) -> bool:
    candidate = path.rsplit("/", 1)[-1].lower()
    if "." not in candidate:
        return False
    extension = f".{candidate.rsplit('.', 1)[-1]}"
    return extension in _STATIC_ASSET_EXTENSIONS


def _is_link_like_json_key(parent_key: str | None) -> bool:
    if parent_key is None:
        return False
    normalized = parent_key.strip().lower().replace("-", "_")
    if normalized in _LINK_LIKE_JSON_KEYS:
        return True
    return normalized.endswith(("_endpoint", "_href", "_link", "_links", "_path", "_uri", "_url"))


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


def _is_path_like(value: str, *, parent_key: str | None = None) -> bool:
    """Return True only if the string looks like a URL or path, not a plain value."""
    stripped = value.strip()
    if not stripped:
        return False

    # Filter out filesystem paths that are not API endpoints.
    fs_prefixes = (
        "/etc/", "/usr/", "/var/", "/tmp/", "/opt/",
        "/home/", "/root/", "/proc/", "/sys/", "/dev/",
    )
    if any(stripped.startswith(p) for p in fs_prefixes):
        return False
    # Paths containing file extensions common in config are not API endpoints.
    config_exts = (
        ".env", ".conf", ".cfg", ".ini", ".log",
        ".pid", ".sock", ".key", ".pem", ".crt",
    )
    if stripped.startswith("/") and any(
        stripped.endswith(ext) for ext in config_exts
    ):
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


def _join_relative_url(base_url: str, relative_path: str) -> str:
    normalized_base = base_url if base_url.endswith("/") else f"{base_url}/"
    normalized_relative = relative_path.lstrip("/")
    return urljoin(normalized_base, normalized_relative)


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


def _normalize_path(absolute_url: str) -> str:
    parsed = urlparse(absolute_url)
    path = unquote(parsed.path or "/")
    if parsed.query:
        path = f"{path}?{unquote(parsed.query)}"
    return path


def _normalize_candidate(base_url: str, candidate: str) -> str | None:
    if not candidate:
        return None
    absolute = urljoin(base_url, candidate)
    parsed_absolute = urlparse(absolute)
    if parsed_absolute.netloc != urlparse(base_url).netloc:
        return None
    if _is_static_asset_path(parsed_absolute.path):
        return None
    return absolute


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


def _resource_param_name_from_path(path: str) -> str:
    clean = path.split("?", 1)[0].rstrip("/")
    leaf = clean.rsplit("/", 1)[-1] if clean else "resource"
    singular = leaf[:-1] if leaf.endswith("s") and len(leaf) > 2 else leaf
    normalized = _slugify(singular).replace("-", "_")
    return f"{normalized or 'resource'}_id"


# ---------------------------------------------------------------------------
# JSON-server helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Sub-resource inference
# ---------------------------------------------------------------------------


def _common_sub_resources(parent_name: str) -> list[str]:
    """Return plausible sub-resource names for a given parent collection."""
    normalized = parent_name.lower().rstrip("s") + "s"  # simple pluralize
    hints = _SUB_RESOURCE_HINTS.get(
        normalized,
        _SUB_RESOURCE_HINTS.get(parent_name.lower(), []),
    )
    if hints:
        return hints
    return _DEFAULT_SUB_RESOURCES


# ---------------------------------------------------------------------------
# Endpoint registration and probing (standalone — take client as first arg)
# ---------------------------------------------------------------------------


def _register_endpoint(
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


def _head_probe(
    client: httpx.Client,
    absolute_url: str,
    *,
    auth_headers: dict[str, str] | None = None,
) -> set[str]:
    """Lightweight HEAD probe; returns {'GET'} if successful, else empty."""
    try:
        response = client.head(absolute_url, headers=auth_headers or {})
        if response.status_code < 400:
            return {"GET"}  # HEAD success implies GET works
    except httpx.HTTPError:
        pass
    return set()


def _probe_and_register(
    client: httpx.Client,
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
        response = client.options(absolute_url, headers=headers)
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
            methods = _head_probe(client, absolute_url, auth_headers=headers)
    except httpx.HTTPError:
        pass

    # Phase 2: HEAD fallback if OPTIONS produced nothing
    if not methods:
        methods = _head_probe(client, absolute_url, auth_headers=headers)

    # Phase 3: GET fallback with Content-Type validation
    if not methods:
        try:
            response = client.get(absolute_url, headers=headers)
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


def _probe_allowed_methods(
    client: httpx.Client,
    endpoint: _ObservedEndpoint,
    *,
    auth_headers: dict[str, str] | None = None,
) -> None:
    headers = auth_headers or {}
    try:
        response = client.options(endpoint.absolute_url, headers=headers)
    except httpx.HTTPError:
        return

    if response.status_code == 405:
        # OPTIONS not allowed, but endpoint exists — try HEAD
        head_methods = _head_probe(client, endpoint.absolute_url, auth_headers=headers)
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


# ---------------------------------------------------------------------------
# Path candidate extraction
# ---------------------------------------------------------------------------


def _extract_candidate_paths(
    base_url: str,
    response: httpx.Response,
) -> list[tuple[str, str]]:
    if len(response.content) > _MAX_BODY_BYTES:
        logger.debug(
            "Skipping candidate extraction for %s: response too large (%d bytes)",
            base_url,
            len(response.content),
        )
        return []
    content_type = response.headers.get("content-type", "").lower()
    if "html" in content_type:
        return _extract_from_html(base_url, response.text)
    if "json" in content_type:
        try:
            return _extract_from_json(base_url, response.json())
        except (json.JSONDecodeError, ValueError, KeyError, TypeError):
            logger.debug("JSON candidate extraction failed for %s", base_url)
            return _extract_from_html(base_url, response.text)
    return _extract_from_html(base_url, response.text)


def _extract_from_html(base_url: str, body: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for href in _HTML_LINK_PATTERN.findall(body):
        normalized = _normalize_candidate(base_url, unescape(href))
        if normalized is not None:
            candidates.append((normalized, "link"))
    for action, method in _HTML_FORM_PATTERN.findall(body):
        normalized = _normalize_candidate(base_url, unescape(action))
        if normalized is None:
            continue
        candidates.append((normalized, "form" if method.upper() == "POST" else "link"))
    return candidates


def _extract_from_json(base_url: str, payload: Any) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for value, parent_key in _walk_json_link_candidates(payload):
        if not _is_path_like(value, parent_key=parent_key):
            continue
        normalized = _normalize_candidate(base_url, value)
        if normalized is not None:
            candidates.append((normalized, "json"))
    return candidates


# ---------------------------------------------------------------------------
# Bootstrap / discovery helpers (standalone — take client as first arg)
# ---------------------------------------------------------------------------


def _bootstrap_json_server(
    client: httpx.Client,
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
        db_response = client.get(db_url, headers=auth_headers)
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
        collection_path = _normalize_path(collection_url)
        _register_endpoint(
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
        _register_endpoint(
            observed,
            path=detail_path,
            absolute_url=detail_url,
            methods={"GET", "PUT", "PATCH", "DELETE"},
            source="json_server_db",
            confidence=0.97,
        )

    return _infer_json_server_relations(payload)


def _bootstrap_current_json_entrypoint(
    client: httpx.Client,
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

    collection_path = _normalize_path(current_url)
    _register_endpoint(
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
    _probe_and_register(
        client,
        detail_path,
        detail_url,
        observed,
        source="json_entrypoint",
        auth_headers=auth_headers,
    )


# ---------------------------------------------------------------------------
# Collection registry discovery (Directus, PocketBase, etc.)
# ---------------------------------------------------------------------------


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _bootstrap_collection_registry(
    client: httpx.Client,
    *,
    current_url: str,
    response: httpx.Response,
    observed: dict[str, _ObservedEndpoint],
) -> list[str]:
    content_type = response.headers.get("content-type", "").lower()
    if "json" not in content_type:
        return []

    try:
        payload = response.json()
    except Exception:
        return []

    candidate_urls: list[str] = []
    strategy = _match_collection_strategy(current_url)
    if strategy is not None:
        for candidate in _apply_collection_strategy(strategy, current_url, payload):
            normalized_path = _normalize_path(candidate.collection_url)
            _register_endpoint(
                observed,
                path=normalized_path,
                absolute_url=candidate.collection_url,
                methods=set(candidate.methods),
                source=candidate.source,
                confidence=0.93,
            )
            candidate_urls.append(candidate.collection_url)
    return candidate_urls


def _match_collection_strategy(current_url: str) -> CollectionDiscoveryStrategy | None:
    """Return the first strategy whose path_suffix matches *current_url*."""
    current_path = urlparse(current_url).path.rstrip("/")
    for strategy in _COLLECTION_STRATEGIES:
        if current_path.endswith(strategy.path_suffix):
            return strategy
    return None


def _apply_collection_strategy(
    strategy: CollectionDiscoveryStrategy,
    current_url: str,
    payload: Any,
) -> list[_RegistryCollectionCandidate]:
    """Apply a generic collection strategy and return discovery candidates."""
    collection_items = _extract_collection_items(payload)
    if collection_items is None:
        return []

    parsed_current = urlparse(current_url)
    registry_root = parsed_current._replace(query="", fragment="").geturl().rstrip("/")
    current_path = parsed_current.path.rstrip("/")
    if current_path.endswith(strategy.path_suffix):
        prefix = current_path[: -len(strategy.path_suffix)]
    else:
        prefix = ""

    seen: set[str] = set()
    candidates: list[_RegistryCollectionCandidate] = []
    for item in collection_items:
        if not isinstance(item, dict):
            continue

        collection_name: str | None = None
        for nf in strategy.name_fields:
            val = item.get(nf)
            if isinstance(val, str) and val.strip():
                collection_name = val
                break
        if collection_name is None:
            continue

        if any(collection_name.startswith(ep) for ep in strategy.exclude_prefixes):
            continue

        if strategy.type_field is not None and strategy.valid_types is not None:
            item_type = item.get(strategy.type_field)
            if item_type not in strategy.valid_types:
                continue

        relative = strategy.item_path_template.format(name=collection_name)
        if strategy.item_path_template.startswith("/items/"):
            collection_path = f"{prefix}{relative}".replace("//", "/")
            collection_url = urljoin(_origin(current_url), collection_path)
        else:
            collection_url = _join_relative_url(registry_root, relative.lstrip("/"))

        if collection_url in seen:
            continue
        seen.add(collection_url)
        candidates.append(
            _RegistryCollectionCandidate(
                collection_url=collection_url,
                methods=("GET", "POST"),
                source="collection_registry",
            )
        )
    return candidates


# ---------------------------------------------------------------------------
# Pagination followup discovery
# ---------------------------------------------------------------------------


def _pagination_followups(
    client: httpx.Client,
    *,
    current_url: str,
    response: httpx.Response,
) -> list[str]:
    followups: set[str] = set()

    # 1. Parse RFC 5988 Link header for rel="next"
    link_next = _parse_link_header_next(response, current_url)
    if link_next is not None:
        followups.add(link_next)

    # 2. Parse JSON body for pagination hints
    content_type = response.headers.get("content-type", "").lower()
    if "json" in content_type:
        try:
            payload = response.json()
        except Exception:
            payload = None
        if payload is not None:
            followups.update(_pagination_followup_urls(current_url, payload))

    return sorted(followups)


def _pagination_followup_urls(current_url: str, payload: Any) -> list[str]:
    followups: set[str] = set()
    if isinstance(payload, dict):
        for key in ("@odata.nextLink", "next", "next_link", "nextLink", "next_url", "nextUrl"):
            value = payload.get(key)
            if isinstance(value, str):
                normalized = _normalize_same_origin_candidate(current_url, value)
                if normalized is not None:
                    followups.add(normalized)

    pagination_state = _pagination_state(payload)
    if pagination_state is not None:
        current_page, total_pages, per_page = pagination_state
        if current_page < total_pages:
            next_page_url = _set_query_param(current_url, "page", current_page + 1)
            if per_page is not None:
                next_page_url = _set_query_param(next_page_url, "perPage", per_page)
            followups.add(next_page_url)
    return sorted(followups)


def _pagination_state(payload: Any) -> tuple[int, int, int | None] | None:
    if not isinstance(payload, dict):
        return None

    candidates: list[dict[str, Any]] = [payload]
    meta = payload.get("meta")
    if isinstance(meta, dict):
        candidates.append(meta)
        pagination = meta.get("pagination")
        if isinstance(pagination, dict):
            candidates.append(pagination)
    pagination = payload.get("pagination")
    if isinstance(pagination, dict):
        candidates.append(pagination)

    for candidate in candidates:
        current_page = _int_like(candidate.get("page"))
        if current_page is None:
            continue
        per_page = _int_like(candidate.get("perPage")) or _int_like(candidate.get("per_page"))
        total_pages = _int_like(candidate.get("totalPages")) or _int_like(
            candidate.get("total_pages")
        )
        total_items = _int_like(candidate.get("totalItems")) or _int_like(candidate.get("total"))
        if total_pages is None and per_page and total_items is not None and total_items >= 0:
            total_pages = max(1, (total_items + per_page - 1) // per_page)
        if total_pages is None:
            continue
        return current_page, total_pages, per_page
    return None


def _normalize_same_origin_candidate(base_url: str, candidate: str) -> str | None:
    absolute = urljoin(base_url, candidate)
    if urlparse(absolute).netloc != urlparse(base_url).netloc:
        return None
    return absolute


def _parse_link_header_next(response: httpx.Response, base_url: str) -> str | None:
    """Parse RFC 5988 Link header and return the rel="next" URL, if any."""
    link_header = response.headers.get("link")
    if link_header is None:
        return None
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' not in part and "rel='next'" not in part:
            continue
        # Extract URL between < and >
        start = part.find("<")
        end = part.find(">")
        if start == -1 or end == -1 or end <= start:
            continue
        url = part[start + 1 : end].strip()
        return _normalize_same_origin_candidate(base_url, url)
    return None


def extract_rate_limit_info(response: httpx.Response) -> dict[str, int | str] | None:
    """Extract rate-limit metadata from standard headers.

    Looks for X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset,
    and RateLimit-* (IETF draft) headers.
    """
    info: dict[str, int | str] = {}
    for header, key in (
        ("x-ratelimit-limit", "limit"),
        ("ratelimit-limit", "limit"),
        ("x-ratelimit-remaining", "remaining"),
        ("ratelimit-remaining", "remaining"),
        ("x-ratelimit-reset", "reset"),
        ("ratelimit-reset", "reset"),
    ):
        value = response.headers.get(header)
        if value is not None and key not in info:
            parsed = _int_like(value)
            if parsed is not None:
                info[key] = parsed
            else:
                info[key] = value
    return info if info else None


def detect_auth_requirements(response: httpx.Response) -> dict[str, str] | None:
    """Detect authentication requirements from a 401 response.

    Parses the WWW-Authenticate header to identify auth scheme.
    Returns None if the response is not a 401 or has no WWW-Authenticate.
    """
    if response.status_code != 401:
        return None
    www_auth = response.headers.get("www-authenticate")
    if www_auth is None:
        return {"scheme": "unknown"}
    # Extract the scheme (first token before space or comma)
    parts = www_auth.split()
    if not parts:
        return {"scheme": "unknown", "raw": www_auth}
    scheme = parts[0].rstrip(",").lower()
    result: dict[str, str] = {"scheme": scheme, "raw": www_auth}
    # Extract realm if present
    lower = www_auth.lower()
    realm_start = lower.find('realm="')
    if realm_start != -1:
        realm_start += 7
        realm_end = www_auth.find('"', realm_start)
        if realm_end != -1:
            result["realm"] = www_auth[realm_start:realm_end]
    return result


def _set_query_param(url: str, name: str, value: int) -> str:
    parsed = urlparse(url)
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    filtered = [(key, existing) for key, existing in query_pairs if key != name]
    filtered.append((name, str(value)))
    encoded = urlencode(filtered, doseq=True)
    return parsed._replace(query=encoded).geturl()


def _int_like(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 429 retry logic
# ---------------------------------------------------------------------------


def _request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    response: httpx.Response | None = None
    for attempt in range(_MAX_RETRY_ATTEMPTS):
        response = client.request(method, url, headers=headers or {})
        if response.status_code != 429 or attempt == _MAX_RETRY_ATTEMPTS - 1:
            return response
        time.sleep(_retry_after_seconds(response))
    assert response is not None
    return response


def _retry_after_seconds(response: httpx.Response) -> float:
    retry_after = response.headers.get("retry-after")
    if retry_after is None:
        return _DEFAULT_RETRY_AFTER_SECONDS
    try:
        return max(float(retry_after), 0.0)
    except ValueError:
        return _DEFAULT_RETRY_AFTER_SECONDS


def _infer_sub_resources(
    client: httpx.Client,
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
                _probe_and_register(
                    client,
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
                    _probe_and_register(
                        client,
                        candidate,
                        candidate_url,
                        inferred,
                        source="inferred",
                        auth_headers=headers,
                    )

    return inferred
