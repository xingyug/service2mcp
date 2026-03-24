"""Tests for IR diff computation."""

from libs.ir.diff import compute_diff
from libs.ir.models import Operation, Param, RiskLevel, RiskMetadata, ServiceIR


def _base_ir(**overrides) -> ServiceIR:
    defaults = {
        "source_hash": "abc123",
        "protocol": "openapi",
        "service_name": "test-api",
        "base_url": "https://api.example.com",
        "operations": [],
    }
    return ServiceIR(**(defaults | overrides))


def _op(id: str, name: str = "", method: str = "GET", **kw) -> Operation:
    defaults = {
        "id": id,
        "name": name or id,
        "method": method,
        "path": f"/{id}",
        "risk": RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
    }
    return Operation(**(defaults | kw),
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
        b = _base_ir(operations=[_op("get_pet"), _op("create_pet", method="POST",
                      risk=RiskMetadata(risk_level=RiskLevel.cautious, confidence=0.9))])
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
        a = _base_ir(operations=[_op("update_pet", method="PUT",
                      risk=RiskMetadata(risk_level=RiskLevel.cautious, confidence=0.9))])
        b = _base_ir(operations=[_op("update_pet", method="PATCH",
                      risk=RiskMetadata(risk_level=RiskLevel.cautious, confidence=0.9))])
        diff = compute_diff(a, b)
        assert len(diff.changed_operations) == 1

    def test_changed_risk_level(self):
        a = _base_ir(operations=[_op("do_thing",
                      risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9))])
        b = _base_ir(operations=[_op("do_thing",
                      risk=RiskMetadata(risk_level=RiskLevel.cautious, confidence=0.9))])
        diff = compute_diff(a, b)
        assert len(diff.changed_operations) == 1
        change_fields = [c[0] if isinstance(c, tuple) else c.field_name
                        for c in diff.changed_operations[0].changes]
        assert "risk.risk_level" in change_fields

    def test_added_param(self):
        a = _base_ir(operations=[_op("get_pet", params=[])])
        b = _base_ir(operations=[_op("get_pet", params=[Param(name="id", type="string", required=True)])])
        diff = compute_diff(a, b)
        assert len(diff.changed_operations) == 1
        assert diff.changed_operations[0].added_params == ["id"]

    def test_removed_param(self):
        a = _base_ir(operations=[_op("get_pet", params=[Param(name="id", type="string", required=True)])])
        b = _base_ir(operations=[_op("get_pet", params=[])])
        diff = compute_diff(a, b)
        assert diff.changed_operations[0].removed_params == ["id"]

    def test_changed_param_type(self):
        a = _base_ir(operations=[_op("get_pet", params=[Param(name="id", type="string")])])
        b = _base_ir(operations=[_op("get_pet", params=[Param(name="id", type="integer")])])
        diff = compute_diff(a, b)
        assert len(diff.changed_operations) == 1
        param_changes = [c for c in diff.changed_operations[0].changes if hasattr(c, "param_name")]
        assert len(param_changes) == 1
        assert param_changes[0].param_name == "id"
        assert param_changes[0].old_value == "string"
        assert param_changes[0].new_value == "integer"


class TestDiffSummary:
    def test_complex_summary(self):
        a = _base_ir(operations=[_op("get_pet"), _op("old_op")])
        b = _base_ir(operations=[_op("get_pet", description="changed"), _op("new_op")])
        diff = compute_diff(a, b)
        summary = diff.summary
        assert "+1 operations" in summary
        assert "-1 operations" in summary
        assert "~1 changed" in summary
