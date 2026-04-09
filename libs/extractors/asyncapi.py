"""AsyncAPI extractor — parses AsyncAPI v2.x and v3.x specs into ServiceIR."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import yaml

from libs.extractors.base import SourceConfig
from libs.extractors.utils import get_content, slugify
from libs.ir.models import (
    AuthConfig,
    AuthType,
    ErrorResponse,
    ErrorSchema,
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
)

logger = logging.getLogger(__name__)

_PROTOCOL_TRANSPORT_MAP: dict[str, EventTransport] = {
    "kafka": EventTransport.kafka,
    "amqp": EventTransport.amqp,
    "amqps": EventTransport.amqp,
    "mqtt": EventTransport.mqtt,
    "mqtts": EventTransport.mqtt,
    "nats": EventTransport.nats,
    "pulsar": EventTransport.pulsar,
    "rabbitmq": EventTransport.rabbitmq,
    "ws": EventTransport.websocket,
    "wss": EventTransport.websocket,
    "http": EventTransport.webhook,
    "https": EventTransport.webhook,
    "sse": EventTransport.sse,
}

_JSON_SCHEMA_TYPE_MAP: dict[str, str] = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}


def _map_type(schema: dict[str, Any]) -> str:
    raw = schema.get("type", "object")
    return _JSON_SCHEMA_TYPE_MAP.get(raw, "object")


def _json_schema_for_param(schema: dict[str, Any], ir_type: str) -> dict[str, Any] | None:
    """Build json_schema for an AsyncAPI param when it carries structure."""
    if ir_type == "object":
        properties = schema.get("properties")
        if properties:
            result: dict[str, Any] = {"type": "object", "properties": properties}
            required = schema.get("required")
            if required:
                result["required"] = required
            return result
        return None
    if ir_type == "array":
        items = schema.get("items", {})
        if not items:
            items = {"type": "string"}
        return {"type": "array", "items": items}
    return None


def _params_from_payload(
    payload: dict[str, Any],
    *,
    required_fields: set[str] | None = None,
) -> list[Param]:
    """Extract Param list from a JSON Schema payload (object with properties)."""
    if not isinstance(payload, dict) or payload.get("type") != "object":
        return []
    properties = payload.get("properties", {})
    if not isinstance(properties, dict):
        return []
    req = required_fields or set(payload.get("required", []))
    params: list[Param] = []
    for name, schema in properties.items():
        if not isinstance(schema, dict):
            continue
        ir_type = _map_type(schema)
        params.append(
            Param(
                name=name,
                type=ir_type,
                required=name in req,
                description=schema.get("description", ""),
                default=schema.get("default"),
                json_schema=_json_schema_for_param(schema, ir_type),
            )
        )
    return params


def _resolve_transport(protocol: str | None) -> EventTransport:
    if protocol is None:
        return EventTransport.async_event
    return _PROTOCOL_TRANSPORT_MAP.get(protocol.lower(), EventTransport.async_event)


class AsyncAPIExtractor:
    """Extract event-driven operations from AsyncAPI v2.x and v3.x specs."""

    protocol_name: str = "asyncapi"

    # ── detection ──────────────────────────────────────────────────────────

    def detect(self, source: SourceConfig) -> float:
        if source.hints.get("protocol") == "asyncapi":
            return 0.95

        # Check file extension first (cheap)
        for path_field in (source.file_path, source.url):
            if path_field:
                lower = path_field.lower()
                if any(
                    lower.endswith(ext)
                    for ext in (".asyncapi.yaml", ".asyncapi.json", ".asyncapi.yml")
                ):
                    return 0.88

        content = get_content(source)
        if content is None:
            return 0.0

        try:
            data = self._parse_content(content)
        except Exception:
            return 0.0

        if isinstance(data, dict) and "asyncapi" in data:
            return 0.92

        return 0.0

    # ── extraction ─────────────────────────────────────────────────────────

    def extract(self, source: SourceConfig) -> ServiceIR:
        content = get_content(source)
        if content is None:
            raise ValueError("AsyncAPI extractor: could not read source content")

        data = self._parse_content(content)
        if not isinstance(data, dict) or "asyncapi" not in data:
            raise ValueError("AsyncAPI extractor: content is not a valid AsyncAPI document")

        source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        spec_version = str(data.get("asyncapi", ""))
        is_v3 = spec_version.startswith("3.")

        info = data.get("info", {})
        title = info.get("title", "AsyncAPI Service")
        description = info.get("description", "")
        version = info.get("version", "0.0.0")
        service_name = slugify(title, camel_case=True)

        # Resolve server / broker config
        servers = data.get("servers", {})
        server_protocol, broker_url = self._resolve_server(servers, is_v3=is_v3)
        transport = _resolve_transport(server_protocol)

        operations: list[Operation] = []
        event_descriptors: list[EventDescriptor] = []

        if is_v3:
            self._extract_v3(
                data,
                service_name=service_name,
                transport=transport,
                broker_url=broker_url,
                server_protocol=server_protocol,
                operations=operations,
                event_descriptors=event_descriptors,
            )
        else:
            self._extract_v2(
                data,
                service_name=service_name,
                transport=transport,
                broker_url=broker_url,
                server_protocol=server_protocol,
                operations=operations,
                event_descriptors=event_descriptors,
            )

        metadata: dict[str, Any] = {
            "asyncapi_version": spec_version,
            "service_version": version,
            "channel_count": len(event_descriptors),
            "operation_count": len(operations),
        }
        if server_protocol:
            metadata["broker_protocol"] = server_protocol

        return ServiceIR(
            source_url=source.url,
            source_hash=source_hash,
            protocol="asyncapi",
            service_name=service_name,
            service_description=description,
            base_url=broker_url,
            auth=AuthConfig(type=AuthType.none),
            operations=operations,
            event_descriptors=event_descriptors,
            metadata=metadata,
        )

    # ── v2.x extraction ───────────────────────────────────────────────────

    def _extract_v2(
        self,
        data: dict[str, Any],
        *,
        service_name: str,
        transport: EventTransport,
        broker_url: str,
        server_protocol: str | None,
        operations: list[Operation],
        event_descriptors: list[EventDescriptor],
    ) -> None:
        channels = data.get("channels", {})
        if not isinstance(channels, dict):
            return

        for channel_name, channel_def in channels.items():
            if not isinstance(channel_def, dict):
                continue

            channel_slug = slugify(channel_name)
            has_subscribe = "subscribe" in channel_def
            has_publish = "publish" in channel_def

            # subscribe → observe (inbound from broker perspective)
            if has_subscribe:
                sub = channel_def["subscribe"]
                payload = self._extract_v2_message_payload(sub)
                op_id_str = sub.get("operationId") or f"observe_{channel_slug}"
                operations.append(
                    self._build_observe_operation(
                        service_name=service_name,
                        channel_slug=channel_slug,
                        channel_name=channel_name,
                        payload=payload,
                        operation_id=op_id_str,
                    )
                )

            # publish → publish (outbound)
            if has_publish:
                pub = channel_def["publish"]
                payload = self._extract_v2_message_payload(pub)
                op_id_str = pub.get("operationId") or f"publish_{channel_slug}"
                operations.append(
                    self._build_publish_operation(
                        service_name=service_name,
                        channel_slug=channel_slug,
                        channel_name=channel_name,
                        payload=payload,
                        operation_id=op_id_str,
                    )
                )

            # Determine direction for event descriptor
            if has_subscribe and has_publish:
                direction = EventDirection.bidirectional
            elif has_subscribe:
                direction = EventDirection.inbound
            else:
                direction = EventDirection.outbound

            bridge = EventBridgeConfig(
                broker_url=broker_url,
                topic=channel_name,
                protocol_version=server_protocol,
            )

            event_descriptors.append(
                EventDescriptor(
                    id=f"{service_name}_event_{channel_slug}",
                    name=channel_name,
                    description=channel_def.get("description", ""),
                    transport=transport,
                    direction=direction,
                    support=EventSupportLevel.planned,
                    channel=channel_name,
                    event_bridge=bridge,
                )
            )

    @staticmethod
    def _extract_v2_message_payload(operation_def: dict[str, Any]) -> dict[str, Any]:
        message = operation_def.get("message", {})
        if not isinstance(message, dict):
            return {}
        payload = message.get("payload", {})
        return payload if isinstance(payload, dict) else {}

    # ── v3.x extraction ───────────────────────────────────────────────────

    def _extract_v3(
        self,
        data: dict[str, Any],
        *,
        service_name: str,
        transport: EventTransport,
        broker_url: str,
        server_protocol: str | None,
        operations: list[Operation],
        event_descriptors: list[EventDescriptor],
    ) -> None:
        channels = data.get("channels", {})
        ops = data.get("operations", {})

        if not isinstance(channels, dict):
            channels = {}
        if not isinstance(ops, dict):
            ops = {}

        # Build channel address map
        channel_addresses: dict[str, str] = {}
        channel_messages: dict[str, dict[str, Any]] = {}
        for ch_name, ch_def in channels.items():
            if not isinstance(ch_def, dict):
                continue
            raw_address = ch_def.get("address", ch_name)
            channel_addresses[ch_name] = raw_address if isinstance(raw_address, str) else ch_name
            # Collect messages from the channel
            msgs = ch_def.get("messages", {})
            if isinstance(msgs, dict):
                for _msg_name, msg_def in msgs.items():
                    if isinstance(msg_def, dict):
                        payload = msg_def.get("payload", {})
                        channel_messages[ch_name] = payload if isinstance(payload, dict) else {}
                        break  # use first message payload

        # Track which channels have receive/send
        channel_has_receive: set[str] = set()
        channel_has_send: set[str] = set()

        for op_name, op_def in ops.items():
            if not isinstance(op_def, dict):
                continue

            action = op_def.get("action", "")
            channel_ref = op_def.get("channel", {})
            ch_name = self._resolve_v3_channel_ref(channel_ref, channels)
            if ch_name is None:
                continue

            address = channel_addresses.get(ch_name, ch_name)
            channel_slug = slugify(address)
            payload = channel_messages.get(ch_name, {})

            if action == "receive":
                channel_has_receive.add(ch_name)
                operations.append(
                    self._build_observe_operation(
                        service_name=service_name,
                        channel_slug=channel_slug,
                        channel_name=address,
                        payload=payload,
                        operation_id=op_name,
                    )
                )
            elif action == "send":
                channel_has_send.add(ch_name)
                operations.append(
                    self._build_publish_operation(
                        service_name=service_name,
                        channel_slug=channel_slug,
                        channel_name=address,
                        payload=payload,
                        operation_id=op_name,
                    )
                )

        # Create event descriptors for each channel
        for ch_name, ch_def in channels.items():
            if not isinstance(ch_def, dict):
                continue
            address = channel_addresses.get(ch_name) or ch_name
            channel_slug = slugify(address)

            has_recv = ch_name in channel_has_receive
            has_send = ch_name in channel_has_send
            if has_recv and has_send:
                direction = EventDirection.bidirectional
            elif has_recv:
                direction = EventDirection.inbound
            elif has_send:
                direction = EventDirection.outbound
            else:
                direction = EventDirection.inbound

            bridge = EventBridgeConfig(
                broker_url=broker_url,
                topic=address,
                protocol_version=server_protocol,
            )

            event_descriptors.append(
                EventDescriptor(
                    id=f"{service_name}_event_{channel_slug}",
                    name=address,
                    description=ch_def.get("description", "")
                    if isinstance(ch_def.get("description", ""), str)
                    else "",
                    transport=transport,
                    direction=direction,
                    support=EventSupportLevel.planned,
                    channel=address,
                    event_bridge=bridge,
                )
            )

    @staticmethod
    def _resolve_v3_channel_ref(
        channel_ref: Any,
        channels: dict[str, Any],
    ) -> str | None:
        """Resolve a v3 channel reference (``$ref`` or inline) to a channel name."""
        if isinstance(channel_ref, dict):
            ref = channel_ref.get("$ref", "")
            if isinstance(ref, str) and ref.startswith("#/channels/"):
                return ref.split("/")[-1]
        if isinstance(channel_ref, str) and channel_ref in channels:
            return channel_ref
        return None

    # ── operation builders ─────────────────────────────────────────────────

    @staticmethod
    def _build_observe_operation(
        *,
        service_name: str,
        channel_slug: str,
        channel_name: str,
        payload: dict[str, Any],
        operation_id: str,
    ) -> Operation:
        return Operation(
            id=f"{service_name}_observe_{channel_slug}",
            name=f"observe_{channel_slug}",
            description=f"Observe events on channel {channel_name}",
            method=None,
            path=None,
            params=_params_from_payload(payload),
            risk=RiskMetadata(
                risk_level=RiskLevel.safe,
                writes_state=False,
                destructive=False,
                confidence=0.9,
            ),
            tags=["asyncapi", "observe", channel_slug],
            error_schema=ErrorSchema(
                responses=[
                    ErrorResponse(
                        error_code="broker_unavailable",
                        description="Message broker is unavailable or connection failed",
                        error_body_schema={
                            "type": "object",
                            "properties": {
                                "error": {"type": "string"},
                                "broker_url": {"type": "string"},
                            },
                        },
                    ),
                    ErrorResponse(
                        error_code="timeout",
                        description="Observation timed out waiting for messages",
                        error_body_schema={
                            "type": "object",
                            "properties": {
                                "error": {"type": "string"},
                                "timeout_seconds": {"type": "number"},
                            },
                        },
                    ),
                ],
                default_error_schema={
                    "type": "object",
                    "properties": {
                        "error": {"type": "string"},
                        "_stub": {"type": "boolean"},
                    },
                },
            ),
        )

    @staticmethod
    def _build_publish_operation(
        *,
        service_name: str,
        channel_slug: str,
        channel_name: str,
        payload: dict[str, Any],
        operation_id: str,
    ) -> Operation:
        return Operation(
            id=f"{service_name}_publish_{channel_slug}",
            name=f"publish_{channel_slug}",
            description=f"Publish event to channel {channel_name}",
            method=None,
            path=None,
            params=_params_from_payload(payload),
            risk=RiskMetadata(
                risk_level=RiskLevel.cautious,
                writes_state=True,
                destructive=False,
                confidence=0.9,
            ),
            tags=["asyncapi", "publish", channel_slug],
            error_schema=ErrorSchema(
                responses=[
                    ErrorResponse(
                        error_code="broker_unavailable",
                        description="Message broker is unavailable or connection failed",
                        error_body_schema={
                            "type": "object",
                            "properties": {
                                "error": {"type": "string"},
                                "broker_url": {"type": "string"},
                            },
                        },
                    ),
                    ErrorResponse(
                        error_code="timeout",
                        description="Observation timed out waiting for messages",
                        error_body_schema={
                            "type": "object",
                            "properties": {
                                "error": {"type": "string"},
                                "timeout_seconds": {"type": "number"},
                            },
                        },
                    ),
                    ErrorResponse(
                        error_code="publish_failed",
                        description="Message could not be delivered to the broker",
                        error_body_schema={
                            "type": "object",
                            "properties": {
                                "error": {"type": "string"},
                                "topic": {"type": "string"},
                            },
                        },
                    ),
                ],
                default_error_schema={
                    "type": "object",
                    "properties": {
                        "error": {"type": "string"},
                        "_stub": {"type": "boolean"},
                    },
                },
            ),
        )

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_content(content: str) -> Any:
        """Parse content as YAML (which is a superset of JSON)."""
        return yaml.safe_load(content)

    @staticmethod
    def _resolve_server(
        servers: Any,
        *,
        is_v3: bool,
    ) -> tuple[str | None, str]:
        """Return ``(protocol, broker_url)`` from the first server entry."""
        if not isinstance(servers, dict) or not servers:
            return None, "localhost"

        server = next(iter(servers.values()))
        if not isinstance(server, dict):
            return None, "localhost"

        protocol = server.get("protocol")

        if is_v3:
            host = server.get("host", "localhost")
            pathname = server.get("pathname", "")
            scheme = protocol or "tcp"
            broker_url = f"{scheme}://{host}{pathname}"
        else:
            broker_url = server.get("url", "localhost")

        return protocol, broker_url
