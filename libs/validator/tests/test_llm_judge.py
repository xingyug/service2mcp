"""Tests for LLM-as-a-Judge tool description quality evaluation."""

from __future__ import annotations

import json

import pytest

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

    def __init__(
        self,
        response: str | None = None,
        fail: bool = False,
        responses: list[str | Exception] | None = None,
    ) -> None:
        self._response = response
        self._fail = fail
        self._responses = list(responses) if responses is not None else None
        self.calls: list[str] = []

    def complete(self, prompt: str, max_tokens: int = 4096) -> object:
        self.calls.append(prompt)
        if self._responses is not None:
            next_response = self._responses.pop(0)
            if isinstance(next_response, Exception):
                raise next_response

            class _Response:
                content = next_response

            return _Response()

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
                        name="id",
                        type="integer",
                        required=True,
                        description="Resource ID",
                        confidence=0.9,
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
        mock_response = json.dumps(
            [
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
            ]
        )
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

    def test_evaluate_llm_failure_marks_batch_as_failed(self) -> None:
        ir = _make_service_ir(2)
        client = MockJudgeLLMClient(fail=True)
        judge = LLMJudge(client)

        result = judge.evaluate(ir)

        assert result.tools_evaluated == 2
        assert result.average_overall == 0.0
        assert result.quality_passed is False
        assert result.low_quality_tools == ["op_0", "op_1"]
        assert all(score.overall == 0.0 for score in result.scores)
        assert len(client.calls) == 1

    def test_evaluate_identifies_low_quality_tools(self) -> None:
        ir = _make_service_ir(2)
        mock_response = json.dumps(
            [
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
            ]
        )
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
                "operation_id": f"op_{i}",
                "accuracy": 0.8,
                "completeness": 0.7,
                "clarity": 0.9,
                "feedback": "ok",
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

        strict = JudgeEvaluation(
            service_name="test",
            tools_evaluated=1,
            average_accuracy=0.8,
            average_completeness=0.7,
            average_clarity=0.8,
            average_overall=0.7,
            quality_threshold=0.95,
        )
        assert strict.quality_passed is False

    def test_parse_markdown_fenced_response(self) -> None:
        ir = _make_service_ir(1)
        fenced_response = (
            "```json\n"
            + json.dumps(
                [
                    {
                        "operation_id": "op_0",
                        "accuracy": 0.8,
                        "completeness": 0.7,
                        "clarity": 0.9,
                        "feedback": "good",
                    }
                ]
            )
            + "\n```"
        )
        client = MockJudgeLLMClient(response=fenced_response)
        judge = LLMJudge(client)

        result = judge.evaluate(ir)
        assert result.tools_evaluated == 1

    def test_clamps_out_of_range_scores(self) -> None:
        ir = _make_service_ir(1)
        mock_response = json.dumps(
            [
                {
                    "operation_id": "op_0",
                    "accuracy": 1.5,
                    "completeness": -0.3,
                    "clarity": 0.8,
                    "feedback": "clamped",
                }
            ]
        )
        client = MockJudgeLLMClient(response=mock_response)
        judge = LLMJudge(client)

        result = judge.evaluate(ir)
        assert result.tools_evaluated == 1
        assert result.scores[0].accuracy == 1.0
        assert result.scores[0].completeness == 0.0


# ── Additional coverage tests ──────────────────────────────────────────────


