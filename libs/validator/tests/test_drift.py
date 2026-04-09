"""Tests for drift detection between deployed and live ServiceIR."""

from __future__ import annotations

from libs.extractors.base import SourceConfig
from libs.ir.models import (
    AuthConfig,
    AuthType,
    ErrorResponse,
    ErrorSchema,
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    Operation,
    Param,
    PromptArgument,
    PromptDefinition,
    ResourceDefinition,
    ResponseExample,
    RetryConfig,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SlaConfig,
)
from libs.validator.drift import (
    DriftReport,
    DriftSeverity,
    _classify_severity,
    check_drift_from_source,
    detect_drift,
)


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

    def test_modified_operation_param_required_and_default_changed(self) -> None:
        deployed = _make_ir(
            [
                _make_op(
                    "list-users",
                    params=[Param(name="limit", type="integer", required=False, default=10)],
                )
            ]
        )
        live = _make_ir(
            [
                _make_op(
                    "list-users",
                    params=[Param(name="limit", type="integer", required=True, default=None)],
                )
            ]
        )

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        detail = report.modified_operations[0]
        assert any("required changed" in change for change in detail.changes)
        assert any("default changed" in change for change in detail.changes)

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

    def test_modified_operation_enabled_changed(self) -> None:
        deployed = _make_ir([_make_op("list-users", enabled=True)])
        live = _make_ir([_make_op("list-users", enabled=False)])

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        detail = report.modified_operations[0]
        assert any("enabled changed" in c for c in detail.changes)

    def test_modified_operation_response_contract_changed(self) -> None:
        deployed = _make_ir(
            [
                _make_op(
                    "list-users",
                    response_schema={"type": "object", "properties": {"name": {"type": "string"}}},
                    error_schema=ErrorSchema(
                        responses=[ErrorResponse(status_code=400, description="bad request")]
                    ),
                    response_examples=[
                        ResponseExample(name="example", body={"name": "alice"}),
                    ],
                )
            ]
        )
        live = _make_ir(
            [
                _make_op(
                    "list-users",
                    response_schema={"type": "object", "properties": {"email": {"type": "string"}}},
                    error_schema=ErrorSchema(
                        responses=[ErrorResponse(status_code=404, description="not found")]
                    ),
                    response_examples=[
                        ResponseExample(name="example", body={"email": "alice@example.com"}),
                    ],
                )
            ]
        )

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        detail = report.modified_operations[0]
        assert "response schema changed" in detail.changes
        assert "error schema changed" in detail.changes
        assert "response examples changed" in detail.changes


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

    def test_schema_change_auth_config(self) -> None:
        deployed = _make_ir(
            auth=AuthConfig(
                type=AuthType.bearer,
                header_name="Authorization",
                runtime_secret_ref="secret://token-a",
            )
        )
        live = _make_ir(
            auth=AuthConfig(
                type=AuthType.bearer,
                header_name="X-Auth",
                runtime_secret_ref="secret://token-b",
            )
        )

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        assert "auth config changed" in report.schema_changes

    def test_schema_change_resource_definitions(self) -> None:
        deployed = _make_ir(
            resource_definitions=[
                ResourceDefinition(
                    id="schema",
                    name="Schema",
                    uri="service://test/schema",
                    content_type="static",
                    content="v1",
                )
            ]
        )
        live = _make_ir(
            resource_definitions=[
                ResourceDefinition(
                    id="schema",
                    name="Schema",
                    uri="service://test/schema",
                    content_type="static",
                    content="v2",
                )
            ]
        )

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        assert "resource changed: schema" in report.schema_changes

    def test_schema_change_prompt_definitions(self) -> None:
        deployed = _make_ir(
            prompt_definitions=[
                PromptDefinition(
                    id="summarize",
                    name="Summarize",
                    template="v1",
                    arguments=[PromptArgument(name="topic", required=True)],
                )
            ]
        )
        live = _make_ir(
            prompt_definitions=[
                PromptDefinition(
                    id="summarize",
                    name="Summarize",
                    template="v2",
                    arguments=[PromptArgument(name="topic", required=True)],
                )
            ]
        )

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        assert "prompt changed: summarize" in report.schema_changes

    def test_schema_change_event_descriptors(self) -> None:
        deployed = _make_ir(
            operations=[_make_op("list-users")],
            event_descriptors=[
                EventDescriptor(
                    id="user-events",
                    name="user-events",
                    transport=EventTransport.sse,
                    support=EventSupportLevel.supported,
                    operation_id="list-users",
                    channel="/events/users",
                )
            ],
        )
        live = _make_ir(
            operations=[_make_op("list-users")],
            event_descriptors=[
                EventDescriptor(
                    id="user-events",
                    name="user-events",
                    transport=EventTransport.sse,
                    support=EventSupportLevel.unsupported,
                    operation_id="list-users",
                    channel="/events/users",
                )
            ],
        )

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        assert "event descriptor changed: user-events" in report.schema_changes


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

        assert restored.service_name == report.service_name
        assert restored.has_drift == report.has_drift
        assert restored.schema_changes == report.schema_changes
        assert restored.checked_at == report.checked_at


