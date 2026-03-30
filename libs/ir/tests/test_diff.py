"""Tests for IR diff computation."""

from typing import Any

from libs.ir.diff import ParamChange, compute_diff
from libs.ir.models import (
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    Operation,
    Param,
    PromptDefinition,
    ResourceDefinition,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
)


def _base_ir(**overrides: Any) -> ServiceIR:
    defaults: dict[str, Any] = {
        "source_hash": "abc123",
        "protocol": "openapi",
        "service_name": "test-api",
        "base_url": "https://api.example.com",
        "operations": [],
    }
    return ServiceIR(**(defaults | overrides))


def _op(id: str, name: str = "", method: str = "GET", **kw: Any) -> Operation:
    defaults: dict[str, Any] = {
        "id": id,
        "name": name or id,
        "method": method,
        "path": f"/{id}",
        "risk": RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
    }
    return Operation(
        **(defaults | kw),
    )


class TestDiffIdentical:
    def test_empty_irs(self):
        a = _base_ir()
        b = _base_ir()
        diff = compute_diff(a, b)
        assert diff.is_empty
        assert diff.summary == "no changes"

    def test_identical_operations(self):
        ops = [_op("get_pet"), _op("list_pets")]
        a = _base_ir(operations=ops)
        b = _base_ir(operations=ops)
        diff = compute_diff(a, b)
        assert diff.is_empty


class TestDiffAddedRemoved:
    def test_added_operation(self):
        a = _base_ir(operations=[_op("get_pet")])
        b = _base_ir(
            operations=[
                _op("get_pet"),
                _op(
                    "create_pet",
                    method="POST",
                    risk=RiskMetadata(risk_level=RiskLevel.cautious, confidence=0.9),
                ),
            ]
        )
        diff = compute_diff(a, b)
        assert diff.added_operations == ["create_pet"]
        assert diff.removed_operations == []
        assert "+1 operations" in diff.summary

    def test_removed_operation(self):
        a = _base_ir(operations=[_op("get_pet"), _op("list_pets")])
        b = _base_ir(operations=[_op("get_pet")])
        diff = compute_diff(a, b)
        assert diff.removed_operations == ["list_pets"]
        assert diff.added_operations == []

    def test_added_and_removed(self):
        a = _base_ir(operations=[_op("old_op")])
        b = _base_ir(operations=[_op("new_op")])
        diff = compute_diff(a, b)
        assert diff.added_operations == ["new_op"]
        assert diff.removed_operations == ["old_op"]


