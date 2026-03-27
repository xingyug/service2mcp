"""Integration test: MCP runtime resource & prompt registration (IRX-007, IRX-008).

Full path test:
1. Create an IR with operations, resources, and prompts
2. Load into runtime
3. Verify list_tools, list_resources, list_prompts
4. Verify read_resource returns static content
5. Verify get_prompt returns rendered template
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.mcp_runtime.loader import (
    create_runtime_server,
    register_ir_prompts,
    register_ir_resources,
    register_ir_tools,
)
from libs.ir.models import (
    Operation,
    Param,
    PromptArgument,
    PromptDefinition,
    ResourceDefinition,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
)


def _make_param(**overrides: Any) -> Param:
    defaults: dict[str, Any] = {
        "name": "id",
        "type": "integer",
        "required": True,
    }
    return Param(**(defaults | overrides))


def _make_risk(level: RiskLevel = RiskLevel.safe) -> RiskMetadata:
    return RiskMetadata(
        writes_state=level != RiskLevel.safe,
        destructive=level == RiskLevel.dangerous,
        risk_level=level,
        confidence=0.9,
    )


def _make_op(id: str = "list_pets", **overrides: Any) -> Operation:
    defaults: dict[str, Any] = {
        "id": id,
        "name": f"Op {id}",
        "description": f"Desc {id}",
        "method": "GET",
        "path": f"/{id}",
        "params": [_make_param()],
        "risk": _make_risk(),
        "enabled": True,
    }
    return Operation(**(defaults | overrides))


def _build_test_ir() -> ServiceIR:
    """Build an IR with 2 operations, 1 resource, 1 prompt."""
    return ServiceIR(
        source_hash="test-hash-123",
        protocol="openapi",
        service_name="test-service",
        base_url="https://test.example.com",
        operations=[
            _make_op("list_items"),
            _make_op("get_item"),
        ],
        resource_definitions=[
            ResourceDefinition(
                id="test-schema",
                name="Test Schema",
                description="Schema for test service",
                uri="service:///test-service/schema",
                mime_type="application/json",
                content_type="static",
                content='{"service": "test-service", "version": "1.0"}',
            ),
        ],
        prompt_definitions=[
            PromptDefinition(
                id="explore-test",
                name="Explore Test Service",
                description="Explore available operations",
                template=(
                    "List operations for {service_name}. "
                    "Focus on {focus_area}."
                ),
                arguments=[
                    PromptArgument(
                        name="service_name",
                        description="Service name",
                        required=False,
                        default="test-service",
                    ),
                    PromptArgument(
                        name="focus_area",
                        description="Area to focus on",
                        required=True,
                    ),
                ],
                tool_ids=["list_items", "get_item"],
            ),
        ],
    )


class TestResourceRegistration:
    def test_register_static_resources(self) -> None:
        server = create_runtime_server("test")
        ir = _build_test_ir()
        registered = register_ir_resources(server, ir)
        assert len(registered) == 1
        assert registered[0].id == "test-schema"

    def test_dynamic_resources_skipped(self) -> None:
        server = create_runtime_server("test")
        ir = ServiceIR(
            source_hash="hash",
            protocol="rest",
            service_name="svc",
            base_url="https://example.com",
            operations=[_make_op("op1")],
            resource_definitions=[
                ResourceDefinition(
                    id="dynamic",
                    name="Dynamic",
                    uri="service:///svc/dynamic",
                    content_type="dynamic",
                    operation_id="op1",
                ),
            ],
        )
        registered = register_ir_resources(server, ir)
        assert len(registered) == 0


class TestPromptRegistration:
    def test_register_prompts(self) -> None:
        server = create_runtime_server("test")
        ir = _build_test_ir()
        registered = register_ir_prompts(server, ir)
        assert len(registered) == 1
        assert registered[0].id == "explore-test"

    def test_register_prompt_with_no_arguments(self) -> None:
        server = create_runtime_server("test")
        ir = ServiceIR(
            source_hash="hash",
            protocol="rest",
            service_name="svc",
            base_url="https://example.com",
            operations=[_make_op("op1")],
            prompt_definitions=[
                PromptDefinition(
                    id="simple",
                    name="Simple Prompt",
                    template="Hello world",
                    tool_ids=["op1"],
                ),
            ],
        )
        registered = register_ir_prompts(server, ir)
        assert len(registered) == 1


class TestEndToEndRegistration:
    """IRX-008: Full path integration test."""

    @pytest.mark.asyncio
    async def test_list_tools_returns_registered_tools(self) -> None:
        server = create_runtime_server("test")
        ir = _build_test_ir()
        register_ir_tools(server, ir)
        tools = await server.list_tools()
        assert len(tools) == 2
        tool_names = {t.name for t in tools}
        assert tool_names == {"list_items", "get_item"}

    @pytest.mark.asyncio
    async def test_list_resources_returns_registered_resources(
        self,
    ) -> None:
        server = create_runtime_server("test")
        ir = _build_test_ir()
        register_ir_resources(server, ir)
        resources = await server.list_resources()
        assert len(resources) == 1
        assert resources[0].name == "Test Schema"
        assert str(resources[0].uri) == "service:///test-service/schema"

    @pytest.mark.asyncio
    async def test_list_prompts_returns_registered_prompts(self) -> None:
        server = create_runtime_server("test")
        ir = _build_test_ir()
        register_ir_prompts(server, ir)
        prompts = await server.list_prompts()
        assert len(prompts) == 1
        assert prompts[0].name == "Explore Test Service"

    @pytest.mark.asyncio
    async def test_read_resource_returns_static_content(self) -> None:
        server = create_runtime_server("test")
        ir = _build_test_ir()
        register_ir_resources(server, ir)
        content = await server.read_resource(
            "service:///test-service/schema",
        )
        # Content should be the static JSON string
        assert "test-service" in str(content)
        assert "1.0" in str(content)

    @pytest.mark.asyncio
    async def test_get_prompt_returns_rendered_template(self) -> None:
        server = create_runtime_server("test")
        ir = _build_test_ir()
        register_ir_prompts(server, ir)
        result = await server.get_prompt(
            "Explore Test Service",
            {"service_name": "my-api", "focus_area": "security"},
        )
        # Result should contain the rendered template
        messages = result.messages
        assert len(messages) >= 1
        text = messages[0].content.text  # type: ignore[union-attr]
        assert "my-api" in text
        assert "security" in text

    @pytest.mark.asyncio
    async def test_full_registration_all_three_types(self) -> None:
        """Verify all three capability types register correctly."""
        server = create_runtime_server("test")
        ir = _build_test_ir()

        ops = register_ir_tools(server, ir)
        resources = register_ir_resources(server, ir)
        prompts = register_ir_prompts(server, ir)

        assert len(ops) == 2
        assert len(resources) == 1
        assert len(prompts) == 1

        listed_tools = await server.list_tools()
        listed_resources = await server.list_resources()
        listed_prompts = await server.list_prompts()

        assert len(listed_tools) == 2
        assert len(listed_resources) == 1
        assert len(listed_prompts) == 1
