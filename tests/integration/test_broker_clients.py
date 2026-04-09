"""Integration tests for real broker event bridge clients (V4).

Tests exercise full lifecycle flows (connect → publish/observe → disconnect),
error propagation, config handling, and factory integration using mocked broker
libraries so no live brokers are required.
"""

from __future__ import annotations

import json
import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.mcp_runtime.event_bridge import (
    EventBridgeClient,
    KafkaEventBridgeClient,
    MqttEventBridgeClient,
    RabbitMQEventBridgeClient,
    StubEventBridgeClient,
    get_event_bridge_client,
)
from libs.ir.models import EventBridgeConfig, EventTransport

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _kafka_config() -> EventBridgeConfig:
    return EventBridgeConfig(broker_url="kafka://broker1:9092", topic="orders")


def _rabbitmq_config() -> EventBridgeConfig:
    return EventBridgeConfig(broker_url="rabbitmq://rmq-host:5672", topic="events")


def _amqp_config() -> EventBridgeConfig:
    return EventBridgeConfig(broker_url="amqp://rmq-host:5672", topic="events")


def _mqtt_config() -> EventBridgeConfig:
    return EventBridgeConfig(broker_url="mqtt://mosquitto:1883", topic="sensors/temp")


def _mqtt_config_no_port() -> EventBridgeConfig:
    return EventBridgeConfig(broker_url="mqtt://mosquitto", topic="sensors/temp")


def _make_kafka_record(
    topic: str = "orders",
    partition: int = 0,
    offset: int = 42,
    key: bytes | None = None,
    value: dict[str, Any] | None = None,
    timestamp: int = 1700000000,
) -> MagicMock:
    rec = MagicMock()
    rec.topic = topic
    rec.partition = partition
    rec.offset = offset
    rec.key = key
    rec.value = value or {"event": "created"}
    rec.timestamp = timestamp
    return rec


