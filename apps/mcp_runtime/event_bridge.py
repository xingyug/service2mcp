"""Pluggable event bridge for message broker interaction."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from libs.ir.models import EventBridgeConfig, EventTransport

logger = logging.getLogger(__name__)


class EventBridgeClient(ABC):
    """Abstract client for message broker interaction."""

    @abstractmethod
    async def connect(self, config: EventBridgeConfig) -> None: ...

    @abstractmethod
    async def observe(
        self,
        topic: str,
        max_messages: int = 10,
        timeout: float = 5.0,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def publish(self, topic: str, message: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    async def disconnect(self) -> None: ...


class StubEventBridgeClient(EventBridgeClient):
    """Stub implementation that returns placeholder responses.

    Used when no real broker client is available.
    """

    async def connect(self, config: EventBridgeConfig) -> None:
        logger.info("Stub event bridge connect: %s", config.broker_url)

    async def observe(
        self,
        topic: str,
        max_messages: int = 10,
        timeout: float = 5.0,
    ) -> list[dict[str, Any]]:
        return [
            {
                "_stub": True,
                "topic": topic,
                "message": "No broker client configured. Install appropriate broker library.",
            }
        ]

    async def publish(self, topic: str, message: dict[str, Any]) -> dict[str, Any]:
        return {
            "_stub": True,
            "topic": topic,
            "status": "not_delivered",
            "message": "No broker client configured.",
        }

    async def disconnect(self) -> None:
        pass


class KafkaEventBridgeClient(EventBridgeClient):
    """Real Kafka client using aiokafka."""

    def __init__(self) -> None:
        self._producer: Any = None
        self._consumer: Any = None
        self._bootstrap_servers: str = ""

    async def connect(self, config: EventBridgeConfig) -> None:
        try:
            from aiokafka import AIOKafkaProducer
        except ImportError as exc:
            raise RuntimeError(
                "aiokafka is required for Kafka transport. "
                "Install with: pip install 'service2mcp[brokers]'"
            ) from exc

        self._bootstrap_servers = config.broker_url.replace("kafka://", "")
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )
        await self._producer.start()
        logger.info("Kafka producer connected to %s", self._bootstrap_servers)

    async def observe(
        self,
        topic: str,
        max_messages: int = 10,
        timeout: float = 5.0,
    ) -> list[dict[str, Any]]:
        try:
            from aiokafka import AIOKafkaConsumer
        except ImportError as exc:
            raise RuntimeError("aiokafka is required for Kafka observe") from exc

        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=self._bootstrap_servers,
            auto_offset_reset="latest",
            consumer_timeout_ms=int(timeout * 1000),
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        )
        await consumer.start()
        messages: list[dict[str, Any]] = []
        try:
            async for msg in consumer:
                messages.append(
                    {
                        "topic": msg.topic,
                        "partition": msg.partition,
                        "offset": msg.offset,
                        "key": msg.key.decode("utf-8") if msg.key else None,
                        "value": msg.value,
                        "timestamp": msg.timestamp,
                    }
                )
                if len(messages) >= max_messages:
                    break
        finally:
            await consumer.stop()
        return messages

    async def publish(self, topic: str, message: dict[str, Any]) -> dict[str, Any]:
        if self._producer is None:
            raise RuntimeError("Kafka producer not connected. Call connect() first.")
        result = await self._producer.send_and_wait(topic, value=message)
        return {
            "status": "delivered",
            "topic": result.topic,
            "partition": result.partition,
            "offset": result.offset,
        }

    async def disconnect(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None
        logger.info("Kafka client disconnected")


class RabbitMQEventBridgeClient(EventBridgeClient):
    """Real RabbitMQ client using aio-pika."""

    def __init__(self) -> None:
        self._connection: Any = None
        self._channel: Any = None

    async def connect(self, config: EventBridgeConfig) -> None:
        try:
            import aio_pika
        except ImportError as exc:
            raise RuntimeError(
                "aio-pika is required for RabbitMQ/AMQP transport. "
                "Install with: pip install 'service2mcp[brokers]'"
            ) from exc

        url = config.broker_url
        if url.startswith("rabbitmq://"):
            url = url.replace("rabbitmq://", "amqp://", 1)
        self._connection = await aio_pika.connect_robust(url)
        self._channel = await self._connection.channel()
        logger.info("RabbitMQ connected to %s", config.broker_url)

    async def observe(
        self,
        topic: str,
        max_messages: int = 10,
        timeout: float = 5.0,
    ) -> list[dict[str, Any]]:
        if self._channel is None:
            raise RuntimeError("RabbitMQ channel not connected. Call connect() first.")

        import asyncio

        queue = await self._channel.declare_queue(topic, passive=True)
        messages: list[dict[str, Any]] = []
        deadline = asyncio.get_event_loop().time() + timeout
        while len(messages) < max_messages:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                msg = await asyncio.wait_for(queue.get(no_ack=True), timeout=remaining)
                messages.append(
                    {
                        "queue": topic,
                        "body": json.loads(msg.body.decode("utf-8")),
                        "message_id": msg.message_id,
                        "routing_key": msg.routing_key,
                    }
                )
            except TimeoutError:
                break
            except Exception:
                break
        return messages

    async def publish(self, topic: str, message: dict[str, Any]) -> dict[str, Any]:
        if self._channel is None:
            raise RuntimeError("RabbitMQ channel not connected. Call connect() first.")

        import aio_pika

        exchange = await self._channel.declare_exchange(
            topic, aio_pika.ExchangeType.FANOUT, durable=True
        )
        msg = aio_pika.Message(body=json.dumps(message).encode("utf-8"))
        await exchange.publish(msg, routing_key="")
        return {"status": "delivered", "exchange": topic}

    async def disconnect(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
            self._channel = None
        logger.info("RabbitMQ client disconnected")


class MqttEventBridgeClient(EventBridgeClient):
    """Real MQTT client using aiomqtt."""

    def __init__(self) -> None:
        self._hostname: str = ""
        self._port: int = 1883
        self._client_module: Any = None

    async def connect(self, config: EventBridgeConfig) -> None:
        try:
            import aiomqtt

            self._client_module = aiomqtt
        except ImportError as exc:
            raise RuntimeError(
                "aiomqtt is required for MQTT transport. "
                "Install with: pip install 'service2mcp[brokers]'"
            ) from exc

        url = config.broker_url.replace("mqtt://", "")
        parts = url.split(":")
        self._hostname = parts[0]
        if len(parts) > 1:
            try:
                self._port = int(parts[1])
            except ValueError:
                self._port = 1883
        logger.info("MQTT configured for %s:%d", self._hostname, self._port)

    async def observe(
        self,
        topic: str,
        max_messages: int = 10,
        timeout: float = 5.0,
    ) -> list[dict[str, Any]]:
        if self._client_module is None:
            raise RuntimeError("MQTT client not configured. Call connect() first.")

        import asyncio

        messages: list[dict[str, Any]] = []
        try:
            async with self._client_module.Client(self._hostname, self._port) as client:
                await client.subscribe(topic)
                deadline = asyncio.get_event_loop().time() + timeout
                async for msg in client.messages:
                    payload = msg.payload
                    try:
                        body = json.loads(
                            payload.decode("utf-8") if isinstance(payload, bytes) else payload
                        )
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        body = (
                            payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
                        )
                    messages.append(
                        {
                            "topic": str(msg.topic),
                            "payload": body,
                            "qos": msg.qos,
                        }
                    )
                    if len(messages) >= max_messages:
                        break
                    if asyncio.get_event_loop().time() >= deadline:
                        break
        except Exception as exc:
            logger.warning("MQTT observe error: %s", exc)
        return messages

    async def publish(self, topic: str, message: dict[str, Any]) -> dict[str, Any]:
        if self._client_module is None:
            raise RuntimeError("MQTT client not configured. Call connect() first.")

        async with self._client_module.Client(self._hostname, self._port) as client:
            await client.publish(topic, json.dumps(message).encode("utf-8"))
        return {"status": "delivered", "topic": topic}

    async def disconnect(self) -> None:
        logger.info("MQTT client disconnected")


def get_event_bridge_client(transport: EventTransport) -> EventBridgeClient:
    """Factory: return an appropriate broker client for the transport, falling back to stub."""
    if transport in (EventTransport.kafka,):
        try:
            import aiokafka  # noqa: F401  # pyright: ignore[reportUnusedImport]

            logger.info("Using KafkaEventBridgeClient for transport=%s", transport.value)
            return KafkaEventBridgeClient()
        except ImportError:
            pass

    if transport in (EventTransport.rabbitmq, EventTransport.amqp):
        try:
            import aio_pika  # noqa: F401  # pyright: ignore[reportUnusedImport]

            logger.info("Using RabbitMQEventBridgeClient for transport=%s", transport.value)
            return RabbitMQEventBridgeClient()
        except ImportError:
            pass

    if transport in (EventTransport.mqtt,):
        try:
            import aiomqtt  # noqa: F401  # pyright: ignore[reportUnusedImport]

            logger.info("Using MqttEventBridgeClient for transport=%s", transport.value)
            return MqttEventBridgeClient()
        except ImportError:
            pass

    logger.debug("Returning stub event bridge client for transport=%s", transport.value)
    return StubEventBridgeClient()
