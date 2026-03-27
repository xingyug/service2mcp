"""Auto-generate MCP prompt definitions from a ServiceIR.

Produces standard prompt templates during the enhance stage:
- explore_{service}: list operations and risk levels
- safe_discovery_{service}: read-only exploration
- manage_{entity}: CRUD prompts for detected entity groups
"""

from __future__ import annotations

from libs.ir.models import (
    PromptArgument,
    PromptDefinition,
    RiskLevel,
    ServiceIR,
    ToolIntent,
)


def generate_prompts(ir: ServiceIR) -> list[PromptDefinition]:
    """Generate standard MCP prompts from a ServiceIR."""
    prompts: list[PromptDefinition] = []
    prompts.append(_explore_prompt(ir))
    prompts.append(_safe_discovery_prompt(ir))
    prompts.extend(_crud_prompts(ir))
    return prompts


def _explore_prompt(ir: ServiceIR) -> PromptDefinition:
    all_op_ids = [op.id for op in ir.operations if op.enabled]
    return PromptDefinition(
        id=f"explore_{ir.service_name}",
        name=f"Explore {ir.service_name}",
        description=(
            f"List available operations for {ir.service_name} "
            "and their risk levels"
        ),
        template=(
            "List available operations for {service_name} "
            "and their risk levels. "
            "Summarize what each tool does before using any."
        ),
        arguments=[
            PromptArgument(
                name="service_name",
                description="Name of the service to explore",
                required=False,
                default=ir.service_name,
            ),
        ],
        tool_ids=all_op_ids,
        tags=["discovery"],
    )


def _safe_discovery_prompt(ir: ServiceIR) -> PromptDefinition:
    safe_ops = [
        op
        for op in ir.operations
        if op.enabled and _is_safe_operation(op)
    ]
    safe_ids = [op.id for op in safe_ops]
    safe_tool_list = ", ".join(safe_ids) if safe_ids else "(none)"
    return PromptDefinition(
        id=f"safe_discovery_{ir.service_name}",
        name=f"Safe discovery {ir.service_name}",
        description=(
            f"Only use discovery (read-only) tools to explore "
            f"{ir.service_name}"
        ),
        template=(
            "Only use discovery (read-only) tools to explore "
            "{service_name}. Available safe tools: "
            + safe_tool_list
        ),
        arguments=[
            PromptArgument(
                name="service_name",
                description="Name of the service to explore safely",
                required=False,
                default=ir.service_name,
            ),
        ],
        tool_ids=safe_ids,
        tags=["discovery", "safe"],
    )


def _is_safe_operation(op: object) -> bool:
    """Check if an operation is read-only / safe."""
    from libs.ir.models import Operation

    if not isinstance(op, Operation):
        return False
    if op.tool_intent == ToolIntent.discovery:
        return True
    if op.risk.risk_level == RiskLevel.safe:
        return True
    if op.method and op.method.upper() == "GET":
        return True
    return False


def _crud_prompts(ir: ServiceIR) -> list[PromptDefinition]:
    """Generate manage_{entity} prompts for CRUD-like operation groups."""
    entity_ops: dict[str, list[str]] = {}
    crud_methods = {"GET", "POST", "PUT", "PATCH", "DELETE"}

    for op in ir.operations:
        if not op.enabled or not op.path or not op.method:
            continue
        if op.method.upper() not in crud_methods:
            continue
        entity = _extract_entity_from_path(op.path)
        if entity:
            entity_ops.setdefault(entity, []).append(op.id)

    prompts: list[PromptDefinition] = []
    for entity, op_ids in entity_ops.items():
        if len(op_ids) < 2:
            continue
        prompts.append(
            PromptDefinition(
                id=f"manage_{entity}",
                name=f"Manage {entity}",
                description=(
                    f"Create, read, update, or delete {entity}"
                ),
                template=(
                    "Create, read, update, or delete {entity}. "
                    "Available tools: "
                    + ", ".join(op_ids)
                ),
                arguments=[
                    PromptArgument(
                        name="entity",
                        description=f"The {entity} entity to manage",
                        required=False,
                        default=entity,
                    ),
                ],
                tool_ids=op_ids,
                tags=["crud", entity],
            )
        )
    return prompts


def _extract_entity_from_path(path: str) -> str | None:
    """Extract the primary entity name from a REST path.

    E.g. /pets/{id} -> pets, /api/v1/users/{user_id}/posts -> posts
    """
    segments = [
        s for s in path.split("/")
        if s and not s.startswith("{") and not s.startswith(":")
    ]
    # Skip common prefixes like api, v1, v2 etc.
    skip = {"api", "v1", "v2", "v3", "v4"}
    meaningful = [s for s in segments if s.lower() not in skip]
    if meaningful:
        return meaningful[-1]
    return None