class TestDriftSeverityClassification:
    """Tests for severity classification of drift findings."""

    def test_param_removed_is_breaking(self) -> None:
        assert _classify_severity("param removed: user_id") == DriftSeverity.breaking

    def test_param_type_changed_is_breaking(self) -> None:
        assert _classify_severity("param 'id' type changed: int -> str") == DriftSeverity.breaking

    def test_param_required_changed_is_breaking(self) -> None:
        assert (
            _classify_severity("param 'name' required changed: False -> True")
            == DriftSeverity.breaking
        )

    def test_risk_level_changed_is_breaking(self) -> None:
        assert _classify_severity("risk level changed: safe -> dangerous") == DriftSeverity.breaking

    def test_method_changed_is_breaking(self) -> None:
        assert _classify_severity("method changed: GET -> POST") == DriftSeverity.breaking

    def test_auth_type_changed_is_breaking(self) -> None:
        assert _classify_severity("auth type changed: none -> bearer") == DriftSeverity.breaking

    def test_param_added_is_non_breaking(self) -> None:
        assert _classify_severity("param added: new_field") == DriftSeverity.non_breaking

    def test_path_changed_is_breaking(self) -> None:
        assert _classify_severity("path changed: /old -> /new") == DriftSeverity.breaking

    def test_enabled_changed_is_breaking(self) -> None:
        assert _classify_severity("enabled changed: True -> False") == DriftSeverity.breaking

    def test_response_schema_changed_is_non_breaking(self) -> None:
        assert _classify_severity("response schema changed") == DriftSeverity.non_breaking

    def test_removed_operations_produce_breaking_report(self) -> None:
        deployed = _make_ir([_make_op("get-users"), _make_op("delete-users")])
        live = _make_ir([_make_op("get-users")])
        report = detect_drift(deployed, live)
        assert report.severity == DriftSeverity.breaking
        assert report.removed_operations == ["delete-users"]

    def test_added_operations_produce_non_breaking_report(self) -> None:
        deployed = _make_ir([_make_op("get-users")])
        live = _make_ir([_make_op("get-users"), _make_op("list-users")])
        report = detect_drift(deployed, live)
        assert report.severity == DriftSeverity.non_breaking
        assert report.added_operations == ["list-users"]

    def test_modified_risk_produces_breaking_severity(self) -> None:
        deployed = _make_ir(
            [_make_op("update-user", risk=RiskMetadata(risk_level=RiskLevel.cautious))]
        )
        live = _make_ir(
            [_make_op("update-user", risk=RiskMetadata(risk_level=RiskLevel.dangerous))]
        )
        report = detect_drift(deployed, live)
        assert report.severity == DriftSeverity.breaking
        assert report.modified_operations[0].severity == DriftSeverity.breaking

    def test_no_drift_is_non_breaking(self) -> None:
        ir = _make_ir([_make_op("get-users")])
        report = detect_drift(ir, ir)
        assert report.severity == DriftSeverity.non_breaking
        assert not report.has_drift

    def test_auth_type_change_is_breaking_schema_drift(self) -> None:
        deployed = _make_ir([_make_op("get-users")])
        live = _make_ir(
            [_make_op("get-users")],
            auth=AuthConfig(type=AuthType.bearer, header_name="Authorization"),
        )
        report = detect_drift(deployed, live)
        assert report.severity == DriftSeverity.breaking


