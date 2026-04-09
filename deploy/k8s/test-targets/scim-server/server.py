import copy
import uuid

from flask import Flask, jsonify, request

app = Flask(__name__)

USERS = [
    {
        "id": "u1",
        "userName": "alice",
        "name": {"familyName": "Smith", "givenName": "Alice"},
        "emails": [{"value": "alice@example.com", "primary": True}],
        "active": True,
    },
    {
        "id": "u2",
        "userName": "bob",
        "name": {"familyName": "Jones", "givenName": "Bob"},
        "emails": [{"value": "bob@example.com", "primary": True}],
        "active": True,
    },
]

GROUPS = [
    {"id": "g1", "displayName": "Engineering", "members": [{"value": "u1", "display": "alice"}]},
    {"id": "g2", "displayName": "Marketing", "members": [{"value": "u2", "display": "bob"}]},
]

USER_SCHEMA = {
    "id": "urn:ietf:params:scim:schemas:core:2.0:User",
    "name": "User",
    "description": "User Account",
    "attributes": [
        {
            "name": "userName",
            "type": "string",
            "multiValued": False,
            "required": True,
            "mutability": "readWrite",
            "returned": "default",
            "uniqueness": "server",
        },
        {
            "name": "name",
            "type": "complex",
            "multiValued": False,
            "required": False,
            "mutability": "readWrite",
            "returned": "default",
            "subAttributes": [
                {
                    "name": "familyName",
                    "type": "string",
                    "multiValued": False,
                    "required": False,
                    "mutability": "readWrite",
                    "returned": "default",
                },
                {
                    "name": "givenName",
                    "type": "string",
                    "multiValued": False,
                    "required": False,
                    "mutability": "readWrite",
                    "returned": "default",
                },
            ],
        },
        {
            "name": "emails",
            "type": "complex",
            "multiValued": True,
            "required": False,
            "mutability": "readWrite",
            "returned": "default",
            "subAttributes": [
                {
                    "name": "value",
                    "type": "string",
                    "multiValued": False,
                    "required": False,
                    "mutability": "readWrite",
                    "returned": "default",
                },
                {
                    "name": "primary",
                    "type": "boolean",
                    "multiValued": False,
                    "required": False,
                    "mutability": "readWrite",
                    "returned": "default",
                },
            ],
        },
        {
            "name": "active",
            "type": "boolean",
            "multiValued": False,
            "required": False,
            "mutability": "readWrite",
            "returned": "default",
        },
        {
            "name": "id",
            "type": "string",
            "multiValued": False,
            "required": False,
            "mutability": "readOnly",
            "returned": "always",
            "uniqueness": "server",
        },
        {
            "name": "meta",
            "type": "complex",
            "multiValued": False,
            "required": False,
            "mutability": "readOnly",
            "returned": "default",
            "subAttributes": [
                {
                    "name": "resourceType",
                    "type": "string",
                    "multiValued": False,
                    "required": False,
                    "mutability": "readOnly",
                    "returned": "default",
                },
                {
                    "name": "location",
                    "type": "string",
                    "multiValued": False,
                    "required": False,
                    "mutability": "readOnly",
                    "returned": "default",
                },
            ],
        },
    ],
    "meta": {
        "resourceType": "Schema",
        "location": "/scim/v2/Schemas/urn:ietf:params:scim:schemas:core:2.0:User",
    },
}

GROUP_SCHEMA = {
    "id": "urn:ietf:params:scim:schemas:core:2.0:Group",
    "name": "Group",
    "description": "Group",
    "attributes": [
        {
            "name": "displayName",
            "type": "string",
            "multiValued": False,
            "required": True,
            "mutability": "readWrite",
            "returned": "default",
        },
        {
            "name": "members",
            "type": "complex",
            "multiValued": True,
            "required": False,
            "mutability": "readWrite",
            "returned": "default",
            "subAttributes": [
                {
                    "name": "value",
                    "type": "string",
                    "multiValued": False,
                    "required": False,
                    "mutability": "readWrite",
                    "returned": "default",
                },
                {
                    "name": "display",
                    "type": "string",
                    "multiValued": False,
                    "required": False,
                    "mutability": "readOnly",
                    "returned": "default",
                },
            ],
        },
        {
            "name": "id",
            "type": "string",
            "multiValued": False,
            "required": False,
            "mutability": "readOnly",
            "returned": "always",
            "uniqueness": "server",
        },
        {
            "name": "meta",
            "type": "complex",
            "multiValued": False,
            "required": False,
            "mutability": "readOnly",
            "returned": "default",
            "subAttributes": [
                {
                    "name": "resourceType",
                    "type": "string",
                    "multiValued": False,
                    "required": False,
                    "mutability": "readOnly",
                    "returned": "default",
                },
                {
                    "name": "location",
                    "type": "string",
                    "multiValued": False,
                    "required": False,
                    "mutability": "readOnly",
                    "returned": "default",
                },
            ],
        },
    ],
    "meta": {
        "resourceType": "Schema",
        "location": "/scim/v2/Schemas/urn:ietf:params:scim:schemas:core:2.0:Group",
    },
}