class TestDiffChanged:
    def test_changed_description(self):
        a = _base_ir(operations=[_op("get_pet", description="Get a pet")])
        b = _base_ir(operations=[_op("get_pet", description="Retrieve a pet by ID")])
        diff = compute_diff(a, b)
        assert len(diff.changed_operations) == 1
        assert diff.changed_operations[0].operation_id == "get_pet"

    def test_changed_method(self):
        a = _base_ir(
            operations=[
                _op(
                    "update_pet",
                    method="PUT",
                    risk=RiskMetadata(risk_level=RiskLevel.cautious, confidence=0.9),
                )
            ]
        )
        b = _base_ir(
            operations=[
                _op(
                    "update_pet",
                    method="PATCH",
                    risk=RiskMetadata(risk_level=RiskLevel.cautious, confidence=0.9),
                )
            ]
        )
        diff = compute_diff(a, b)
        assert len(diff.changed_operations) == 1

    def test_changed_risk_level(self):
        a = _base_ir(
            operations=[
                _op("do_thing", risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9))
            ]
        )
        b = _base_ir(
            operations=[
                _op("do_thing", risk=RiskMetadata(risk_level=RiskLevel.cautious, confidence=0.9))
            ]
        )
        diff = compute_diff(a, b)
        assert len(diff.changed_operations) == 1
        change_fields = [
            c[0] if isinstance(c, tuple) else c.field_name
            for c in diff.changed_operations[0].changes
        ]
        assert "risk.risk_level" in change_fields

    def test_added_param(self):
        a = _base_ir(operations=[_op("get_pet", params=[])])
        b = _base_ir(
            operations=[_op("get_pet", params=[Param(name="id", type="string", required=True)])]
        )
        diff = compute_diff(a, b)
        assert len(diff.changed_operations) == 1
        assert diff.changed_operations[0].added_params == ["id"]

    def test_removed_param(self):
        a = _base_ir(
            operations=[_op("get_pet", params=[Param(name="id", type="string", required=True)])]
        )
        b = _base_ir(operations=[_op("get_pet", params=[])])
        diff = compute_diff(a, b)
        assert diff.changed_operations[0].removed_params == ["id"]

    def test_changed_param_type(self):
        a = _base_ir(operations=[_op("get_pet", params=[Param(name="id", type="string")])])
        b = _base_ir(operations=[_op("get_pet", params=[Param(name="id", type="integer")])])
        diff = compute_diff(a, b)
        assert len(diff.changed_operations) == 1
        param_changes = [
            c for c in diff.changed_operations[0].changes if isinstance(c, ParamChange)
        ]
        assert len(param_changes) == 1
        assert param_changes[0].param_name == "id"
        assert param_changes[0].old_value == "string"
        assert param_changes[0].new_value == "integer"

    def test_changed_param_required(self) -> None:
        a = _base_ir(
            operations=[_op("get_pet", params=[Param(name="id", type="string", required=False)])]
        )
        b = _base_ir(
            operations=[_op("get_pet", params=[Param(name="id", type="string", required=True)])]
        )
        diff = compute_diff(a, b)
        assert len(diff.changed_operations) == 1
        param_changes = [
            c for c in diff.changed_operations[0].changes if isinstance(c, ParamChange)
        ]
        assert len(param_changes) == 1
        assert param_changes[0].field_name == "required"
        assert param_changes[0].old_value is False
        assert param_changes[0].new_value is True

    def test_changed_param_default(self) -> None:
        a = _base_ir(
            operations=[_op("list", params=[Param(name="limit", type="integer", default=10)])]
        )
        b = _base_ir(
            operations=[_op("list", params=[Param(name="limit", type="integer", default=50)])]
        )
        diff = compute_diff(a, b)
        assert len(diff.changed_operations) == 1
        param_changes = [
            c for c in diff.changed_operations[0].changes if isinstance(c, ParamChange)
        ]
        assert len(param_changes) == 1
        assert param_changes[0].field_name == "default"
        assert param_changes[0].old_value == 10
        assert param_changes[0].new_value == 50

    def test_changed_enabled_field(self) -> None:
        a = _base_ir(operations=[_op("get_pet", enabled=True)])
        b = _base_ir(operations=[_op("get_pet", enabled=False)])
        diff = compute_diff(a, b)
        assert len(diff.changed_operations) == 1
        field_changes = [
            c
            for c in diff.changed_operations[0].changes
            if isinstance(c, tuple) and c[0] == "enabled"
        ]
        assert len(field_changes) == 1
        assert field_changes[0] == ("enabled", True, False)

    def test_changed_risk_writes_state(self) -> None:
        a = _base_ir(
            operations=[
                _op(
                    "do_thing",
                    risk=RiskMetadata(
                        risk_level=RiskLevel.safe,
                        confidence=0.9,
                        writes_state=False,
                    ),
                )
            ]
        )
        b = _base_ir(
            operations=[
                _op(
                    "do_thing",
                    risk=RiskMetadata(
                        risk_level=RiskLevel.safe,
                        confidence=0.9,
                        writes_state=True,
                    ),
                )
            ]
        )
        diff = compute_diff(a, b)
        assert len(diff.changed_operations) == 1
        fields = [
            c[0] if isinstance(c, tuple) else c.field_name
            for c in diff.changed_operations[0].changes
        ]
        assert "risk.writes_state" in fields

    def test_changed_risk_destructive(self) -> None:
        a = _base_ir(
            operations=[
                _op(
                    "x",
                    risk=RiskMetadata(
                        risk_level=RiskLevel.safe,
                        confidence=0.9,
                        destructive=False,
                    ),
                )
            ]
        )
        b = _base_ir(
            operations=[
                _op(
                    "x",
                    risk=RiskMetadata(
                        risk_level=RiskLevel.dangerous,
                        confidence=0.9,
                        destructive=True,
                    ),
                )
            ]
        )
        diff = compute_diff(a, b)
        fields = [
            c[0] if isinstance(c, tuple) else c.field_name
            for c in diff.changed_operations[0].changes
        ]
        assert "risk.destructive" in fields
        assert "risk.risk_level" in fields

    def test_changed_risk_idempotent(self) -> None:
        a = _base_ir(
            operations=[
                _op(
                    "x",
                    risk=RiskMetadata(
                        risk_level=RiskLevel.safe,
                        confidence=0.9,
                        idempotent=True,
                    ),
                )
            ]
        )
        b = _base_ir(
            operations=[
                _op(
                    "x",
                    risk=RiskMetadata(
                        risk_level=RiskLevel.safe,
                        confidence=0.9,
                        idempotent=False,
                    ),
                )
            ]
        )
        diff = compute_diff(a, b)
        fields = [
            c[0] if isinstance(c, tuple) else c.field_name
            for c in diff.changed_operations[0].changes
        ]
        assert "risk.idempotent" in fields

    def test_changed_risk_external_side_effect(self) -> None:
        a = _base_ir(
            operations=[
                _op(
                    "x",
                    risk=RiskMetadata(
                        risk_level=RiskLevel.safe,
                        confidence=0.9,
                        external_side_effect=False,
                    ),
                )
            ]
        )
        b = _base_ir(
            operations=[
                _op(
                    "x",
                    risk=RiskMetadata(
                        risk_level=RiskLevel.cautious,
                        confidence=0.9,
                        external_side_effect=True,
                    ),
                )
            ]
        )
        diff = compute_diff(a, b)
        fields = [
            c[0] if isinstance(c, tuple) else c.field_name
            for c in diff.changed_operations[0].changes
        ]
        assert "risk.external_side_effect" in fields


