"""JSON Schema generation and validation utilities for the IR."""

from __future__ import annotations

import json
from typing import Any

from libs.ir.models import ServiceIR


def generate_json_schema() -> dict[str, Any]:
    """Generate the JSON Schema for ServiceIR."""
    return ServiceIR.model_json_schema()


def generate_json_schema_string(indent: int = 2) -> str:
    """Generate the JSON Schema as a formatted string."""
    return json.dumps(generate_json_schema(), indent=indent)


def serialize_ir(ir: ServiceIR) -> str:
    """Serialize a ServiceIR to JSON string."""
    return ir.model_dump_json(indent=2)


def deserialize_ir(json_str: str) -> ServiceIR:
    """Deserialize a JSON string to ServiceIR."""
    return ServiceIR.model_validate_json(json_str)


def ir_to_dict(ir: ServiceIR) -> dict[str, Any]:
    """Convert a ServiceIR to a dictionary."""
    return ir.model_dump(mode="json")


def ir_from_dict(data: dict[str, Any]) -> ServiceIR:
    """Create a ServiceIR from a dictionary."""
    return ServiceIR.model_validate(data)
