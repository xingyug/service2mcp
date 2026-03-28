"""Ground truth for Swagger PetStore v3 (https://petstore3.swagger.io).

PetStore v3 is the canonical OpenAPI 3.0 demo.  It has 19 documented
operations across 3 tags (pet, store, user) and serves as the B-005
spec-first target because it ships a machine-readable OpenAPI spec at a
well-known URL.

Usage in tests::

    from tests.fixtures.ground_truth.petstore_v3 import (
        GROUND_TRUTH,
        OPENAPI_SPEC_URL,
        build_petstore_transport,
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from tests.fixtures.ground_truth.jsonplaceholder import EndpointTruth


def _ep(
    method: str,
    path: str,
    *,
    resource_group: str = "",
    writes_state: bool = False,
    destructive: bool = False,
    idempotent: bool = True,
    description: str = "",
) -> EndpointTruth:
    return EndpointTruth(
        method=method,
        path=path,
        resource_group=resource_group,
        writes_state=writes_state,
        destructive=destructive,
        idempotent=idempotent,
        description=description,
    )


# Canonical operations per the official PetStore v3 OpenAPI spec.
GROUND_TRUTH: list[EndpointTruth] = [
    # --- pet ---
    _ep(
        "PUT",
        "/pet",
        resource_group="pet",
        writes_state=True,
        description="Update an existing pet",
    ),
    _ep(
        "POST",
        "/pet",
        resource_group="pet",
        writes_state=True,
        idempotent=False,
        description="Add a new pet",
    ),
    _ep("GET", "/pet/findByStatus", resource_group="pet", description="Find pets by status"),
    _ep("GET", "/pet/findByTags", resource_group="pet", description="Find pets by tags"),
    _ep("GET", "/pet/{petId}", resource_group="pet", description="Find pet by ID"),
    _ep(
        "POST",
        "/pet/{petId}",
        resource_group="pet",
        writes_state=True,
        idempotent=False,
        description="Update pet with form data",
    ),
    _ep(
        "DELETE",
        "/pet/{petId}",
        resource_group="pet",
        destructive=True,
        description="Delete a pet",
    ),
    _ep(
        "POST",
        "/pet/{petId}/uploadImage",
        resource_group="pet",
        writes_state=True,
        idempotent=False,
        description="Upload pet image",
    ),
    # --- store ---
    _ep(
        "GET",
        "/store/inventory",
        resource_group="store",
        description="Returns pet inventories",
    ),
    _ep(
        "POST",
        "/store/order",
        resource_group="store",
        writes_state=True,
        idempotent=False,
        description="Place order",
    ),
    _ep(
        "GET",
        "/store/order/{orderId}",
        resource_group="store",
        description="Find purchase order by ID",
    ),
    _ep(
        "DELETE",
        "/store/order/{orderId}",
        resource_group="store",
        destructive=True,
        description="Delete purchase order",
    ),
    # --- user ---
    _ep(
        "POST",
        "/user",
        resource_group="user",
        writes_state=True,
        idempotent=False,
        description="Create user",
    ),
    _ep(
        "POST",
        "/user/createWithList",
        resource_group="user",
        writes_state=True,
        idempotent=False,
        description="Create users from list",
    ),
    _ep("GET", "/user/login", resource_group="user", description="Log user into system"),
    _ep("GET", "/user/logout", resource_group="user", description="Log out current user"),
    _ep("GET", "/user/{username}", resource_group="user", description="Get user by name"),
    _ep(
        "PUT",
        "/user/{username}",
        resource_group="user",
        writes_state=True,
        description="Update user",
    ),
    _ep(
        "DELETE",
        "/user/{username}",
        resource_group="user",
        destructive=True,
        description="Delete user",
    ),
]

GROUND_TRUTH_BY_KEY: dict[tuple[str, str], EndpointTruth] = {
    (ep.method, ep.path): ep for ep in GROUND_TRUTH
}

RESOURCE_GROUPS: list[str] = sorted({ep.resource_group for ep in GROUND_TRUTH})

BASE_URL = "https://petstore3.swagger.io/api/v3"
OPENAPI_SPEC_URL = "https://petstore3.swagger.io/api/v3/openapi.json"


# ---------------------------------------------------------------------------
# Minimal inline OpenAPI 3.0 spec (trimmed to the essentials)
# ---------------------------------------------------------------------------

_OPENAPI_SPEC: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {
        "title": "Swagger Petstore - OpenAPI 3.0",
        "description": "Petstore server based on OpenAPI 3.0 specification.",
        "version": "1.0.17",
    },
    "servers": [{"url": "/api/v3"}],
    "paths": {
        "/pet": {
            "put": {
                "tags": ["pet"],
                "summary": "Update an existing pet",
                "operationId": "updatePet",
                "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                "responses": {"200": {"description": "Successful operation"}},
            },
            "post": {
                "tags": ["pet"],
                "summary": "Add a new pet to the store",
                "operationId": "addPet",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["name", "photoUrls"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "photoUrls": {"type": "array", "items": {"type": "string"}},
                                    "status": {
                                        "type": "string",
                                        "enum": ["available", "pending", "sold"],
                                    },
                                },
                            }
                        }
                    },
                    "required": True,
                },
                "responses": {"200": {"description": "Successful operation"}},
            },
        },
        "/pet/findByStatus": {
            "get": {
                "tags": ["pet"],
                "summary": "Finds Pets by status",
                "operationId": "findPetsByStatus",
                "parameters": [
                    {
                        "name": "status",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string", "enum": ["available", "pending", "sold"]},
                    }
                ],
                "responses": {"200": {"description": "successful operation"}},
            },
        },
        "/pet/findByTags": {
            "get": {
                "tags": ["pet"],
                "summary": "Finds Pets by tags",
                "operationId": "findPetsByTags",
                "parameters": [
                    {
                        "name": "tags",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "array", "items": {"type": "string"}},
                    }
                ],
                "responses": {"200": {"description": "successful operation"}},
            },
        },
        "/pet/{petId}": {
            "get": {
                "tags": ["pet"],
                "summary": "Find pet by ID",
                "operationId": "getPetById",
                "parameters": [
                    {
                        "name": "petId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer", "format": "int64"},
                    }
                ],
                "responses": {"200": {"description": "successful operation"}},
            },
            "post": {
                "tags": ["pet"],
                "summary": "Updates a pet in the store with form data",
                "operationId": "updatePetWithForm",
                "parameters": [
                    {
                        "name": "petId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer", "format": "int64"},
                    },
                    {"name": "name", "in": "query", "schema": {"type": "string"}},
                    {"name": "status", "in": "query", "schema": {"type": "string"}},
                ],
                "responses": {"405": {"description": "Invalid input"}},
            },
            "delete": {
                "tags": ["pet"],
                "summary": "Deletes a pet",
                "operationId": "deletePet",
                "parameters": [
                    {
                        "name": "petId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer", "format": "int64"},
                    }
                ],
                "responses": {"400": {"description": "Invalid pet value"}},
            },
        },
        "/pet/{petId}/uploadImage": {
            "post": {
                "tags": ["pet"],
                "summary": "uploads an image",
                "operationId": "uploadFile",
                "parameters": [
                    {
                        "name": "petId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer", "format": "int64"},
                    }
                ],
                "requestBody": {
                    "content": {
                        "application/octet-stream": {
                            "schema": {"type": "string", "format": "binary"},
                        }
                    },
                },
                "responses": {"200": {"description": "successful operation"}},
            },
        },
        "/store/inventory": {
            "get": {
                "tags": ["store"],
                "summary": "Returns pet inventories by status",
                "operationId": "getInventory",
                "responses": {"200": {"description": "successful operation"}},
            },
        },
        "/store/order": {
            "post": {
                "tags": ["store"],
                "summary": "Place an order for a pet",
                "operationId": "placeOrder",
                "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                "responses": {"200": {"description": "successful operation"}},
            },
        },
        "/store/order/{orderId}": {
            "get": {
                "tags": ["store"],
                "summary": "Find purchase order by ID",
                "operationId": "getOrderById",
                "parameters": [
                    {
                        "name": "orderId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer", "format": "int64"},
                    }
                ],
                "responses": {"200": {"description": "successful operation"}},
            },
            "delete": {
                "tags": ["store"],
                "summary": "Delete purchase order by ID",
                "operationId": "deleteOrder",
                "parameters": [
                    {
                        "name": "orderId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer", "format": "int64"},
                    }
                ],
                "responses": {"400": {"description": "Invalid ID supplied"}},
            },
        },
        "/user": {
            "post": {
                "tags": ["user"],
                "summary": "Create user",
                "operationId": "createUser",
                "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                "responses": {"default": {"description": "successful operation"}},
            },
        },
        "/user/createWithList": {
            "post": {
                "tags": ["user"],
                "summary": "Creates list of users with given input array",
                "operationId": "createUsersWithListInput",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {"type": "array", "items": {"type": "object"}},
                        }
                    },
                },
                "responses": {"200": {"description": "Successful operation"}},
            },
        },
        "/user/login": {
            "get": {
                "tags": ["user"],
                "summary": "Logs user into the system",
                "operationId": "loginUser",
                "parameters": [
                    {"name": "username", "in": "query", "schema": {"type": "string"}},
                    {"name": "password", "in": "query", "schema": {"type": "string"}},
                ],
                "responses": {"200": {"description": "successful operation"}},
            },
        },
        "/user/logout": {
            "get": {
                "tags": ["user"],
                "summary": "Logs out current logged in user session",
                "operationId": "logoutUser",
                "responses": {"default": {"description": "successful operation"}},
            },
        },
        "/user/{username}": {
            "get": {
                "tags": ["user"],
                "summary": "Get user by user name",
                "operationId": "getUserByName",
                "parameters": [
                    {
                        "name": "username",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {"200": {"description": "successful operation"}},
            },
            "put": {
                "tags": ["user"],
                "summary": "Update user",
                "operationId": "updateUser",
                "parameters": [
                    {
                        "name": "username",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                "responses": {"default": {"description": "successful operation"}},
            },
            "delete": {
                "tags": ["user"],
                "summary": "Delete user",
                "operationId": "deleteUser",
                "parameters": [
                    {
                        "name": "username",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {"400": {"description": "Invalid username supplied"}},
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Mock HTTP transport for offline testing
# ---------------------------------------------------------------------------


@dataclass
class _PetStoreState:
    """Mutable state for the PetStore mock transport."""

    call_log: list[tuple[str, str]] = field(default_factory=list)


_SAMPLE_PET: dict[str, Any] = {
    "id": 1,
    "name": "doggie",
    "photoUrls": ["https://example.com/dog.png"],
    "status": "available",
    "category": {"id": 1, "name": "Dogs"},
    "tags": [{"id": 0, "name": "tag0"}],
}

_SAMPLE_ORDER: dict[str, Any] = {
    "id": 10,
    "petId": 198772,
    "quantity": 7,
    "shipDate": "2026-03-27T00:00:00.000Z",
    "status": "approved",
    "complete": True,
}

_SAMPLE_USER: dict[str, Any] = {
    "id": 1,
    "username": "theUser",
    "firstName": "John",
    "lastName": "James",
    "email": "john@example.com",
    "password": "12345",
    "phone": "12345",
    "userStatus": 1,
}


def _handle_petstore_request(
    request: httpx.Request,
    state: _PetStoreState,
) -> httpx.Response:
    """Route mock PetStore request."""
    import json as _json

    path = request.url.path.rstrip("/") or "/"
    method = request.method.upper()
    state.call_log.append((method, path))

    # Spec endpoint
    if path in ("/api/v3/openapi.json", "/openapi.json"):
        return httpx.Response(200, json=_OPENAPI_SPEC)

    # Strip /api/v3 prefix for path matching
    api_path = path
    if api_path.startswith("/api/v3"):
        api_path = api_path[len("/api/v3") :]

    if not api_path:
        api_path = "/"

    # Route by path pattern
    if api_path == "/pet" and method == "PUT":
        return httpx.Response(200, json=_SAMPLE_PET)
    if api_path == "/pet" and method == "POST":
        body = _json.loads(request.content) if request.content else {}
        return httpx.Response(200, json={**_SAMPLE_PET, **body})
    if api_path == "/pet/findByStatus" and method == "GET":
        return httpx.Response(200, json=[_SAMPLE_PET])
    if api_path == "/pet/findByTags" and method == "GET":
        return httpx.Response(200, json=[_SAMPLE_PET])
    if api_path.startswith("/pet/") and api_path.count("/") == 1:
        if method == "GET":
            return httpx.Response(200, json=_SAMPLE_PET)
        if method == "POST":
            return httpx.Response(200, json=_SAMPLE_PET)
        if method == "DELETE":
            return httpx.Response(200, json={})
    if api_path.endswith("/uploadImage") and method == "POST":
        return httpx.Response(200, json={"code": 200, "type": "unknown", "message": "uploaded"})
    if api_path == "/store/inventory" and method == "GET":
        return httpx.Response(200, json={"available": 10, "pending": 2, "sold": 5})
    if api_path == "/store/order" and method == "POST":
        return httpx.Response(200, json=_SAMPLE_ORDER)
    if api_path.startswith("/store/order/"):
        if method == "GET":
            return httpx.Response(200, json=_SAMPLE_ORDER)
        if method == "DELETE":
            return httpx.Response(200, json={})
    if api_path == "/user" and method == "POST":
        return httpx.Response(200, json=_SAMPLE_USER)
    if api_path == "/user/createWithList" and method == "POST":
        return httpx.Response(200, json=_SAMPLE_USER)
    if api_path == "/user/login" and method == "GET":
        return httpx.Response(
            200,
            json="session-token-abc123",
            headers={"X-Rate-Limit": "100", "X-Expires-After": "2026-12-31T00:00:00Z"},
        )
    if api_path == "/user/logout" and method == "GET":
        return httpx.Response(200, json={})
    if api_path.startswith("/user/") and api_path.count("/") == 1:
        if method == "GET":
            return httpx.Response(200, json=_SAMPLE_USER)
        if method == "PUT":
            return httpx.Response(200, json=_SAMPLE_USER)
        if method == "DELETE":
            return httpx.Response(200, json={})

    return httpx.Response(404, json={"error": "Not found"})


def build_petstore_transport() -> httpx.MockTransport:
    """Build a mock transport simulating PetStore v3 responses."""
    state = _PetStoreState()

    def handler(request: httpx.Request) -> httpx.Response:
        return _handle_petstore_request(request, state)

    return httpx.MockTransport(handler)


def get_petstore_spec_json() -> str:
    """Return the inline PetStore OpenAPI spec as a JSON string."""
    import json

    return json.dumps(_OPENAPI_SPEC, indent=2)
