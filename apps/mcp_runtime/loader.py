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
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import ValidationError

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

    # Keep DNS rebinding protection enabled by default. Operators running the
    # runtime behind non-localhost service DNS names can explicitly opt out.
    disable_rebinding_protection = os.getenv(
        "MCP_DISABLE_DNS_REBINDING_PROTECTION", "false"
    ).lower() in ("true", "1", "yes")
    return FastMCP(
        name=name,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=not disable_rebinding_protection,
        ),
    )


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
        original_kwargs = {param_name_map[name]: value for name, value in kwargs.items()}
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
    base_type = _IR_TYPE_TO_PYTHON.get(param.type, Any)
    if param.required and param.default is None:
        return base_type
    return base_type | None


def _python_parameter_name(name: str, *, existing_names: Any) -> str:
    candidate = re.sub(r"\W", "_", name).strip("_")
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
        "message": "Upstream proxying is implemented in T-011.",
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
