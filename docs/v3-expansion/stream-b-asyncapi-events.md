# Stream B: AsyncAPI + Event Systems

## Goal

Turn event-driven systems into observable, controllable MCP capabilities.  An agent should be able to subscribe to a Kafka topic, peek at recent messages, publish events, and manage webhooks — all through MCP tools with governance.

## Scope

- **In scope:** AsyncAPI 2.x/3.x spec parsing, event observation bridge (Kafka/RabbitMQ/Pulsar), webhook management adapter, SSE subscription enhancement.
- **Out of scope:** Native Kafka/RabbitMQ/Pulsar consumers as MCP transports (deferred), bidirectional WebSocket streaming, custom protocol adapters.

---

## Architecture

```
AsyncAPI spec (YAML/JSON)
        │
        ▼
┌────────────────────┐
│ AsyncAPIExtractor  │  libs/extractors/asyncapi.py
│ protocol_name=     │
│   "asyncapi"       │
│ detect() → 0.0-1.0│
│ extract() → ServiceIR│
└────────┬───────────┘
         │
         ▼ ServiceIR (protocol="asyncapi")
         │   Operations: observe_*, publish_*, manage_webhook_*
         │   EventDescriptors: channels with transport + direction
         │
         ├─────────────────────────────────┐
         ▼                                 ▼
┌────────────────────┐          ┌────────────────────┐
│ EventBridge        │          │ WebhookAdapter     │
│ apps/mcp_runtime/  │          │ apps/mcp_runtime/  │
│ event_bridge.py    │          │ webhook_adapter.py │
│                    │          │                    │
│ observe: peek/poll │          │ register/list/     │
│ publish: send msg  │          │ delete webhooks    │
└────────────────────┘          └────────────────────┘
```

## IR Extension

Extend `EventTransport` enum:
```python
class EventTransport(StrEnum):
    # ... existing ...
    kafka = "kafka"
    rabbitmq = "rabbitmq"
    pulsar = "pulsar"
```

New config model:
```python
class EventBridgeConfig(BaseModel):
    """Runtime configuration for event bridge observation/control."""

    transport: EventTransport
    broker_url_ref: str = Field(min_length=1, description="Secret ref for broker connection string")
    topic_or_queue: str = Field(min_length=1)
    consumer_group: str | None = None
    max_peek_messages: int = Field(default=10, gt=0, le=100)
    publish_allowed: bool = False
    serialization: Literal["json", "avro", "protobuf", "raw"] = "json"
    schema_registry_url: str | None = None
```

On `EventDescriptor`:
```python
event_bridge: EventBridgeConfig | None = None
```

## New Files

| File | Purpose |
|---|---|
| `libs/extractors/asyncapi.py` | AsyncAPI 2.x/3.x spec parser → ServiceIR |
| `apps/mcp_runtime/event_bridge.py` | Event observation/control runtime (peek, poll, publish) |
| `apps/mcp_runtime/webhook_adapter.py` | Webhook registration/management adapter |
| `tests/fixtures/asyncapi_specs/` | AsyncAPI spec fixtures (pet store events, order events, IoT) |
| `libs/extractors/tests/test_asyncapi.py` | Unit tests for AsyncAPI extractor |
| `tests/integration/test_event_bridge.py` | Integration tests for event bridge |
| `tests/integration/test_webhook_adapter.py` | Integration tests for webhook management |

## Modified Files

| File | Change |
|---|---|
| `libs/ir/models.py` | Add `EventBridgeConfig`, extend `EventTransport`, add field on `EventDescriptor` |
| `libs/extractors/__init__.py` | Register AsyncAPI extractor |
| `libs/validator/capability_matrix.py` | Add `asyncapi` row |

---

## Task Backlog

### EVT-001: EventBridgeConfig model + EventTransport extension
**File:** `libs/ir/models.py`
**What:** Add `EventBridgeConfig` Pydantic model. Extend `EventTransport` enum with `kafka`, `rabbitmq`, `pulsar`. Add `event_bridge: EventBridgeConfig | None = None` to `EventDescriptor`. Add coherence validator: `event_bridge` requires transport in {kafka, rabbitmq, pulsar}.
**Tests:** Unit tests — valid config, transport mismatch errors, serialization validation.
**Exit:** `pytest libs/ir/tests/ -q` green, `mypy` clean.

### EVT-002: AsyncAPI spec parser core
**File:** `libs/extractors/asyncapi.py`
**What:** Parse AsyncAPI 2.x and 3.x YAML/JSON specs:
- Extract `info` → service metadata
- Extract `channels` → event descriptors with topics, direction (publish/subscribe)
- Extract `messages` → message schemas
- Extract `servers` → broker connection info
- Handle both 2.x (`channels[path].subscribe/publish`) and 3.x (`channels[name].messages`, `operations`) formats.
**Tests:** Parse at least 3 AsyncAPI spec fixtures (2.6 and 3.0).
**Exit:** Parser handles both spec versions without error.

