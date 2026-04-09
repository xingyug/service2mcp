"""Integration tests for AsyncAPI event bridge and webhook runtime dispatch."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from apps.mcp_runtime.event_bridge import EventBridgeClient
from apps.mcp_runtime.observability import RuntimeObservability
from apps.mcp_runtime.proxy import RuntimeProxy
from apps.mcp_runtime.webhook_adapter import WebhookAdapter
from libs.ir.models import (
    AuthConfig,
    AuthType,
    EventBridgeConfig,
    EventDescriptor,
    EventDirection,
    EventSupportLevel,
    EventTransport,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)


def _risk_safe() -> RiskMetadata:
    return RiskMetadata(
        risk_level=RiskLevel.safe,
        confidence=1.0,
        source=SourceType.extractor,
        writes_state=False,
        destructive=False,
        external_side_effect=False,
        idempotent=True,
    )


def _build_event_ir(
    *,
    operations: list[Operation],
    event_descriptors: list[EventDescriptor],
) -> ServiceIR:
    return ServiceIR(
        source_hash="e" * 64,
        protocol="asyncapi",
        service_name="event-dispatch-test",
        service_description="Event dispatch integration test IR",
        base_url="https://broker.example.test",
        auth=AuthConfig(type=AuthType.none),
        operations=operations,
        event_descriptors=event_descriptors,
    )


def _mock_event_bridge() -> EventBridgeClient:
    """Create a mock EventBridgeClient with AsyncMock methods."""
    client = AsyncMock(spec=EventBridgeClient)
    client.publish.return_value = {"status": "published", "topic": "orders"}
    client.observe.return_value = [{"key": "msg-1", "value": "hello"}]
    client.disconnect.return_value = None
    return client


# ---------------------------------------------------------------------------
# Event bridge: publish (outbound)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_bridge_publish_dispatch() -> None:
    op = Operation(
        id="publishOrder",
        name="Publish Order",
        description="Publish an order event to Kafka.",
        method="POST",
        path="/orders",
        params=[Param(name="order_id", type="string", required=True)],
        risk=_risk_safe(),
        enabled=True,
    )
    ed = EventDescriptor(
        id="publishOrder-event",
        name="publishOrder",
        transport=EventTransport.kafka,
        direction=EventDirection.outbound,
        support=EventSupportLevel.supported,
        channel="orders.created",
        operation_id="publishOrder",
        event_bridge=EventBridgeConfig(broker_url="kafka://broker:9092"),
    )
    ir = _build_event_ir(operations=[op], event_descriptors=[ed])
    bridge = _mock_event_bridge()

    proxy = RuntimeProxy(
        ir,
        observability=RuntimeObservability(),
        event_bridge_client=bridge,
    )
    result = await proxy.invoke(op, {"order_id": "abc-123"})

    assert result["status"] == "ok"
    assert result["transport"] == "kafka"
    bridge.publish.assert_awaited_once_with("orders.created", {"order_id": "abc-123"})  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Event bridge: observe (inbound)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_bridge_observe_dispatch() -> None:
    op = Operation(
        id="consumeInventory",
        name="Consume Inventory",
        description="Observe inventory updates from RabbitMQ.",
        method="GET",
        path="/inventory",
        params=[],
        risk=_risk_safe(),
        enabled=True,
    )
    ed = EventDescriptor(
        id="consumeInventory-event",
        name="consumeInventory",
        transport=EventTransport.rabbitmq,
        direction=EventDirection.inbound,
        support=EventSupportLevel.supported,
        channel="inventory.updates",
        operation_id="consumeInventory",
        event_bridge=EventBridgeConfig(broker_url="amqp://broker:5672"),
    )
    ir = _build_event_ir(operations=[op], event_descriptors=[ed])
    bridge = _mock_event_bridge()

    proxy = RuntimeProxy(
        ir,
        observability=RuntimeObservability(),
        event_bridge_client=bridge,
    )
    result = await proxy.invoke(op, {"max_messages": 5, "timeout": 2.0})

    assert result["status"] == "ok"
    assert result["transport"] == "rabbitmq"
    assert "messages" in result["result"]
    bridge.observe.assert_awaited_once_with("inventory.updates", max_messages=5, timeout=2.0)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Webhook: register
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_register_dispatch() -> None:
    op = Operation(
        id="registerWebhook",
        name="Register Webhook",
        description="Register a webhook endpoint.",
        method="POST",
        path="/webhooks",
        params=[Param(name="action", type="string", required=False)],
        risk=_risk_safe(),
        enabled=True,
    )
    ed = EventDescriptor(
        id="registerWebhook-event",
        name="registerWebhook",
        transport=EventTransport.webhook,
        direction=EventDirection.inbound,
        support=EventSupportLevel.supported,
        channel="payment.received",
        operation_id="registerWebhook",
    )
    ir = _build_event_ir(operations=[op], event_descriptors=[ed])
    adapter = WebhookAdapter(callback_base_url="https://callback.example.test")

    proxy = RuntimeProxy(
        ir,
        observability=RuntimeObservability(),
        webhook_adapter=adapter,
    )
    result = await proxy.invoke(op, {"action": "register"})

    assert result["status"] == "ok"
    assert result["transport"] == "webhook"
    reg = result["result"]
    assert reg["channel"] == "payment.received"
    assert "registration_id" in reg


# ---------------------------------------------------------------------------
# Webhook: observe (default action)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_observe_dispatch() -> None:
    op = Operation(
        id="observePayments",
        name="Observe Payments",
        description="Observe incoming webhook payloads.",
        method="GET",
        path="/webhooks/payments",
        params=[],
        risk=_risk_safe(),
        enabled=True,
    )
    ed = EventDescriptor(
        id="observePayments-event",
        name="observePayments",
        transport=EventTransport.callback,
        direction=EventDirection.inbound,
        support=EventSupportLevel.supported,
        channel="payments",
        operation_id="observePayments",
    )
    ir = _build_event_ir(operations=[op], event_descriptors=[ed])
    adapter = WebhookAdapter(callback_base_url="https://callback.example.test")
    # Pre-populate some payloads
    adapter.register("payments")
    adapter.receive_payload("payments", {"amount": 99})

    proxy = RuntimeProxy(
        ir,
        observability=RuntimeObservability(),
        webhook_adapter=adapter,
    )
    result = await proxy.invoke(op, {})

    assert result["status"] == "ok"
    assert result["transport"] == "callback"
    assert result["result"]["payloads"] == [{"amount": 99}]


# ---------------------------------------------------------------------------
# Error: event bridge not configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_bridge_not_configured_raises() -> None:
    op = Operation(
        id="publishOrder",
        name="Publish Order",
        description="Publish an order event.",
        method="POST",
        path="/orders",
        params=[],
        risk=_risk_safe(),
        enabled=True,
    )
    ed = EventDescriptor(
        id="publishOrder-event",
        name="publishOrder",
        transport=EventTransport.kafka,
        direction=EventDirection.outbound,
        support=EventSupportLevel.supported,
        channel="orders.created",
        operation_id="publishOrder",
    )
    ir = _build_event_ir(operations=[op], event_descriptors=[ed])

    proxy = RuntimeProxy(
        ir,
        observability=RuntimeObservability(),
        # event_bridge_client intentionally omitted
    )
    with pytest.raises(ToolError, match="event bridge client"):
        await proxy.invoke(op, {"order_id": "fail"})


# ---------------------------------------------------------------------------
# Error: webhook adapter not configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_not_configured_raises() -> None:
    op = Operation(
        id="observePayments",
        name="Observe Payments",
        description="Observe webhook payloads.",
        method="GET",
        path="/webhooks/payments",
        params=[],
        risk=_risk_safe(),
        enabled=True,
    )
    ed = EventDescriptor(
        id="observePayments-event",
        name="observePayments",
        transport=EventTransport.webhook,
        direction=EventDirection.inbound,
        support=EventSupportLevel.supported,
        channel="payments",
        operation_id="observePayments",
    )
    ir = _build_event_ir(operations=[op], event_descriptors=[ed])

    proxy = RuntimeProxy(
        ir,
        observability=RuntimeObservability(),
        # webhook_adapter intentionally omitted
    )
    with pytest.raises(ToolError, match="webhook adapter"):
        await proxy.invoke(op, {})


# ---------------------------------------------------------------------------
# Mixed IR: event + HTTP operations coexist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_coexists_with_http() -> None:
    """An IR with both event and HTTP operations dispatches each correctly."""
    event_op = Operation(
        id="publishOrder",
        name="Publish Order",
        description="Publish via Kafka.",
        method="POST",
        path="/orders",
        params=[],
        risk=_risk_safe(),
        enabled=True,
    )
    http_op = Operation(
        id="getAccount",
        name="Get Account",
        description="Fetch one account via HTTP.",
        method="GET",
        path="/accounts/{account_id}",
        params=[Param(name="account_id", type="string", required=True)],
        risk=_risk_safe(),
        enabled=True,
    )
    ed = EventDescriptor(
        id="publishOrder-event",
        name="publishOrder",
        transport=EventTransport.mqtt,
        direction=EventDirection.outbound,
        support=EventSupportLevel.supported,
        channel="orders.topic",
        operation_id="publishOrder",
    )
    ir = _build_event_ir(operations=[event_op, http_op], event_descriptors=[ed])
    bridge = _mock_event_bridge()

    proxy = RuntimeProxy(
        ir,
        observability=RuntimeObservability(),
        event_bridge_client=bridge,
    )

    # Event operation goes through event bridge
    event_result = await proxy.invoke(event_op, {"payload": "data"})
    assert event_result["status"] == "ok"
    assert event_result["transport"] == "mqtt"
    bridge.publish.assert_awaited_once()  # type: ignore[attr-defined]

    # HTTP operation falls through _dispatch_native (returns None) and
    # would proceed to _perform_request. We don't mock the HTTP layer
    # here; we just verify the event op didn't interfere.
    assert proxy._find_event_descriptor_for_operation(http_op) is None
