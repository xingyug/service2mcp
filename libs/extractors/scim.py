"""SCIM 2.0 extractor — parses /Schemas + /ServiceProviderConfig.

Generates resource CRUD operations from SCIM schema discovery responses.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from libs.extractors.base import SourceConfig
from libs.ir.models import (
    AuthConfig,
    AuthType,
    ErrorResponse,
    ErrorSchema,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
)

logger = logging.getLogger(__name__)


def _pluralize(name: str) -> str:
    """Return a basic English plural form of *name*."""
    lower = name.lower()
    if lower.endswith(("s", "x", "z", "sh", "ch")):
        return f"{name}es"
    if lower.endswith("y") and len(lower) > 1 and lower[-2] not in "aeiou":
        return f"{name[:-1]}ies"
    return f"{name}s"


SCIM_TYPE_MAP: dict[str, str] = {
    "string": "string",
    "boolean": "boolean",
    "decimal": "number",
    "integer": "integer",
    "dateTime": "string",
    "complex": "object",
    "reference": "string",
}

SCIM_ERROR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schemas": {"type": "array", "items": {"type": "string"}},
        "detail": {"type": "string"},
        "status": {"type": "string"},
        "scimType": {"type": "string"},
    },
}

_STANDARD_SCIM_RESOURCES: tuple[dict[str, Any], ...] = (
    {
        "id": "urn:ietf:params:scim:schemas:core:2.0:User",
        "name": "User",
        "description": "User Account",
        "attributes": [
            {
                "name": "userName",
                "type": "string",
                "mutability": "readWrite",
                "required": True,
                "multiValued": False,
                "description": "Unique identifier for the user",
            },
            {
                "name": "name",
                "type": "complex",
                "mutability": "readWrite",
                "required": False,
                "multiValued": False,
                "description": "Name of the user",
            },
            {
                "name": "emails",
                "type": "complex",
                "mutability": "readWrite",
                "required": False,
                "multiValued": True,
                "description": "Email addresses",
            },
            {
                "name": "active",
                "type": "boolean",
                "mutability": "readWrite",
                "required": False,
                "multiValued": False,
                "description": "Active status",
            },
            {
                "name": "id",
                "type": "string",
                "mutability": "readOnly",
                "required": False,
                "multiValued": False,
                "description": "Unique identifier",
            },
            {
                "name": "meta",
                "type": "complex",
                "mutability": "readOnly",
                "required": False,
                "multiValued": False,
                "description": "Resource metadata",
            },
        ],
    },
    {
        "id": "urn:ietf:params:scim:schemas:core:2.0:Group",
        "name": "Group",
        "description": "Group",
        "attributes": [
            {
                "name": "displayName",
                "type": "string",
                "mutability": "readWrite",
                "required": True,
                "multiValued": False,
                "description": "Display name for the group",
            },
            {
                "name": "members",
                "type": "complex",
                "mutability": "readWrite",
                "required": False,
                "multiValued": True,
                "description": "Group members",
            },
            {
                "name": "id",
                "type": "string",
                "mutability": "readOnly",
                "required": False,
                "multiValued": False,
                "description": "Unique identifier",
            },
            {
                "name": "meta",
                "type": "complex",
                "mutability": "readOnly",
                "required": False,
                "multiValued": False,
                "description": "Resource metadata",
            },
        ],
    },
)


class SCIMExtractor:
    """Extract SCIM 2.0 schema discovery into ServiceIR operations."""

    protocol_name: str = "scim"

    # ── Detection ──────────────────────────────────────────────────────────

    def detect(self, source: SourceConfig) -> float:
        if source.hints.get("protocol") == "scim":
            return 1.0

        url = source.url or ""
        if "/scim/v2" in url or "/scim/" in url:
            return 0.85

        content = self._raw_content(source)
        if content and '"schemas"' in content and "urn:ietf:params:scim:schemas:" in content:
            return 0.9

        return 0.0

    # ── Extraction ─────────────────────────────────────────────────────────

    def extract(self, source: SourceConfig) -> ServiceIR:
        raw = self._raw_content(source)
        if not raw:
            raise ValueError("No content available for extraction")

        data = json.loads(raw)
        source_hash = hashlib.sha256(raw.encode()).hexdigest()

        resources = self._extract_resources(data)
        if not resources:
            resources = self._fallback_resources(source, data)
        spc = self._extract_service_provider_config(data)

        operations: list[Operation] = []
        resource_names: list[str] = []

        for resource in resources:
            name: str = resource.get("name", "")
            if not name:
                logger.warning(
                    "SCIM resource without 'name' field skipped: %s",
                    resource.get("id", "<unknown>"),
                )
                continue
            resource_names.append(name)
            plural = _pluralize(name)
            lower = name.lower()
            attributes: list[dict[str, Any]] = resource.get("attributes", [])

            operations.extend(
                self._build_resource_operations(
                    lower,
                    plural,
                    attributes,
                    spc,
                )
            )

        # Global operations based on service provider config
        if spc.get("changePassword", {}).get("supported"):
            operations.append(self._change_password_op())
        if spc.get("bulk", {}).get("supported"):
            operations.append(self._bulk_op())

        base_url = self._normalize_base_url(source.url)

        return ServiceIR(
            source_url=source.url,
            source_hash=source_hash,
            protocol="scim",
            service_name="SCIM Service",
            service_description="SCIM 2.0 provisioning service",
            base_url=base_url,
            auth=AuthConfig(type=AuthType.none),
            operations=operations,
            metadata={
                "scim_version": "2.0",
                "resource_types": resource_names,
                "service_provider_config": {k: v for k, v in spc.items() if isinstance(v, dict)},
            },
        )

    def _extract_resources(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Support both standard SCIM ListResponse and older wrapped fixtures."""
        resources = data.get("Resources")
        if isinstance(resources, list):
            return [item for item in resources if isinstance(item, dict)]

        schemas_section = data.get("schemas", {})
        if isinstance(schemas_section, dict):
            wrapped_resources = schemas_section.get("Resources", [])
            if isinstance(wrapped_resources, list):
                return [item for item in wrapped_resources if isinstance(item, dict)]

        return []

    def _extract_service_provider_config(self, data: dict[str, Any]) -> dict[str, Any]:
        raw = data.get("service_provider_config", {})
        return raw if isinstance(raw, dict) else {}

    def _fallback_resources(
        self,
        source: SourceConfig,
        data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        url_path = urlparse(source.url or "").path.rstrip("/")
        if url_path.endswith("/Users") or url_path.endswith("/Groups"):
            return [copy.deepcopy(resource) for resource in _STANDARD_SCIM_RESOURCES]

        schemas = data.get("schemas")
        if isinstance(schemas, list) and (
            "urn:ietf:params:scim:api:messages:2.0:ListResponse" in schemas
        ):
            return [copy.deepcopy(resource) for resource in _STANDARD_SCIM_RESOURCES]

        return []

    @staticmethod
    def _normalize_base_url(source_url: str | None) -> str:
        if not source_url:
            return "https://scim.example.com"
        for suffix in ("/Schemas", "/ServiceProviderConfig", "/ResourceTypes", "/Users", "/Groups"):
            if source_url.endswith(suffix):
                return source_url[: -len(suffix)]
        return source_url

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _raw_content(self, source: SourceConfig) -> str:
        if source.file_content:
            return source.file_content
        if source.file_path:
            return Path(source.file_path).read_text(encoding="utf-8")
        if source.url:
            headers: dict[str, str] = {}
            if source.auth_header:
                headers["Authorization"] = source.auth_header
            elif source.auth_token:
                headers["Authorization"] = f"Bearer {source.auth_token}"
            resp = httpx.get(source.url, headers=headers, timeout=30)
            resp.raise_for_status()
            return resp.text
        return ""

    def _map_type(self, attr: dict[str, Any]) -> str:
        scim_type = attr.get("type", "string")
        multi = attr.get("multiValued", False)
        if scim_type == "complex" and multi:
            return "array"
        return SCIM_TYPE_MAP.get(scim_type, "string")

    def _writable_for_create(self, attrs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return attributes included in create operations (exclude readOnly)."""
        return [a for a in attrs if a.get("mutability") not in ("readOnly",)]

    def _writable_for_update(self, attrs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return attributes included in update operations (exclude readOnly and immutable)."""
        return [a for a in attrs if a.get("mutability") not in ("readOnly", "immutable")]

    def _attrs_to_params(self, attrs: list[dict[str, Any]]) -> list[Param]:
        return [
            Param(
                name=a["name"],
                type=self._map_type(a),
                required=a.get("required", False),
                description=a.get("description", ""),
            )
            for a in attrs
        ]

    def _error_schema(self) -> ErrorSchema:
        return ErrorSchema(
            responses=[
                ErrorResponse(status_code=400, description="Bad request"),
                ErrorResponse(status_code=401, description="Unauthorized"),
                ErrorResponse(status_code=404, description="Resource not found"),
                ErrorResponse(status_code=409, description="Conflict"),
            ],
            default_error_schema=SCIM_ERROR_SCHEMA,
        )

    def _build_resource_operations(
        self,
        lower: str,
        plural: str,
        attributes: list[dict[str, Any]],
        spc: dict[str, Any],
    ) -> list[Operation]:
        ops: list[Operation] = []

        # list
        ops.append(
            Operation(
                id=f"list_{lower}s",
                name=f"list_{lower}s",
                description=f"List {plural} with optional filtering",
                method="GET",
                path=f"/{plural}",
                params=[
                    Param(name="filter", type="string", description="SCIM filter expression"),
                    Param(name="startIndex", type="integer", description="1-based start index"),
                    Param(name="count", type="integer", description="Number of results to return"),
                ],
                risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=1.0),
                error_schema=self._error_schema(),
            )
        )

        # get
        ops.append(
            Operation(
                id=f"get_{lower}",
                name=f"get_{lower}",
                description=f"Get a single {lower} by ID",
                method="GET",
                path=f"/{plural}/{{id}}",
                params=[
                    Param(
                        name="id", type="string", required=True, description=f"{lower} identifier"
                    ),
                ],
                risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=1.0),
                error_schema=self._error_schema(),
            )
        )

        # create
        create_attrs = self._writable_for_create(attributes)
        ops.append(
            Operation(
                id=f"create_{lower}",
                name=f"create_{lower}",
                description=f"Create a new {lower}",
                method="POST",
                path=f"/{plural}",
                params=self._attrs_to_params(create_attrs),
                risk=RiskMetadata(
                    risk_level=RiskLevel.cautious,
                    writes_state=True,
                    confidence=1.0,
                ),
                error_schema=self._error_schema(),
            )
        )

        # update
        update_attrs = self._writable_for_update(attributes)
        ops.append(
            Operation(
                id=f"update_{lower}",
                name=f"update_{lower}",
                description=f"Replace an existing {lower}",
                method="PUT",
                path=f"/{plural}/{{id}}",
                params=[
                    Param(
                        name="id", type="string", required=True, description=f"{lower} identifier"
                    ),
                    *self._attrs_to_params(update_attrs),
                ],
                risk=RiskMetadata(
                    risk_level=RiskLevel.cautious,
                    writes_state=True,
                    idempotent=True,
                    confidence=1.0,
                ),
                error_schema=self._error_schema(),
            )
        )

        # patch (only if supported)
        if spc.get("patch", {}).get("supported"):
            ops.append(
                Operation(
                    id=f"patch_{lower}",
                    name=f"patch_{lower}",
                    description=f"Partially update a {lower}",
                    method="PATCH",
                    path=f"/{plural}/{{id}}",
                    params=[
                        Param(
                            name="id",
                            type="string",
                            required=True,
                            description=f"{lower} identifier",
                        ),
                        Param(
                            name="Operations",
                            type="array",
                            required=True,
                            description="SCIM patch operations",
                        ),
                    ],
                    risk=RiskMetadata(
                        risk_level=RiskLevel.cautious,
                        writes_state=True,
                        confidence=1.0,
                    ),
                    error_schema=self._error_schema(),
                )
            )

        # delete
        ops.append(
            Operation(
                id=f"delete_{lower}",
                name=f"delete_{lower}",
                description=f"Delete a {lower}",
                method="DELETE",
                path=f"/{plural}/{{id}}",
                params=[
                    Param(
                        name="id", type="string", required=True, description=f"{lower} identifier"
                    ),
                ],
                risk=RiskMetadata(
                    risk_level=RiskLevel.dangerous,
                    destructive=True,
                    writes_state=True,
                    confidence=1.0,
                ),
                error_schema=self._error_schema(),
            )
        )

        return ops

    def _change_password_op(self) -> Operation:
        return Operation(
            id="change_password",
            name="change_password",
            description="Change the authenticated user's password",
            method="POST",
            path="/Me/ChangePassword",
            params=[
                Param(
                    name="oldPassword",
                    type="string",
                    required=True,
                    description="Current password",
                ),
                Param(
                    name="newPassword",
                    type="string",
                    required=True,
                    description="New password",
                ),
            ],
            risk=RiskMetadata(
                risk_level=RiskLevel.cautious,
                writes_state=True,
                confidence=1.0,
            ),
            error_schema=self._error_schema(),
        )

    def _bulk_op(self) -> Operation:
        return Operation(
            id="bulk_operation",
            name="bulk_operation",
            description="Execute bulk SCIM operations",
            method="POST",
            path="/Bulk",
            params=[
                Param(
                    name="Operations",
                    type="array",
                    required=True,
                    description="List of bulk operations",
                ),
            ],
            risk=RiskMetadata(
                risk_level=RiskLevel.dangerous,
                writes_state=True,
                confidence=1.0,
            ),
            error_schema=self._error_schema(),
        )