class TestDiffSummary:
    def test_complex_summary(self) -> None:
        a = _base_ir(operations=[_op("get_pet"), _op("old_op")])
        b = _base_ir(
            operations=[
                _op("get_pet", description="changed"),
                _op("new_op"),
            ]
        )
        diff = compute_diff(a, b)
        summary = diff.summary
        assert "+1 operations" in summary
        assert "-1 operations" in summary
        assert "~1 changed" in summary

    def test_changed_only_summary(self) -> None:
        a = _base_ir(operations=[_op("x", description="old")])
        b = _base_ir(operations=[_op("x", description="new")])
        diff = compute_diff(a, b)
        assert diff.summary == "~1 changed"
        assert not diff.added_operations
        assert not diff.removed_operations


class TestDiffExtendedSurfaces:
    def test_top_level_service_changes_are_reported(self) -> None:
        a = _base_ir(base_url="https://old.example.com")
        b = _base_ir(base_url="https://new.example.com")

        diff = compute_diff(a, b)

        service_diff = next(
            operation
            for operation in diff.changed_operations
            if operation.operation_id == "__service__"
        )
        assert (
            "base_url",
            "https://old.example.com",
            "https://new.example.com",
        ) in service_diff.changes

    def test_response_contract_changes_are_reported(self) -> None:
        a = _base_ir(
            operations=[
                _op(
                    "get_user",
                    response_schema={"type": "object", "properties": {"name": {"type": "string"}}},
                )
            ]
        )
        b = _base_ir(
            operations=[
                _op(
                    "get_user",
                    response_schema={"type": "object", "properties": {"email": {"type": "string"}}},
                )
            ]
        )

        diff = compute_diff(a, b)

        op_diff = next(
            operation
            for operation in diff.changed_operations
            if operation.operation_id == "get_user"
        )
        assert any(
            isinstance(change, tuple) and change[0] == "response_schema"
            for change in op_diff.changes
        )

    def test_request_execution_contract_changes_are_reported(self) -> None:
        a = _base_ir(
            operations=[
                _op(
                    "submit",
                    method="POST",
                    request_body_mode="json",
                    body_param_name="body",
                )
            ]
        )
        b = _base_ir(
            operations=[
                _op(
                    "submit",
                    method="POST",
                    request_body_mode="multipart",
                    body_param_name="payload",
                )
            ]
        )

        diff = compute_diff(a, b)

        op_diff = next(
            operation for operation in diff.changed_operations if operation.operation_id == "submit"
        )
        fields = {change[0] for change in op_diff.changes if isinstance(change, tuple)}
        assert {"request_body_mode", "body_param_name"} <= fields

    def test_resource_prompt_and_event_changes_are_reported(self) -> None:
        a = _base_ir(
            resource_definitions=[
                ResourceDefinition(
                    id="catalog",
                    name="catalog",
                    uri="service://catalog",
                    content_type="static",
                    content="old",
                )
            ],
            prompt_definitions=[
                PromptDefinition(
                    id="summarize",
                    name="summarize",
                    template="Old template",
                )
            ],
            event_descriptors=[
                EventDescriptor(
                    id="inventory",
                    name="inventory",
                    transport=EventTransport.sse,
                    support=EventSupportLevel.unsupported,
                )
            ],
        )
        b = _base_ir(
            resource_definitions=[
                ResourceDefinition(
                    id="catalog",
                    name="catalog",
                    uri="service://catalog",
                    content_type="static",
                    content="new",
                )
            ],
            prompt_definitions=[
                PromptDefinition(
                    id="summarize",
                    name="summarize",
                    template="New template",
                )
            ],
            event_descriptors=[
                EventDescriptor(
                    id="inventory",
                    name="inventory",
                    transport=EventTransport.sse,
                    support=EventSupportLevel.supported,
                )
            ],
        )

        diff = compute_diff(a, b)
        operation_ids = {operation.operation_id for operation in diff.changed_operations}

        assert "__resource_definitions__" in operation_ids
        assert "__prompt_definitions__" in operation_ids
        assert "__event_descriptors__" in operation_ids
