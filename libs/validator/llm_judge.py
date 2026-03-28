"""LLM-as-a-Judge evaluation pipeline for tool description quality.

Uses an LLM to evaluate generated MCP tool descriptions for accuracy,
completeness, and clarity.  Each tool receives per-dimension scores and
an overall quality score.  Results can feed back into IR confidence fields.

Activation: pass an ``LLMClient`` to ``LLMJudge`` and call ``evaluate()``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from libs.ir.models import Operation, ServiceIR

logger = logging.getLogger(__name__)


class JudgeLLMClient(Protocol):
    """Minimal LLM interface for judge evaluation — matches enhancer.LLMClient."""

    def complete(self, prompt: str, max_tokens: int = 4096) -> Any: ...


@dataclass(frozen=True)
class ToolQualityScore:
    """Quality evaluation for a single tool's description."""

    operation_id: str
    tool_name: str
    accuracy: float  # 0.0-1.0: factual correctness of description
    completeness: float  # 0.0-1.0: covers all important aspects
    clarity: float  # 0.0-1.0: clear, unambiguous, actionable
    overall: float  # 0.0-1.0: weighted composite
    feedback: str  # brief improvement suggestion


@dataclass
class JudgeEvaluation:
    """Aggregate evaluation result for all tools in a service."""

    service_name: str
    tools_evaluated: int
    average_accuracy: float
    average_completeness: float
    average_clarity: float
    average_overall: float
    scores: list[ToolQualityScore] = field(default_factory=list)
    low_quality_tools: list[str] = field(default_factory=list)

    @property
    def quality_passed(self) -> bool:
        """True if average overall quality is above 0.6 threshold."""
        return self.average_overall >= 0.6


JUDGE_PROMPT_TEMPLATE = """\
You are an expert evaluator of API tool descriptions for AI agents. \
Rate the quality of each tool description on three dimensions.

Evaluation criteria:
1. **Accuracy** (0.0-1.0): Is the description factually correct? Does it accurately \
   describe what the API endpoint does based on its name, method, and path?
2. **Completeness** (0.0-1.0): Does the description cover all important aspects? \
   Are parameters explained? Are side effects mentioned?
3. **Clarity** (0.0-1.0): Is the description clear, unambiguous, and actionable \
   for an AI agent deciding which tool to call?

Service: {service_name} ({protocol})
Base URL: {base_url}

Tools to evaluate:
{tools_json}

Return ONLY valid JSON array. No markdown, no explanation:
[
  {{
    "operation_id": "<id>",
    "accuracy": 0.8,
    "completeness": 0.7,
    "clarity": 0.9,
    "feedback": "Brief suggestion for improvement"
  }}
]
"""


class LLMJudge:
    """Evaluates tool description quality using an LLM-as-a-Judge approach."""

    def __init__(
        self,
        client: JudgeLLMClient,
        *,
        batch_size: int = 10,
        low_quality_threshold: float = 0.5,
    ) -> None:
        self._client = client
        self._batch_size = batch_size
        self._low_quality_threshold = low_quality_threshold

    def evaluate(self, ir: ServiceIR) -> JudgeEvaluation:
        """Evaluate all enabled operations in the ServiceIR.

        Returns a JudgeEvaluation with per-tool scores and aggregates.
        On LLM failure, returns an evaluation with zero scores.
        """
        enabled_ops = [op for op in ir.operations if op.enabled]
        if not enabled_ops:
            return JudgeEvaluation(
                service_name=ir.service_name,
                tools_evaluated=0,
                average_accuracy=0.0,
                average_completeness=0.0,
                average_clarity=0.0,
                average_overall=0.0,
            )

        all_scores: list[ToolQualityScore] = []
        for batch in self._batch_operations(enabled_ops):
            try:
                batch_scores = self._evaluate_batch(ir, batch)
                all_scores.extend(batch_scores)
            except Exception:
                logger.warning("LLM judge evaluation failed for batch", exc_info=True)

        if not all_scores:
            return JudgeEvaluation(
                service_name=ir.service_name,
                tools_evaluated=0,
                average_accuracy=0.0,
                average_completeness=0.0,
                average_clarity=0.0,
                average_overall=0.0,
            )

        avg_accuracy = sum(s.accuracy for s in all_scores) / len(all_scores)
        avg_completeness = sum(s.completeness for s in all_scores) / len(all_scores)
        avg_clarity = sum(s.clarity for s in all_scores) / len(all_scores)
        avg_overall = sum(s.overall for s in all_scores) / len(all_scores)

        low_quality = [
            s.operation_id
            for s in all_scores
            if s.overall < self._low_quality_threshold
        ]

        return JudgeEvaluation(
            service_name=ir.service_name,
            tools_evaluated=len(all_scores),
            average_accuracy=avg_accuracy,
            average_completeness=avg_completeness,
            average_clarity=avg_clarity,
            average_overall=avg_overall,
            scores=all_scores,
            low_quality_tools=low_quality,
        )

    def _batch_operations(self, operations: list[Operation]) -> list[list[Operation]]:
        return [
            operations[i : i + self._batch_size]
            for i in range(0, len(operations), self._batch_size)
        ]

    def _evaluate_batch(
        self, ir: ServiceIR, batch: list[Operation]
    ) -> list[ToolQualityScore]:
        tools_json = json.dumps(
            [
                {
                    "operation_id": op.id,
                    "name": op.name,
                    "description": op.description,
                    "method": op.method,
                    "path": op.path,
                    "params": [
                        {"name": p.name, "type": p.type, "description": p.description}
                        for p in op.params
                    ],
                    "risk_level": op.risk.risk_level.value if op.risk else "unknown",
                }
                for op in batch
            ],
            indent=2,
        )

        prompt = JUDGE_PROMPT_TEMPLATE.format(
            service_name=ir.service_name,
            protocol=ir.protocol,
            base_url=ir.base_url,
            tools_json=tools_json,
        )

        response = self._client.complete(prompt, max_tokens=4096)
        content = response.content if hasattr(response, "content") else str(response)
        return self._parse_judge_response(content, batch)

    def _parse_judge_response(
        self, content: str, batch: list[Operation]
    ) -> list[ToolQualityScore]:
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
                logger.warning("LLM judge response is not a JSON array")
                return []

            op_map = {op.id: op for op in batch}
            scores: list[ToolQualityScore] = []

            for item in data:
                op_id = item.get("operation_id", "")
                if op_id not in op_map:
                    continue

                try:
                    accuracy = _clamp(float(item.get("accuracy", 0.5)))
                    completeness = _clamp(float(item.get("completeness", 0.5)))
                    clarity = _clamp(float(item.get("clarity", 0.5)))
                except (ValueError, TypeError):
                    accuracy = completeness = clarity = 0.5
                overall = (accuracy * 0.35 + completeness * 0.35 + clarity * 0.30)
                feedback = item.get("feedback", "")

                scores.append(
                    ToolQualityScore(
                        operation_id=op_id,
                        tool_name=op_map[op_id].name,
                        accuracy=accuracy,
                        completeness=completeness,
                        clarity=clarity,
                        overall=round(overall, 3),
                        feedback=feedback,
                    )
                )

            return scores

        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.warning("Failed to parse LLM judge response", exc_info=True)
            return []


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))
