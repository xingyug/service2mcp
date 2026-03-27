"""Semantic tool grouping via LLM-ITL intent clustering.

Clusters related API operations into business-intent groups using an LLM.
Each group represents a coherent set of operations serving a single business
purpose (e.g. "User Management", "Order Processing").

Activation: instantiate ``ToolGrouper`` with an ``LLMClient`` and call ``group()``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from libs.ir.models import Operation, ServiceIR, SourceType, ToolGroup

logger = logging.getLogger(__name__)


class GroupingLLMClient(Protocol):
    """Minimal LLM interface for tool grouping — matches enhancer.LLMClient."""

    def complete(self, prompt: str, max_tokens: int = 4096) -> Any: ...


@dataclass
class GroupingResult:
    """Result of a tool grouping operation."""

    groups: list[ToolGroup]
    ungrouped_operations: list[str] = field(default_factory=list)
    llm_calls: int = 0


GROUPING_PROMPT_TEMPLATE = """\
You are an API organization expert. Given the following API operations, \
cluster them into logical business-intent groups. Each group should represent \
a coherent set of operations that serve a single business purpose.

Rules:
1. Every operation MUST be assigned to exactly one group.
2. Group by business intent (e.g. "User Management", "Order Processing"), \
   not by HTTP method or technical pattern.
3. Use clear, concise group labels (2-4 words).
4. Include a brief intent description for each group.
5. Return ONLY valid JSON. No markdown, no explanation.
6. Each group should have a confidence score (0.0-1.0).

Service: {service_name} ({protocol})

Operations:
{operations_json}

Return JSON array:
[
  {{
    "id": "user-management",
    "label": "User Management",
    "intent": "CRUD operations for user accounts and profiles",
    "operation_ids": ["list_users", "get_user", "create_user"],
    "confidence": 0.85
  }}
]
"""


class ToolGrouper:
    """Groups operations into business-intent clusters using an LLM."""

    def __init__(self, client: GroupingLLMClient) -> None:
        self._client = client

    def group(self, ir: ServiceIR) -> GroupingResult:
        """Cluster all enabled operations in the ServiceIR into groups.

        Returns a GroupingResult with ToolGroup instances ready to be
        assigned to ``ServiceIR.tool_grouping``.
        """
        enabled_ops = [op for op in ir.operations if op.enabled]
        if not enabled_ops:
            return GroupingResult(groups=[], llm_calls=0)

        operations_json = json.dumps(
            [
                {
                    "operation_id": op.id,
                    "name": op.name,
                    "description": op.description,
                    "method": op.method,
                    "path": op.path,
                    "risk_level": op.risk.risk_level.value if op.risk else "unknown",
                    "tags": op.tags,
                }
                for op in enabled_ops
            ],
            indent=2,
        )

        prompt = GROUPING_PROMPT_TEMPLATE.format(
            service_name=ir.service_name,
            protocol=ir.protocol,
            operations_json=operations_json,
        )

        try:
            response = self._client.complete(prompt, max_tokens=4096)
            content = response.content if hasattr(response, "content") else str(response)
        except Exception:
            logger.warning("LLM tool grouping call failed", exc_info=True)
            return GroupingResult(groups=[], llm_calls=1)

        groups = self._parse_grouping_response(content, enabled_ops)

        # Identify ungrouped operations
        grouped_ids: set[str] = set()
        for g in groups:
            grouped_ids.update(g.operation_ids)
        ungrouped = [op.id for op in enabled_ops if op.id not in grouped_ids]

        return GroupingResult(
            groups=groups,
            ungrouped_operations=ungrouped,
            llm_calls=1,
        )

    def _parse_grouping_response(
        self,
        content: str,
        operations: list[Operation],
    ) -> list[ToolGroup]:
        """Parse LLM response into ToolGroup instances."""
        valid_ids = {op.id for op in operations}

        try:
            text = content.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                if lines[-1].strip() == "```":
                    text = "\n".join(lines[1:-1])
                else:
                    text = "\n".join(lines[1:])
                text = text.strip()

            data = json.loads(text)
            if not isinstance(data, list):
                logger.warning("LLM grouping response is not a JSON array")
                return []

            groups: list[ToolGroup] = []
            for item in data:
                group_id = item.get("id", "")
                label = item.get("label", "")
                if not group_id or not label:
                    continue

                # Only include operation IDs that actually exist
                op_ids = [
                    oid for oid in item.get("operation_ids", [])
                    if oid in valid_ids
                ]

                if not op_ids:
                    continue

                groups.append(
                    ToolGroup(
                        id=group_id,
                        label=label,
                        intent=item.get("intent", ""),
                        operation_ids=op_ids,
                        source=SourceType.llm,
                        confidence=max(0.0, min(1.0, float(item.get("confidence", 0.5)))),
                    )
                )

            return groups

        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.warning("Failed to parse LLM grouping response", exc_info=True)
            return []


def apply_grouping(ir: ServiceIR, result: GroupingResult) -> ServiceIR:
    """Apply grouping result to a ServiceIR, returning a new copy with tool_grouping set."""
    if not result.groups:
        return ir
    return ir.model_copy(update={"tool_grouping": result.groups})
