"""IR loading and MCP tool registration for the generic runtime."""

from __future__ import annotations

import gzip
import inspect
import json
import keyword
import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field, ValidationError, create_model

from libs.ir import ServiceIR, deserialize_ir
from libs.ir.models import Operation, Param, PromptDefinition, ResourceDefinition

type ToolResult = dict[str, Any]
type ToolHandler = Callable[[Operation, dict[str, Any]], ToolResult | Awaitable[ToolResult]]

_IR_TYPE_TO_PYTHON: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list[object],
    "object": dict[str, object],
}


class RuntimeLoadError(RuntimeError):
    """Raised when the runtime cannot load a valid ServiceIR."""


def load_service_ir(path: str | Path) -> ServiceIR:
    """Read and validate a ServiceIR JSON file from disk."""

    ir_path = Path(path)
    try:
        payload_bytes = ir_path.read_bytes()
    except OSError as exc:
        raise RuntimeLoadError(f"Unable to read ServiceIR from {ir_path}: {exc}") from exc

    try:
        if ir_path.suffix == ".gz" or payload_bytes.startswith(b"\x1f\x8b"):
            payload_bytes = gzip.decompress(payload_bytes)
        json_payload = payload_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeLoadError(f"Unable to decode ServiceIR at {ir_path}: {exc}") from exc

    try:
        return deserialize_ir(json_payload)
    except (ValidationError, ValueError) as exc:
        raise RuntimeLoadError(f"Invalid ServiceIR at {ir_path}: {exc}") from exc


def create_runtime_server(name: str = "generic-mcp-runtime") -> FastMCP:
    """Create the FastMCP server used by the runtime."""

    # Keep DNS rebinding protection enabled by default. Generated runtimes can
    # allow their own in-cluster service DNS names via MCP_ALLOWED_HOSTS.
    disable_rebinding_protection = os.getenv(
        "MCP_DISABLE_DNS_REBINDING_PROTECTION", "false"
    ).lower() in ("true", "1", "yes")
    allowed_hosts = _split_csv_env("MCP_ALLOWED_HOSTS")
    allowed_origins = _split_csv_env("MCP_ALLOWED_ORIGINS")
    return FastMCP(
        name=name,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=not disable_rebinding_protection,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        ),
    )


def _split_csv_env(name: str) -> list[str]:
    raw_value = os.getenv(name, "")
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def register_ir_tools(
    server: FastMCP,
    service_ir: ServiceIR,
    *,
    tool_handler: ToolHandler | None = None,
) -> dict[str, Operation]:
    """Register one MCP tool per enabled IR operation."""

    registered_operations: dict[str, Operation] = {}
    for operation in service_ir.operations:
        if not operation.enabled:
            continue

        tool_fn, param_name_map = build_tool_function(operation, tool_handler=tool_handler)
        server.add_tool(
            tool_fn,
            name=operation.id,
            title=operation.name,
            description=operation.description or operation.name,
            meta={
                "operation_id": operation.id,
                "operation_name": operation.name,
                "method": operation.method,
                "path": operation.path,
                "param_name_map": param_name_map,
            },
        )
        registered_operations[operation.id] = operation

    return registered_operations