# ---------------------------------------------------------------------------
# Kafka integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestKafkaLifecycle:
    """Full lifecycle tests for KafkaEventBridgeClient with mocked aiokafka."""

    @pytest.fixture()
    def mock_aiokafka(self) -> Any:
        mod: Any = types.ModuleType("aiokafka")
        mod.AIOKafkaProducer = MagicMock()
        mod.AIOKafkaConsumer = MagicMock()
        return mod

    @pytest.mark.asyncio
    async def test_connect_creates_producer(self, mock_aiokafka: Any) -> None:
        producer_instance = AsyncMock()
        mock_aiokafka.AIOKafkaProducer.return_value = producer_instance

        client = KafkaEventBridgeClient()
        with patch.dict(sys.modules, {"aiokafka": mock_aiokafka}):
            await client.connect(_kafka_config())

        mock_aiokafka.AIOKafkaProducer.assert_called_once()
        call_kwargs = mock_aiokafka.AIOKafkaProducer.call_args
        assert call_kwargs.kwargs["bootstrap_servers"] == "broker1:9092"
        producer_instance.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_publish_serialises_and_sends(self, mock_aiokafka: Any) -> None:
        producer_instance = AsyncMock()
        send_result = MagicMock(topic="orders", partition=0, offset=7)
        producer_instance.send_and_wait.return_value = send_result
        mock_aiokafka.AIOKafkaProducer.return_value = producer_instance

        client = KafkaEventBridgeClient()
        with patch.dict(sys.modules, {"aiokafka": mock_aiokafka}):
            await client.connect(_kafka_config())
            result = await client.publish("orders", {"action": "buy"})

        producer_instance.send_and_wait.assert_awaited_once_with("orders", value={"action": "buy"})
        assert result["status"] == "delivered"
        assert result["topic"] == "orders"
        assert result["partition"] == 0
        assert result["offset"] == 7

    @pytest.mark.asyncio
    async def test_observe_collects_messages(self, mock_aiokafka: Any) -> None:
        records = [_make_kafka_record(offset=i) for i in range(3)]

        producer_instance = AsyncMock()
        mock_aiokafka.AIOKafkaProducer.return_value = producer_instance

        consumer_instance = AsyncMock()
        consumer_instance.start = AsyncMock()
        consumer_instance.stop = AsyncMock()

        async def _aiter(*_: Any, **__: Any) -> Any:
            for r in records:
                yield r

        consumer_instance.__aiter__ = _aiter
        mock_aiokafka.AIOKafkaConsumer.return_value = consumer_instance

        client = KafkaEventBridgeClient()
        with patch.dict(sys.modules, {"aiokafka": mock_aiokafka}):
            await client.connect(_kafka_config())
            messages = await client.observe("orders", max_messages=3, timeout=5.0)

        assert len(messages) == 3
        assert messages[0]["offset"] == 0
        assert messages[2]["offset"] == 2
        assert messages[0]["topic"] == "orders"
        consumer_instance.start.assert_awaited_once()
        consumer_instance.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_observe_respects_max_messages(self, mock_aiokafka: Any) -> None:
        records = [_make_kafka_record(offset=i) for i in range(10)]

        producer_instance = AsyncMock()
        mock_aiokafka.AIOKafkaProducer.return_value = producer_instance

        consumer_instance = AsyncMock()
        consumer_instance.start = AsyncMock()
        consumer_instance.stop = AsyncMock()

        async def _aiter(*_: Any, **__: Any) -> Any:
            for r in records:
                yield r

        consumer_instance.__aiter__ = _aiter
        mock_aiokafka.AIOKafkaConsumer.return_value = consumer_instance

        client = KafkaEventBridgeClient()
        with patch.dict(sys.modules, {"aiokafka": mock_aiokafka}):
            await client.connect(_kafka_config())
            messages = await client.observe("orders", max_messages=2)

        assert len(messages) == 2

    @pytest.mark.asyncio
    async def test_observe_consumer_created_with_correct_params(self, mock_aiokafka: Any) -> None:
        producer_instance = AsyncMock()
        mock_aiokafka.AIOKafkaProducer.return_value = producer_instance

        consumer_instance = AsyncMock()
        consumer_instance.start = AsyncMock()
        consumer_instance.stop = AsyncMock()

        async def _aiter(*_: Any, **__: Any) -> Any:
            return
            yield  # pragma: no cover – makes this an async generator

        consumer_instance.__aiter__ = _aiter
        mock_aiokafka.AIOKafkaConsumer.return_value = consumer_instance

        client = KafkaEventBridgeClient()
        with patch.dict(sys.modules, {"aiokafka": mock_aiokafka}):
            await client.connect(_kafka_config())
            await client.observe("orders", timeout=3.0)

        call_args = mock_aiokafka.AIOKafkaConsumer.call_args
        assert call_args.args[0] == "orders"
        assert call_args.kwargs["bootstrap_servers"] == "broker1:9092"
        assert call_args.kwargs["auto_offset_reset"] == "latest"
        assert call_args.kwargs["consumer_timeout_ms"] == 3000

    @pytest.mark.asyncio
    async def test_disconnect_stops_producer(self, mock_aiokafka: Any) -> None:
        producer_instance = AsyncMock()
        mock_aiokafka.AIOKafkaProducer.return_value = producer_instance

        client = KafkaEventBridgeClient()
        with patch.dict(sys.modules, {"aiokafka": mock_aiokafka}):
            await client.connect(_kafka_config())
            await client.disconnect()

        producer_instance.stop.assert_awaited_once()
        assert client._producer is None

    @pytest.mark.asyncio
    async def test_connect_failure_propagates(self, mock_aiokafka: Any) -> None:
        producer_instance = AsyncMock()
        producer_instance.start.side_effect = ConnectionError("broker unreachable")
        mock_aiokafka.AIOKafkaProducer.return_value = producer_instance

        client = KafkaEventBridgeClient()
        with patch.dict(sys.modules, {"aiokafka": mock_aiokafka}):
            with pytest.raises(ConnectionError, match="broker unreachable"):
                await client.connect(_kafka_config())

    @pytest.mark.asyncio
    async def test_publish_failure_propagates(self, mock_aiokafka: Any) -> None:
        producer_instance = AsyncMock()
        producer_instance.send_and_wait.side_effect = RuntimeError("send failed")
        mock_aiokafka.AIOKafkaProducer.return_value = producer_instance

        client = KafkaEventBridgeClient()
        with patch.dict(sys.modules, {"aiokafka": mock_aiokafka}):
            await client.connect(_kafka_config())
            with pytest.raises(RuntimeError, match="send failed"):
                await client.publish("orders", {"x": 1})

    @pytest.mark.asyncio
    async def test_observe_consumer_stop_called_on_error(self, mock_aiokafka: Any) -> None:
        producer_instance = AsyncMock()
        mock_aiokafka.AIOKafkaProducer.return_value = producer_instance

        consumer_instance = AsyncMock()
        consumer_instance.start = AsyncMock()
        consumer_instance.stop = AsyncMock()

        async def _aiter_err(*_: Any, **__: Any) -> Any:
            yield _make_kafka_record()
            raise RuntimeError("stream broke")

        consumer_instance.__aiter__ = _aiter_err
        mock_aiokafka.AIOKafkaConsumer.return_value = consumer_instance

        client = KafkaEventBridgeClient()
        with patch.dict(sys.modules, {"aiokafka": mock_aiokafka}):
            await client.connect(_kafka_config())
            with pytest.raises(RuntimeError, match="stream broke"):
                await client.observe("orders")

        # consumer.stop is still called in the finally block
        consumer_instance.stop.assert_awaited_once()


