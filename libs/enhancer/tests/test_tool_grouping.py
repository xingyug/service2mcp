"""Tests for semantic tool grouping via LLM-ITL intent clustering."""

from __future__ import annotations

import json

from libs.enhancer.tool_grouping import (
    GroupingResult,
    ToolGrouper,
    apply_grouping,
)
from libs.ir.models import (
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
    ToolGroup,
)


class MockGroupingLLMClient:
    """Mock LLM client for tool grouping tests."""

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


def _make_ir() -> ServiceIR:
    """Create a ServiceIR with operations spanning multiple business domains."""
    operations = [
        Operation(
            id="list_users",
            name="List Users",
            description="List all users",
            method="GET",
            path="/api/users",
            risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
            source=SourceType.extractor,
            confidence=0.9,
        ),
        Operation(
            id="get_user",
            name="Get User",
            description="Get user by ID",
            method="GET",
            path="/api/users/{id}",
            params=[Param(name="id", type="string", required=True, confidence=0.9)],
            risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
            source=SourceType.extractor,
            confidence=0.9,
        ),
        Operation(
            id="list_orders",
            name="List Orders",
            description="List all orders",
            method="GET",
            path="/api/orders",
            risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
            source=SourceType.extractor,
            confidence=0.9,
        ),
        Operation(
            id="get_order",
            name="Get Order",
            description="Get order by ID",
            method="GET",
            path="/api/orders/{id}",
            params=[Param(name="id", type="string", required=True, confidence=0.9)],
            risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
            source=SourceType.extractor,
            confidence=0.9,
        ),
    ]
    return ServiceIR(
        source_hash="test_hash",
        protocol="rest",
        service_name="test-service",
        base_url="https://api.example.com",
        operations=operations,
    )