def build_tool_function(
    operation: Operation,
    *,
    tool_handler: ToolHandler | None = None,
) -> tuple[Callable[..., Awaitable[ToolResult]], dict[str, str]]:
    """Build a callable whose signature mirrors the IR operation parameters."""

    param_name_map: dict[str, str] = {}
    signature_params: list[inspect.Parameter] = []

    for param in operation.params:
        python_name = _python_parameter_name(param.name, existing_names=param_name_map.keys())
        param_name_map[python_name] = param.name
        signature_params.append(_build_signature_parameter(python_name, param))

    async def tool_impl(**kwargs: Any) -> ToolResult:
        # Build original_kwargs carefully: when multiple python names map to
        # the same IR name (e.g. path "index" and body "index_2" both → "index"),
        # don't let a default None from the optional duplicate overwrite the
        # explicit value from the required one.
        original_kwargs: dict[str, Any] = {}
        for name, value in kwargs.items():
            ir_name = param_name_map[name]
            unwrapped = _unwrap_pydantic(value)
            if ir_name in original_kwargs and unwrapped is None:
                continue
            original_kwargs[ir_name] = unwrapped
        if tool_handler is None:
            return _default_tool_handler(operation, original_kwargs)

        result = tool_handler(operation, original_kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    tool_impl.__name__ = f"tool_{_python_parameter_name(operation.id, existing_names=())}"
    tool_impl.__doc__ = operation.description or operation.name
    tool_impl.__annotations__ = {
        name: parameter.annotation
        for name, parameter in zip(param_name_map, signature_params, strict=True)
    }
    tool_impl.__annotations__["return"] = dict[str, Any]
    setattr(
        tool_impl,
        "__signature__",
        inspect.Signature(
            parameters=signature_params,
            return_annotation=dict[str, Any],
        ),
    )

    return tool_impl, param_name_map


def _build_signature_parameter(name: str, param: Param) -> inspect.Parameter:
    annotation = _python_annotation_for_param(param)
    default: Any
    if param.required and param.default is None:
        default = inspect.Parameter.empty
    else:
        default = param.default
    return inspect.Parameter(
        name,
        inspect.Parameter.KEYWORD_ONLY,
        annotation=annotation,
        default=default,
    )


def _python_annotation_for_param(param: Param) -> Any:
    if param.json_schema:
        base_type = _pydantic_model_from_schema(param.name, param.json_schema)
    elif param.type == "array":
        # No json_schema — fall back to list[Any] (better than collapsing to scalar)
        base_type = list[object]
    else:
        base_type = _IR_TYPE_TO_PYTHON.get(param.type, Any)
    if param.required and param.default is None:
        result_type = base_type
    else:
        result_type = base_type | None
    if param.description:
        return Annotated[result_type, Field(description=param.description)]
    return result_type


def _unwrap_pydantic(value: Any) -> Any:
    """Convert Pydantic model instances back to plain dicts/lists.

    json_schema annotations cause the MCP SDK to pass Pydantic model
    instances instead of raw dicts.  Protocol proxy handlers (SOAP, REST,
    GraphQL, …) expect plain Python primitives, so we convert here.
    """
    if isinstance(value, BaseModel):
        return {k: _unwrap_pydantic(v) for k, v in value.model_dump().items()}
    if isinstance(value, list):
        return [_unwrap_pydantic(item) for item in value]
    return value


_SCHEMA_MODEL_CACHE: dict[str, type[BaseModel]] = {}

_JSON_SCHEMA_TYPE_TO_PYTHON: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}

_MAX_SCHEMA_DEPTH = 10


def _pydantic_model_from_schema(name: str, schema: dict[str, Any], *, _depth: int = 0) -> Any:
    """Build a Pydantic model from a JSON Schema dict.

    This gives the MCP runtime structured sub-fields for complex params
    (e.g. SOAP Address with street/city/zipCode) instead of opaque
    ``dict[str, object]``.
    """
    if _depth >= _MAX_SCHEMA_DEPTH:
        return dict[str, object]

    enum_values = schema.get("enum")
    if enum_values:
        return Literal[tuple(enum_values)]

    schema_type = schema.get("type", "object")

    if schema_type == "array":
        items_schema = schema.get("items", {})
        item_type = _resolve_schema_type(f"{name}_item", items_schema, _depth=_depth + 1)
        return list[item_type]  # type: ignore[valid-type]

    if schema_type != "object":
        return _JSON_SCHEMA_TYPE_TO_PYTHON.get(schema_type, Any)

    properties = schema.get("properties", {})
    if not properties:
        return dict[str, object]

    cache_key = json.dumps(schema, sort_keys=True)
    if cache_key in _SCHEMA_MODEL_CACHE:
        return _SCHEMA_MODEL_CACHE[cache_key]

    required_set = set(schema.get("required", []))
    field_definitions: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        prop_type = _resolve_schema_type(f"{name}_{prop_name}", prop_schema, _depth=_depth + 1)
        if prop_name in required_set:
            field_definitions[prop_name] = (prop_type, ...)
        else:
            field_definitions[prop_name] = (prop_type | None, None)

    model_name = _safe_model_name(name)
    model = create_model(model_name, **field_definitions)
    _SCHEMA_MODEL_CACHE[cache_key] = model
    return model