class TestSlaDrift:
    """Tests for SLA configuration drift detection."""

    def test_sla_added_is_non_breaking(self) -> None:
        """Adding SLA config to an operation is non-breaking (improvement)."""
        deployed = _make_ir([_make_op("list-users")])
        live = _make_ir([_make_op("list-users", sla=SlaConfig(latency_budget_ms=500))])

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        detail = report.modified_operations[0]
        assert "sla config added" in detail.changes
        assert detail.severity == DriftSeverity.non_breaking

    def test_sla_removed_is_breaking(self) -> None:
        """Removing SLA config is breaking (removed guarantee)."""
        deployed = _make_ir([_make_op("list-users", sla=SlaConfig(latency_budget_ms=500))])
        live = _make_ir([_make_op("list-users")])

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        detail = report.modified_operations[0]
        assert "sla config removed" in detail.changes
        assert detail.severity == DriftSeverity.breaking
        assert report.severity == DriftSeverity.breaking

    def test_sla_budget_tightened_is_non_breaking(self) -> None:
        """Tightening SLA budget (lower latency) is non-breaking (improvement)."""
        deployed = _make_ir([_make_op("list-users", sla=SlaConfig(latency_budget_ms=500))])
        live = _make_ir([_make_op("list-users", sla=SlaConfig(latency_budget_ms=200))])

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        detail = report.modified_operations[0]
        assert any("sla latency budget tightened" in c for c in detail.changes)
        assert detail.severity == DriftSeverity.non_breaking

    def test_sla_budget_relaxed_is_breaking(self) -> None:
        """Relaxing SLA budget (higher latency) is breaking."""
        deployed = _make_ir([_make_op("list-users", sla=SlaConfig(latency_budget_ms=200))])
        live = _make_ir([_make_op("list-users", sla=SlaConfig(latency_budget_ms=500))])

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        detail = report.modified_operations[0]
        assert any("sla latency budget relaxed" in c for c in detail.changes)
        assert detail.severity == DriftSeverity.breaking
        assert report.severity == DriftSeverity.breaking

    def test_sla_timeout_changed(self) -> None:
        """Timeout changes should be detected."""
        deployed = _make_ir([_make_op("list-users", sla=SlaConfig(timeout_ms=3000))])
        live = _make_ir([_make_op("list-users", sla=SlaConfig(timeout_ms=5000))])

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        detail = report.modified_operations[0]
        assert any("sla timeout relaxed" in c for c in detail.changes)
        assert detail.severity == DriftSeverity.breaking

    def test_sla_retry_config_changed(self) -> None:
        """Retry config changes should be detected."""
        deployed = _make_ir(
            [
                _make_op(
                    "list-users",
                    sla=SlaConfig(retry=RetryConfig(max_retries=3, backoff_base_ms=100)),
                )
            ]
        )
        live = _make_ir(
            [
                _make_op(
                    "list-users",
                    sla=SlaConfig(retry=RetryConfig(max_retries=5, backoff_base_ms=200)),
                )
            ]
        )

        report = detect_drift(deployed, live)

        assert report.has_drift is True
        detail = report.modified_operations[0]
        assert "sla retry config changed" in detail.changes

    def test_no_sla_on_either_side_no_drift(self) -> None:
        """If neither side has SLA, no SLA drift."""
        deployed = _make_ir([_make_op("list-users")])
        live = _make_ir([_make_op("list-users")])

        report = detect_drift(deployed, live)

        assert report.has_drift is False
        assert report.modified_operations == []

    def test_sla_budget_set_to_none_reports_removed(self) -> None:
        """Removing latency_budget (set to None) within SLA config should say 'removed'."""
        deployed = _make_ir(
            [_make_op("list-users", sla=SlaConfig(latency_budget_ms=500, timeout_ms=3000))]
        )
        live = _make_ir(
            [_make_op("list-users", sla=SlaConfig(latency_budget_ms=None, timeout_ms=3000))]
        )
        report = detect_drift(deployed, live)
        assert report.has_drift is True
        detail = report.modified_operations[0]
        assert any("removed" in c for c in detail.changes)
        assert not any("tightened" in c for c in detail.changes)

    def test_sla_budget_added_to_existing_sla_reports_added(self) -> None:
        """Adding latency_budget to existing SLA config should say 'added'."""
        deployed = _make_ir(
            [_make_op("list-users", sla=SlaConfig(latency_budget_ms=None, timeout_ms=3000))]
        )
        live = _make_ir(
            [_make_op("list-users", sla=SlaConfig(latency_budget_ms=200, timeout_ms=3000))]
        )
        report = detect_drift(deployed, live)
        assert report.has_drift is True
        detail = report.modified_operations[0]
        assert any("added" in c for c in detail.changes)
