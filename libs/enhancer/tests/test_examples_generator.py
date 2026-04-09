"""Tests for LLM-assisted response example generation."""

from __future__ import annotations

import json

from libs.enhancer.enhancer import LLMResponse
from libs.enhancer.examples_generator import (
    ExamplesGenerator,
    _example_from_schema,
    extract_examples_from_spec,
    generate_from_schema,
    generate_synthetic_examples,
)
from libs.ir.models import (
    Operation,
    ResponseExample,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)

# ── Mock LLM Client ───────────────────────────────────────────────────────


class MockLLMClient:
    def __init__(self, response: str = "[]", fail: bool = False) -> None:
        self.response = response
        self.fail = fail
        self.calls: list[str] = []

    def complete(self, prompt: str, max_tokens: int = 4096) -> LLMResponse:
        self.calls.append(prompt)
        if self.fail:
            raise RuntimeError("LLM API error")
        return LLMResponse(content=self.response, input_tokens=100, output_tokens=50)


# ── Helpers ────────────────────────────────────────────────────────────────

_SAFE_RISK = RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9)


def _make_op(
    op_id: str = "op_1",
    *,
    response_schema: dict[str, object] | None = None,
    response_examples: list[ResponseExample] | None = None,
) -> Operation:
    return Operation(
        id=op_id,
        name=f"Test {op_id}",
        method="GET",
        path=f"/{op_id}",
        risk=_SAFE_RISK,
        response_schema=response_schema,
        response_examples=response_examples or [],
        enabled=True,
    )


def _make_ir(*ops: Operation) -> ServiceIR:
    return ServiceIR(
        source_hash="abc123",
        protocol="openapi",
        service_name="test-api",
        base_url="https://api.example.com",
        operations=list(ops),
    )


# ── generate_from_schema tests ────────────────────────────────────────────


def test_generate_from_schema_simple_object() -> None:
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
        },
    }
    result = generate_from_schema(schema)
    assert result == {"name": "example_string", "age": 0}


def test_generate_from_schema_array() -> None:
    schema = {
        "type": "array",
        "items": {"type": "object", "properties": {"id": {"type": "integer"}}},
    }
    result = generate_from_schema(schema)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0] == {"id": 0}


def test_generate_from_schema_nested_object() -> None:
    schema = {
        "type": "object",
        "properties": {
            "user": {
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "active": {"type": "boolean"},
                },
            },
            "score": {"type": "number"},
        },
    }
    result = generate_from_schema(schema)
    assert result == {
        "user": {"email": "example_string", "active": True},
        "score": 0.0,
    }


def test_generate_from_schema_returns_none_for_empty() -> None:
    assert generate_from_schema({}) is None


# ── ExamplesGenerator tests ───────────────────────────────────────────────


def test_generator_skips_ops_with_existing_examples() -> None:
    existing = ResponseExample(name="existing", source=SourceType.extractor)
    op = _make_op(
        response_schema={"type": "object", "properties": {"id": {"type": "integer"}}},
        response_examples=[existing],
    )
    client = MockLLMClient()
    result = ExamplesGenerator(client).generate(_make_ir(op))
    assert len(client.calls) == 0
    assert result.operations[0].response_examples == [existing]


def test_generator_skips_ops_without_schema() -> None:
    op = _make_op(response_schema=None)
    client = MockLLMClient()
    result = ExamplesGenerator(client).generate(_make_ir(op))
    assert len(client.calls) == 0
    assert result.operations[0].response_examples == []


def test_generator_adds_examples_with_llm() -> None:
    llm_response = json.dumps(
        [
            {
                "name": "Success response",
                "description": "A user object",
                "status_code": 200,
                "body": {"id": 42, "name": "Alice"},
            }
        ]
    )
    op = _make_op(
        response_schema={"type": "object", "properties": {"id": {"type": "integer"}}},
    )
    client = MockLLMClient(response=llm_response)
    result = ExamplesGenerator(client).generate(_make_ir(op))

    assert len(client.calls) == 1
    examples = result.operations[0].response_examples
    assert len(examples) == 1
    assert examples[0].name == "Success response"
    assert examples[0].source == SourceType.llm
    assert examples[0].status_code == 200
    assert examples[0].body == {"id": 42, "name": "Alice"}


def test_generator_handles_llm_failure() -> None:
    op = _make_op(
        response_schema={"type": "object", "properties": {"id": {"type": "integer"}}},
    )
    client = MockLLMClient(fail=True)
    result = ExamplesGenerator(client).generate(_make_ir(op))
    assert result.operations[0].response_examples == []


