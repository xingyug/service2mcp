"""LLM-assisted response example generation for operations lacking examples."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from libs.enhancer.enhancer import AsyncLLMClient, LLMClient, LLMResponse
from libs.ir.models import Operation, ResponseExample, ServiceIR, SourceType

logger = logging.getLogger(__name__)

# ── Format-aware defaults ──────────────────────────────────────────────────

_STRING_FORMAT_DEFAULTS: dict[str, str] = {
    "email": "user@example.com",
    "date-time": "2024-01-01T00:00:00Z",
    "date": "2024-01-01",
    "time": "00:00:00Z",
    "uri": "https://example.com",
    "url": "https://example.com",
    "hostname": "example.com",
    "ipv4": "192.0.2.1",
    "ipv6": "::1",
    "uuid": "550e8400-e29b-41d4-a716-446655440000",
}

# ── Prompt Template ────────────────────────────────────────────────────────

_PROMPT_TEMPLATE = """\
You are a technical API documentation expert. Generate realistic response \
examples for the following API operation.

Operation: {name}
Description: {description}
Response Schema:
```json
{schema}
```

Instructions:
- Return a JSON array of 1-2 example response objects.
- Each element must have: "name" (str), "description" (str), "status_code" (int), "body" (object).
{list_hint}
- Use realistic, plausible values (not "string" or 0).
- Return ONLY valid JSON, no markdown fences or commentary.
"""

_LIST_HINT = "- The schema describes an array; include 2-3 items in the body array.\n"
_SINGLE_HINT = "- Generate one realistic example object.\n"


def _is_array_schema(schema: dict[str, Any]) -> bool:
    """Return True if the schema's top-level type is array."""
    return schema.get("type") == "array"


# ── Schema-only generation (no LLM) ───────────────────────────────────────


def generate_from_schema(response_schema: dict[str, Any]) -> dict[str, Any] | list[Any] | None:
    """Generate a plausible example from a JSON Schema without LLM.

    Returns None if the schema is too complex to auto-generate.
    Uses simple type-based defaults:
    - string → "example_string" (or format-aware: email, uri, date-time, etc.)
    - integer → 0
    - number → 0.0
    - boolean → true
    - array → [<one item>]
    - object → recurse properties
    """
    if not response_schema:
        return None
    result = _generate_value(response_schema)
    if isinstance(result, (dict, list)):
        return result
    return None


def _generate_value(schema: dict[str, Any], *, _depth: int = 0) -> Any:
    """Recursively generate a value from a JSON Schema node."""
    if _depth >= 10:
        return None
    schema_type = schema.get("type")

    if schema_type == "string":
        fmt = schema.get("format", "")
        return _STRING_FORMAT_DEFAULTS.get(fmt, "example_string")
    if schema_type == "integer":
        return 0
    if schema_type == "number":
        return 0.0
    if schema_type == "boolean":
        return True
    if schema_type == "array":
        items = schema.get("items", {})
        if not items:
            return []
        return [_generate_value(items, _depth=_depth + 1)]
    if schema_type == "object":
        properties = schema.get("properties", {})
        if not properties:
            return {}
        return {key: _generate_value(prop, _depth=_depth + 1) for key, prop in properties.items()}

    # $ref or unsupported / missing type → None signals "too complex"
    return None


# ── Spec extraction (no LLM) ──────────────────────────────────────────────


def _example_from_schema(schema: dict[str, Any], name: str = "example") -> ResponseExample:
    """Generate a basic example from a JSON Schema definition."""
    body = _generate_value(schema)
    normalized_body: dict[str, Any] | str | None
    if isinstance(body, dict):
        normalized_body = body
    elif isinstance(body, str):
        normalized_body = body
    else:
        normalized_body = None
    return ResponseExample(
        name=name,
        description="Auto-generated from response schema",
        status_code=200,
        body=normalized_body,
        source=SourceType.extractor,
    )


