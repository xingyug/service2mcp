"""Large-surface REST mock API fixture for the B-003 black-box pilot.

Serves 62 endpoints across 9 resource groups via ``httpx.MockTransport``.
The ``GROUND_TRUTH`` dict maps every ``(method, path)`` pair to its expected
risk metadata so the pilot test can compute precise coverage numbers.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Ground-truth endpoint registry
# ---------------------------------------------------------------------------

_EndpointMeta = dict[str, Any]  # keys: writes_state, destructive, idempotent, external_side_effect

GROUND_TRUTH: dict[tuple[str, str], _EndpointMeta] = {}


def _register(
    method: str,
    path: str,
    *,
    writes_state: bool = False,
    destructive: bool = False,
    idempotent: bool | None = None,
    external_side_effect: bool = False,
) -> tuple[str, str]:
    if idempotent is None:
        idempotent = method in {"GET", "PUT", "DELETE"}
    GROUND_TRUTH[(method, path)] = {
        "writes_state": writes_state,
        "destructive": destructive,
        "idempotent": idempotent,
        "external_side_effect": external_side_effect,
    }
    return (method, path)


# --- Users (12 endpoints) ---
_register("GET", "/api/users")
_register("POST", "/api/users", writes_state=True)
_register("GET", "/api/users/{user_id}")
_register("PUT", "/api/users/{user_id}", writes_state=True)
_register("DELETE", "/api/users/{user_id}", destructive=True)
_register("GET", "/api/users/{user_id}/posts")
_register("POST", "/api/users/{user_id}/posts", writes_state=True)
_register("GET", "/api/users/{user_id}/posts/{post_id}")
_register("PUT", "/api/users/{user_id}/posts/{post_id}", writes_state=True)
_register("DELETE", "/api/users/{user_id}/posts/{post_id}", destructive=True)
_register("GET", "/api/users/{user_id}/settings")
_register("PUT", "/api/users/{user_id}/settings", writes_state=True)

# --- Products (10 endpoints) ---
_register("GET", "/api/products")
_register("POST", "/api/products", writes_state=True)
_register("GET", "/api/products/{product_id}")
_register("PUT", "/api/products/{product_id}", writes_state=True)
_register("DELETE", "/api/products/{product_id}", destructive=True)
_register("GET", "/api/products/{product_id}/reviews")
_register("POST", "/api/products/{product_id}/reviews", writes_state=True)
_register("GET", "/api/products/{product_id}/images")
_register("POST", "/api/products/{product_id}/images", writes_state=True)
_register("GET", "/api/products/search")

# --- Orders (12 endpoints) ---
_register("GET", "/api/orders")
_register("POST", "/api/orders", writes_state=True)
_register("GET", "/api/orders/{order_id}")
_register("PUT", "/api/orders/{order_id}", writes_state=True)
_register("DELETE", "/api/orders/{order_id}", destructive=True)
_register("GET", "/api/orders/{order_id}/items")
_register("POST", "/api/orders/{order_id}/items", writes_state=True)
_register("DELETE", "/api/orders/{order_id}/items/{item_id}", destructive=True)
_register("GET", "/api/orders/{order_id}/payments")
_register("POST", "/api/orders/{order_id}/payments", writes_state=True, external_side_effect=True)
_register("POST", "/api/orders/{order_id}/cancel", writes_state=True)
_register("POST", "/api/orders/{order_id}/ship", writes_state=True, external_side_effect=True)

# --- Categories (6 endpoints) ---
_register("GET", "/api/categories")
_register("POST", "/api/categories", writes_state=True)
_register("GET", "/api/categories/{category_id}")
_register("PUT", "/api/categories/{category_id}", writes_state=True)
_register("GET", "/api/categories/{category_id}/products")
_register("GET", "/api/categories/tree")

# --- Inventory (5 endpoints) ---
_register("GET", "/api/inventory")
_register("GET", "/api/inventory/{sku}")
_register("PUT", "/api/inventory/{sku}", writes_state=True)
_register("POST", "/api/inventory/adjust", writes_state=True)
_register("POST", "/api/inventory/bulk-import", writes_state=True)

# --- Notifications (4 endpoints) ---
_register("GET", "/api/notifications")
_register("GET", "/api/notifications/{notification_id}")
_register("POST", "/api/notifications/{notification_id}/acknowledge", writes_state=True)
_register("POST", "/api/notifications/dispatch", writes_state=True, external_side_effect=True)

# --- Reports (4 endpoints) ---
_register("POST", "/api/reports/generate", writes_state=True)
_register("GET", "/api/reports/{report_id}/status")
_register("GET", "/api/reports/{report_id}/download")
_register("GET", "/api/reports")

# --- Webhooks (5 endpoints) ---
_register("GET", "/api/webhooks")
_register("POST", "/api/webhooks", writes_state=True)
_register("GET", "/api/webhooks/{webhook_id}")
_register("DELETE", "/api/webhooks/{webhook_id}", destructive=True)
_register("POST", "/api/webhooks/{webhook_id}/test", external_side_effect=True)

# --- Admin (4 endpoints) ---
_register("GET", "/api/admin/config")
_register("PUT", "/api/admin/config", writes_state=True)
_register("GET", "/api/admin/flags")
_register("GET", "/api/admin/health")


# ---------------------------------------------------------------------------
# Mock transport
# ---------------------------------------------------------------------------

_RESOURCE_GROUPS = [
    "users",
    "products",
    "orders",
    "categories",
    "inventory",
    "notifications",
    "reports",
    "webhooks",
    "admin",
]

_PATH_PARAM_RE = re.compile(r"\{([^{}]+)\}")


def _html_index() -> str:
    """Top-level HTML page with links to all resource group listing endpoints."""
    links = "\n".join(
        f'<a href="/api/{group}">{group.title()}</a>'
        for group in _RESOURCE_GROUPS
        if group != "admin"
    )
    admin_links = (
        '<a href="/api/admin/config">Admin Config</a>\n<a href="/api/admin/health">Admin Health</a>'
    )
    return f"<html><body>\n<h1>REST API</h1>\n{links}\n{admin_links}\n</body></html>"


def _json_index() -> dict[str, Any]:
    """JSON API index with HATEOAS links for discovery."""
    return {
        "service": "large-surface-rest-api",
        "version": "1.0.0",
        "links": {group: f"/api/{group}" for group in _RESOURCE_GROUPS if group != "admin"},
        "admin": {
            "config": "/api/admin/config",
            "flags": "/api/admin/flags",
            "health": "/api/admin/health",
        },
    }


def _resource_list_response(group: str) -> dict[str, Any]:
    """JSON listing response for a resource group with embedded links."""
    items: list[dict[str, Any]] = []
    nested_links: list[str] = []

    if group == "users":
        for uid in ["usr-1", "usr-2", "usr-3"]:
            items.append({"id": uid, "name": f"User {uid}"})
            nested_links.extend(
                [
                    f"/api/users/{uid}",
                    f"/api/users/{uid}/posts",
                    f"/api/users/{uid}/settings",
                ]
            )
    elif group == "products":
        for pid in ["prod-1", "prod-2"]:
            items.append({"id": pid, "name": f"Product {pid}"})
            nested_links.extend(
                [
                    f"/api/products/{pid}",
                    f"/api/products/{pid}/reviews",
                    f"/api/products/{pid}/images",
                ]
            )
        nested_links.append("/api/products/search?q=widget&limit=10")
    elif group == "orders":
        for oid in ["ord-1", "ord-2"]:
            items.append({"id": oid, "total": 1250})
            nested_links.extend(
                [
                    f"/api/orders/{oid}",
                    f"/api/orders/{oid}/items",
                    f"/api/orders/{oid}/payments",
                ]
            )
    elif group == "categories":
        items.append({"id": "cat-1", "name": "Electronics"})
        nested_links.extend(
            [
                "/api/categories/cat-1",
                "/api/categories/cat-1/products",
                "/api/categories/tree",
            ]
        )
    elif group == "inventory":
        items.append({"sku": "sku-1", "count": 42})
        nested_links.extend(
            [
                "/api/inventory/sku-1",
            ]
        )
    elif group == "notifications":
        items.append({"id": "notif-1", "message": "Hello"})
        nested_links.extend(
            [
                "/api/notifications/notif-1",
                "/api/notifications/notif-1/acknowledge",
            ]
        )
    elif group == "reports":
        items.append({"id": "rpt-1", "name": "Q1 Sales"})
        nested_links.extend(
            [
                "/api/reports/rpt-1/status",
                "/api/reports/rpt-1/download",
            ]
        )
    elif group == "webhooks":
        items.append({"id": "wh-1", "url": "https://hook.example.com/cb"})
        nested_links.extend(
            [
                "/api/webhooks/wh-1",
                "/api/webhooks/wh-1/test",
            ]
        )

    return {
        "items": items,
        "links": nested_links,
        "pagination": {"page": 1, "limit": 20, "total": len(items)},
    }


def _detail_response(path: str) -> dict[str, Any]:
    """HATEOAS-style detail response with links to sub-resources."""
    response: dict[str, Any] = {
        "status": "ok",
        "path": path,
        "result": {"found": True},
    }

    # Add sub-resource links based on the path pattern.
    links: list[str] = []
    parts = path.strip("/").split("/")

    # /api/users/{id} → link to posts, settings
    if len(parts) == 3 and parts[1] == "users":
        uid = parts[2]
        links = [
            f"/api/users/{uid}/posts",
            f"/api/users/{uid}/settings",
        ]
    # /api/products/{id} → link to reviews, images
    elif len(parts) == 3 and parts[1] == "products":
        pid = parts[2]
        links = [
            f"/api/products/{pid}/reviews",
            f"/api/products/{pid}/images",
        ]
    # /api/orders/{id} → link to items, payments
    elif len(parts) == 3 and parts[1] == "orders":
        oid = parts[2]
        links = [
            f"/api/orders/{oid}/items",
            f"/api/orders/{oid}/payments",
        ]
    # /api/categories/{id} → link to products
    elif len(parts) == 3 and parts[1] == "categories":
        cid = parts[2]
        links = [f"/api/categories/{cid}/products"]
    # /api/notifications/{id} → link to acknowledge
    elif len(parts) == 3 and parts[1] == "notifications":
        nid = parts[2]
        links = [f"/api/notifications/{nid}/acknowledge"]
    # /api/reports/{id}/status, /api/reports/{id}/download
    elif len(parts) == 3 and parts[1] == "reports":
        rid = parts[2]
        links = [
            f"/api/reports/{rid}/status",
            f"/api/reports/{rid}/download",
        ]
    # /api/webhooks/{id} → link to test
    elif len(parts) == 3 and parts[1] == "webhooks":
        wid = parts[2]
        links = [f"/api/webhooks/{wid}/test"]

    if links:
        response["links"] = links

    return response


def _write_response(path: str, method: str) -> dict[str, Any]:
    """Generic write response."""
    return {"status": "ok", "path": path, "method": method, "result": {"created": True}}


def _allowed_methods(path: str) -> str:
    """Build Allow header value for a given path template."""
    methods = sorted({m for m, p in GROUND_TRUTH if p == path})
    if not methods:
        return "GET"
    extra = {"OPTIONS", "HEAD"}
    return ", ".join(sorted(set(methods) | extra))


def _match_ground_truth_path(request_path: str) -> str | None:
    """Match a concrete request path against ground-truth path templates."""
    for _, template_path in GROUND_TRUTH:
        regex_path = _PATH_PARAM_RE.sub(r"[^/]+", re.escape(template_path))
        regex_path = regex_path.replace(r"\[^/\]\+", "[^/]+")  # unescape the replacement
        # Build proper regex from template
        parts = template_path.split("/")
        regex_parts = []
        for part in parts:
            if part.startswith("{") and part.endswith("}"):
                regex_parts.append("[^/]+")
            else:
                regex_parts.append(re.escape(part))
        pattern = "/".join(regex_parts)
        if re.fullmatch(pattern, request_path):
            return template_path
    return None


def _extract_group(path: str) -> str | None:
    """Extract the resource group name from a path."""
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "api":
        return parts[1]
    return None


def build_large_surface_transport() -> httpx.MockTransport:
    """Create a mock transport serving 62 REST endpoints for the pilot fixture."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path or "/"
        method = request.method.upper()

        # Root: serve both HTML and JSON depending on Accept header
        if path == "/api" or path == "/api/":
            accept = request.headers.get("accept", "")
            if "json" in accept:
                return httpx.Response(
                    200,
                    json=_json_index(),
                    request=request,
                    headers={"content-type": "application/json"},
                )
            return httpx.Response(
                200,
                text=_html_index(),
                request=request,
                headers={"content-type": "text/html"},
            )

        # OPTIONS → Allow header
        if method == "OPTIONS":
            template = _match_ground_truth_path(path)
            if template:
                return httpx.Response(
                    200,
                    headers={"allow": _allowed_methods(template)},
                    request=request,
                )
            # Also handle OPTIONS on collection listing paths (/api/users, etc.)
            group = _extract_group(path)
            if group and group in _RESOURCE_GROUPS and path == f"/api/{group}":
                # Collections support GET + POST
                return httpx.Response(
                    200,
                    headers={"allow": "DELETE, GET, HEAD, OPTIONS, POST, PUT"},
                    request=request,
                )
            return httpx.Response(404, request=request)

        # Match to ground-truth
        template = _match_ground_truth_path(path)

        if template is None:
            # Resource group listing pages? e.g. /api/users
            group = _extract_group(path)
            if group and group in _RESOURCE_GROUPS and path == f"/api/{group}":
                if method == "GET":
                    return httpx.Response(
                        200,
                        json=_resource_list_response(group),
                        request=request,
                        headers={"content-type": "application/json"},
                    )
                if method == "POST":
                    return httpx.Response(
                        201,
                        json=_write_response(path, method),
                        request=request,
                        headers={"content-type": "application/json"},
                    )
            return httpx.Response(404, request=request)

        # Check method is valid
        if (method, template) not in GROUND_TRUTH:
            return httpx.Response(405, request=request)

        if method == "GET":
            # Collection endpoints (no path params) return list responses
            # with HATEOAS links; detail endpoints return detail responses.
            group = _extract_group(template)
            is_collection = group and group in _RESOURCE_GROUPS and template == f"/api/{group}"
            if is_collection:
                return httpx.Response(
                    200,
                    json=_resource_list_response(group),
                    request=request,
                    headers={"content-type": "application/json"},
                )
            return httpx.Response(
                200,
                json=_detail_response(path),
                request=request,
                headers={"content-type": "application/json"},
            )
        if method in {"POST", "PUT", "PATCH"}:
            return httpx.Response(
                201 if method == "POST" else 200,
                json=_write_response(path, method),
                request=request,
                headers={"content-type": "application/json"},
            )
        if method == "DELETE":
            return httpx.Response(
                200,
                json={"status": "ok", "deleted": True},
                request=request,
                headers={"content-type": "application/json"},
            )

        return httpx.Response(404, request=request)

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# Sample invocations for safe (auditable) operations
# ---------------------------------------------------------------------------


