"""Tests for LLM-as-a-Judge tool description quality evaluation."""

from __future__ import annotations

import json

from libs.ir.models import (
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)
from libs.validator.llm_judge import (
    JudgeEvaluation,
    LLMJudge,
    ToolQualityScore,
    _clamp,
)


class MockJudgeLLMClient:
    """Mock LLM client for judge evaluation tests."""

    def __init__(self, response: str | None = None, fail: bool = False) -> None:
        self._response = response
        self._fail = fail
        self.calls: list[str] = []

    def complete(self, prompt: str, max_tokens: int = 4096) -> object:
        self.calls.append(prompt)
        if self._fail:
            raise RuntimeError("LLM API error")

        class _Response:
            content = self._response or "[]"

        return _Response()


def _make_service_ir(num_ops: int = 3) -> ServiceIR:
    operations = []
    for i in range(num_ops):
        operations.append(
            Operation(
                id=f"op_{i}",
                name=f"Operation {i}",
                description=f"Performs operation {i} on the service.",
                method="GET",
                path=f"/endpoint_{i}",
                params=[
                    Param(
                        name="id", type="integer", required=True,
                        description="Resource ID", confidence=0.9,
                    ),
                ],
                risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
                source=SourceType.extractor,
                confidence=0.9,
            )
        )
    return ServiceIR(
        source_hash="test_hash",
        protocol="rest",
        service_name="test-service",
        base_url="https://api.example.com",
        operations=operations,
    )


class TestClamp:
    def test_clamp_within_range(self) -> None:
        assert _clamp(0.5) == 0.5

    def test_clamp_below_min(self) -> None:
        assert _clamp(-0.1) == 0.0

    def test_clamp_above_max(self) -> None:
        assert _clamp(1.5) == 1.0

    def test_clamp_at_boundaries(self) -> None:
        assert _clamp(0.0) == 0.0
        assert _clamp(1.0) == 1.0


class TestLLMJudge:
    def test_evaluate_returns_scores(self) -> None:
        ir = _make_service_ir(2)
        mock_response = json.dumps([
            {
                "operation_id": "op_0",
                "accuracy": 0.9,
                "completeness": 0.8,
                "clarity": 0.85,
                "feedback": "Good description.",
            },
            {
                "operation_id": "op_1",
                "accuracy": 0.7,
                "completeness": 0.6,
                "clarity": 0.75,
                "feedback": "Could explain parameters better.",
            },
        ])
        client = MockJudgeLLMClient(response=mock_response)
        judge = LLMJudge(client)

        result = judge.evaluate(ir)

        assert result.tools_evaluated == 2
        assert result.service_name == "test-service"
        assert result.average_accuracy > 0
        assert result.average_completeness > 0
        assert result.average_clarity > 0
        assert result.average_overall > 0
        assert len(result.scores) == 2
        assert result.quality_passed is True

    def test_evaluate_empty_ir(self) -> None:
        ir = _make_service_ir(0)
        client = MockJudgeLLMClient()
        judge = LLMJudge(client)

        result = judge.evaluate(ir)

        assert result.tools_evaluated == 0
        assert result.average_overall == 0.0
        assert len(client.calls) == 0

    def test_evaluate_llm_failure_returns_empty(self) -> None:
        ir = _make_service_ir(2)
        client = MockJudgeLLMClient(fail=True)
        judge = LLMJudge(client)

        result = judge.evaluate(ir)

        assert result.tools_evaluated == 0
        assert len(client.calls) == 1

    def test_evaluate_identifies_low_quality_tools(self) -> None:
        ir = _make_service_ir(2)
        mock_response = json.dumps([
            {
                "operation_id": "op_0",
                "accuracy": 0.3,
                "completeness": 0.2,
                "clarity": 0.3,
                "feedback": "Very poor description.",
            },
            {
                "operation_id": "op_1",
                "accuracy": 0.9,
                "completeness": 0.8,
                "clarity": 0.85,
                "feedback": "Good.",
            },
        ])
        client = MockJudgeLLMClient(response=mock_response)
        judge = LLMJudge(client, low_quality_threshold=0.5)

        result = judge.evaluate(ir)

        assert "op_0" in result.low_quality_tools
        assert "op_1" not in result.low_quality_tools

    def test_evaluate_batches_large_ir(self) -> None:
        ir = _make_service_ir(15)
        # Return scores for all ops
        all_scores = [
            {
                "operation_id": f"op_{i}", "accuracy": 0.8,
                "completeness": 0.7, "clarity": 0.9, "feedback": "ok",
            }
            for i in range(15)
        ]
        # Split into what the 2 batches would need
        client = MockJudgeLLMClient(response=json.dumps(all_scores))
        judge = LLMJudge(client, batch_size=10)

        judge.evaluate(ir)

        # Should have made 2 LLM calls (batches of 10 and 5)
        assert len(client.calls) == 2

    def test_overall_score_is_weighted_average(self) -> None:
        score = ToolQualityScore(
            operation_id="op_1",
            tool_name="test",
            accuracy=1.0,
            completeness=1.0,
            clarity=1.0,
            overall=round(1.0 * 0.35 + 1.0 * 0.35 + 1.0 * 0.30, 3),
            feedback="",
        )
        assert score.overall == 1.0

    def test_quality_passed_threshold(self) -> None:
        passing = JudgeEvaluation(
            service_name="test",
            tools_evaluated=1,
            average_accuracy=0.8,
            average_completeness=0.7,
            average_clarity=0.8,
            average_overall=0.7,
        )
        assert passing.quality_passed is True

        failing = JudgeEvaluation(
            service_name="test",
            tools_evaluated=1,
            average_accuracy=0.4,
            average_completeness=0.3,
            average_clarity=0.4,
            average_overall=0.35,
        )
        assert failing.quality_passed is False

    def test_parse_markdown_fenced_response(self) -> None:
        ir = _make_service_ir(1)
        fenced_response = "```json\n" + json.dumps([{
            "operation_id": "op_0", "accuracy": 0.8,
            "completeness": 0.7, "clarity": 0.9, "feedback": "good",
        }]) + "\n```"
        client = MockJudgeLLMClient(response=fenced_response)
        judge = LLMJudge(client)

        result = judge.evaluate(ir)
        assert result.tools_evaluated == 1

    def test_clamps_out_of_range_scores(self) -> None:
        ir = _make_service_ir(1)
        mock_response = json.dumps([{
            "operation_id": "op_0", "accuracy": 1.5,
            "completeness": -0.3, "clarity": 0.8, "feedback": "clamped",
        }])
        client = MockJudgeLLMClient(response=mock_response)
        judge = LLMJudge(client)

        result = judge.evaluate(ir)
        assert result.tools_evaluated == 1
        assert result.scores[0].accuracy == 1.0
        assert result.scores[0].completeness == 0.0
