"""Unit tests for the event bridge client and factory."""

from __future__ import annotations

from unittest.mock import patch

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


@pytest.fixture()
def stub_client() -> StubEventBridgeClient:
    return StubEventBridgeClient()


@pytest.fixture()
def bridge_config() -> EventBridgeConfig:
    return EventBridgeConfig(broker_url="kafka://localhost:9092", topic="test-topic")


class TestStubEventBridgeClient:
    @pytest.mark.asyncio()
    async def test_stub_client_connect(
        self, stub_client: StubEventBridgeClient, bridge_config: EventBridgeConfig
    ) -> None:
        # connect should not raise
        await stub_client.connect(bridge_config)

    @pytest.mark.asyncio()
    async def test_stub_client_observe(self, stub_client: StubEventBridgeClient) -> None:
        result = await stub_client.observe("my-topic")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["_stub"] is True
        assert result[0]["topic"] == "my-topic"

    @pytest.mark.asyncio()
    async def test_stub_client_publish(self, stub_client: StubEventBridgeClient) -> None:
        result = await stub_client.publish("my-topic", {"key": "value"})
        assert isinstance(result, dict)
        assert result["_stub"] is True
        assert result["topic"] == "my-topic"
        assert result["status"] == "not_delivered"

    @pytest.mark.asyncio()
    async def test_stub_client_disconnect(self, stub_client: StubEventBridgeClient) -> None:
        # disconnect should not raise
        await stub_client.disconnect()

    @pytest.mark.asyncio()
    async def test_stub_client_observe_respects_topic(
        self, stub_client: StubEventBridgeClient
    ) -> None:
        r1 = await stub_client.observe("topic-a")
        r2 = await stub_client.observe("topic-b")
        assert r1[0]["topic"] == "topic-a"
        assert r2[0]["topic"] == "topic-b"


class TestEventBridgeFactory:
    def test_get_client_returns_stub(self) -> None:
        client = get_event_bridge_client(EventTransport.kafka)
        assert isinstance(client, StubEventBridgeClient)
        assert isinstance(client, EventBridgeClient)

    def test_get_client_returns_stub_for_amqp(self) -> None:
        client = get_event_bridge_client(EventTransport.amqp)
        assert isinstance(client, StubEventBridgeClient)

    def test_get_client_returns_stub_for_mqtt(self) -> None:
        client = get_event_bridge_client(EventTransport.mqtt)
        assert isinstance(client, StubEventBridgeClient)


class TestKafkaEventBridgeClient:
    @pytest.mark.asyncio()
    async def test_connect_raises_without_aiokafka(self) -> None:
        client = KafkaEventBridgeClient()
        config = EventBridgeConfig(broker_url="kafka://localhost:9092", topic="test")
        with patch.dict("sys.modules", {"aiokafka": None}):
            with pytest.raises(RuntimeError, match="aiokafka is required"):
                await client.connect(config)

    @pytest.mark.asyncio()
    async def test_publish_raises_when_not_connected(self) -> None:
        client = KafkaEventBridgeClient()
        with pytest.raises(RuntimeError, match="not connected"):
            await client.publish("topic", {"key": "value"})

    @pytest.mark.asyncio()
    async def test_disconnect_is_safe_when_not_connected(self) -> None:
        client = KafkaEventBridgeClient()
        await client.disconnect()  # Should not raise


class TestRabbitMQEventBridgeClient:
    @pytest.mark.asyncio()
    async def test_connect_raises_without_aio_pika(self) -> None:
        client = RabbitMQEventBridgeClient()
        config = EventBridgeConfig(broker_url="rabbitmq://localhost:5672", topic="test")
        with patch.dict("sys.modules", {"aio_pika": None}):
            with pytest.raises(RuntimeError, match="aio-pika is required"):
                await client.connect(config)

    @pytest.mark.asyncio()
    async def test_publish_raises_when_not_connected(self) -> None:
        client = RabbitMQEventBridgeClient()
        with pytest.raises(RuntimeError, match="not connected"):
            await client.publish("topic", {"key": "value"})

    @pytest.mark.asyncio()
    async def test_observe_raises_when_not_connected(self) -> None:
        client = RabbitMQEventBridgeClient()
        with pytest.raises(RuntimeError, match="not connected"):
            await client.observe("topic")

    @pytest.mark.asyncio()
    async def test_disconnect_is_safe_when_not_connected(self) -> None:
        client = RabbitMQEventBridgeClient()
        await client.disconnect()  # Should not raise


class TestMqttEventBridgeClient:
    @pytest.mark.asyncio()
    async def test_connect_raises_without_aiomqtt(self) -> None:
        client = MqttEventBridgeClient()
        config = EventBridgeConfig(broker_url="mqtt://localhost:1883", topic="test")
        with patch.dict("sys.modules", {"aiomqtt": None}):
            with pytest.raises(RuntimeError, match="aiomqtt is required"):
                await client.connect(config)

    @pytest.mark.asyncio()
    async def test_publish_raises_when_not_connected(self) -> None:
        client = MqttEventBridgeClient()
        with pytest.raises(RuntimeError, match="not configured"):
            await client.publish("topic", {"key": "value"})

    @pytest.mark.asyncio()
    async def test_observe_raises_when_not_connected(self) -> None:
        client = MqttEventBridgeClient()
        with pytest.raises(RuntimeError, match="not configured"):
            await client.observe("topic")

    @pytest.mark.asyncio()
    async def test_disconnect_is_safe_when_not_connected(self) -> None:
        client = MqttEventBridgeClient()
        await client.disconnect()  # Should not raise


class TestEventBridgeFactoryWithBrokers:
    def test_get_client_returns_kafka_when_available(self) -> None:
        with patch.dict("sys.modules", {"aiokafka": __import__("unittest").mock.MagicMock()}):
            client = get_event_bridge_client(EventTransport.kafka)
            assert isinstance(client, KafkaEventBridgeClient)

    def test_get_client_returns_rabbitmq_when_available(self) -> None:
        with patch.dict("sys.modules", {"aio_pika": __import__("unittest").mock.MagicMock()}):
            client = get_event_bridge_client(EventTransport.rabbitmq)
            assert isinstance(client, RabbitMQEventBridgeClient)

    def test_get_client_returns_mqtt_when_available(self) -> None:
        with patch.dict("sys.modules", {"aiomqtt": __import__("unittest").mock.MagicMock()}):
            client = get_event_bridge_client(EventTransport.mqtt)
            assert isinstance(client, MqttEventBridgeClient)

    def test_get_client_falls_back_to_stub_kafka(self) -> None:
        with patch.dict("sys.modules", {"aiokafka": None}):
            client = get_event_bridge_client(EventTransport.kafka)
            assert isinstance(client, StubEventBridgeClient)

    def test_get_client_falls_back_to_stub_rabbitmq(self) -> None:
        with patch.dict("sys.modules", {"aio_pika": None}):
            client = get_event_bridge_client(EventTransport.rabbitmq)
            assert isinstance(client, StubEventBridgeClient)

    def test_get_client_falls_back_to_stub_mqtt(self) -> None:
        with patch.dict("sys.modules", {"aiomqtt": None}):
            client = get_event_bridge_client(EventTransport.mqtt)
            assert isinstance(client, StubEventBridgeClient)