# ---------------------------------------------------------------------------
# RabbitMQ integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRabbitMQLifecycle:
    """Full lifecycle tests for RabbitMQEventBridgeClient with mocked aio_pika."""

    @pytest.fixture()
    def mock_aio_pika(self) -> Any:
        mod: Any = types.ModuleType("aio_pika")
        mod.connect_robust = AsyncMock()
        mod.ExchangeType = MagicMock()
        mod.ExchangeType.FANOUT = "fanout"
        mod.Message = MagicMock()
        return mod

    @pytest.mark.asyncio
    async def test_connect_rewrites_rabbitmq_scheme(self, mock_aio_pika: Any) -> None:
        connection = AsyncMock()
        connection.channel = AsyncMock(return_value=AsyncMock())
        mock_aio_pika.connect_robust.return_value = connection

        client = RabbitMQEventBridgeClient()
        with patch.dict(sys.modules, {"aio_pika": mock_aio_pika}):
            await client.connect(_rabbitmq_config())

        mock_aio_pika.connect_robust.assert_awaited_once_with("amqp://rmq-host:5672")

    @pytest.mark.asyncio
    async def test_connect_preserves_amqp_scheme(self, mock_aio_pika: Any) -> None:
        connection = AsyncMock()
        connection.channel = AsyncMock(return_value=AsyncMock())
        mock_aio_pika.connect_robust.return_value = connection

        client = RabbitMQEventBridgeClient()
        with patch.dict(sys.modules, {"aio_pika": mock_aio_pika}):
            await client.connect(_amqp_config())

        mock_aio_pika.connect_robust.assert_awaited_once_with("amqp://rmq-host:5672")

    @pytest.mark.asyncio
    async def test_connect_opens_channel(self, mock_aio_pika: Any) -> None:
        channel = AsyncMock()
        connection = AsyncMock()
        connection.channel = AsyncMock(return_value=channel)
        mock_aio_pika.connect_robust.return_value = connection

        client = RabbitMQEventBridgeClient()
        with patch.dict(sys.modules, {"aio_pika": mock_aio_pika}):
            await client.connect(_rabbitmq_config())

        connection.channel.assert_awaited_once()
        assert client._channel is channel

    @pytest.mark.asyncio
    async def test_publish_creates_exchange_and_sends(self, mock_aio_pika: Any) -> None:
        exchange = AsyncMock()
        channel = AsyncMock()
        channel.declare_exchange = AsyncMock(return_value=exchange)
        connection = AsyncMock()
        connection.channel = AsyncMock(return_value=channel)
        mock_aio_pika.connect_robust.return_value = connection

        msg_sentinel = MagicMock()
        mock_aio_pika.Message.return_value = msg_sentinel

        client = RabbitMQEventBridgeClient()
        with patch.dict(sys.modules, {"aio_pika": mock_aio_pika}):
            await client.connect(_rabbitmq_config())
            result = await client.publish("events", {"type": "order.created"})

        channel.declare_exchange.assert_awaited_once_with("events", "fanout", durable=True)
        exchange.publish.assert_awaited_once_with(msg_sentinel, routing_key="")
        # Verify Message was created with correct JSON body
        msg_body = mock_aio_pika.Message.call_args.kwargs["body"]
        assert json.loads(msg_body) == {"type": "order.created"}
        assert result == {"status": "delivered", "exchange": "events"}

    @pytest.mark.asyncio
    async def test_observe_declares_passive_queue_and_collects(self, mock_aio_pika: Any) -> None:
        msg1 = MagicMock()
        msg1.body = json.dumps({"seq": 1}).encode()
        msg1.message_id = "m1"
        msg1.routing_key = "rk1"

        queue = AsyncMock()
        queue.get = AsyncMock(side_effect=[msg1, TimeoutError()])

        channel = AsyncMock()
        channel.declare_queue = AsyncMock(return_value=queue)
        connection = AsyncMock()
        connection.channel = AsyncMock(return_value=channel)
        mock_aio_pika.connect_robust.return_value = connection

        client = RabbitMQEventBridgeClient()
        with patch.dict(sys.modules, {"aio_pika": mock_aio_pika}):
            await client.connect(_rabbitmq_config())
            messages = await client.observe("events", max_messages=5, timeout=1.0)

        channel.declare_queue.assert_awaited_once_with("events", passive=True)
        assert len(messages) == 1
        assert messages[0]["body"] == {"seq": 1}
        assert messages[0]["queue"] == "events"
        assert messages[0]["message_id"] == "m1"

    @pytest.mark.asyncio
    async def test_observe_stops_at_max_messages(self, mock_aio_pika: Any) -> None:
        def _make_msg(seq: int) -> MagicMock:
            m = MagicMock()
            m.body = json.dumps({"seq": seq}).encode()
            m.message_id = f"m{seq}"
            m.routing_key = ""
            return m

        msgs = [_make_msg(i) for i in range(5)]
        queue = AsyncMock()
        queue.get = AsyncMock(side_effect=msgs + [TimeoutError()])

        channel = AsyncMock()
        channel.declare_queue = AsyncMock(return_value=queue)
        connection = AsyncMock()
        connection.channel = AsyncMock(return_value=channel)
        mock_aio_pika.connect_robust.return_value = connection

        client = RabbitMQEventBridgeClient()
        with patch.dict(sys.modules, {"aio_pika": mock_aio_pika}):
            await client.connect(_rabbitmq_config())
            messages = await client.observe("events", max_messages=3, timeout=10.0)

        assert len(messages) == 3

    @pytest.mark.asyncio
    async def test_disconnect_closes_connection(self, mock_aio_pika: Any) -> None:
        connection = AsyncMock()
        connection.channel = AsyncMock(return_value=AsyncMock())
        mock_aio_pika.connect_robust.return_value = connection

        client = RabbitMQEventBridgeClient()
        with patch.dict(sys.modules, {"aio_pika": mock_aio_pika}):
            await client.connect(_rabbitmq_config())
            await client.disconnect()

        connection.close.assert_awaited_once()
        assert client._connection is None
        assert client._channel is None

    @pytest.mark.asyncio
    async def test_connect_failure_propagates(self, mock_aio_pika: Any) -> None:
        mock_aio_pika.connect_robust.side_effect = ConnectionError("refused")

        client = RabbitMQEventBridgeClient()
        with patch.dict(sys.modules, {"aio_pika": mock_aio_pika}):
            with pytest.raises(ConnectionError, match="refused"):
                await client.connect(_rabbitmq_config())

    @pytest.mark.asyncio
    async def test_publish_failure_propagates(self, mock_aio_pika: Any) -> None:
        exchange = AsyncMock()
        exchange.publish.side_effect = RuntimeError("exchange error")
        channel = AsyncMock()
        channel.declare_exchange = AsyncMock(return_value=exchange)
        connection = AsyncMock()
        connection.channel = AsyncMock(return_value=channel)
        mock_aio_pika.connect_robust.return_value = connection

        client = RabbitMQEventBridgeClient()
        with patch.dict(sys.modules, {"aio_pika": mock_aio_pika}):
            await client.connect(_rabbitmq_config())
            with pytest.raises(RuntimeError, match="exchange error"):
                await client.publish("events", {"x": 1})


