"""Unit tests for MCP runtime loader — build_tool_function, register_ir_tools, param naming."""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from apps.mcp_runtime.loader import (
    RuntimeLoadError,
    _default_tool_handler,
    _python_parameter_name,
    build_tool_function,
    create_runtime_server,
    load_service_ir,
    register_ir_tools,
)
from libs.ir.models import (
    AuthConfig,
    AuthType,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)


def _op(
    op_id: str = "getItem",
    *,
    method: str = "GET",
    params: list[Param] | None = None,
    enabled: bool = True,
) -> Operation:
    return Operation(
        id=op_id,
        name=op_id,
        description=f"Test op {op_id}",
        method=method,
        path=f"/{op_id}",
        params=params or [],
        risk=RiskMetadata(
            risk_level=RiskLevel.safe,
            confidence=1.0,
            source=SourceType.extractor,
            writes_state=False,
            destructive=False,
            external_side_effect=False,
            idempotent=True,
        ),
        enabled=enabled,
    )


def _ir(operations: list[Operation] | None = None) -> ServiceIR:
    return ServiceIR(
        source_hash="a" * 64,
        protocol="rest",
        service_name="loader-test",
        base_url="https://example.test",
        auth=AuthConfig(type=AuthType.none),
        operations=[_op()] if operations is None else operations,
    )


class TestBuildToolFunction:
    @pytest.mark.asyncio
    async def test_generated_function_has_correct_signature(self) -> None:
        op = _op(params=[
            Param(name="item_id", type="string", required=True),
            Param(name="limit", type="integer", required=False, default=10),
        ])
        fn, name_map = build_tool_function(op)
        sig = inspect.signature(fn)
        assert "item_id" in sig.parameters
        assert "limit" in sig.parameters
        assert sig.parameters["item_id"].default is inspect.Parameter.empty
        assert sig.parameters["limit"].default == 10
        assert name_map == {"item_id": "item_id", "limit": "limit"}

    @pytest.mark.asyncio
    async def test_default_handler_returns_not_implemented(self) -> None:
        op = _op(params=[Param(name="id", type="string", required=True)])
        fn, _ = build_tool_function(op)
        result = await fn(id="123")
        assert result["status"] == "not_implemented"
        assert result["operation_id"] == "getItem"
        assert result["arguments"] == {"id": "123"}

    @pytest.mark.asyncio
    async def test_custom_sync_handler(self) -> None:
        op = _op(params=[Param(name="q", type="string", required=True)])

        def handler(operation: Operation, args: dict[str, Any]) -> dict[str, Any]:
            return {"found": True, "query": args["q"]}

        fn, _ = build_tool_function(op, tool_handler=handler)
        result = await fn(q="test")
        assert result == {"found": True, "query": "test"}

    @pytest.mark.asyncio
    async def test_custom_async_handler(self) -> None:
        op = _op(params=[Param(name="q", type="string", required=True)])

        async def handler(
            operation: Operation, args: dict[str, Any]
        ) -> dict[str, Any]:
            return {"async": True, "query": args["q"]}

        fn, _ = build_tool_function(op, tool_handler=handler)
        result = await fn(q="hello")
        assert result == {"async": True, "query": "hello"}

    @pytest.mark.asyncio
    async def test_param_name_remapping(self) -> None:
        op = _op(params=[
            Param(name="item-id", type="string", required=True),
        ])
        fn, name_map = build_tool_function(op)
        # "item-id" is not a valid Python identifier, should be remapped
        assert "item_id" in name_map
        assert name_map["item_id"] == "item-id"
        result = await fn(item_id="abc")
        assert result["arguments"] == {"item-id": "abc"}


class TestPythonParameterName:
    def test_simple_name_unchanged(self) -> None:
        assert _python_parameter_name("limit", existing_names=()) == "limit"

    def test_hyphen_replaced(self) -> None:
        assert _python_parameter_name("item-id", existing_names=()) == "item_id"

    def test_leading_digit_prefixed(self) -> None:
        result = _python_parameter_name("1st", existing_names=())
        assert result == "param_1st"

    def test_keyword_suffixed(self) -> None:
        result = _python_parameter_name("class", existing_names=())
        assert result == "class_"

    def test_empty_becomes_param(self) -> None:
        result = _python_parameter_name("---", existing_names=())
        assert result == "param"

    def test_dedup_on_collision(self) -> None:
        result = _python_parameter_name(
            "id", existing_names={"id"}
        )
        assert result == "id_2"

    def test_dedup_chain(self) -> None:
        result = _python_parameter_name(
            "id", existing_names={"id", "id_2"}
        )
        assert result == "id_3"


class TestRegisterIrTools:
    def test_disabled_operations_skipped(self) -> None:
        server = create_runtime_server("test")
        ir = _ir(operations=[
            _op("enabled_op", enabled=True),
            _op("disabled_op", enabled=False),
        ])
        registered = register_ir_tools(server, ir)
        assert "enabled_op" in registered
        assert "disabled_op" not in registered

    def test_all_enabled_operations_registered(self) -> None:
        server = create_runtime_server("test")
        ir = _ir(operations=[_op("op_a"), _op("op_b"), _op("op_c")])
        registered = register_ir_tools(server, ir)
        assert set(registered.keys()) == {"op_a", "op_b", "op_c"}

    def test_empty_operations_returns_empty(self) -> None:
        server = create_runtime_server("test")
        ir = _ir(operations=[])
        registered = register_ir_tools(server, ir)
        assert registered == {}


class TestLoadServiceIr:
    def test_loads_valid_ir(self, tmp_path: Any) -> None:
        ir = _ir()
        path = tmp_path / "ir.json"
        path.write_text(ir.model_dump_json(indent=2))
        loaded = load_service_ir(path)
        assert loaded.service_name == "loader-test"

    def test_raises_on_missing_file(self, tmp_path: Any) -> None:
        with pytest.raises(RuntimeLoadError, match="Unable to read"):
            load_service_ir(tmp_path / "nonexistent.json")

    def test_raises_on_invalid_json(self, tmp_path: Any) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{invalid json")
        with pytest.raises(RuntimeLoadError, match="Invalid ServiceIR"):
            load_service_ir(path)

    def test_raises_on_invalid_ir(self, tmp_path: Any) -> None:
        path = tmp_path / "incomplete.json"
        path.write_text('{"protocol": "rest"}')
        with pytest.raises(RuntimeLoadError, match="Invalid ServiceIR"):
            load_service_ir(path)


class TestDefaultToolHandler:
    def test_returns_not_implemented(self) -> None:
        op = _op()
        result = _default_tool_handler(op, {"key": "val"})
        assert result["status"] == "not_implemented"
        assert result["operation_id"] == "getItem"
        assert result["arguments"] == {"key": "val"}