def test_generator_preserves_existing_examples() -> None:
    existing = ResponseExample(name="kept", source=SourceType.extractor)
    op_with = _make_op(
        "op_with",
        response_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        response_examples=[existing],
    )
    op_without = _make_op(
        "op_without",
        response_schema={"type": "object", "properties": {"x": {"type": "string"}}},
    )
    llm_response = json.dumps([{"name": "generated", "status_code": 200, "body": {"x": "hi"}}])
    client = MockLLMClient(response=llm_response)
    result = ExamplesGenerator(client).generate(_make_ir(op_with, op_without))

    assert result.operations[0].response_examples == [existing]
    assert result.operations[1].response_examples[0].name == "generated"
    assert result.operations[1].response_examples[0].source == SourceType.llm


def test_generate_returns_new_ir_not_mutated() -> None:
    op = _make_op(
        response_schema={"type": "object", "properties": {"id": {"type": "integer"}}},
    )
    original_ir = _make_ir(op)
    llm_response = json.dumps([{"name": "new", "status_code": 200, "body": {"id": 1}}])
    client = MockLLMClient(response=llm_response)

    new_ir = ExamplesGenerator(client).generate(original_ir)

    assert original_ir.operations[0].response_examples == []
    assert len(new_ir.operations[0].response_examples) == 1
    assert new_ir is not original_ir


def test_generate_from_schema_returns_none_for_non_dict_or_list_result() -> None:
    """Test generate_from_schema returns None when _generate_value returns non-dict/non-list."""
    # This tests the isinstance(result, (dict, list)) check on line 62-64
    schema = {"type": "unsupported"}
    result = generate_from_schema(schema)
    assert result is None


def test_generate_value_returns_none_for_unsupported_type() -> None:
    """Test _generate_value returns None for unsupported types."""
    from libs.enhancer.examples_generator import _generate_value

    schema = {"type": "unsupported_type"}
    result = _generate_value(schema)
    assert result is None


def test_generate_value_handles_empty_array_items() -> None:
    """Test _generate_value handles arrays with no items schema."""
    from libs.enhancer.examples_generator import _generate_value

    schema = {"type": "array"}  # No items specified
    result = _generate_value(schema)
    assert result == []


def test_generate_value_handles_empty_object_properties() -> None:
    """Test _generate_value handles objects with no properties."""
    from libs.enhancer.examples_generator import _generate_value

    schema = {"type": "object"}  # No properties specified
    result = _generate_value(schema)
    assert result == {}


def test_parse_examples_handles_unparseable_json() -> None:
    """Test _parse_examples handles invalid JSON gracefully."""
    from libs.enhancer.examples_generator import _parse_examples

    result = _parse_examples("invalid json")
    assert result == []


def test_parse_examples_converts_single_dict_to_list() -> None:
    """Test _parse_examples converts single dict to list."""
    from libs.enhancer.examples_generator import _parse_examples

    single_example = '{"name": "test", "status_code": 200}'
    result = _parse_examples(single_example)

    assert len(result) == 1
    assert result[0].name == "test"
    assert result[0].status_code == 200


def test_parse_examples_skips_non_dict_items() -> None:
    """Test _parse_examples skips non-dict items in list."""
    from libs.enhancer.examples_generator import _parse_examples

    mixed_list = '["not a dict", {"name": "valid", "status_code": 200}, 42]'
    result = _parse_examples(mixed_list)

    assert len(result) == 1
    assert result[0].name == "valid"


# ── _example_from_schema tests ────────────────────────────────────────────


def test_example_from_string_schema() -> None:
    ex = _example_from_schema({"type": "string"})
    assert ex.body == "example_string"
    assert ex.status_code == 200
    assert ex.source == SourceType.extractor


def test_example_from_integer_schema() -> None:
    ex = _example_from_schema({"type": "integer"})
    # int is not dict/list/str → body falls back to None
    assert ex.body is None
    assert ex.status_code == 200


def test_example_from_number_schema() -> None:
    ex = _example_from_schema({"type": "number"})
    assert ex.body is None


def test_example_from_boolean_schema() -> None:
    ex = _example_from_schema({"type": "boolean"})
    assert ex.body is None


def test_example_from_object_schema() -> None:
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
        },
    }
    ex = _example_from_schema(schema)
    assert ex.body == {"name": "example_string", "age": 0}
    assert ex.status_code == 200
    assert ex.source == SourceType.extractor


def test_example_from_array_schema() -> None:
    # Array bodies are normalized to None (ResponseExample.body doesn't accept list)
    ex = _example_from_schema({"type": "array", "items": {"type": "string"}})
    assert ex.body is None
    assert ex.status_code == 200