# ---------------------------------------------------------------------------
# MQTT integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMqttLifecycle:
    """Full lifecycle tests for MqttEventBridgeClient with mocked aiomqtt."""

    @pytest.fixture()
    def mock_aiomqtt(self) -> Any:
        mod: Any = types.ModuleType("aiomqtt")
        mod.Client = MagicMock()
        return mod

    @pytest.mark.asyncio
    async def test_connect_parses_host_and_port(self, mock_aiomqtt: Any) -> None:
        client = MqttEventBridgeClient()
        with patch.dict(sys.modules, {"aiomqtt": mock_aiomqtt}):
            await client.connect(_mqtt_config())

        assert client._hostname == "mosquitto"
        assert client._port == 1883

    @pytest.mark.asyncio
    async def test_connect_default_port_when_omitted(self, mock_aiomqtt: Any) -> None:
        client = MqttEventBridgeClient()
        with patch.dict(sys.modules, {"aiomqtt": mock_aiomqtt}):
            await client.connect(_mqtt_config_no_port())

        assert client._hostname == "mosquitto"
        assert client._port == 1883

    @pytest.mark.asyncio
    async def test_connect_non_numeric_port_defaults(self, mock_aiomqtt: Any) -> None:
        config = EventBridgeConfig(broker_url="mqtt://host:notaport", topic="t")
        client = MqttEventBridgeClient()
        with patch.dict(sys.modules, {"aiomqtt": mock_aiomqtt}):
            await client.connect(config)

        assert client._port == 1883

    @pytest.mark.asyncio
    async def test_publish_connect_per_operation(self, mock_aiomqtt: Any) -> None:
        mock_ctx = AsyncMock()
        mock_ctx.publish = AsyncMock()
        mock_aiomqtt.Client.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_aiomqtt.Client.return_value.__aexit__ = AsyncMock(return_value=False)

        client = MqttEventBridgeClient()
        with patch.dict(sys.modules, {"aiomqtt": mock_aiomqtt}):
            await client.connect(_mqtt_config())
            result = await client.publish("sensors/temp", {"temp": 22.5})

        mock_aiomqtt.Client.assert_called_with("mosquitto", 1883)
        mock_ctx.publish.assert_awaited_once()
        pub_args = mock_ctx.publish.call_args
        assert pub_args.args[0] == "sensors/temp"
        assert json.loads(pub_args.args[1]) == {"temp": 22.5}
        assert result == {"status": "delivered", "topic": "sensors/temp"}

    @pytest.mark.asyncio
    async def test_observe_subscribes_and_collects(self, mock_aiomqtt: Any) -> None:
        msg1 = MagicMock()
        msg1.topic = "sensors/temp"
        msg1.payload = json.dumps({"temp": 20}).encode()
        msg1.qos = 0

        msg2 = MagicMock()
        msg2.topic = "sensors/temp"
        msg2.payload = json.dumps({"temp": 21}).encode()
        msg2.qos = 1

        mock_ctx = AsyncMock()
        mock_ctx.subscribe = AsyncMock()

        async def _msg_aiter(*_: Any, **__: Any) -> Any:
            yield msg1
            yield msg2

        mock_ctx.messages = MagicMock()
        mock_ctx.messages.__aiter__ = _msg_aiter

        mock_aiomqtt.Client.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_aiomqtt.Client.return_value.__aexit__ = AsyncMock(return_value=False)

        client = MqttEventBridgeClient()
        with patch.dict(sys.modules, {"aiomqtt": mock_aiomqtt}):
            await client.connect(_mqtt_config())
            messages = await client.observe("sensors/temp", max_messages=5, timeout=2.0)

        mock_ctx.subscribe.assert_awaited_once_with("sensors/temp")
        assert len(messages) == 2
        assert messages[0]["payload"] == {"temp": 20}
        assert messages[1]["qos"] == 1

    @pytest.mark.asyncio
    async def test_observe_respects_max_messages(self, mock_aiomqtt: Any) -> None:
        def _mk(seq: int) -> MagicMock:
            m = MagicMock()
            m.topic = "t"
            m.payload = json.dumps({"s": seq}).encode()
            m.qos = 0
            return m

        items = [_mk(i) for i in range(10)]

        mock_ctx = AsyncMock()
        mock_ctx.subscribe = AsyncMock()

        async def _msg_aiter(*_: Any, **__: Any) -> Any:
            for it in items:
                yield it

        mock_ctx.messages = MagicMock()
        mock_ctx.messages.__aiter__ = _msg_aiter

        mock_aiomqtt.Client.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_aiomqtt.Client.return_value.__aexit__ = AsyncMock(return_value=False)

        client = MqttEventBridgeClient()
        with patch.dict(sys.modules, {"aiomqtt": mock_aiomqtt}):
            await client.connect(_mqtt_config())
            messages = await client.observe("t", max_messages=3, timeout=5.0)

        assert len(messages) == 3

    @pytest.mark.asyncio
    async def test_observe_handles_non_json_payload(self, mock_aiomqtt: Any) -> None:
        msg = MagicMock()
        msg.topic = "raw"
        msg.payload = b"not json"
        msg.qos = 0

        mock_ctx = AsyncMock()
        mock_ctx.subscribe = AsyncMock()

        async def _msg_aiter(*_: Any, **__: Any) -> Any:
            yield msg

        mock_ctx.messages = MagicMock()
        mock_ctx.messages.__aiter__ = _msg_aiter

        mock_aiomqtt.Client.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_aiomqtt.Client.return_value.__aexit__ = AsyncMock(return_value=False)

        client = MqttEventBridgeClient()
        with patch.dict(sys.modules, {"aiomqtt": mock_aiomqtt}):
            await client.connect(_mqtt_config())
            messages = await client.observe("raw", max_messages=1, timeout=1.0)

        assert len(messages) == 1
        assert messages[0]["payload"] == "not json"

    @pytest.mark.asyncio
    async def test_observe_error_returns_partial(self, mock_aiomqtt: Any) -> None:
        """Client-level exception during observe returns empty list (logged)."""
        mock_aiomqtt.Client.return_value.__aenter__ = AsyncMock(
            side_effect=OSError("connection refused")
        )
        mock_aiomqtt.Client.return_value.__aexit__ = AsyncMock(return_value=False)

        client = MqttEventBridgeClient()
        with patch.dict(sys.modules, {"aiomqtt": mock_aiomqtt}):
            await client.connect(_mqtt_config())
            messages = await client.observe("t", timeout=0.5)

        assert messages == []

    @pytest.mark.asyncio
    async def test_publish_failure_propagates(self, mock_aiomqtt: Any) -> None:
        mock_aiomqtt.Client.return_value.__aenter__ = AsyncMock(
            side_effect=OSError("connection refused")
        )
        mock_aiomqtt.Client.return_value.__aexit__ = AsyncMock(return_value=False)

        client = MqttEventBridgeClient()
        with patch.dict(sys.modules, {"aiomqtt": mock_aiomqtt}):
            await client.connect(_mqtt_config())
            with pytest.raises(OSError, match="connection refused"):
                await client.publish("t", {"x": 1})

    @pytest.mark.asyncio
    async def test_disconnect_is_noop(self) -> None:
        client = MqttEventBridgeClient()
        await client.disconnect()  # should not raise