class TestToolGrouper:
    def test_groups_operations_by_intent(self) -> None:
        ir = _make_ir()
        mock_response = json.dumps(
            [
                {
                    "id": "user-management",
                    "label": "User Management",
                    "intent": "CRUD operations for user accounts",
                    "operation_ids": ["list_users", "get_user"],
                    "confidence": 0.85,
                },
                {
                    "id": "order-processing",
                    "label": "Order Processing",
                    "intent": "Order lifecycle management",
                    "operation_ids": ["list_orders", "get_order"],
                    "confidence": 0.80,
                },
            ]
        )
        client = MockGroupingLLMClient(response=mock_response)
        grouper = ToolGrouper(client)

        result = grouper.group(ir)

        assert len(result.groups) == 2
        assert result.ungrouped_operations == []
        assert result.llm_calls == 1
        assert result.groups[0].label == "User Management"
        assert set(result.groups[0].operation_ids) == {"list_users", "get_user"}
        assert result.groups[1].label == "Order Processing"

    def test_empty_ir_returns_empty(self) -> None:
        ir = ServiceIR(
            source_hash="test_hash",
            protocol="rest",
            service_name="test",
            base_url="https://example.com",
        )
        client = MockGroupingLLMClient()
        grouper = ToolGrouper(client)

        result = grouper.group(ir)

        assert len(result.groups) == 0
        assert len(client.calls) == 0

    def test_llm_failure_returns_empty(self) -> None:
        ir = _make_ir()
        client = MockGroupingLLMClient(fail=True)
        grouper = ToolGrouper(client)

        result = grouper.group(ir)

        assert len(result.groups) == 0
        assert result.llm_calls == 1

    def test_filters_unknown_operation_ids(self) -> None:
        ir = _make_ir()
        mock_response = json.dumps(
            [
                {
                    "id": "all-ops",
                    "label": "All Operations",
                    "intent": "Everything",
                    "operation_ids": ["list_users", "nonexistent_op"],
                    "confidence": 0.7,
                },
            ]
        )
        client = MockGroupingLLMClient(response=mock_response)
        grouper = ToolGrouper(client)

        result = grouper.group(ir)

        assert len(result.groups) == 1
        assert result.groups[0].operation_ids == ["list_users"]

    def test_identifies_ungrouped_operations(self) -> None:
        ir = _make_ir()
        mock_response = json.dumps(
            [
                {
                    "id": "user-management",
                    "label": "User Management",
                    "intent": "User ops",
                    "operation_ids": ["list_users", "get_user"],
                    "confidence": 0.8,
                },
            ]
        )
        client = MockGroupingLLMClient(response=mock_response)
        grouper = ToolGrouper(client)

        result = grouper.group(ir)

        assert set(result.ungrouped_operations) == {"list_orders", "get_order"}

    def test_skips_groups_with_no_valid_ops(self) -> None:
        ir = _make_ir()
        mock_response = json.dumps(
            [
                {
                    "id": "phantom",
                    "label": "Phantom Group",
                    "intent": "Nothing valid",
                    "operation_ids": ["fake_op_1", "fake_op_2"],
                    "confidence": 0.5,
                },
            ]
        )
        client = MockGroupingLLMClient(response=mock_response)
        grouper = ToolGrouper(client)

        result = grouper.group(ir)

        assert len(result.groups) == 0

    def test_parse_markdown_fenced_response(self) -> None:
        ir = _make_ir()
        fenced = (
            "```json\n"
            + json.dumps(
                [
                    {
                        "id": "g1",
                        "label": "Group 1",
                        "intent": "test",
                        "operation_ids": ["list_users"],
                        "confidence": 0.7,
                    }
                ]
            )
            + "\n```"
        )
        client = MockGroupingLLMClient(response=fenced)
        grouper = ToolGrouper(client)

        result = grouper.group(ir)
        assert len(result.groups) == 1

    def test_non_array_json_returns_empty(self) -> None:
        """Lines 148-149: valid JSON but not an array → empty groups."""
        ir = _make_ir()
        client = MockGroupingLLMClient(response='{"not": "an array"}')
        grouper = ToolGrouper(client)

        result = grouper.group(ir)

        assert len(result.groups) == 0
        assert result.llm_calls == 1

    def test_non_dict_array_item_skipped(self) -> None:
        """Line 154: array item that is not a dict → skipped."""
        ir = _make_ir()
        response = json.dumps(
            [
                "not a dict",
                {
                    "id": "g1",
                    "label": "Group 1",
                    "intent": "test",
                    "operation_ids": ["list_users"],
                    "confidence": 0.8,
                },
            ]
        )
        client = MockGroupingLLMClient(response=response)
        grouper = ToolGrouper(client)

        result = grouper.group(ir)

        assert len(result.groups) == 1
        assert result.groups[0].id == "g1"

    def test_missing_id_or_label_skipped(self) -> None:
        """Line 158: group item missing id or label → skipped."""
        ir = _make_ir()
        response = json.dumps(
            [
                {"label": "No ID", "operation_ids": ["list_users"], "confidence": 0.8},
                {"id": "no-label", "operation_ids": ["get_user"], "confidence": 0.8},
                {
                    "id": "valid",
                    "label": "Valid",
                    "operation_ids": ["list_orders"],
                    "confidence": 0.8,
                },
            ]
        )
        client = MockGroupingLLMClient(response=response)
        grouper = ToolGrouper(client)

        result = grouper.group(ir)

        assert len(result.groups) == 1
        assert result.groups[0].id == "valid"

    def test_malformed_json_returns_empty(self) -> None:
        """Lines 179-181: malformed JSON from LLM → empty groups."""
        ir = _make_ir()
        client = MockGroupingLLMClient(response="this is not valid json {{{")
        grouper = ToolGrouper(client)

        result = grouper.group(ir)

        assert len(result.groups) == 0
        assert result.llm_calls == 1


class TestApplyGrouping:
    def test_apply_grouping_sets_tool_grouping(self) -> None:
        ir = _make_ir()
        groups = [
            ToolGroup(
                id="user-mgmt",
                label="User Management",
                intent="User ops",
                operation_ids=["list_users", "get_user"],
                source=SourceType.llm,
                confidence=0.85,
            ),
        ]
        result = GroupingResult(groups=groups)

        updated = apply_grouping(ir, result)

        assert len(updated.tool_grouping) == 1
        assert updated.tool_grouping[0].id == "user-mgmt"
        # Original IR unchanged
        assert len(ir.tool_grouping) == 0

    def test_apply_empty_grouping_returns_original(self) -> None:
        ir = _make_ir()
        result = GroupingResult(groups=[])

        updated = apply_grouping(ir, result)

        assert updated is ir  # same object returned
