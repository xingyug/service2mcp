"""IR transformation pipeline: operator-defined rewrites for ServiceIR."""

from __future__ import annotations

import logging
import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from libs.ir.models import RiskLevel, ServiceIR

logger = logging.getLogger(__name__)


class TransformAction(StrEnum):
    """Available transformation actions."""

    rename_operation = "rename_operation"
    filter_by_tag = "filter_by_tag"
    exclude_by_tag = "exclude_by_tag"
    add_tag = "add_tag"
    remove_tag = "remove_tag"
    override_risk = "override_risk"
    disable_operation = "disable_operation"
    enable_operation = "enable_operation"
    set_metadata = "set_metadata"
    rename_service = "rename_service"


class TransformRule(BaseModel):
    """A single transformation rule to apply to an IR."""

    action: TransformAction
    target: str = Field(
        default="*",
        description="Operation ID pattern (supports * wildcards) or service-level scope",
    )
    value: Any = None

    model_config = ConfigDict(use_enum_values=True)


def apply_transforms(
    ir: ServiceIR,
    rules: list[TransformRule],
) -> ServiceIR:
    """Apply a sequence of transformation rules to a ServiceIR.

    Each rule is applied in order. Rules that target operations use glob-style
    pattern matching on operation IDs (``*`` matches any characters).

    Parameters
    ----------
    ir:
        The ServiceIR to transform.
    rules:
        Ordered list of transformation rules.

    Returns
    -------
    ServiceIR:
        A new ServiceIR with all transformations applied.
    """
    result = ir
    for rule in rules:
        result = _apply_single(result, rule)
    return result


def _matches(pattern: str, value: str) -> bool:
    """Check if a value matches a glob-style pattern (* wildcards)."""
    regex = re.escape(pattern).replace(r"\*", ".*")
    return bool(re.fullmatch(regex, value))


def _apply_single(ir: ServiceIR, rule: TransformRule) -> ServiceIR:
    """Apply a single transformation rule."""
    action = TransformAction(rule.action)

    if action is TransformAction.rename_service:
        return ir.model_copy(update={"service_name": str(rule.value)})

    if action is TransformAction.set_metadata:
        if not isinstance(rule.value, dict):
            logger.warning("set_metadata requires dict value, got %s", type(rule.value))
            return ir
        merged = {**ir.metadata, **rule.value}
        return ir.model_copy(update={"metadata": merged})

    if action is TransformAction.filter_by_tag:
        tag = str(rule.value)
        filtered = [op for op in ir.operations if tag in op.tags]
        return ir.model_copy(update={"operations": filtered})

    if action is TransformAction.exclude_by_tag:
        tag = str(rule.value)
        filtered = [op for op in ir.operations if tag not in op.tags]
        return ir.model_copy(update={"operations": filtered})

    # Per-operation transforms
    updated_ops = []
    for op in ir.operations:
        if _matches(rule.target, op.id):
            op = _transform_operation(op, action, rule.value)
        updated_ops.append(op)

    return ir.model_copy(update={"operations": updated_ops})


def _transform_operation(op: Any, action: TransformAction, value: Any) -> Any:
    """Apply a single action to a matched operation."""
    if action is TransformAction.rename_operation:
        if isinstance(value, dict):
            new_id = value.get("id", op.id)
            new_name = value.get("name", op.name)
        else:
            new_id = str(value)
            new_name = str(value)
        return op.model_copy(update={"id": new_id, "name": new_name})

    if action is TransformAction.add_tag:
        tags = list(op.tags)
        tag = str(value)
        if tag not in tags:
            tags.append(tag)
        return op.model_copy(update={"tags": tags})

    if action is TransformAction.remove_tag:
        tags = [t for t in op.tags if t != str(value)]
        return op.model_copy(update={"tags": tags})

    if action is TransformAction.override_risk:
        risk_level = RiskLevel(str(value))
        new_risk = op.risk.model_copy(update={"risk_level": risk_level})
        return op.model_copy(update={"risk": new_risk})

    if action is TransformAction.disable_operation:
        return op.model_copy(update={"enabled": False})

    if action is TransformAction.enable_operation:
        return op.model_copy(update={"enabled": True})

    return op