# ---------------------------------------------------------------------------
# Factory integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFactoryIntegration:
    """Factory selects correct client type based on transport and library availability."""

    def test_kafka_returns_real_client_when_importable(self) -> None:
        fake_mod = types.ModuleType("aiokafka")
        with patch.dict(sys.modules, {"aiokafka": fake_mod}):
            client = get_event_bridge_client(EventTransport.kafka)
        assert isinstance(client, KafkaEventBridgeClient)

    def test_kafka_falls_back_when_not_importable(self) -> None:
        with patch.dict(sys.modules, {"aiokafka": None}):
            client = get_event_bridge_client(EventTransport.kafka)
        assert isinstance(client, StubEventBridgeClient)

    def test_rabbitmq_returns_real_client_when_importable(self) -> None:
        fake_mod = types.ModuleType("aio_pika")
        with patch.dict(sys.modules, {"aio_pika": fake_mod}):
            client = get_event_bridge_client(EventTransport.rabbitmq)
        assert isinstance(client, RabbitMQEventBridgeClient)

    def test_amqp_returns_rabbitmq_client_when_importable(self) -> None:
        fake_mod = types.ModuleType("aio_pika")
        with patch.dict(sys.modules, {"aio_pika": fake_mod}):
            client = get_event_bridge_client(EventTransport.amqp)
        assert isinstance(client, RabbitMQEventBridgeClient)

    def test_rabbitmq_falls_back_when_not_importable(self) -> None:
        with patch.dict(sys.modules, {"aio_pika": None}):
            client = get_event_bridge_client(EventTransport.rabbitmq)
        assert isinstance(client, StubEventBridgeClient)

    def test_mqtt_returns_real_client_when_importable(self) -> None:
        fake_mod = types.ModuleType("aiomqtt")
        with patch.dict(sys.modules, {"aiomqtt": fake_mod}):
            client = get_event_bridge_client(EventTransport.mqtt)
        assert isinstance(client, MqttEventBridgeClient)

    def test_mqtt_falls_back_when_not_importable(self) -> None:
        with patch.dict(sys.modules, {"aiomqtt": None}):
            client = get_event_bridge_client(EventTransport.mqtt)
        assert isinstance(client, StubEventBridgeClient)

    def test_all_returned_clients_are_event_bridge_clients(self) -> None:
        for transport in (
            EventTransport.kafka,
            EventTransport.rabbitmq,
            EventTransport.amqp,
            EventTransport.mqtt,
        ):
            client = get_event_bridge_client(transport)
            assert isinstance(client, EventBridgeClient)