### EVT-003: AsyncAPI extractor detect()
**File:** `libs/extractors/asyncapi.py`
**What:** Implement `AsyncAPIExtractor.detect(source: SourceConfig) -> float`:
- `source.hints.get("protocol") == "asyncapi"` → 1.0
- File content contains `asyncapi: "2.` or `asyncapi: "3.` → 0.95
- File path ends in `asyncapi.yaml` / `asyncapi.json` → 0.85
- Content has `channels:` and `info:` keys → 0.5
- Otherwise → 0.0
**Tests:** `libs/extractors/tests/test_asyncapi.py::TestDetect`
**Exit:** All detection scenarios covered.

### EVT-004: AsyncAPI extractor extract()
**File:** `libs/extractors/asyncapi.py`
**What:** Implement `AsyncAPIExtractor.extract(source: SourceConfig) -> ServiceIR`:
- For each channel, generate MCP tool operations:
  - `observe_{channel}` — peek at recent messages (read-only, risk=safe)
  - `publish_to_{channel}` — send a message (write, risk=cautious)
  - `get_{channel}_schema` — retrieve the message schema (read-only, risk=safe)
- Generate `EventDescriptor` entries for each channel with transport, direction, support level
- Message schemas become operation params
- Set `protocol="asyncapi"` on ServiceIR
**Tests:** Full extraction from 2.x and 3.x specs, verify operation shapes.
**Exit:** Extracted IR validates against ServiceIR model, operations match expected patterns.

### EVT-005: Event bridge — observation pattern
**File:** `apps/mcp_runtime/event_bridge.py`
**What:** `EventBridgeObserver` class:
- `peek(topic, max_messages, offset?)` — read recent messages without committing
- `poll(topic, timeout_ms, max_messages)` — consumer poll
- `get_topic_info(topic)` — metadata: partition count, latest offsets, consumer lag
- Backend-agnostic interface with Kafka implementation first
- Uses `event_bridge.broker_url_ref` to resolve connection from secrets
**Security:** Read-only by default. `publish_allowed` must be true on the EventBridgeConfig.
**Tests:** Mock Kafka client, verify peek/poll/metadata flows.
**Exit:** Observer pattern works with mocked broker.

### EVT-006: Event bridge — publish pattern
**File:** `apps/mcp_runtime/event_bridge.py`
**What:** `EventBridgePublisher` class:
- `publish(topic, key, value, headers?)` — publish a single message
- Validates message against schema if `schema_registry_url` is configured
- Only available when `event_bridge.publish_allowed=True`
- JSON and raw serialization support (Avro/Protobuf deferred)
**Risk:** Publish operations always classified as `cautious` minimum.
**Tests:** Mock publisher, schema validation test.
**Exit:** Publish with and without schema validation.

### EVT-007: Webhook management adapter
**File:** `apps/mcp_runtime/webhook_adapter.py`
**What:** `WebhookAdapter` class for managing webhook registrations:
- `register_webhook(url, events, secret?)` — register a new webhook endpoint
- `list_webhooks()` — list active webhook registrations
- `delete_webhook(id)` — remove a webhook registration
- `get_webhook_deliveries(id, limit?)` — check recent delivery status
- Works with upstream APIs that have webhook management endpoints (GitHub, Stripe pattern)
**Tests:** Mock HTTP responses for webhook CRUD operations.
**Exit:** Full CRUD lifecycle test passes.

### EVT-008: Runtime tool registration for events
**File:** `apps/mcp_runtime/loader.py`
**What:** Extend `register_ir_tools` to handle:
- Operations backed by `event_bridge` descriptors → wire to `EventBridgeObserver`/`EventBridgePublisher`
- Operations for webhook management → wire to `WebhookAdapter`
**Tests:** Integration test: async IR → tool registration → tool list includes event tools.
**Exit:** Event-backed tools appear in `mcp.list_tools()`.

### EVT-009: Capability matrix update
**File:** `libs/validator/capability_matrix.py`
**What:** Add `asyncapi` row to `_CAPABILITY_ROWS` and `_CAPABILITY_ORDER`.
**Tests:** Matrix includes asyncapi row with correct capability flags.
**Exit:** Matrix renders correctly.

### EVT-010: End-to-end integration test
**Files:** `tests/integration/test_event_bridge.py`
**What:** Full path test:
1. Parse an AsyncAPI 2.6 spec fixture (order events with Kafka channel)
2. Extract → ServiceIR with event operations
3. Register in MCP runtime with mocked Kafka
4. Call `observe_orders` → get peek result
5. Call `publish_to_orders` → verify message sent to mock
6. Verify risk classification (observe=safe, publish=cautious)
**Exit:** Integration test green.

---

## Key Design Decisions

1. **Bridge pattern, not native consumer.** The event bridge peeks/polls on demand — it does NOT maintain a long-running consumer loop. This keeps the MCP tool model (request → response) intact.
2. **Observation before action.** By default, all event channels are observe-only. Publish requires explicit `publish_allowed: true` in the config.
3. **AsyncAPI 2.x and 3.x both supported.** The spec had a major structural change between versions. We handle both with a version-switching parser.
4. **Schema validation on publish is optional.** If `schema_registry_url` is set, we validate. Otherwise, we trust the caller. This keeps the happy path simple.
5. **Webhook management is a separate adapter.** It doesn't go through the event bridge — it uses standard HTTP against the upstream API's webhook management endpoints.