def test_example_from_email_format() -> None:
    ex = _example_from_schema({"type": "string", "format": "email"})
    assert ex.body == "user@example.com"


def test_example_from_datetime_format() -> None:
    ex = _example_from_schema({"type": "string", "format": "date-time"})
    assert ex.body == "2024-01-01T00:00:00Z"


def test_example_from_uri_format() -> None:
    ex = _example_from_schema({"type": "string", "format": "uri"})
    assert ex.body == "https://example.com"


def test_example_from_nested_object() -> None:
    schema = {
        "type": "object",
        "properties": {
            "user": {
                "type": "object",
                "properties": {
                    "profile": {
                        "type": "object",
                        "properties": {
                            "email": {"type": "string", "format": "email"},
                        },
                    },
                },
            },
        },
    }
    ex = _example_from_schema(schema)
    assert ex.body == {"user": {"profile": {"email": "user@example.com"}}}


def test_example_from_empty_schema() -> None:
    ex = _example_from_schema({})
    assert ex.body is None
    assert ex.status_code == 200
    assert ex.description == "Auto-generated from response schema"


# ── extract_examples_from_spec tests ──────────────────────────────────────


def test_extract_from_openapi_spec_with_examples() -> None:
    op = _make_op("listPets", response_schema={"type": "object"})
    ir = _make_ir(op)
    spec_data = {
        "paths": {
            "/pets": {
                "get": {
                    "operationId": "listPets",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "example": {"id": 1, "name": "Fido"},
                                },
                            },
                        },
                    },
                },
            },
        },
    }
    result = extract_examples_from_spec(ir, spec_data)
    examples = result.operations[0].response_examples
    assert len(examples) == 1
    assert examples[0].body == {"id": 1, "name": "Fido"}
    assert examples[0].status_code == 200


def test_extract_preserves_existing_examples() -> None:
    existing = ResponseExample(name="manual", source=SourceType.extractor)
    op = _make_op("getUser", response_examples=[existing])
    ir = _make_ir(op)
    spec_data = {
        "paths": {
            "/users": {
                "get": {
                    "operationId": "getUser",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "example": {"id": 99},
                                },
                            },
                        },
                    },
                },
            },
        },
    }
    result = extract_examples_from_spec(ir, spec_data)
    assert result.operations[0].response_examples == [existing]


def test_extract_no_spec_data_noop() -> None:
    op = _make_op("op1")
    ir = _make_ir(op)
    result = extract_examples_from_spec(ir, None)
    assert result is ir


def test_extract_empty_paths_noop() -> None:
    op = _make_op("op1")
    ir = _make_ir(op)
    result = extract_examples_from_spec(ir, {"paths": {}})
    assert result is ir


def test_extracted_source_is_extractor() -> None:
    op = _make_op("listItems")
    ir = _make_ir(op)
    spec_data = {
        "paths": {
            "/items": {
                "get": {
                    "operationId": "listItems",
                    "responses": {
                        "200": {
                            "description": "Items list",
                            "content": {
                                "application/json": {
                                    "example": {"items": []},
                                },
                            },
                        },
                    },
                },
            },
        },
    }
    result = extract_examples_from_spec(ir, spec_data)
    assert result.operations[0].response_examples[0].source == SourceType.extractor


# ── generate_synthetic_examples tests ─────────────────────────────────────


def test_generate_adds_examples_for_schema() -> None:
    schema = {"type": "object", "properties": {"id": {"type": "integer"}}}
    op = _make_op(response_schema=schema)
    ir = _make_ir(op)
    result = generate_synthetic_examples(ir)
    examples = result.operations[0].response_examples
    assert len(examples) == 1
    assert examples[0].body == {"id": 0}


def test_generate_skips_ops_with_existing_examples() -> None:
    existing = ResponseExample(name="existing", source=SourceType.extractor)
    op = _make_op(
        response_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        response_examples=[existing],
    )
    ir = _make_ir(op)
    result = generate_synthetic_examples(ir)
    assert result.operations[0].response_examples == [existing]


def test_generate_no_schema_no_example() -> None:
    op = _make_op(response_schema=None)
    ir = _make_ir(op)
    result = generate_synthetic_examples(ir)
    assert result.operations[0].response_examples == []


def test_generate_without_llm_client() -> None:
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    op = _make_op(response_schema=schema)
    ir = _make_ir(op)
    result = generate_synthetic_examples(ir, llm_client=None)
    assert len(result.operations[0].response_examples) == 1


def test_generate_source_is_extractor() -> None:
    schema = {"type": "object", "properties": {"val": {"type": "string"}}}
    op = _make_op(response_schema=schema)
    ir = _make_ir(op)
    result = generate_synthetic_examples(ir)
    assert result.operations[0].response_examples[0].source == SourceType.extractor


