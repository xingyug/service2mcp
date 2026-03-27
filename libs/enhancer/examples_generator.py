"""LLM-assisted response example generation for operations lacking examples."""

from __future__ import annotations

import json
import logging
from typing import Any

from libs.enhancer.enhancer import LLMClient, LLMResponse
from libs.ir.models import Operation, ResponseExample, ServiceIR, SourceType

logger = logging.getLogger(__name__)

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
    - string → "example"
    - integer → 1
    - number → 1.0
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


def _generate_value(schema: dict[str, Any]) -> Any:
    """Recursively generate a value from a JSON Schema node."""
    schema_type = schema.get("type")

    if schema_type == "string":
        return "example"
    if schema_type == "integer":
        return 1
    if schema_type == "number":
        return 1.0
    if schema_type == "boolean":
        return True
    if schema_type == "array":
        items = schema.get("items", {})
        if not items:
            return []
        return [_generate_value(items)]
    if schema_type == "object":
        properties = schema.get("properties", {})
        if not properties:
            return {}
        return {key: _generate_value(prop) for key, prop in properties.items()}

    # Unsupported / missing type → None signals "too complex"
    return None


# ── LLM-powered generator ─────────────────────────────────────────────────


class ExamplesGenerator:
    """Generate synthetic response examples for operations that lack them.

    Only generates examples for operations that:
    1. Have a response_schema (we need a schema to generate from)
    2. Don't already have response_examples (never overwrite extractor data)
    """

    def __init__(self, llm_client: LLMClient) -> None:
        self._client = llm_client

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
        except Exception:
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
        except Exception:
            logger.warning("Skipping malformed example item: %s", item, exc_info=True)
    return examples
