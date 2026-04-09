"""Unit tests for the AsyncAPI extractor."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from libs.extractors.asyncapi import AsyncAPIExtractor, _json_schema_for_param, _params_from_payload
from libs.extractors.base import SourceConfig
from libs.ir.models import (
    ErrorSchema,
    EventDirection,
    EventSupportLevel,
    EventTransport,
    RiskLevel,
    SourceType,
)

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "asyncapi_specs"


@pytest.fixture()
def extractor() -> AsyncAPIExtractor:
    return AsyncAPIExtractor()


@pytest.fixture()
def v2_source() -> SourceConfig:
    return SourceConfig(file_path=str(FIXTURES / "simple_v2.yaml"))


@pytest.fixture()
def v3_source() -> SourceConfig:
    return SourceConfig(file_path=str(FIXTURES / "simple_v3.yaml"))


@pytest.fixture()
def mqtt_source() -> SourceConfig:
    return SourceConfig(file_path=str(FIXTURES / "mqtt_events.yaml"))


# ── Detection tests ───────────────────────────────────────────────────────


class TestDetection:
    def test_detect_with_protocol_hint(self, extractor: AsyncAPIExtractor) -> None:
        source = SourceConfig(
            file_content="asyncapi: '2.6.0'\ninfo:\n  title: X\n  version: '1'",
            hints={"protocol": "asyncapi"},
        )
        assert extractor.detect(source) == 0.95

    def test_detect_asyncapi_v2_content(
        self, extractor: AsyncAPIExtractor, v2_source: SourceConfig
    ) -> None:
        confidence = extractor.detect(v2_source)
        assert confidence >= 0.88

    def test_detect_asyncapi_v3_content(
        self, extractor: AsyncAPIExtractor, v3_source: SourceConfig
    ) -> None:
        confidence = extractor.detect(v3_source)
        assert confidence >= 0.88

    def test_detect_asyncapi_yaml_extension(self, extractor: AsyncAPIExtractor) -> None:
        source = SourceConfig(file_path="/specs/events.asyncapi.yaml")
        confidence = extractor.detect(source)
        assert confidence > 0

    def test_detect_asyncapi_json_extension(self, extractor: AsyncAPIExtractor) -> None:
        source = SourceConfig(file_path="/specs/events.asyncapi.json")
        assert extractor.detect(source) == 0.88

    def test_detect_asyncapi_yml_extension(self, extractor: AsyncAPIExtractor) -> None:
        source = SourceConfig(file_path="/specs/events.asyncapi.yml")
        assert extractor.detect(source) == 0.88

    def test_detect_returns_zero_for_openapi(self, extractor: AsyncAPIExtractor) -> None:
        source = SourceConfig(file_content="openapi: '3.0.0'\ninfo:\n  title: API\n  version: '1'")
        assert extractor.detect(source) == 0.0

    def test_detect_returns_zero_for_random_yaml(self, extractor: AsyncAPIExtractor) -> None:
        source = SourceConfig(file_content="name: some-app\nversion: 1.0\nfoo: bar")
        assert extractor.detect(source) == 0.0

    def test_detect_returns_zero_for_url_only(self, extractor: AsyncAPIExtractor) -> None:
        source = SourceConfig(url="https://example.com/not-real")
        assert extractor.detect(source) == 0.0


# ── Extraction v2 tests ──────────────────────────────────────────────────


class TestExtractV2:
    def test_extract_v2_service_name(
        self, extractor: AsyncAPIExtractor, v2_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v2_source)
        assert ir.service_name == "user-events"

    def test_extract_v2_protocol(
        self, extractor: AsyncAPIExtractor, v2_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v2_source)
        assert ir.protocol == "asyncapi"

    def test_extract_v2_operations_count(
        self, extractor: AsyncAPIExtractor, v2_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v2_source)
        # 2 subscribe (user/created, user/deleted) + 1 publish (user/created)
        assert len(ir.operations) >= 3

    def test_extract_v2_observe_operation(
        self, extractor: AsyncAPIExtractor, v2_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v2_source)
        observe_names = [op.name for op in ir.operations if op.name.startswith("observe_")]
        assert "observe_user-created" in observe_names
        assert "observe_user-deleted" in observe_names

    def test_extract_v2_publish_operation(
        self, extractor: AsyncAPIExtractor, v2_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v2_source)
        publish_names = [op.name for op in ir.operations if op.name.startswith("publish_")]
        assert "publish_user-created" in publish_names

    def test_extract_v2_observe_risk_is_safe(
        self, extractor: AsyncAPIExtractor, v2_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v2_source)
        observe_ops = [op for op in ir.operations if op.name.startswith("observe_")]
        assert len(observe_ops) > 0
        for op in observe_ops:
            assert op.risk.risk_level == RiskLevel.safe
            assert op.risk.writes_state is False

    def test_extract_v2_publish_risk_is_cautious(
        self, extractor: AsyncAPIExtractor, v2_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v2_source)
        publish_ops = [op for op in ir.operations if op.name.startswith("publish_")]
        assert len(publish_ops) > 0
        for op in publish_ops:
            assert op.risk.risk_level == RiskLevel.cautious
            assert op.risk.writes_state is True

    def test_extract_v2_event_descriptors(
        self, extractor: AsyncAPIExtractor, v2_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v2_source)
        assert len(ir.event_descriptors) == 2
        channels = {ed.channel for ed in ir.event_descriptors}
        assert "user/created" in channels
        assert "user/deleted" in channels

    def test_extract_v2_event_descriptor_directions(
        self, extractor: AsyncAPIExtractor, v2_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v2_source)
        by_channel = {ed.channel: ed for ed in ir.event_descriptors}
        # user/created has both subscribe and publish → bidirectional
        assert by_channel["user/created"].direction == EventDirection.bidirectional
        # user/deleted has only subscribe → inbound
        assert by_channel["user/deleted"].direction == EventDirection.inbound

    def test_extract_v2_kafka_transport(
        self, extractor: AsyncAPIExtractor, v2_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v2_source)
        for ed in ir.event_descriptors:
            assert ed.transport == EventTransport.kafka

    def test_extract_v2_event_bridge_config(
        self, extractor: AsyncAPIExtractor, v2_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v2_source)
        for ed in ir.event_descriptors:
            assert ed.event_bridge is not None
            assert ed.event_bridge.broker_url == "kafka://broker.example.com:9092"

    def test_extract_v2_params_from_payload(
        self, extractor: AsyncAPIExtractor, v2_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v2_source)
        observe_created = next(op for op in ir.operations if op.name == "observe_user-created")
        param_names = {p.name for p in observe_created.params}
        assert "userId" in param_names
        assert "email" in param_names

    def test_extract_v2_support_level(
        self, extractor: AsyncAPIExtractor, v2_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v2_source)
        for ed in ir.event_descriptors:
            assert ed.support == EventSupportLevel.planned


# ── Extraction v3 tests ──────────────────────────────────────────────────


class TestExtractV3:
    def test_extract_v3_service_name(
        self, extractor: AsyncAPIExtractor, v3_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v3_source)
        assert ir.service_name == "order-events"

    def test_extract_v3_operations_count(
        self, extractor: AsyncAPIExtractor, v3_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v3_source)
        # 2 receive + 1 send = 3 operations
        assert len(ir.operations) == 3

    def test_extract_v3_channel_address(
        self, extractor: AsyncAPIExtractor, v3_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v3_source)
        channels = {ed.channel for ed in ir.event_descriptors}
        assert "orders/created" in channels
        assert "orders/shipped" in channels

    def test_extract_v3_amqp_transport(
        self, extractor: AsyncAPIExtractor, v3_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v3_source)
        for ed in ir.event_descriptors:
            assert ed.transport == EventTransport.amqp

    def test_extract_v3_receive_and_send_operations(
        self, extractor: AsyncAPIExtractor, v3_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v3_source)
        observe_names = [op.name for op in ir.operations if op.name.startswith("observe_")]
        publish_names = [op.name for op in ir.operations if op.name.startswith("publish_")]
        assert len(observe_names) == 2
        assert len(publish_names) == 1
        assert "observe_orders-created" in observe_names
        assert "observe_orders-shipped" in observe_names
        assert "publish_orders-created" in publish_names

    def test_extract_v3_event_descriptor_directions(
        self, extractor: AsyncAPIExtractor, v3_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v3_source)
        by_channel = {ed.channel: ed for ed in ir.event_descriptors}
        # orders/created has both receive and send → bidirectional
        assert by_channel["orders/created"].direction == EventDirection.bidirectional
        # orders/shipped has only receive → inbound
        assert by_channel["orders/shipped"].direction == EventDirection.inbound

    def test_extract_v3_broker_url(
        self, extractor: AsyncAPIExtractor, v3_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v3_source)
        assert ir.base_url == "amqp://rabbitmq.example.com:5672"


# ── MQTT tests ───────────────────────────────────────────────────────────


class TestExtractMQTT:
    def test_extract_mqtt_transport(
        self, extractor: AsyncAPIExtractor, mqtt_source: SourceConfig
    ) -> None:
        ir = extractor.extract(mqtt_source)
        for ed in ir.event_descriptors:
            assert ed.transport == EventTransport.mqtt

    def test_extract_mqtt_observe_only(
        self, extractor: AsyncAPIExtractor, mqtt_source: SourceConfig
    ) -> None:
        ir = extractor.extract(mqtt_source)
        # Only one subscribe channel, no publish
        assert len(ir.operations) == 1
        assert ir.operations[0].name.startswith("observe_")

    def test_extract_mqtt_event_descriptors(
        self, extractor: AsyncAPIExtractor, mqtt_source: SourceConfig
    ) -> None:
        ir = extractor.extract(mqtt_source)
        assert len(ir.event_descriptors) == 1
        ed = ir.event_descriptors[0]
        assert ed.channel == "sensors/temperature"
        assert ed.direction == EventDirection.inbound

    def test_extract_mqtt_params(
        self, extractor: AsyncAPIExtractor, mqtt_source: SourceConfig
    ) -> None:
        ir = extractor.extract(mqtt_source)
        param_names = {p.name for p in ir.operations[0].params}
        assert "sensorId" in param_names
        assert "temperature" in param_names

    def test_extract_mqtt_broker_url(
        self, extractor: AsyncAPIExtractor, mqtt_source: SourceConfig
    ) -> None:
        ir = extractor.extract(mqtt_source)
        assert ir.base_url == "mqtt://iot-broker:1883"


# ── Edge cases ────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_extract_empty_channels(self, extractor: AsyncAPIExtractor) -> None:
        source = SourceConfig(
            file_content=(
                "asyncapi: '2.6.0'\ninfo:\n  title: Empty\n  version: '1'\nchannels: {}\n"
            )
        )
        ir = extractor.extract(source)
        assert len(ir.operations) == 0
        assert len(ir.event_descriptors) == 0

    def test_extract_invalid_yaml(self, extractor: AsyncAPIExtractor) -> None:
        source = SourceConfig(file_content="{{not: valid: yaml: [[")
        with pytest.raises(Exception):
            extractor.extract(source)

    def test_extract_no_asyncapi_key(self, extractor: AsyncAPIExtractor) -> None:
        source = SourceConfig(file_content="info:\n  title: Missing Key\n  version: '1'\n")
        with pytest.raises(ValueError, match="not a valid AsyncAPI document"):
            extractor.extract(source)

    def test_operation_ids_unique(
        self, extractor: AsyncAPIExtractor, v2_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v2_source)
        ids = [op.id for op in ir.operations]
        assert len(ids) == len(set(ids)), "Operation IDs must be unique"

    def test_source_is_extractor(
        self, extractor: AsyncAPIExtractor, v2_source: SourceConfig
    ) -> None:
        ir = extractor.extract(v2_source)
        for op in ir.operations:
            assert op.source == SourceType.extractor

    def test_extract_no_content(self, extractor: AsyncAPIExtractor) -> None:
        source = SourceConfig(url="https://nonexistent.invalid/spec.yaml")
        with pytest.raises(ValueError, match="could not read source content"):
            extractor.extract(source)

    def test_extract_no_servers(self, extractor: AsyncAPIExtractor) -> None:
        source = SourceConfig(
            file_content=(
                "asyncapi: '2.6.0'\n"
                "info:\n  title: NoServer\n  version: '1'\n"
                "channels:\n"
                "  test/ch:\n"
                "    subscribe:\n"
                "      operationId: onTest\n"
                "      message:\n"
                "        payload:\n"
                "          type: object\n"
            )
        )
        ir = extractor.extract(source)
        assert ir.base_url == "localhost"
        assert len(ir.operations) == 1


# ── Error schema tests ───────────────────────────────────────────────────


class TestAsyncAPIErrorSchema:
    def test_asyncapi_operations_have_error_schema(
        self, extractor: AsyncAPIExtractor, v2_source: SourceConfig
    ) -> None:
        """AsyncAPI operations should have broker-related error schema."""
        ir = extractor.extract(v2_source)
        observe_ops = [op for op in ir.operations if op.name.startswith("observe_")]
        publish_ops = [op for op in ir.operations if op.name.startswith("publish_")]
        assert len(observe_ops) > 0
        assert len(publish_ops) > 0

        for op in observe_ops:
            assert op.error_schema is not None, f"Observe op {op.name} missing error_schema"
            assert isinstance(op.error_schema, ErrorSchema)
            error_codes = {r.error_code for r in op.error_schema.responses}
            assert "broker_unavailable" in error_codes
            assert "timeout" in error_codes
            assert "publish_failed" not in error_codes
            assert op.error_schema.default_error_schema is not None

        for op in publish_ops:
            assert op.error_schema is not None, f"Publish op {op.name} missing error_schema"
            assert isinstance(op.error_schema, ErrorSchema)
            error_codes = {r.error_code for r in op.error_schema.responses}
            assert "broker_unavailable" in error_codes
            assert "timeout" in error_codes
            assert "publish_failed" in error_codes
            assert op.error_schema.default_error_schema is not None

    def test_asyncapi_v3_operations_have_error_schema(
        self, extractor: AsyncAPIExtractor, v3_source: SourceConfig
    ) -> None:
        """AsyncAPI v3 operations should also have broker-related error schema."""
        ir = extractor.extract(v3_source)
        for op in ir.operations:
            assert op.error_schema is not None, f"Operation {op.name} missing error_schema"
            assert len(op.error_schema.responses) >= 2
            assert op.error_schema.default_error_schema is not None


class TestAsyncAPIJsonSchema:
    """Tests for json_schema emission on complex params."""

    def test_params_from_payload_emits_json_schema_for_object(self) -> None:
        """Object params with nested properties should get json_schema."""
        payload: dict[str, Any] = {
            "type": "object",
            "properties": {
                "address": {
                    "type": "object",
                    "properties": {
                        "street": {"type": "string"},
                        "city": {"type": "string"},
                    },
                    "required": ["street"],
                },
                "name": {"type": "string"},
            },
        }
        params = _params_from_payload(payload)
        by_name = {p.name: p for p in params}

        assert by_name["address"].json_schema is not None
        assert by_name["address"].json_schema["type"] == "object"
        assert "street" in by_name["address"].json_schema["properties"]
        assert by_name["address"].json_schema["required"] == ["street"]

        # Simple string param should not have json_schema
        assert by_name["name"].json_schema is None

    def test_params_from_payload_emits_json_schema_for_array(self) -> None:
        """Array params should get json_schema with items."""
        payload: dict[str, Any] = {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        }
        params = _params_from_payload(payload)
        assert len(params) == 1
        assert params[0].json_schema is not None
        assert params[0].json_schema["type"] == "array"
        assert params[0].json_schema["items"] == {"type": "string"}

    def test_params_from_payload_array_without_items_defaults(self) -> None:
        """Array param without items spec should default to string items."""
        payload: dict[str, Any] = {
            "type": "object",
            "properties": {
                "ids": {"type": "array"},
            },
        }
        params = _params_from_payload(payload)
        assert params[0].json_schema is not None
        assert params[0].json_schema["items"] == {"type": "string"}

    def test_json_schema_for_param_scalar_returns_none(self) -> None:
        """Scalar types should not produce json_schema."""
        assert _json_schema_for_param({"type": "string"}, "string") is None
        assert _json_schema_for_param({"type": "integer"}, "integer") is None
        assert _json_schema_for_param({"type": "boolean"}, "boolean") is None

    def test_json_schema_for_param_object_without_properties_returns_none(self) -> None:
        """Object without nested properties should not produce json_schema."""
        assert _json_schema_for_param({"type": "object"}, "object") is None

    def test_v2_extraction_includes_json_schema(
        self, extractor: AsyncAPIExtractor, v2_source: SourceConfig
    ) -> None:
        """V2 extraction should propagate json_schema for complex payload fields."""
        ir = extractor.extract(v2_source)
        # All params in the simple_v2 fixture are scalars (string),
        # so json_schema should be None for them
        for op in ir.operations:
            for param in op.params:
                assert param.json_schema is None, (
                    f"Scalar param {param.name} should not have json_schema"
                )
