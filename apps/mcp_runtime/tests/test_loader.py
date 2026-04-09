"""Unit tests for MCP runtime loader — build_tool_function, register_ir_tools, param naming."""

from __future__ import annotations

import gzip
import inspect
import keyword
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError, create_model

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
    path: str | None = None,
    params: list[Param] | None = None,
    enabled: bool = True,
    risk: RiskMetadata | None = None,
) -> Operation:
    return Operation(
        id=op_id,
        name=op_id,
        description=f"Test op {op_id}",
        method=method,
        path=path or f"/{op_id}",
        params=params or [],
        risk=risk
        or RiskMetadata(
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


class TestCreateRuntimeServer:
    def test_enables_dns_rebinding_protection_by_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MCP_DISABLE_DNS_REBINDING_PROTECTION", raising=False)

        server = create_runtime_server("test")

        assert server.settings.transport_security.enable_dns_rebinding_protection is True

    def test_allows_explicit_dns_rebinding_opt_out(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("MCP_DISABLE_DNS_REBINDING_PROTECTION", "true")

        server = create_runtime_server("test")

        assert server.settings.transport_security.enable_dns_rebinding_protection is False

    def test_reads_allowed_hosts_and_origins_from_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MCP_DISABLE_DNS_REBINDING_PROTECTION", raising=False)
        monkeypatch.setenv(
            "MCP_ALLOWED_HOSTS",
            "runtime.test:*, runtime.test.ns.svc.cluster.local:*",
        )
        monkeypatch.setenv(
            "MCP_ALLOWED_ORIGINS",
            "https://runtime.test, https://runtime.test.ns.svc.cluster.local",
        )

        server = create_runtime_server("test")

        assert server.settings.transport_security.enable_dns_rebinding_protection is True
        assert server.settings.transport_security.allowed_hosts == [
            "runtime.test:*",
            "runtime.test.ns.svc.cluster.local:*",
        ]
        assert server.settings.transport_security.allowed_origins == [
            "https://runtime.test",
            "https://runtime.test.ns.svc.cluster.local",
        ]


class TestBuildToolFunction:
    @pytest.mark.asyncio
    async def test_generated_function_has_correct_signature(self) -> None:
        op = _op(
            params=[
                Param(name="item_id", type="string", required=True),
                Param(name="limit", type="integer", required=False, default=10),
            ]
        )
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

        async def handler(operation: Operation, args: dict[str, Any]) -> dict[str, Any]:
            return {"async": True, "query": args["q"]}

        fn, _ = build_tool_function(op, tool_handler=handler)
        result = await fn(q="hello")
        assert result == {"async": True, "query": "hello"}

    @pytest.mark.asyncio
    async def test_param_name_remapping(self) -> None:
        op = _op(
            params=[
                Param(name="item-id", type="string", required=True),
            ]
        )
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
        result = _python_parameter_name("id", existing_names={"id"})
        assert result == "id_2"

    def test_dedup_chain(self) -> None:
        result = _python_parameter_name("id", existing_names={"id", "id_2"})
        assert result == "id_3"


class TestRegisterIrTools:
    def test_disabled_operations_skipped(self) -> None:
        server = create_runtime_server("test")
        ir = _ir(
            operations=[
                _op("enabled_op", enabled=True),
                _op("disabled_op", enabled=False),
            ]
        )
        registered = register_ir_tools(server, ir)
        assert "enabled_op" in registered
        assert "disabled_op" not in registered

    def test_all_enabled_operations_registered(self) -> None:
        server = create_runtime_server("test")
        ir = _ir(operations=[_op("op_a"), _op("op_b"), _op("op_c")])
        registered = register_ir_tools(server, ir)
        assert set(registered.keys()) == {"op_a", "op_b", "op_c"}

    def test_enabled_mutating_operations_registered(self) -> None:
        server = create_runtime_server("test")
        ir = _ir(
            operations=[
                _op(
                    "create_order",
                    method="POST",
                    risk=RiskMetadata(
                        risk_level=RiskLevel.dangerous,
                        confidence=1.0,
                        source=SourceType.extractor,
                        writes_state=True,
                        destructive=False,
                        external_side_effect=True,
                        idempotent=False,
                    ),
                ),
                _op(
                    "delete_order",
                    method="DELETE",
                    risk=RiskMetadata(
                        risk_level=RiskLevel.dangerous,
                        confidence=1.0,
                        source=SourceType.extractor,
                        writes_state=True,
                        destructive=True,
                        external_side_effect=True,
                        idempotent=True,
                    ),
                ),
            ]
        )

        registered = register_ir_tools(server, ir)

        assert set(registered.keys()) == {"create_order", "delete_order"}
        assert registered["create_order"].method == "POST"
        assert registered["delete_order"].method == "DELETE"

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

    def test_loads_gzipped_ir(self, tmp_path: Any) -> None:
        ir = _ir()
        path = tmp_path / "ir.json.gz"
        path.write_bytes(gzip.compress(ir.model_dump_json(indent=2).encode("utf-8"), mtime=0))

        loaded = load_service_ir(path)

        assert loaded.service_name == "loader-test"

    def test_raises_on_invalid_json(self, tmp_path: Any) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{invalid json")
        with pytest.raises(RuntimeLoadError, match="Invalid ServiceIR"):
            load_service_ir(path)

    def test_raises_on_invalid_gzip_payload(self, tmp_path: Any) -> None:
        path = tmp_path / "bad.json.gz"
        path.write_bytes(b"not-a-gzip-stream")

        with pytest.raises(RuntimeLoadError, match="Unable to decode"):
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


class TestJsonSchemaAnnotations:
    """Params with json_schema produce structured Pydantic annotations."""

    def test_object_param_with_json_schema(self) -> None:
        """Object param with json_schema creates Pydantic model annotation."""
        param = Param(
            name="address",
            type="object",
            required=True,
            json_schema={
                "type": "object",
                "properties": {
                    "street": {"type": "string"},
                    "city": {"type": "string"},
                },
                "required": ["street", "city"],
            },
        )
        op = _op(params=[param])
        fn, _ = build_tool_function(op)
        sig = inspect.signature(fn)
        ann = sig.parameters["address"].annotation
        # Should be a Pydantic model, not dict[str, object]
        assert hasattr(ann, "model_json_schema")
        schema = ann.model_json_schema()
        assert "street" in schema["properties"]
        assert "city" in schema["properties"]

    def test_array_param_with_json_schema(self) -> None:
        """Array param with json_schema creates list[Model] annotation."""
        param = Param(
            name="items",
            type="array",
            required=True,
            json_schema={
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sku": {"type": "string"},
                        "qty": {"type": "integer"},
                    },
                    "required": ["sku", "qty"],
                },
            },
        )
        op = _op(params=[param])
        fn, _ = build_tool_function(op)
        sig = inspect.signature(fn)
        ann = sig.parameters["items"].annotation
        # Should be list[SomeModel]
        assert hasattr(ann, "__origin__") and ann.__origin__ is list

    def test_param_without_json_schema_unchanged(self) -> None:
        """Params without json_schema still use the simple type mapping."""
        param = Param(name="name", type="string", required=True)
        op = _op(params=[param])
        fn, _ = build_tool_function(op)
        sig = inspect.signature(fn)
        assert sig.parameters["name"].annotation is str


class TestSchemaDepthLimit:
    """Verify deeply nested schemas don't cause stack overflow."""

    def test_deeply_nested_schema_uses_fallback(self) -> None:
        from apps.mcp_runtime.loader import _resolve_schema_type

        schema: dict = {"type": "string"}
        for _ in range(20):
            schema = {"type": "object", "properties": {"nested": schema}}
        # Should not raise RecursionError
        result = _resolve_schema_type("deep", schema)
        assert result is not None

    def test_deeply_nested_array_uses_fallback(self) -> None:
        from apps.mcp_runtime.loader import _resolve_schema_type

        schema: dict = {"type": "string"}
        for _ in range(20):
            schema = {"type": "array", "items": schema}
        result = _resolve_schema_type("deep_arr", schema)
        assert result is not None


class TestUnwrapPydantic:
    """Verify that Pydantic model instances are converted to plain dicts."""

    def test_unwrap_pydantic_model_to_dict(self) -> None:
        from apps.mcp_runtime.loader import _unwrap_pydantic

        model_cls = create_model("Address", street=(str, ...), city=(str, ...))
        instance = model_cls(street="123 Main", city="NYC")
        result = _unwrap_pydantic(instance)
        assert isinstance(result, dict)
        assert result == {"street": "123 Main", "city": "NYC"}

    def test_unwrap_list_of_pydantic_models(self) -> None:
        from apps.mcp_runtime.loader import _unwrap_pydantic

        item_cls = create_model("Item", sku=(str, ...), qty=(int, ...))
        items = [item_cls(sku="X1", qty=1), item_cls(sku="X2", qty=2)]
        result = _unwrap_pydantic(items)
        assert isinstance(result, list)
        assert all(isinstance(r, dict) for r in result)
        assert result[0] == {"sku": "X1", "qty": 1}

    def test_unwrap_scalar_passthrough(self) -> None:
        from apps.mcp_runtime.loader import _unwrap_pydantic

        assert _unwrap_pydantic("hello") == "hello"
        assert _unwrap_pydantic(42) == 42
        assert _unwrap_pydantic(None) is None


class TestEnumJsonSchema:
    """Verify that json_schema with enum produces Literal type annotations."""

    def test_enum_param_produces_literal_type(self) -> None:
        param = Param(
            name="priority",
            type="string",
            required=True,
            json_schema={"type": "string", "enum": ["STANDARD", "EXPRESS", "OVERNIGHT"]},
        )
        op = _op(params=[param])
        fn, _ = build_tool_function(op)
        sig = inspect.signature(fn)
        ann = sig.parameters["priority"].annotation
        # Literal types have __args__
        assert hasattr(ann, "__args__")
        assert set(ann.__args__) == {"STANDARD", "EXPRESS", "OVERNIGHT"}

    def test_param_description_flows_to_mcp_schema(self) -> None:
        """Verify IR param descriptions appear in MCP tool inputSchema."""
        from typing import get_args, get_origin

        from annotated_types import BaseMetadata  # noqa: F401
        from pydantic.fields import FieldInfo

        params = [
            Param(name="q", type="string", required=False, description="Search keyword"),
            Param(name="topic", type="boolean", required=False, description="Limit to topic"),
            Param(name="no_desc", type="string", required=True, description=""),
        ]
        op = _op(params=params)
        fn, _ = build_tool_function(op)
        sig = inspect.signature(fn)

        # 'q' should have description via Annotated[..., Field(description=...)]
        q_ann = sig.parameters["q"].annotation
        assert get_origin(q_ann).__name__ == "Annotated" if get_origin(q_ann) else False
        q_metadata = get_args(q_ann)
        field_info = next(a for a in q_metadata if isinstance(a, FieldInfo))
        assert field_info.description == "Search keyword"

        # 'topic' should also have description
        topic_ann = sig.parameters["topic"].annotation
        topic_metadata = get_args(topic_ann)
        topic_field = next(a for a in topic_metadata if isinstance(a, FieldInfo))
        assert topic_field.description == "Limit to topic"

        # 'no_desc' should NOT have Annotated metadata (empty description)
        no_desc_ann = sig.parameters["no_desc"].annotation
        assert not hasattr(no_desc_ann, "__metadata__")


# ---------------------------------------------------------------------------
# Edge-case regression tests
# ---------------------------------------------------------------------------


class TestEdgeCaseParams:
    """Regression tests for unusual parameter names, types, and schemas."""

    def test_empty_param_name(self) -> None:
        """Empty param names are now rejected at the IR model level."""
        with pytest.raises(ValidationError):
            Param(name="", type="string", required=True)

    def test_numeric_prefix_param_name(self) -> None:
        op = _op(params=[Param(name="123abc", type="string")])
        fn, name_map = build_tool_function(op)
        python_name = next(iter(name_map))
        assert not python_name[0].isdigit(), "Python name must not start with digit"

    def test_python_keyword_param_names(self) -> None:
        op = _op(
            params=[
                Param(name="class", type="string"),
                Param(name="for", type="integer"),
            ]
        )
        fn, name_map = build_tool_function(op)
        for python_name in name_map:
            assert python_name.isidentifier()
            assert not keyword.iskeyword(python_name)

    def test_duplicate_param_names_get_unique_python_names(self) -> None:
        op = _op(
            params=[
                Param(name="same", type="string"),
                Param(name="same", type="integer"),
            ]
        )
        fn, name_map = build_tool_function(op)
        python_names = list(name_map.keys())
        assert len(python_names) == len(set(python_names)), "Python names must be unique"

    def test_unknown_param_type_defaults_to_any(self) -> None:
        op = _op(params=[Param(name="x", type="binary")])
        fn, name_map = build_tool_function(op)
        sig = inspect.signature(fn)
        assert "x" in sig.parameters

    def test_deeply_nested_json_schema(self) -> None:
        schema: dict[str, Any] = {"type": "object", "properties": {}}
        current = schema
        for i in range(20):
            child: dict[str, Any] = {"type": "object", "properties": {}}
            current["properties"][f"level_{i}"] = child
            current = child
        current["properties"]["leaf"] = {"type": "string"}

        op = _op(params=[Param(name="deep", type="object", json_schema=schema)])
        fn, name_map = build_tool_function(op)
        assert "deep" in name_map

    def test_json_schema_with_allof_fallback(self) -> None:
        schema = {
            "allOf": [
                {"type": "object", "properties": {"a": {"type": "string"}}},
            ]
        }
        op = _op(params=[Param(name="body", type="object", json_schema=schema)])
        fn, name_map = build_tool_function(op)
        assert "body" in name_map

    def test_empty_ir_registers_no_tools(self) -> None:
        ir = _ir(operations=[])
        server = create_runtime_server("empty-test")
        ops = register_ir_tools(server, ir)
        assert len(ops) == 0

    def test_array_type_without_json_schema_produces_list(self) -> None:
        """Array param without json_schema should still produce list annotation."""
        op = _op(params=[Param(name="ids", type="array", required=True)])
        fn, name_map = build_tool_function(op)
        sig = inspect.signature(fn)
        ann = sig.parameters["ids"].annotation
        assert ann is not None
        # Should be list[object], not a scalar type
        assert hasattr(ann, "__origin__") and ann.__origin__ is list

    def test_array_type_with_json_schema_produces_typed_list(self) -> None:
        """Array param with json_schema should produce list[str], etc."""
        schema = {"type": "array", "items": {"type": "string"}}
        op = _op(params=[Param(name="tags", type="array", json_schema=schema)])
        fn, name_map = build_tool_function(op)
        sig = inspect.signature(fn)
        ann = sig.parameters["tags"].annotation
        # Should be list[str] | None (optional since no required + no default)
        ann_str = str(ann)
        assert "list[str]" in ann_str

    @pytest.mark.asyncio
    async def test_duplicate_param_names_path_vs_body_no_overwrite(self) -> None:
        """When path and body params share the same IR name (e.g. 'index'),
        the optional body duplicate's default None must NOT overwrite the
        required path param value."""
        op = _op(
            method="POST",
            path="/repos/{owner}/{repo}/issues/{index}/blocks",
            params=[
                Param(name="owner", type="string", required=True),
                Param(name="repo", type="string", required=True),
                Param(name="index", type="string", required=True),
                # Body duplicates (same IR name, optional):
                Param(name="index", type="integer", required=False),
                Param(name="owner", type="string", required=False),
                Param(name="repo", type="string", required=False),
            ],
        )
        captured: dict[str, Any] = {}

        def handler(operation: Operation, args: dict[str, Any]) -> dict[str, Any]:
            captured.update(args)
            return {"ok": True}

        fn, name_map = build_tool_function(op, tool_handler=handler)

        # Simulate FastMCP sending required values + default None for optionals
        await fn(owner="myorg", repo="myrepo", index="42",
                 index_2=None, owner_2=None, repo_2=None)

        assert captured["owner"] == "myorg"
        assert captured["repo"] == "myrepo"
        assert captured["index"] == "42"

    @pytest.mark.asyncio
    async def test_duplicate_param_both_provided(self) -> None:
        """When both the path param and body duplicate are non-None,
        the later (body) value wins — it's an intentional override."""
        op = _op(
            method="POST",
            path="/items/{id}",
            params=[
                Param(name="id", type="string", required=True),
                Param(name="id", type="integer", required=False),
            ],
        )
        captured: dict[str, Any] = {}

        def handler(operation: Operation, args: dict[str, Any]) -> dict[str, Any]:
            captured.update(args)
            return {"ok": True}

        fn, _ = build_tool_function(op, tool_handler=handler)
        await fn(id="path_val", id_2=99)  # body override is non-None
        assert captured["id"] == 99

    @given(name=st.text(min_size=0, max_size=100))
    @settings(max_examples=200)
    def test_param_name_always_valid_identifier(self, name: str) -> None:
        from apps.mcp_runtime.loader import _python_parameter_name

        result = _python_parameter_name(name, existing_names=())
        assert result.isidentifier()
        assert not keyword.iskeyword(result)