def extract_examples_from_spec(ir: ServiceIR, spec_data: dict[str, Any] | None = None) -> ServiceIR:
    """Extract response examples from the original API spec.

    Handles OpenAPI examples, GraphQL examples, etc.
    This is a pure extractor pass — no LLM calls.
    """
    if not spec_data:
        return ir

    paths = spec_data.get("paths", {})
    if not isinstance(paths, dict):
        return ir

    # Build a lookup from operation id to extracted examples
    spec_examples: dict[str, list[ResponseExample]] = {}
    for _path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for _method, op_spec in methods.items():
            if not isinstance(op_spec, dict):
                continue
            op_id = op_spec.get("operationId")
            if not op_id:
                continue
            examples = _extract_openapi_response_examples(op_spec)
            if examples:
                spec_examples[op_id] = examples

    if not spec_examples:
        return ir

    new_ops: list[Operation] = []
    for op in ir.operations:
        found = spec_examples.get(op.id, [])
        if found and not op.response_examples:
            op = op.model_copy(update={"response_examples": found})
        new_ops.append(op)
    return ir.model_copy(update={"operations": new_ops})


def _extract_openapi_response_examples(op_spec: dict[str, Any]) -> list[ResponseExample]:
    """Extract examples from an OpenAPI operation's response section."""
    examples: list[ResponseExample] = []
    responses = op_spec.get("responses", {})
    if not isinstance(responses, dict):
        return examples

    for status_code_str, response_obj in responses.items():
        if not isinstance(response_obj, dict):
            continue
        try:
            status_code = int(status_code_str)
        except (ValueError, TypeError):
            status_code = None

        # OpenAPI 3.x: content → media type → example / examples
        content = response_obj.get("content", {})
        if isinstance(content, dict):
            for _media, media_obj in content.items():
                if not isinstance(media_obj, dict):
                    continue
                # Single example
                if "example" in media_obj:
                    examples.append(
                        ResponseExample(
                            name=f"spec_example_{status_code_str}",
                            description=response_obj.get("description", ""),
                            status_code=status_code,
                            body=media_obj["example"],
                            source=SourceType.extractor,
                        )
                    )
                # Named examples
                named = media_obj.get("examples", {})
                if isinstance(named, dict):
                    for ex_name, ex_obj in named.items():
                        if not isinstance(ex_obj, dict):
                            continue
                        value = ex_obj.get("value")
                        if value is None:
                            continue
                        examples.append(
                            ResponseExample(
                                name=ex_name,
                                description=ex_obj.get("summary", ""),
                                status_code=status_code,
                                body=value,
                                source=SourceType.extractor,
                            )
                        )

        # Swagger 2.x: examples → media type → value
        swagger_examples = response_obj.get("examples", {})
        if isinstance(swagger_examples, dict):
            for _media, value in swagger_examples.items():
                examples.append(
                    ResponseExample(
                        name=f"spec_example_{status_code_str}",
                        description=response_obj.get("description", ""),
                        status_code=status_code,
                        body=value,
                        source=SourceType.extractor,
                    )
                )

    return examples


def generate_synthetic_examples(ir: ServiceIR, llm_client: Any | None = None) -> ServiceIR:
    """Generate synthetic response examples using response_schema and LLM if available.

    If no LLM client, generates basic structural examples from JSON Schema.
    Falls back gracefully — never fails.
    """
    new_ops: list[Operation] = []
    for op in ir.operations:
        if op.response_schema and not op.response_examples:
            try:
                example = _example_from_schema(op.response_schema, name=f"{op.name}_example")
                new_ops.append(op.model_copy(update={"response_examples": [example]}))
            except Exception:
                logger.warning(
                    "Failed to generate synthetic example for '%s'", op.id, exc_info=True
                )
                new_ops.append(op)
        else:
            new_ops.append(op)
    return ir.model_copy(update={"operations": new_ops})


# ── LLM-powered generator ─────────────────────────────────────────────────