class TestParseJudgeResponseEdgeCases:
    """Tests for _parse_judge_response edge cases (lines 204-246)."""

    def _make_judge(self, response: str) -> tuple[LLMJudge, MockJudgeLLMClient]:
        client = MockJudgeLLMClient(response=response)
        judge = LLMJudge(client)
        return judge, client

    def test_markdown_fence_without_closing_backticks(self) -> None:
        """Line 205: opening ``` but no closing ``` should still parse."""
        ir = _make_service_ir(1)
        scores_json = json.dumps(
            [
                {
                    "operation_id": "op_0",
                    "accuracy": 0.8,
                    "completeness": 0.7,
                    "clarity": 0.9,
                    "feedback": "ok",
                }
            ]
        )
        # Fence with opening but NO closing backticks
        fenced = f"```json\n{scores_json}"
        judge, _ = self._make_judge(fenced)

        result = judge.evaluate(ir)
        assert result.tools_evaluated == 1
        assert result.scores[0].accuracy == 0.8

    def test_non_array_json_returns_empty(self) -> None:
        """Non-array JSON marks the whole batch failed instead of dropping it."""
        ir = _make_service_ir(1)
        judge, _ = self._make_judge('{"not": "an array"}')

        result = judge.evaluate(ir)
        assert result.tools_evaluated == 1
        assert result.scores[0].overall == 0.0
        assert "not a JSON array" in result.scores[0].feedback

    def test_non_numeric_score_defaults_to_half(self) -> None:
        """Lines 225-226: non-numeric score values default to 0.5."""
        ir = _make_service_ir(1)
        response = json.dumps(
            [
                {
                    "operation_id": "op_0",
                    "accuracy": "high",
                    "completeness": "medium",
                    "clarity": "low",
                    "feedback": "text scores",
                }
            ]
        )
        judge, _ = self._make_judge(response)

        result = judge.evaluate(ir)
        assert result.tools_evaluated == 1
        assert result.scores[0].accuracy == 0.5
        assert result.scores[0].completeness == 0.5
        assert result.scores[0].clarity == 0.5

    def test_malformed_json_returns_empty(self) -> None:
        """Malformed JSON marks the whole batch failed instead of returning []."""
        ir = _make_service_ir(1)
        judge, _ = self._make_judge("{invalid json content!!}")

        result = judge.evaluate(ir)
        assert result.tools_evaluated == 1
        assert result.scores[0].overall == 0.0
        assert "failed to parse" in result.scores[0].feedback.lower()

    def test_partial_batch_response_marks_missing_operations_failed(self) -> None:
        ir = _make_service_ir(2)
        response = json.dumps(
            [
                {
                    "operation_id": "op_0",
                    "accuracy": 0.8,
                    "completeness": 0.7,
                    "clarity": 0.9,
                    "feedback": "ok",
                }
            ]
        )
        judge, _ = self._make_judge(response)

        result = judge.evaluate(ir)

        assert result.tools_evaluated == 2
        assert result.scores[0].operation_id == "op_0"
        assert result.scores[1].operation_id == "op_1"
        assert result.scores[1].overall == 0.0
        assert "omitted this operation" in result.scores[1].feedback
        assert "op_1" in result.low_quality_tools

    def test_duplicate_operation_ids_are_not_double_counted(self) -> None:
        ir = _make_service_ir(1)
        response = json.dumps(
            [
                {
                    "operation_id": "op_0",
                    "accuracy": 0.8,
                    "completeness": 0.7,
                    "clarity": 0.9,
                    "feedback": "first",
                },
                {
                    "operation_id": "op_0",
                    "accuracy": 0.1,
                    "completeness": 0.1,
                    "clarity": 0.1,
                    "feedback": "duplicate",
                },
            ]
        )
        judge, _ = self._make_judge(response)

        result = judge.evaluate(ir)

        assert result.tools_evaluated == 1
        assert len(result.scores) == 1
        assert result.scores[0].feedback == "first"

    def test_evaluate_uses_configured_threshold_for_quality_passed(self) -> None:
        ir = _make_service_ir(1)
        response = json.dumps(
            [
                {
                    "operation_id": "op_0",
                    "accuracy": 0.7,
                    "completeness": 0.7,
                    "clarity": 0.7,
                    "feedback": "middling",
                }
            ]
        )
        client = MockJudgeLLMClient(response=response)
        judge = LLMJudge(client, low_quality_threshold=0.95)

        result = judge.evaluate(ir)

        assert result.average_overall == 0.7
        assert result.quality_passed is False
        assert result.low_quality_tools == ["op_0"]

    def test_failed_batch_does_not_drop_other_batches_from_denominator(self) -> None:
        ir = _make_service_ir(2)
        client = MockJudgeLLMClient(
            responses=[
                RuntimeError("first batch failed"),
                json.dumps(
                    [
                        {
                            "operation_id": "op_1",
                            "accuracy": 0.9,
                            "completeness": 0.9,
                            "clarity": 0.9,
                            "feedback": "good",
                        }
                    ]
                ),
            ]
        )
        judge = LLMJudge(client, batch_size=1)

        result = judge.evaluate(ir)

        assert result.tools_evaluated == 2
        assert result.scores[0].operation_id == "op_0"
        assert result.scores[0].overall == 0.0
        assert result.scores[1].operation_id == "op_1"
        assert result.scores[1].overall > 0.0
        assert "op_0" in result.low_quality_tools

    def test_batch_size_must_be_positive(self) -> None:
        client = MockJudgeLLMClient()
        with pytest.raises(ValueError, match="greater than zero"):
            LLMJudge(client, batch_size=0)
