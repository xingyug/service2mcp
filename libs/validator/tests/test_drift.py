"""Tests for drift detection between deployed and live ServiceIR."""

from __future__ import annotations

from libs.extractors.base import SourceConfig
from libs.ir.models import (
    AuthConfig,
    AuthType,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
)
from libs.validator.drift import DriftReport, check_drift_from_source, detect_drift


def _make_op(op_id: str, **kwargs: object) -> Operation:
    defaults: dict[str, object] = {
        "id": op_id,
        "name": op_id,
        "method": "GET",
        "path": f"/{op_id}",
        "risk": RiskMetadata(risk_level=RiskLevel.safe),
    }
    defaults.update(kwargs)
    return Operation(**defaults)


def _make_ir(operations: list[Operation] | None = None, **kwargs: object) -> ServiceIR:
    defaults: dict[str, object] = {
        "source_hash": "abc123",
        "protocol": "openapi",
        "service_name": "test-svc",
        "base_url": "https://api.example.com",
        "operations": operations or [],
    }
    defaults.update(kwargs)
    return ServiceIR(**defaults)


class MockExtractor:
    """Extractor double that returns a pre-built IR."""

    protocol_name: str = "mock"

    def __init__(self, ir: ServiceIR) -> None:
        self._ir = ir

    def detect(self, source: SourceConfig) -> float:
        return 1.0

    def extract(self, source: SourceConfig) -> ServiceIR:
        return self._ir


class TestCheckDriftFromSource:
    def test_check_drift_from_source_no_drift(self) -> None:
        ops = [_make_op("list-users"), _make_op("get-user")]
        deployed = _make_ir(ops)
        live = _make_ir(ops)
        extractor = MockExtractor(live)
        source = SourceConfig(url="https://api.example.com")

        report = check_drift_from_source(deployed, source, extractor)

        assert report.has_drift is False
        assert report.added_operations == []
        assert report.removed_operations == []
        assert report.modified_operations == []

    def test_check_drift_from_source_with_drift(self) -> None:
        deployed = _make_ir([_make_op("list-users")])
        live = _make_ir([_make_op("list-users"), _make_op("create-user")])
        extractor = MockExtractor(live)
        source = SourceConfig(url="https://api.example.com")

        report = check_drift_from_source(deployed, source, extractor)

        assert report.has_drift is True
        assert report.added_operations == ["create-user"]


class TestNoDrift:
    def test_no_drift_identical_irs(self) -> None:
        ops = [_make_op("list-users"), _make_op("get-user")]
        deployed = _make_ir(ops)
        live = _make_ir(ops)

        report = detect_drift(deployed, live)

        assert report.has_drift is False
        assert report.added_operations == []
        assert report.removed_operations == []
        assert report.modified_operations == []
        assert report.schema_changes == []


class TestAddedOperations:
    def test_added_operations_detected(self) -> None:
        deployed = _make_ir([_make_op("list-users")])
        live = _make_ir([_make_op("list-users"), _make_op("create-user")])

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        assert report.added_operations == ["create-user"]
        assert report.removed_operations == []


class TestRemovedOperations:
    def test_removed_operations_detected(self) -> None:
        deployed = _make_ir([_make_op("list-users"), _make_op("delete-user")])
        live = _make_ir([_make_op("list-users")])

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        assert report.removed_operations == ["delete-user"]
        assert report.added_operations == []


class TestModifiedOperations:
    def test_modified_operation_param_added(self) -> None:
        deployed = _make_ir([_make_op("list-users")])
        live = _make_ir([_make_op("list-users", params=[Param(name="limit", type="integer")])])

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        assert len(report.modified_operations) == 1
        detail = report.modified_operations[0]
        assert detail.operation_id == "list-users"
        assert any("param added: limit" in c for c in detail.changes)

    def test_modified_operation_param_type_changed(self) -> None:
        deployed = _make_ir([_make_op("list-users", params=[Param(name="limit", type="string")])])
        live = _make_ir([_make_op("list-users", params=[Param(name="limit", type="integer")])])

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        detail = report.modified_operations[0]
        assert any("type changed" in c and "limit" in c for c in detail.changes)

    def test_modified_operation_risk_level_changed(self) -> None:
        deployed = _make_ir(
            [_make_op("delete-user", risk=RiskMetadata(risk_level=RiskLevel.cautious))]
        )
        live = _make_ir(
            [_make_op("delete-user", risk=RiskMetadata(risk_level=RiskLevel.dangerous))]
        )

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        detail = report.modified_operations[0]
        assert any("risk level changed" in c for c in detail.changes)

    def test_modified_operation_path_changed(self) -> None:
        deployed = _make_ir([_make_op("list-users", path="/v1/users")])
        live = _make_ir([_make_op("list-users", path="/v2/users")])

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        detail = report.modified_operations[0]
        assert any("path changed" in c for c in detail.changes)


class TestSchemaChanges:
    def test_schema_change_base_url(self) -> None:
        deployed = _make_ir(base_url="https://api.v1.example.com")
        live = _make_ir(base_url="https://api.v2.example.com")

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        assert any("base_url changed" in c for c in report.schema_changes)

    def test_schema_change_auth_type(self) -> None:
        deployed = _make_ir(auth=AuthConfig(type=AuthType.bearer))
        live = _make_ir(auth=AuthConfig(type=AuthType.api_key))

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        assert any("auth type changed" in c for c in report.schema_changes)


class TestCombinedDrift:
    def test_combined_drift(self) -> None:
        deployed = _make_ir(
            operations=[
                _make_op("list-users"),
                _make_op("delete-user", risk=RiskMetadata(risk_level=RiskLevel.safe)),
            ],
            base_url="https://old.example.com",
        )
        live = _make_ir(
            operations=[
                _make_op("list-users", params=[Param(name="q", type="string")]),
                _make_op(
                    "delete-user",
                    risk=RiskMetadata(risk_level=RiskLevel.dangerous),
                ),
                _make_op("create-user"),
            ],
            base_url="https://new.example.com",
        )

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        assert report.added_operations == ["create-user"]
        assert report.removed_operations == []
        assert len(report.modified_operations) == 2
        assert any("base_url changed" in c for c in report.schema_changes)


class TestDriftReportRoundTrip:
    def test_drift_report_round_trip(self) -> None:
        ops = [_make_op("list-users")]
        deployed = _make_ir(ops, base_url="https://old.example.com")
        live = _make_ir(ops, base_url="https://new.example.com")

        report = detect_drift(deployed, live)
        data = report.model_dump_json()
        restored = DriftReport.model_validate_json(data)

        assert restored.service_id == report.service_id
        assert restored.has_drift == report.has_drift
        assert restored.schema_changes == report.schema_changes
        assert restored.checked_at == report.checked_at
