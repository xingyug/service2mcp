"""Drift detection — compare a deployed ServiceIR against a fresh extraction."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from libs.ir.models import ServiceIR


class DriftDetail(BaseModel):
    """Details of changes in a single operation."""

    operation_id: str
    changes: list[str]  # human-readable change descriptions


class DriftReport(BaseModel):
    """Report of differences between deployed IR and live source."""

    service_id: str
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    has_drift: bool
    added_operations: list[str] = Field(default_factory=list)
    removed_operations: list[str] = Field(default_factory=list)
    modified_operations: list[DriftDetail] = Field(default_factory=list)
    schema_changes: list[str] = Field(default_factory=list)


def _compare_params(deployed_op: Any, live_op: Any) -> list[str]:
    """Compare parameters between two operations and return change descriptions."""
    changes: list[str] = []

    deployed_params = {p.name: p for p in deployed_op.params}
    live_params = {p.name: p for p in live_op.params}

    for name in sorted(live_params.keys() - deployed_params.keys()):
        changes.append(f"param added: {name}")

    for name in sorted(deployed_params.keys() - live_params.keys()):
        changes.append(f"param removed: {name}")

    for name in sorted(deployed_params.keys() & live_params.keys()):
        d_param = deployed_params[name]
        l_param = live_params[name]
        if d_param.type != l_param.type:
            changes.append(f"param '{name}' type changed: {d_param.type} -> {l_param.type}")

    return changes


def _compare_operation(deployed_op: Any, live_op: Any) -> list[str]:
    """Compare two operations with the same ID and return change descriptions."""
    changes: list[str] = []

    changes.extend(_compare_params(deployed_op, live_op))

    if deployed_op.risk.risk_level != live_op.risk.risk_level:
        changes.append(
            f"risk level changed: {deployed_op.risk.risk_level.value} -> "
            f"{live_op.risk.risk_level.value}"
        )

    if deployed_op.path != live_op.path:
        changes.append(f"path changed: {deployed_op.path} -> {live_op.path}")

    if deployed_op.method != live_op.method:
        changes.append(f"method changed: {deployed_op.method} -> {live_op.method}")

    return changes


def _compare_schema(deployed_ir: ServiceIR, live_ir: ServiceIR) -> list[str]:
    """Compare schema-level attributes between two IRs."""
    changes: list[str] = []

    if deployed_ir.base_url != live_ir.base_url:
        changes.append(f"base_url changed: {deployed_ir.base_url} -> {live_ir.base_url}")

    if deployed_ir.auth.type != live_ir.auth.type:
        changes.append(
            f"auth type changed: {deployed_ir.auth.type.value} -> {live_ir.auth.type.value}"
        )

    if deployed_ir.service_name != live_ir.service_name:
        changes.append(
            f"service_name changed: {deployed_ir.service_name} -> {live_ir.service_name}"
        )

    return changes


def detect_drift(deployed_ir: ServiceIR, live_ir: ServiceIR) -> DriftReport:
    """Compare a deployed ServiceIR against a freshly extracted one.

    Comparison logic:
    - Operation set difference (added/removed by operation ID)
    - Per-operation: param changes (added/removed/type changed),
      risk level changes, path changes
    - Schema-level: auth config changes, base URL changes
    """
    deployed_ops = {op.id: op for op in deployed_ir.operations}
    live_ops = {op.id: op for op in live_ir.operations}

    added = sorted(live_ops.keys() - deployed_ops.keys())
    removed = sorted(deployed_ops.keys() - live_ops.keys())

    modified: list[DriftDetail] = []
    for op_id in sorted(deployed_ops.keys() & live_ops.keys()):
        changes = _compare_operation(deployed_ops[op_id], live_ops[op_id])
        if changes:
            modified.append(DriftDetail(operation_id=op_id, changes=changes))

    schema_changes = _compare_schema(deployed_ir, live_ir)

    has_drift = bool(added or removed or modified or schema_changes)

    return DriftReport(
        service_id=deployed_ir.service_name,
        has_drift=has_drift,
        added_operations=added,
        removed_operations=removed,
        modified_operations=modified,
        schema_changes=schema_changes,
    )
