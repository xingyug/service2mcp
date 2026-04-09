"""Ground truth for JSONPlaceholder (https://jsonplaceholder.typicode.com).

JSONPlaceholder is a free fake REST API with 6 resource types and ~20
canonical endpoints.  It is the primary REST discovery target
because it responds to OPTIONS, returns JSON array/object bodies, and
has a predictable resource hierarchy.

Usage in tests::

    from tests.fixtures.ground_truth.jsonplaceholder import (
        GROUND_TRUTH,
        build_jsonplaceholder_transport,
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Ground-truth endpoint registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EndpointTruth:
    """Expected properties for one canonical endpoint."""

    method: str
    path: str
    writes_state: bool = False
    destructive: bool = False
    external_side_effect: bool = False
    idempotent: bool = True
    resource_group: str = ""
    description: str = ""


def _ep(
    method: str,
    path: str,
    *,
    resource_group: str = "",
    writes_state: bool = False,
    destructive: bool = False,
    external_side_effect: bool = False,
    idempotent: bool = True,
    description: str = "",
) -> EndpointTruth:
    return EndpointTruth(
        method=method,
        path=path,
        resource_group=resource_group,
        writes_state=writes_state,
        destructive=destructive,
        external_side_effect=external_side_effect,
        idempotent=idempotent,
        description=description,
    )


# Canonical endpoints per JSONPlaceholder documentation.
GROUND_TRUTH: list[EndpointTruth] = [
    # --- posts ---
    _ep("GET", "/posts", resource_group="posts", description="List all posts"),
    _ep("GET", "/posts/{id}", resource_group="posts", description="Get post by ID"),
    _ep(
        "POST",
        "/posts",
        resource_group="posts",
        writes_state=True,
        idempotent=False,
        description="Create post",
    ),
    _ep(
        "PUT",
        "/posts/{id}",
        resource_group="posts",
        writes_state=True,
        description="Replace post",
    ),
    _ep(
        "PATCH",
        "/posts/{id}",
        resource_group="posts",
        writes_state=True,
        description="Partial update post",
    ),
    _ep(
        "DELETE",
        "/posts/{id}",
        resource_group="posts",
        destructive=True,
        description="Delete post",
    ),
    _ep(
        "GET",
        "/posts/{id}/comments",
        resource_group="posts",
        description="List comments for post",
    ),
    # --- comments ---
    _ep("GET", "/comments", resource_group="comments", description="List all comments"),
    _ep("GET", "/comments/{id}", resource_group="comments", description="Get comment by ID"),
    # --- albums ---
    _ep("GET", "/albums", resource_group="albums", description="List all albums"),
    _ep("GET", "/albums/{id}", resource_group="albums", description="Get album by ID"),
    _ep("GET", "/albums/{id}/photos", resource_group="albums", description="List photos for album"),
    # --- photos ---
    _ep("GET", "/photos", resource_group="photos", description="List all photos"),
    _ep("GET", "/photos/{id}", resource_group="photos", description="Get photo by ID"),
    # --- todos ---
    _ep("GET", "/todos", resource_group="todos", description="List all todos"),
    _ep("GET", "/todos/{id}", resource_group="todos", description="Get todo by ID"),
    # --- users ---
    _ep("GET", "/users", resource_group="users", description="List all users"),
    _ep("GET", "/users/{id}", resource_group="users", description="Get user by ID"),
    _ep("GET", "/users/{id}/posts", resource_group="users", description="List posts for user"),
    _ep("GET", "/users/{id}/todos", resource_group="users", description="List todos for user"),
    _ep("GET", "/users/{id}/albums", resource_group="users", description="List albums for user"),
]

GROUND_TRUTH_BY_KEY: dict[tuple[str, str], EndpointTruth] = {
    (ep.method, ep.path): ep for ep in GROUND_TRUTH
}

RESOURCE_GROUPS: list[str] = sorted({ep.resource_group for ep in GROUND_TRUTH})

BASE_URL = "https://jsonplaceholder.typicode.com"


# ---------------------------------------------------------------------------
# Mock HTTP transport for offline testing
# ---------------------------------------------------------------------------


@dataclass
class _MockState:
    """Mutable response state for the mock transport."""

    call_log: list[tuple[str, str]] = field(default_factory=list)


# Sample response bodies matching real JSONPlaceholder shapes.
_USERS_LIST: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "Leanne Graham",
        "username": "Bret",
        "email": "Sincere@april.biz",
        "phone": "1-770-736-8031 x56442",
        "website": "hildegard.org",
    },
    {
        "id": 2,
        "name": "Ervin Howell",
        "username": "Antonette",
        "email": "Shanna@melissa.tv",
        "phone": "010-692-6593 x09125",
        "website": "anastasia.net",
    },
]

_POSTS_LIST: list[dict[str, Any]] = [
    {"id": 1, "userId": 1, "title": "sunt aut facere", "body": "quia et suscipit"},
    {"id": 2, "userId": 1, "title": "qui est esse", "body": "est rerum tempore"},
]

_COMMENTS_LIST: list[dict[str, Any]] = [
    {
        "id": 1,
        "postId": 1,
        "name": "id labore",
        "email": "Eliseo@gardner.biz",
        "body": "laudantium enim",
    },
    {
        "id": 2,
        "postId": 1,
        "name": "quo vero",
        "email": "Jayne_Kuhic@sydney.com",
        "body": "est natus enim",
    },
]

_ALBUMS_LIST: list[dict[str, Any]] = [
    {"id": 1, "userId": 1, "title": "quidem molestiae enim"},
    {"id": 2, "userId": 1, "title": "sunt qui excepturi"},
]

_PHOTOS_LIST: list[dict[str, Any]] = [
    {
        "id": 1,
        "albumId": 1,
        "title": "accusamus beatae",
        "url": "https://via.placeholder.com/600/92c952",
        "thumbnailUrl": "https://via.placeholder.com/150/92c952",
    },
]

_TODOS_LIST: list[dict[str, Any]] = [
    {"id": 1, "userId": 1, "title": "delectus aut autem", "completed": False},
    {"id": 2, "userId": 1, "title": "quis ut nam facilis", "completed": True},
]

_COLLECTION_MAP: dict[str, list[dict[str, Any]]] = {
    "/posts": _POSTS_LIST,
    "/comments": _COMMENTS_LIST,
    "/albums": _ALBUMS_LIST,
    "/photos": _PHOTOS_LIST,
    "/todos": _TODOS_LIST,
    "/users": _USERS_LIST,
}

_NESTED_MAP: dict[str, list[dict[str, Any]]] = {
    "/posts/{id}/comments": _COMMENTS_LIST,
    "/albums/{id}/photos": _PHOTOS_LIST,
    "/users/{id}/posts": _POSTS_LIST,
    "/users/{id}/todos": _TODOS_LIST,
    "/users/{id}/albums": _ALBUMS_LIST,
}


def _match_path(request_path: str) -> tuple[str | None, dict[str, str]]:
    """Match a concrete request path to a ground-truth path template."""
    import re

    parts = request_path.rstrip("/").split("/")

    # Try nested patterns: /<resource>/<id>/<sub>
    if len(parts) == 4:
        pattern = f"/{parts[1]}/{{id}}/{parts[3]}"
        if pattern in _NESTED_MAP:
            return pattern, {"id": parts[2]}

    # Try item patterns: /<resource>/<id>
    if len(parts) == 3 and re.match(r"^\d+$", parts[2]):
        collection = f"/{parts[1]}"
        if collection in _COLLECTION_MAP:
            return f"/{parts[1]}/{{id}}", {"id": parts[2]}

    # Try collection patterns: /<resource>
    if len(parts) == 2:
        collection = f"/{parts[1]}"
        if collection in _COLLECTION_MAP:
            return collection, {}

    return None, {}


def _handle_request(request: httpx.Request, state: _MockState) -> httpx.Response:
    """Route a mock request and return a realistic response."""
    import json

    path = request.url.path.rstrip("/") or "/"
    method = request.method.upper()
    state.call_log.append((method, path))

    # Root: return link map (HATEOAS-style)
    if path == "/" or path == "":
        links = {res: f"{BASE_URL}{res}" for res in sorted(_COLLECTION_MAP)}
        return httpx.Response(200, json=links)

    # /api is not a real JSONPlaceholder endpoint
    if path == "/api":
        return httpx.Response(404, json={"error": "Not found"})

    # OPTIONS: return Allow header for any matched path
    matched_template, params = _match_path(path)
    if method == "OPTIONS":
        if matched_template is not None:
            allowed = sorted({ep.method for ep in GROUND_TRUTH if ep.path == matched_template})
            return httpx.Response(
                204,
                headers={"Allow": ", ".join(allowed)},
            )
        return httpx.Response(404)

    if matched_template is None:
        return httpx.Response(404, json={"error": "Not found"})

    # Nested collection
    if matched_template in _NESTED_MAP:
        if method == "GET":
            return httpx.Response(200, json=_NESTED_MAP[matched_template])
        return httpx.Response(405)

    # Item (has {id} in template and id captured)
    if "{id}" in matched_template and params.get("id"):
        collection_key = "/" + matched_template.split("/")[1]
        items = _COLLECTION_MAP.get(collection_key, [])
        item_id = int(params["id"])
        item = next((i for i in items if i.get("id") == item_id), None)

        if method == "GET":
            if item:
                return httpx.Response(200, json=item)
            return httpx.Response(404, json={"error": "Not found"})
        if method in ("PUT", "PATCH"):
            body = json.loads(request.content) if request.content else {}
            merged = {**(item or {}), **body, "id": item_id}
            return httpx.Response(200, json=merged)
        if method == "DELETE":
            return httpx.Response(200, json={})
        return httpx.Response(405)

    # Collection
    collection_key = matched_template
    if method == "GET":
        return httpx.Response(200, json=_COLLECTION_MAP.get(collection_key, []))
    if method == "POST":
        body = json.loads(request.content) if request.content else {}
        new_item = {**body, "id": 101}
        return httpx.Response(201, json=new_item)
    return httpx.Response(405)


def build_jsonplaceholder_transport() -> httpx.MockTransport:
    """Build a mock transport simulating JSONPlaceholder responses."""
    state = _MockState()

    def handler(request: httpx.Request) -> httpx.Response:
        return _handle_request(request, state)

    return httpx.MockTransport(handler)


def get_mock_state(transport: httpx.MockTransport) -> _MockState:
    """Retrieve the internal call log state from a mock transport.

    This relies on the transport's handler closure capturing ``state``.
    """
    # The handler is a closure; extract state via __closure__
    handler = transport._handler  # type: ignore[attr-defined]
    for cell in handler.__closure__ or ():
        val = cell.cell_contents
        if isinstance(val, _MockState):
            return val
    raise RuntimeError("Could not find _MockState in transport closure")
