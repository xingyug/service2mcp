"""Discovery vs Action tool intent derivation and description bifurcation.

Derives ``ToolIntent`` (discovery / action) from ``RiskMetadata`` and optionally
prepends intent context to tool descriptions so AI agents can distinguish
safe read-only tools from state-mutating ones.

Usage::

    from libs.enhancer.tool_intent import derive_tool_intents, bifurcate_descriptions

    ir = derive_tool_intents(ir)           # tag each op with tool_intent
    ir = bifurcate_descriptions(ir)        # prepend [DISCOVERY] / [ACTION] to descriptions
"""

from __future__ import annotations

import logging

from libs.ir.models import Operation, RiskLevel, ServiceIR, ToolIntent

logger = logging.getLogger(__name__)

# Methods considered read-only for intent derivation.
_DISCOVERY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def derive_tool_intent(operation: Operation) -> ToolIntent:
    """Derive tool intent for a single operation from its risk metadata and method.

    Rules (in priority order):
    1. If risk.writes_state or risk.destructive or risk.external_side_effect → action
    2. If risk_level is dangerous or cautious → action
    3. If method is GET/HEAD/OPTIONS and risk_level is safe → discovery
    4. Otherwise → action (conservative default)
    """
    risk = operation.risk

    # Explicit risk signals
    if risk.writes_state or risk.destructive or risk.external_side_effect:
        return ToolIntent.action

    # Risk level signals
    if risk.risk_level in (RiskLevel.dangerous, RiskLevel.cautious):
        return ToolIntent.action

    # Method-based safe classification
    if operation.method and operation.method.upper() in _DISCOVERY_METHODS:
        if risk.risk_level == RiskLevel.safe:
            return ToolIntent.discovery

    # Conservative default
    return ToolIntent.action


def derive_tool_intents(ir: ServiceIR) -> ServiceIR:
    """Derive and set tool_intent for all operations in the IR.

    Only sets tool_intent on operations where it is currently None,
    preserving any explicit user or LLM overrides.

    Returns a new ServiceIR copy with intents applied.
    """
    new_operations: list[Operation] = []
    changed = False

    for op in ir.operations:
        if op.tool_intent is not None:
            new_operations.append(op)
            continue

        intent = derive_tool_intent(op)
        new_operations.append(op.model_copy(update={"tool_intent": intent}))
        changed = True

    if not changed:
        return ir

    return ir.model_copy(update={"operations": new_operations})


_DISCOVERY_PREFIX = "[DISCOVERY] "
_ACTION_PREFIX = "[ACTION] "


def bifurcate_descriptions(ir: ServiceIR) -> ServiceIR:
    """Prepend intent tags to operation descriptions.

    Adds ``[DISCOVERY]`` or ``[ACTION]`` prefix to descriptions for
    operations that have ``tool_intent`` set and whose descriptions
    don't already carry the prefix.

    Returns a new ServiceIR copy with prefixed descriptions.
    """
    new_operations: list[Operation] = []
    changed = False

    for op in ir.operations:
        if op.tool_intent is None:
            new_operations.append(op)
            continue

        prefix = (
            _DISCOVERY_PREFIX
            if op.tool_intent == ToolIntent.discovery
            else _ACTION_PREFIX
        )

        if op.description.startswith(prefix):
            new_operations.append(op)
            continue

        # Strip any existing prefix before adding the correct one
        desc = op.description
        for p in (_DISCOVERY_PREFIX, _ACTION_PREFIX):
            if desc.startswith(p):
                desc = desc[len(p):]
                break

        new_operations.append(
            op.model_copy(update={"description": f"{prefix}{desc}"})
        )
        changed = True

    if not changed:
        return ir

    return ir.model_copy(update={"operations": new_operations})