def _user_resource(u):
    r = copy.deepcopy(u)
    r["schemas"] = ["urn:ietf:params:scim:schemas:core:2.0:User"]
    r["meta"] = {"resourceType": "User", "location": f"/scim/v2/Users/{u['id']}"}
    return r


def _group_resource(g):
    r = copy.deepcopy(g)
    r["schemas"] = ["urn:ietf:params:scim:schemas:core:2.0:Group"]
    r["meta"] = {"resourceType": "Group", "location": f"/scim/v2/Groups/{g['id']}"}
    return r


@app.route("/healthz")
def healthz():
    return "ok", 200


@app.route("/scim/v2/Schemas")
def schemas():
    return jsonify(
        {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": 2,
            "Resources": [USER_SCHEMA, GROUP_SCHEMA],
        }
    )


@app.route("/scim/v2/ServiceProviderConfig")
def service_provider_config():
    return jsonify(
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "patch": {"supported": True},
            "bulk": {"supported": True, "maxOperations": 100, "maxPayloadSize": 1048576},
            "filter": {"supported": True, "maxResults": 200},
            "changePassword": {"supported": False},
            "sort": {"supported": False},
            "etag": {"supported": False},
            "authenticationSchemes": [
                {
                    "type": "httpbasic",
                    "name": "HTTP Basic",
                    "description": "Authentication via HTTP Basic",
                }
            ],
        }
    )


@app.route("/scim/v2/ResourceTypes")
def resource_types():
    return jsonify(
        {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": 2,
            "Resources": [
                {
                    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                    "id": "User",
                    "name": "User",
                    "endpoint": "/scim/v2/Users",
                    "schema": "urn:ietf:params:scim:schemas:core:2.0:User",
                    "meta": {
                        "resourceType": "ResourceType",
                        "location": "/scim/v2/ResourceTypes/User",
                    },
                },
                {
                    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                    "id": "Group",
                    "name": "Group",
                    "endpoint": "/scim/v2/Groups",
                    "schema": "urn:ietf:params:scim:schemas:core:2.0:Group",
                    "meta": {
                        "resourceType": "ResourceType",
                        "location": "/scim/v2/ResourceTypes/Group",
                    },
                },
            ],
        }
    )


# --- Users ---


@app.route("/scim/v2/Users", methods=["GET", "POST"])
def users_collection():
    if request.method == "GET":
        resources = [_user_resource(u) for u in USERS]
        return jsonify(
            {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
                "totalResults": len(resources),
                "Resources": resources,
            }
        )
    body = request.get_json(force=True)
    new_user = {
        "id": str(uuid.uuid4()),
        "userName": body.get("userName", ""),
        "name": body.get("name", {}),
        "emails": body.get("emails", []),
        "active": body.get("active", True),
    }
    USERS.append(new_user)
    return jsonify(_user_resource(new_user)), 201


@app.route("/scim/v2/Users/<user_id>", methods=["GET", "PUT", "PATCH", "DELETE"])
def user_item(user_id):
    user = next((u for u in USERS if u["id"] == user_id), None)
    if user is None:
        return jsonify(
            {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "detail": "User not found",
                "status": 404,
            }
        ), 404

    if request.method == "GET":
        return jsonify(_user_resource(user))

    if request.method == "PUT":
        body = request.get_json(force=True)
        user.update({k: body[k] for k in ("userName", "name", "emails", "active") if k in body})
        return jsonify(_user_resource(user))

    if request.method == "PATCH":
        body = request.get_json(force=True)
        for op in body.get("Operations", []):
            if op.get("op") == "replace":
                user.update(op.get("value", {}))
        return jsonify(_user_resource(user))

    # DELETE
    USERS[:] = [u for u in USERS if u["id"] != user_id]
    return "", 204


# --- Groups ---


@app.route("/scim/v2/Groups", methods=["GET", "POST"])
def groups_collection():
    if request.method == "GET":
        resources = [_group_resource(g) for g in GROUPS]
        return jsonify(
            {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
                "totalResults": len(resources),
                "Resources": resources,
            }
        )
    body = request.get_json(force=True)
    new_group = {
        "id": str(uuid.uuid4()),
        "displayName": body.get("displayName", ""),
        "members": body.get("members", []),
    }
    GROUPS.append(new_group)
    return jsonify(_group_resource(new_group)), 201


@app.route("/scim/v2/Groups/<group_id>", methods=["GET", "PUT", "PATCH", "DELETE"])
def group_item(group_id):
    group = next((g for g in GROUPS if g["id"] == group_id), None)
    if group is None:
        return jsonify(
            {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "detail": "Group not found",
                "status": 404,
            }
        ), 404

    if request.method == "GET":
        return jsonify(_group_resource(group))

    if request.method == "PUT":
        body = request.get_json(force=True)
        group.update({k: body[k] for k in ("displayName", "members") if k in body})
        return jsonify(_group_resource(group))

    if request.method == "PATCH":
        body = request.get_json(force=True)
        for op in body.get("Operations", []):
            if op.get("op") == "replace":
                group.update(op.get("value", {}))
        return jsonify(_group_resource(group))

    # DELETE
    GROUPS[:] = [g for g in GROUPS if g["id"] != group_id]
    return "", 204


if __name__ == "__main__":
    print("SCIM 2.0 mock server listening on :8000")
    app.run(host="0.0.0.0", port=8000)
