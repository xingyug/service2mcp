"""Auto-generate MCP resource definitions from a ServiceIR.

Produces three standard resources during the enhance stage:
- schema: the API schema/metadata summary
- operations: list of available operations
- auth-requirements: authentication configuration
"""

from __future__ import annotations

import json

from libs.ir.models import ResourceDefinition, ServiceIR


def generate_resources(ir: ServiceIR) -> list[ResourceDefinition]:
    """Generate standard MCP resources from a ServiceIR."""
    return [
        _schema_resource(ir),
        _operations_resource(ir),
        _auth_requirements_resource(ir),
    ]


def _service_uri(service_name: str, resource: str) -> str:
    return f"service:///{service_name}/{resource}"


def _schema_resource(ir: ServiceIR) -> ResourceDefinition:
    content = json.dumps(
        {
            "service_name": ir.service_name,
            "protocol": ir.protocol,
            "base_url": ir.base_url,
            "description": ir.service_description,
            "ir_version": ir.ir_version,
            "compiler_version": ir.compiler_version,
            "operation_count": len(ir.operations),
        },
        indent=2,
    )
    return ResourceDefinition(
        id=f"{ir.service_name}-schema",
        name=f"{ir.service_name} schema",
        description=f"API schema summary for {ir.service_name}",
        uri=_service_uri(ir.service_name, "schema"),
        mime_type="application/json",
        content_type="static",
        content=content,
    )


def _operations_resource(ir: ServiceIR) -> ResourceDefinition:
    ops = [
        {
            "id": op.id,
            "name": op.name,
            "description": op.description,
            "method": op.method,
            "path": op.path,
            "risk_level": op.risk.risk_level.value,
            "enabled": op.enabled,
        }
        for op in ir.operations
    ]
    return ResourceDefinition(
        id=f"{ir.service_name}-operations",
        name=f"{ir.service_name} operations",
        description=(
            f"List of available operations for {ir.service_name}"
        ),
        uri=_service_uri(ir.service_name, "operations"),
        mime_type="application/json",
        content_type="static",
        content=json.dumps(ops, indent=2),
    )


def _auth_requirements_resource(ir: ServiceIR) -> ResourceDefinition:
    auth_info: dict[str, str | list[str]] = {
        "type": ir.auth.type.value,
    }
    if ir.auth.oauth2 is not None:
        auth_info["oauth2_token_url"] = ir.auth.oauth2.token_url
        auth_info["oauth2_scopes"] = ir.auth.oauth2.scopes
    if ir.auth.header_name is not None:
        auth_info["header_name"] = ir.auth.header_name
    if ir.auth.api_key_location is not None:
        auth_info["api_key_location"] = ir.auth.api_key_location
    return ResourceDefinition(
        id=f"{ir.service_name}-auth-requirements",
        name=f"{ir.service_name} auth-requirements",
        description=(
            f"Authentication requirements for {ir.service_name}"
        ),
        uri=_service_uri(ir.service_name, "auth-requirements"),
        mime_type="application/json",
        content_type="static",
        content=json.dumps(auth_info, indent=2),
    )
