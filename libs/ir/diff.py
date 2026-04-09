"""Structured diff computation between two ServiceIR instances.

Used by the registry for version comparison and by rollback decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel

from libs.ir.models import Operation, Param, ServiceIR


@dataclass
class ParamChange:
    param_name: str
    field_name: str
    old_value: Any
    new_value: Any


@dataclass
class OperationDiff:
    operation_id: str
    operation_name: str
    changes: list[ParamChange | tuple[str, Any, Any]] = field(default_factory=list)
    added_params: list[str] = field(default_factory=list)
    removed_params: list[str] = field(default_factory=list)


@dataclass
class IRDiff:
    added_operations: list[str] = field(default_factory=list)
    removed_operations: list[str] = field(default_factory=list)
    changed_operations: list[OperationDiff] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return (
            not self.added_operations
            and not self.removed_operations
            and not self.changed_operations
        )

    @property
    def summary(self) -> str:
        parts = []
        if self.added_operations:
            parts.append(f"+{len(self.added_operations)} operations")
        if self.removed_operations:
            parts.append(f"-{len(self.removed_operations)} operations")
        if self.changed_operations:
            parts.append(f"~{len(self.changed_operations)} changed")
        return ", ".join(parts) if parts else "no changes"


# Fields on Operation to compare (excluding volatile fields like confidence, source)
_SERVICE_COMPARE_FIELDS = (
    "service_name",
    "service_description",
    "base_url",
    "protocol",
    "auth",
    "operation_chains",
    "tool_grouping",
    "metadata",
    "tenant",
    "environment",
)
_OP_COMPARE_FIELDS = (
    "name",
    "description",
    "method",
    "path",
    "enabled",
    "response_schema",
    "error_schema",
    "response_examples",
    "response_strategy",
    "request_body_mode",
    "body_param_name",
    "async_job",
    "graphql",
    "sql",
    "grpc_unary",
    "soap",
    "jsonrpc",
    "tags",
    "tool_intent",
)
_RISK_COMPARE_FIELDS = (
    "writes_state",
    "destructive",
    "external_side_effect",
    "idempotent",
    "risk_level",
)
_SPECIAL_SERVICE_DIFF_ID = "__service__"
_SPECIAL_RESOURCE_DIFF_ID = "__resource_definitions__"
_SPECIAL_PROMPT_DIFF_ID = "__prompt_definitions__"
_SPECIAL_EVENT_DIFF_ID = "__event_descriptors__"


def _normalize_diff_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_normalize_diff_value(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_diff_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_diff_value(item) for key, item in value.items()}
    return value


def _diff_params(
    old_params: list[Param],
    new_params: list[Param],
) -> tuple[list[str], list[str], list[ParamChange]]:
    """Compare parameter lists, returning added, removed, and changed."""
    old_map = {p.name: p for p in old_params}
    new_map = {p.name: p for p in new_params}

    added = [name for name in new_map if name not in old_map]
    removed = [name for name in old_map if name not in new_map]

    changes: list[ParamChange] = []
    for name in old_map.keys() & new_map.keys():
        old_p, new_p = old_map[name], new_map[name]
        for f in ("type", "required", "description", "default"):
            old_val = getattr(old_p, f)
            new_val = getattr(new_p, f)
            if old_val != new_val:
                changes.append(
                    ParamChange(
                        param_name=name,
                        field_name=f,
                        old_value=old_val,
                        new_value=new_val,
                    )
                )

    return added, removed, changes


def _diff_operations(old_op: Operation, new_op: Operation) -> OperationDiff | None:
    """Compare two operations with the same ID. Returns None if identical."""
    diff = OperationDiff(operation_id=old_op.id, operation_name=new_op.name)

    # Compare top-level fields
    for f in _OP_COMPARE_FIELDS:
        old_val = getattr(old_op, f)
        new_val = getattr(new_op, f)
        if old_val != new_val:
            diff.changes.append((f, _normalize_diff_value(old_val), _normalize_diff_value(new_val)))

    # Compare risk metadata
    for f in _RISK_COMPARE_FIELDS:
        old_val = getattr(old_op.risk, f)
        new_val = getattr(new_op.risk, f)
        if old_val != new_val:
            diff.changes.append(
                (f"risk.{f}", _normalize_diff_value(old_val), _normalize_diff_value(new_val))
            )

    # Compare params
    added, removed, param_changes = _diff_params(old_op.params, new_op.params)
    diff.added_params = added
    diff.removed_params = removed
    diff.changes.extend(param_changes)

    if not diff.changes and not diff.added_params and not diff.removed_params:
        return None
    return diff


def _diff_service_level(old: ServiceIR, new: ServiceIR) -> OperationDiff | None:
    diff = OperationDiff(
        operation_id=_SPECIAL_SERVICE_DIFF_ID,
        operation_name="Service metadata",
    )
    for field_name in _SERVICE_COMPARE_FIELDS:
        old_value = getattr(old, field_name)
        new_value = getattr(new, field_name)
        if old_value != new_value:
            diff.changes.append(
                (
                    field_name,
                    _normalize_diff_value(old_value),
                    _normalize_diff_value(new_value),
                )
            )
    if not diff.changes:
        return None
    return diff


def _diff_capability_surface(
    *,
    operation_id: str,
    operation_name: str,
    field_name: str,
    old_value: Any,
    new_value: Any,
) -> OperationDiff | None:
    normalized_old = _normalize_diff_value(old_value)
    normalized_new = _normalize_diff_value(new_value)
    if normalized_old == normalized_new:
        return None
    return OperationDiff(
        operation_id=operation_id,
        operation_name=operation_name,
        changes=[(field_name, normalized_old, normalized_new)],
    )


def compute_diff(old: ServiceIR, new: ServiceIR) -> IRDiff:
    """Compute a structured diff between two ServiceIR instances."""
    old_ops = {op.id: op for op in old.operations}
    new_ops = {op.id: op for op in new.operations}

    result = IRDiff()

    result.added_operations = [id for id in new_ops if id not in old_ops]
    result.removed_operations = [id for id in old_ops if id not in new_ops]

    service_diff = _diff_service_level(old, new)
    if service_diff is not None:
        result.changed_operations.append(service_diff)

    for capability_diff in (
        _diff_capability_surface(
            operation_id=_SPECIAL_RESOURCE_DIFF_ID,
            operation_name="Resource definitions",
            field_name="resource_definitions",
            old_value=old.resource_definitions,
            new_value=new.resource_definitions,
        ),
        _diff_capability_surface(
            operation_id=_SPECIAL_PROMPT_DIFF_ID,
            operation_name="Prompt definitions",
            field_name="prompt_definitions",
            old_value=old.prompt_definitions,
            new_value=new.prompt_definitions,
        ),
        _diff_capability_surface(
            operation_id=_SPECIAL_EVENT_DIFF_ID,
            operation_name="Event descriptors",
            field_name="event_descriptors",
            old_value=old.event_descriptors,
            new_value=new.event_descriptors,
        ),
    ):
        if capability_diff is not None:
            result.changed_operations.append(capability_diff)

    for op_id in old_ops.keys() & new_ops.keys():
        op_diff = _diff_operations(old_ops[op_id], new_ops[op_id])
        if op_diff is not None:
            result.changed_operations.append(op_diff)

    return result