def _resolve_schema_type(name: str, schema: dict[str, Any], *, _depth: int = 0) -> Any:
    """Resolve a JSON Schema fragment to a Python type."""
    if _depth >= _MAX_SCHEMA_DEPTH:
        return Any
    enum_values = schema.get("enum")
    if enum_values:
        return Literal[tuple(enum_values)]
    schema_type = schema.get("type", "string")
    if schema_type == "object" and schema.get("properties"):
        return _pydantic_model_from_schema(name, schema, _depth=_depth + 1)
    if schema_type == "array":
        items = schema.get("items", {})
        item_type = _resolve_schema_type(f"{name}_item", items, _depth=_depth + 1)
        return list[item_type]  # type: ignore[valid-type]
    return _JSON_SCHEMA_TYPE_TO_PYTHON.get(schema_type, Any)


def _safe_model_name(name: str) -> str:
    """Turn an arbitrary name into a valid Python class identifier."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", name).strip("_")
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"Model_{cleaned}"
    return cleaned[:80].title().replace("_", "")


def _python_parameter_name(name: str, *, existing_names: Any) -> str:
    candidate = re.sub(r"[^a-zA-Z0-9_]", "_", name).strip("_")
    if not candidate:
        candidate = "param"
    if candidate[0].isdigit():
        candidate = f"param_{candidate}"
    if keyword.iskeyword(candidate):
        candidate = f"{candidate}_"

    existing = set(existing_names)
    unique_candidate = candidate
    suffix = 2
    while unique_candidate in existing:
        unique_candidate = f"{candidate}_{suffix}"
        suffix += 1
    return unique_candidate


def _default_tool_handler(operation: Operation, arguments: dict[str, Any]) -> ToolResult:
    return {
        "status": "not_implemented",
        "message": "Upstream proxying is implemented by the runtime proxy.",
        "operation_id": operation.id,
        "method": operation.method,
        "path": operation.path,
        "arguments": arguments,
    }


def register_ir_resources(
    server: FastMCP,
    service_ir: ServiceIR,
    *,
    tool_handler: ToolHandler | None = None,
) -> list[ResourceDefinition]:
    """Register MCP resources from IR resource definitions."""
    from mcp.server.fastmcp.resources import FunctionResource

    registered: list[ResourceDefinition] = []
    operations_by_id = {
        operation.id: operation for operation in service_ir.operations if operation.enabled
    }
    for resource_def in service_ir.resource_definitions:
        if resource_def.content_type == "dynamic":
            operation_id = resource_def.operation_id
            if operation_id is None:
                raise RuntimeLoadError(
                    f"Dynamic resource {resource_def.id!r} is missing operation_id."
                )
            operation = operations_by_id.get(operation_id)
            if operation is None:
                raise RuntimeLoadError(
                    f"Dynamic resource {resource_def.id!r} references unavailable operation "
                    f"{operation_id!r}."
                )
            arguments = _dynamic_resource_arguments(resource_def, operation)
            fn_resource = FunctionResource(
                uri=resource_def.uri,  # pyright: ignore[reportArgumentType]
                name=resource_def.name,
                description=resource_def.description or resource_def.name,
                mime_type=resource_def.mime_type,
                fn=_make_dynamic_resource_reader(
                    resource_def,
                    operation,
                    arguments,
                    tool_handler=tool_handler,
                ),
            )
            server.add_resource(fn_resource)
            registered.append(resource_def)
            continue

        static_content = resource_def.content or ""

        def _make_fn(content: str) -> Any:
            async def read_resource() -> str:
                return content

            return read_resource

        fn_resource = FunctionResource(
            uri=resource_def.uri,  # pyright: ignore[reportArgumentType]
            name=resource_def.name,
            description=resource_def.description or resource_def.name,
            mime_type=resource_def.mime_type,
            fn=_make_fn(static_content),
        )
        server.add_resource(fn_resource)
        registered.append(resource_def)

    return registered


def _dynamic_resource_arguments(
    resource_def: ResourceDefinition,
    operation: Operation,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {}
    missing_required = [
        param.name for param in operation.params if param.required and param.default is None
    ]
    if missing_required:
        raise RuntimeLoadError(
            f"Dynamic resource {resource_def.id!r} references operation {operation.id!r} "
            f"with required params: {missing_required}."
        )
    for param in operation.params:
        if param.default is not None:
            arguments[param.name] = param.default
    return arguments


def _make_dynamic_resource_reader(
    resource_def: ResourceDefinition,
    operation: Operation,
    arguments: dict[str, Any],
    *,
    tool_handler: ToolHandler | None = None,
) -> Any:
    async def read_resource() -> str:
        result = (
            tool_handler(operation, dict(arguments))
            if tool_handler is not None
            else _default_tool_handler(operation, dict(arguments))
        )
        if inspect.isawaitable(result):
            result = await result
        return _resource_payload_to_text(resource_def, result)

    return read_resource


def _resource_payload_to_text(resource_def: ResourceDefinition, payload: Any) -> str:
    if isinstance(payload, dict) and "result" in payload:
        payload = payload["result"]
    if isinstance(payload, str):
        return payload
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    try:
        return json.dumps(payload, ensure_ascii=True)
    except TypeError as exc:
        raise RuntimeLoadError(
            f"Dynamic resource {resource_def.id!r} returned a non-serializable payload."
        ) from exc


def register_ir_prompts(
    server: FastMCP,
    service_ir: ServiceIR,
) -> list[PromptDefinition]:
    """Register MCP prompts from IR prompt definitions."""
    from mcp.server.fastmcp.prompts import Prompt
    from mcp.server.fastmcp.prompts.base import (
        PromptArgument as MCPPromptArgument,
    )

    registered: list[PromptDefinition] = []
    for prompt_def in service_ir.prompt_definitions:
        template = prompt_def.template

        def _make_fn(
            tmpl: str,
            args: list[Any],
        ) -> Any:
            arg_names = [a.name for a in args]

            async def get_prompt(**kwargs: str) -> str:
                result = tmpl
                for name in arg_names:
                    if name in kwargs:
                        result = result.replace(
                            "{" + name + "}",
                            kwargs[name],
                        )
                return result

            # Build proper signature so FastMCP can introspect
            params = [
                inspect.Parameter(
                    a.name,
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=str,
                    default=(inspect.Parameter.empty if a.required else (a.default or "")),
                )
                for a in args
            ]
            get_prompt.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
                parameters=params,
                return_annotation=str,
            )
            return get_prompt

        mcp_args = [
            MCPPromptArgument(
                name=a.name,
                description=a.description or a.name,
                required=a.required,
            )
            for a in prompt_def.arguments
        ]

        prompt = Prompt(  # pyright: ignore[reportCallIssue]
            name=prompt_def.name,
            description=prompt_def.description or prompt_def.name,
            arguments=mcp_args if mcp_args else None,
            fn=_make_fn(template, prompt_def.arguments),
        )
        server.add_prompt(prompt)
        registered.append(prompt_def)

    return registered


def register_runtime_resources(
    server: FastMCP,
    service_ir: ServiceIR,
    observability: Any | None = None,
) -> None:
    """Register dynamic runtime resources (live stats, not from IR).

    These resources are generated at read-time from runtime state,
    not from pre-compiled static content in the ServiceIR.
    """
    from mcp.server.fastmcp.resources import FunctionResource

    service_name = service_ir.service_name

    async def _read_runtime_stats() -> str:
        stats: dict[str, Any] = {
            "service_name": service_name,
            "protocol": service_ir.protocol,
            "tool_count": len([op for op in service_ir.operations if op.enabled]),
            "ir_version": service_ir.ir_version,
        }
        if observability is not None:
            collected: dict[str, dict[str, float]] = {}
            try:
                for metric in observability.registry.collect():
                    for sample in metric.samples:
                        op_id = sample.labels.get("operation_id")
                        if op_id is None:
                            continue
                        if op_id not in collected:
                            collected[op_id] = {}
                        key = f"{sample.name}"
                        if "outcome" in sample.labels:
                            key += f"_{sample.labels['outcome']}"
                        collected[op_id][key] = sample.value
            except Exception:
                pass
            if collected:
                stats["operation_metrics"] = collected
        return json.dumps(stats, indent=2)

    fn_resource = FunctionResource(
        uri=f"service:///{service_name}/runtime-stats",  # pyright: ignore[reportArgumentType]
        name=f"{service_name} runtime-stats",
        description=f"Live runtime statistics for {service_name}",
        mime_type="application/json",
        fn=_read_runtime_stats,
    )
    server.add_resource(fn_resource)