class ExamplesGenerator:
    """Generate synthetic response examples for operations that lack them.

    Only generates examples for operations that:
    1. Have a response_schema (we need a schema to generate from)
    2. Don't already have response_examples (never overwrite extractor data)
    """

    def __init__(self, llm_client: LLMClient, *, max_concurrency: int = 5) -> None:
        self._client = llm_client
        self._max_concurrency = max_concurrency

    def generate(self, ir: ServiceIR) -> ServiceIR:
        """Return a copy of the IR with generated examples for eligible operations."""
        new_operations: list[Operation] = []
        for op in ir.operations:
            if self._needs_examples(op):
                examples = self._generate_for_operation(op, ir.service_name)
                if examples:
                    op = op.model_copy(update={"response_examples": examples})
            new_operations.append(op)
        return ir.model_copy(update={"operations": new_operations})

    async def generate_async(self, ir: ServiceIR) -> ServiceIR:
        """Return a copy of the IR with generated examples (parallel LLM calls)."""
        import asyncio

        eligible = [(i, op) for i, op in enumerate(ir.operations) if self._needs_examples(op)]
        if not eligible:
            return ir

        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def _gen(op: Operation) -> list[ResponseExample]:
            async with semaphore:
                return await self._generate_for_operation_async(op, ir.service_name)

        tasks = [_gen(op) for _, op in eligible]
        results = await asyncio.gather(*tasks)

        idx_to_examples: dict[int, list[ResponseExample]] = {}
        for (idx, _), examples in zip(eligible, results):
            if examples:
                idx_to_examples[idx] = examples

        new_operations: list[Operation] = []
        for i, op in enumerate(ir.operations):
            if i in idx_to_examples:
                op = op.model_copy(update={"response_examples": idx_to_examples[i]})
            new_operations.append(op)
        return ir.model_copy(update={"operations": new_operations})

    def _needs_examples(self, op: Operation) -> bool:
        """Check if an operation is eligible for example generation."""
        return op.response_schema is not None and len(op.response_examples) == 0

    def _generate_for_operation(self, op: Operation, service_name: str) -> list[ResponseExample]:
        """Generate examples for a single operation using LLM."""
        assert op.response_schema is not None  # guaranteed by _needs_examples

        schema_json = json.dumps(op.response_schema, indent=2)
        list_hint = _LIST_HINT if _is_array_schema(op.response_schema) else _SINGLE_HINT

        prompt = _PROMPT_TEMPLATE.format(
            name=op.name,
            description=op.description or f"{service_name} / {op.name}",
            schema=schema_json,
            list_hint=list_hint,
        )

        try:
            response: LLMResponse = self._client.complete(prompt)
            return _parse_examples(response.content)
        except (
            json.JSONDecodeError,
            httpx.HTTPError,
            ValueError,
            KeyError,
            TypeError,
            RuntimeError,
        ):
            logger.warning("Failed to generate examples for operation '%s'", op.id, exc_info=True)
            return []

    async def _generate_for_operation_async(
        self, op: Operation, service_name: str
    ) -> list[ResponseExample]:
        """Generate examples for a single operation using LLM (async)."""
        import asyncio

        assert op.response_schema is not None

        schema_json = json.dumps(op.response_schema, indent=2)
        list_hint = _LIST_HINT if _is_array_schema(op.response_schema) else _SINGLE_HINT

        prompt = _PROMPT_TEMPLATE.format(
            name=op.name,
            description=op.description or f"{service_name} / {op.name}",
            schema=schema_json,
            list_hint=list_hint,
        )

        try:
            if isinstance(self._client, AsyncLLMClient):
                response: LLMResponse = await self._client.complete_async(prompt)
            else:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(None, self._client.complete, prompt)
            return _parse_examples(response.content)
        except (
            json.JSONDecodeError,
            httpx.HTTPError,
            ValueError,
            KeyError,
            TypeError,
            RuntimeError,
        ):
            logger.warning("Failed to generate examples for operation '%s'", op.id, exc_info=True)
            return []


def _parse_examples(raw: str) -> list[ResponseExample]:
    """Parse the LLM response into ResponseExample objects."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("LLM returned unparseable JSON for examples")
        return []

    if not isinstance(data, list):
        data = [data]

    examples: list[ResponseExample] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            examples.append(
                ResponseExample(
                    name=item.get("name", "example"),
                    description=item.get("description", ""),
                    status_code=item.get("status_code"),
                    body=item.get("body"),
                    source=SourceType.llm,
                )
            )
        except (TypeError, ValueError, KeyError):
            logger.warning("Skipping malformed example item: %s", item, exc_info=True)
    return examples