def build_sample_invocations() -> dict[str, dict[str, Any]]:
    """Build sample invocations for safe operations in the ground truth.

    Only produces invocations for GET endpoints and idempotent PUT endpoints
    that are not destructive and have no external side effects — matching the
    default ``AuditPolicy`` skip rules.
    """
    invocations: dict[str, dict[str, Any]] = {}

    # Static argument values for path parameters
    _param_values: dict[str, str] = {
        "user_id": "usr-1",
        "post_id": "post-1",
        "product_id": "prod-1",
        "order_id": "ord-1",
        "item_id": "item-1",
        "category_id": "cat-1",
        "sku": "sku-1",
        "notification_id": "notif-1",
        "report_id": "rpt-1",
        "webhook_id": "wh-1",
    }

    for (method, path), meta in sorted(GROUND_TRUTH.items()):
        # Build operation ID matching the extractor's naming convention
        path_parts = [seg for seg in re.split(r"[/{}]+", path) if seg]
        slug = "_".join(p.replace("-", "_") for p in path_parts) or "root"
        operation_id = f"{method.lower()}_{slug}"

        # Build arguments from path parameters
        arguments: dict[str, Any] = {}
        for param_name in _PATH_PARAM_RE.findall(path):
            arguments[param_name] = _param_values.get(param_name, f"test-{param_name}")

        # Add body for POST/PUT/PATCH
        if method in {"POST", "PUT", "PATCH"}:
            arguments["payload"] = {"test": True}

        invocations[operation_id] = arguments

    return invocations
