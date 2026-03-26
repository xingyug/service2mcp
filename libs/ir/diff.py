"""Structured diff computation between two ServiceIR instances.

Used by the registry for version comparison and by rollback decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
_OP_COMPARE_FIELDS = ("name", "description", "method", "path", "enabled")
_RISK_COMPARE_FIELDS = (
    "writes_state",
    "destructive",
    "external_side_effect",
    "idempotent",
    "risk_level",
)


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
            diff.changes.append((f, old_val, new_val))

    # Compare risk metadata
    for f in _RISK_COMPARE_FIELDS:
        old_val = getattr(old_op.risk, f)
        new_val = getattr(new_op.risk, f)
        if old_val != new_val:
            diff.changes.append((f"risk.{f}", old_val, new_val))

    # Compare params
    added, removed, param_changes = _diff_params(old_op.params, new_op.params)
    diff.added_params = added
    diff.removed_params = removed
    diff.changes.extend(param_changes)

    if not diff.changes and not diff.added_params and not diff.removed_params:
        return None
    return diff


def compute_diff(old: ServiceIR, new: ServiceIR) -> IRDiff:
    """Compute a structured diff between two ServiceIR instances."""
    old_ops = {op.id: op for op in old.operations}
    new_ops = {op.id: op for op in new.operations}

    result = IRDiff()

    result.added_operations = [id for id in new_ops if id not in old_ops]
    result.removed_operations = [id for id in old_ops if id not in new_ops]

    for op_id in old_ops.keys() & new_ops.keys():
        op_diff = _diff_operations(old_ops[op_id], new_ops[op_id])
        if op_diff is not None:
            result.changed_operations.append(op_diff)

    return result