# ---------------------------------------------------------------------------
# Cross-client config compatibility tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEventBridgeConfigCompatibility:
    """All broker clients accept the same EventBridgeConfig model."""

    @pytest.mark.asyncio
    async def test_kafka_accepts_event_bridge_config(self) -> None:
        mod: Any = types.ModuleType("aiokafka")
        producer = AsyncMock()
        mod.AIOKafkaProducer = MagicMock(return_value=producer)

        client = KafkaEventBridgeClient()
        config = EventBridgeConfig(
            broker_url="kafka://k-host:9092",
            topic="t",
            group_id="g1",
            metadata={"custom": "value"},
        )
        with patch.dict(sys.modules, {"aiokafka": mod}):
            await client.connect(config)
        assert client._bootstrap_servers == "k-host:9092"

    @pytest.mark.asyncio
    async def test_rabbitmq_accepts_event_bridge_config(self) -> None:
        mod: Any = types.ModuleType("aio_pika")
        conn = AsyncMock()
        conn.channel = AsyncMock(return_value=AsyncMock())
        mod.connect_robust = AsyncMock(return_value=conn)

        client = RabbitMQEventBridgeClient()
        config = EventBridgeConfig(
            broker_url="rabbitmq://r-host:5672",
            topic="q",
            queue="my-q",
            metadata={"vhost": "/"},
        )
        with patch.dict(sys.modules, {"aio_pika": mod}):
            await client.connect(config)
        assert client._connection is conn

    @pytest.mark.asyncio
    async def test_mqtt_accepts_event_bridge_config(self) -> None:
        mod: Any = types.ModuleType("aiomqtt")
        mod.Client = MagicMock()

        client = MqttEventBridgeClient()
        config = EventBridgeConfig(
            broker_url="mqtt://m-host:8883",
            topic="iot/data",
            metadata={"tls": True},
        )
        with patch.dict(sys.modules, {"aiomqtt": mod}):
            await client.connect(config)
        assert client._hostname == "m-host"
        assert client._port == 8883

    @pytest.mark.asyncio
    async def test_kafka_strips_scheme_from_broker_url(self) -> None:
        mod: Any = types.ModuleType("aiokafka")
        mod.AIOKafkaProducer = MagicMock(return_value=AsyncMock())

        client = KafkaEventBridgeClient()
        config = EventBridgeConfig(broker_url="kafka://a:9092", topic="t")
        with patch.dict(sys.modules, {"aiokafka": mod}):
            await client.connect(config)
        assert client._bootstrap_servers == "a:9092"

    @pytest.mark.asyncio
    async def test_rabbitmq_rewrites_scheme(self) -> None:
        mod: Any = types.ModuleType("aio_pika")
        conn = AsyncMock()
        conn.channel = AsyncMock(return_value=AsyncMock())
        mod.connect_robust = AsyncMock(return_value=conn)

        client = RabbitMQEventBridgeClient()
        config = EventBridgeConfig(broker_url="rabbitmq://host:5672", topic="t")
        with patch.dict(sys.modules, {"aio_pika": mod}):
            await client.connect(config)
        mod.connect_robust.assert_awaited_once_with("amqp://host:5672")

    @pytest.mark.asyncio
    async def test_mqtt_strips_scheme_from_broker_url(self) -> None:
        mod: Any = types.ModuleType("aiomqtt")
        mod.Client = MagicMock()

        client = MqttEventBridgeClient()
        config = EventBridgeConfig(broker_url="mqtt://myhost:9999", topic="t")
        with patch.dict(sys.modules, {"aiomqtt": mod}):
            await client.connect(config)
        assert client._hostname == "myhost"
        assert client._port == 9999
