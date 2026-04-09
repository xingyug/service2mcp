"""Tests for new IR v3 model additions — PaginationConfig, CliOperationConfig, EventBridgeConfig."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from libs.ir.models import (
    CliOperationConfig,
    EventBridgeConfig,
    EventDescriptor,
    EventTransport,
    GraphQLOperationConfig,
    GraphQLOperationType,
    Operation,
    PaginationConfig,
    RiskLevel,
    RiskMetadata,
)

# ── Helpers ────────────────────────────────────────────────────────────────

_SAFE_RISK = RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9)


def _make_op(**kwargs: Any) -> Operation:
    defaults: dict[str, Any] = {
        "id": "op1",
        "name": "TestOp",
        "method": "GET",
        "path": "/test",
        "risk": _SAFE_RISK,
        "enabled": True,
    }
    defaults.update(kwargs)
    return Operation(**defaults)


# ── PaginationConfig tests ────────────────────────────────────────────────


class TestPaginationConfig:
    def test_pagination_config_defaults(self) -> None:
        pc = PaginationConfig()
        assert pc.style == "none"
        assert pc.page_param is None
        assert pc.limit_param is None
        assert pc.cursor_param is None
        assert pc.next_field is None
        assert pc.total_field is None
        assert pc.default_page_size is None
        assert pc.max_page_size is None

    def test_pagination_config_cursor_style(self) -> None:
        pc = PaginationConfig(
            style="cursor",
            cursor_param="after",
            limit_param="first",
            next_field="endCursor",
        )
        assert pc.style == "cursor"
        assert pc.cursor_param == "after"
        assert pc.limit_param == "first"
        assert pc.next_field == "endCursor"

    def test_pagination_config_serialization(self) -> None:
        pc = PaginationConfig(
            style="offset",
            page_param="offset",
            limit_param="limit",
            total_field="total",
            default_page_size=20,
            max_page_size=100,
        )
        data = pc.model_dump()
        assert data["style"] == "offset"
        assert data["page_param"] == "offset"
        assert data["limit_param"] == "limit"
        assert data["total_field"] == "total"
        assert data["default_page_size"] == 20
        assert data["max_page_size"] == 100
        # Round-trip
        restored = PaginationConfig.model_validate(data)
        assert restored == pc


# ── CliOperationConfig tests ──────────────────────────────────────────────


class TestCliOperationConfig:
    def test_cli_config_defaults(self) -> None:
        cfg = CliOperationConfig(command="git")
        assert cfg.command == "git"
        assert cfg.subcommands == []
        assert cfg.args_style == "gnu"
        assert cfg.env_vars == {}
        assert cfg.working_dir is None
        assert cfg.timeout_seconds == 30
        assert cfg.sandbox_mode == "none"
        assert cfg.output_format == "auto"

    def test_cli_config_full(self) -> None:
        cfg = CliOperationConfig(
            command="docker",
            subcommands=["container", "ls"],
            args_style="posix",
            env_vars={"DOCKER_HOST": "unix:///var/run/docker.sock"},
            working_dir="/app",
            timeout_seconds=120,
            sandbox_mode="docker",
            output_format="json",
        )
        assert cfg.command == "docker"
        assert cfg.subcommands == ["container", "ls"]
        assert cfg.args_style == "posix"
        assert cfg.env_vars == {"DOCKER_HOST": "unix:///var/run/docker.sock"}
        assert cfg.working_dir == "/app"
        assert cfg.timeout_seconds == 120
        assert cfg.sandbox_mode == "docker"
        assert cfg.output_format == "json"

    def test_cli_config_timeout_bounds(self) -> None:
        # ge=1
        with pytest.raises(ValidationError):
            CliOperationConfig(command="ls", timeout_seconds=0)
        # le=3600
        with pytest.raises(ValidationError):
            CliOperationConfig(command="ls", timeout_seconds=3601)
        # boundary values succeed
        CliOperationConfig(command="ls", timeout_seconds=1)
        CliOperationConfig(command="ls", timeout_seconds=3600)

    def test_cli_operation_exclusive(self) -> None:
        cli_cfg = CliOperationConfig(command="kubectl")
        graphql_cfg = GraphQLOperationConfig(
            operation_type=GraphQLOperationType.query,
            operation_name="getUser",
            document="query { user { id } }",
        )
        with pytest.raises(ValidationError, match="cli.*cannot be combined with.*graphql"):
            _make_op(cli=cli_cfg, graphql=graphql_cfg)


# ── EventBridgeConfig tests ──────────────────────────────────────────────


class TestEventBridgeConfig:
    def test_event_bridge_config_defaults(self) -> None:
        cfg = EventBridgeConfig(broker_url="kafka://localhost:9092")
        assert cfg.broker_url == "kafka://localhost:9092"
        assert cfg.topic is None
        assert cfg.queue is None
        assert cfg.group_id is None
        assert cfg.auth_ref is None
        assert cfg.protocol_version is None
        assert cfg.metadata == {}

    def test_event_bridge_config_full(self) -> None:
        cfg = EventBridgeConfig(
            broker_url="amqp://rabbit:5672",
            topic="orders.created",
            queue="order-processor",
            group_id="processor-group",
            auth_ref="rabbit-creds",
            protocol_version="0.9.1",
            metadata={"exchange": "orders"},
        )
        assert cfg.broker_url == "amqp://rabbit:5672"
        assert cfg.topic == "orders.created"
        assert cfg.queue == "order-processor"
        assert cfg.group_id == "processor-group"
        assert cfg.auth_ref == "rabbit-creds"
        assert cfg.protocol_version == "0.9.1"
        assert cfg.metadata == {"exchange": "orders"}

    def test_event_descriptor_with_bridge(self) -> None:
        bridge = EventBridgeConfig(
            broker_url="kafka://broker:9092",
            topic="events.user.created",
            group_id="user-svc",
        )
        desc = EventDescriptor(
            id="evt-user-created",
            name="UserCreated",
            transport=EventTransport.kafka,
            event_bridge=bridge,
        )
        assert desc.event_bridge is not None
        assert desc.event_bridge.broker_url == "kafka://broker:9092"
        assert desc.event_bridge.topic == "events.user.created"


# ── EventTransport new values ─────────────────────────────────────────────


class TestEventTransportNewValues:
    def test_kafka_transport_exists(self) -> None:
        assert EventTransport.kafka == "kafka"

    def test_rabbitmq_transport_exists(self) -> None:
        assert EventTransport.rabbitmq == "rabbitmq"

    def test_mqtt_transport_exists(self) -> None:
        assert EventTransport.mqtt == "mqtt"

    def test_amqp_transport_exists(self) -> None:
        assert EventTransport.amqp == "amqp"

    def test_pulsar_transport_exists(self) -> None:
        assert EventTransport.pulsar == "pulsar"

    def test_nats_transport_exists(self) -> None:
        assert EventTransport.nats == "nats"
