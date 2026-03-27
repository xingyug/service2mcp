"""Tests for LLM-assisted response example generation."""

from __future__ import annotations

import json

from libs.enhancer.enhancer import LLMResponse
from libs.enhancer.examples_generator import ExamplesGenerator, generate_from_schema
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
    response_schema: dict | None = None,
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
    assert result == {"name": "example", "age": 1}


def test_generate_from_schema_array() -> None:
    schema = {
        "type": "array",
        "items": {"type": "object", "properties": {"id": {"type": "integer"}}},
    }
    result = generate_from_schema(schema)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0] == {"id": 1}


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
        "user": {"email": "example", "active": True},
        "score": 1.0,
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