# ── Async ExamplesGenerator Tests ─────────────────────────────────────────


class AsyncMockLLMClient:
    """Mock LLM client with async support for testing parallel examples generation."""

    def __init__(self, response: str = "[]", fail: bool = False, delay: float = 0.0) -> None:
        self.response = response
        self.fail = fail
        self._delay = delay
        self.calls: list[str] = []
        self.concurrent_count = 0
        self.max_concurrent = 0

    def complete(self, prompt: str, max_tokens: int = 4096) -> LLMResponse:
        self.calls.append(prompt)
        if self.fail:
            raise RuntimeError("LLM API error")
        return LLMResponse(content=self.response, input_tokens=100, output_tokens=50)

    async def complete_async(self, prompt: str, max_tokens: int = 4096) -> LLMResponse:
        import asyncio

        self.concurrent_count += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent_count)
        self.calls.append(prompt)
        try:
            if self._delay:
                await asyncio.sleep(self._delay)
            if self.fail:
                raise RuntimeError("LLM API error")
            return LLMResponse(content=self.response, input_tokens=100, output_tokens=50)
        finally:
            self.concurrent_count -= 1


_EXAMPLE_RESPONSE = json.dumps(
    [{"name": "example", "description": "test", "status_code": 200, "body": {"id": 1}}]
)


class TestExamplesGeneratorAsync:
    async def test_generate_async_produces_examples(self) -> None:
        client = AsyncMockLLMClient(response=_EXAMPLE_RESPONSE)
        gen = ExamplesGenerator(client)
        schema = {"type": "object", "properties": {"id": {"type": "integer"}}}
        op = _make_op(response_schema=schema)
        ir = _make_ir(op)

        result = await gen.generate_async(ir)
        assert len(result.operations[0].response_examples) > 0
        assert result.operations[0].response_examples[0].source == SourceType.llm

    async def test_generate_async_parallel_calls(self) -> None:
        client = AsyncMockLLMClient(response=_EXAMPLE_RESPONSE, delay=0.05)
        gen = ExamplesGenerator(client, max_concurrency=5)
        schema = {"type": "object", "properties": {"id": {"type": "integer"}}}
        ops = [_make_op(op_id=f"op_{i}", response_schema=schema) for i in range(10)]
        ir = _make_ir(*ops)

        await gen.generate_async(ir)
        assert len(client.calls) == 10
        assert client.max_concurrent > 1  # should have parallel calls

    async def test_generate_async_respects_concurrency_limit(self) -> None:
        client = AsyncMockLLMClient(response=_EXAMPLE_RESPONSE, delay=0.05)
        gen = ExamplesGenerator(client, max_concurrency=2)
        schema = {"type": "object", "properties": {"id": {"type": "integer"}}}
        ops = [_make_op(op_id=f"op_{i}", response_schema=schema) for i in range(8)]
        ir = _make_ir(*ops)

        await gen.generate_async(ir)
        assert len(client.calls) == 8
        assert client.max_concurrent <= 2

    async def test_generate_async_skips_ops_with_examples(self) -> None:
        client = AsyncMockLLMClient(response=_EXAMPLE_RESPONSE)
        gen = ExamplesGenerator(client)
        existing = [ResponseExample(name="ex", status_code=200, body={"ok": True})]
        schema = {"type": "object", "properties": {"id": {"type": "integer"}}}
        op = _make_op(response_schema=schema, response_examples=existing)
        ir = _make_ir(op)

        result = await gen.generate_async(ir)
        assert len(client.calls) == 0
        assert result.operations[0].response_examples == existing

    async def test_generate_async_handles_failure(self) -> None:
        client = AsyncMockLLMClient(fail=True)
        gen = ExamplesGenerator(client)
        schema = {"type": "object", "properties": {"id": {"type": "integer"}}}
        op = _make_op(response_schema=schema)
        ir = _make_ir(op)

        result = await gen.generate_async(ir)
        assert len(result.operations[0].response_examples) == 0

    async def test_generate_async_fallback_to_sync_client(self) -> None:
        """Async generation falls back to sync client via executor."""
        client = MockLLMClient(response=_EXAMPLE_RESPONSE)
        gen = ExamplesGenerator(client)
        schema = {"type": "object", "properties": {"id": {"type": "integer"}}}
        op = _make_op(response_schema=schema)
        ir = _make_ir(op)

        result = await gen.generate_async(ir)
        assert len(result.operations[0].response_examples) > 0
        assert len(client.calls) == 1
