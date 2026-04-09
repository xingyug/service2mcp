"""E2E: IR composition pipeline — compose multiple ServiceIR objects into a federated IR.

Tests the full compose_irs() pipeline covering:
- Multi-protocol composition producing 'federated' protocol
- Single-protocol composition preserving the original protocol
- Operation, event, resource, prompt, chain, and tool-group merging with prefixed IDs
- Conflict detection and error handling for duplicate operation IDs
- Composed IR validation through PreDeployValidator schema check
"""

from __future__ import annotations

import pytest

from libs.ir.compose import CompositionConflictError, CompositionStrategy, compose_irs
from libs.ir.models import (
    AuthConfig,
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    Operation,
    OperationChain,
    Param,
    PromptDefinition,
    ResourceDefinition,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    ToolGroup,
)
from libs.validator.pre_deploy import PreDeployValidator

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_operation(
    op_id: str,
    *,
    risk_level: RiskLevel = RiskLevel.safe,
    method: str = "GET",
    path: str = "/test",
    tags: list[str] | None = None,
) -> Operation:
    return Operation(
        id=op_id,
        name=op_id,
        description=f"Operation {op_id}",
        method=method,
        path=path,
        risk=RiskMetadata(risk_level=risk_level, confidence=0.9),
        tags=tags or [],
        params=[Param(name="q", type="string", required=False)],
    )


def _make_ir(
    name: str,
    protocol: str,
    operations: list[Operation],
    *,
    events: list[EventDescriptor] | None = None,
    resources: list[ResourceDefinition] | None = None,
    prompts: list[PromptDefinition] | None = None,
    chains: list[OperationChain] | None = None,
    groups: list[ToolGroup] | None = None,
) -> ServiceIR:
    return ServiceIR(
        source_url=f"https://example.com/{name}",
        source_hash="abc123",
        protocol=protocol,
        service_name=name,
        service_description=f"Service {name}",
        base_url=f"https://api.{name}.example.com",
        auth=AuthConfig(),
        operations=operations,
        event_descriptors=events or [],
        resource_definitions=resources or [],
        prompt_definitions=prompts or [],
        operation_chains=chains or [],
        tool_grouping=groups or [],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMultiProtocolComposition:
    """Compose IRs from different protocols — must produce 'federated'."""

    async def test_federated_protocol_from_mixed_protocols(self) -> None:
        openapi_ir = _make_ir("petstore", "openapi", [_make_operation("list_pets")])
        grpc_ir = _make_ir("inventory", "grpc", [_make_operation("get_item")])
        graphql_ir = _make_ir("catalog", "graphql", [_make_operation("search")])

        merged = compose_irs([openapi_ir, grpc_ir, graphql_ir])

        assert merged.protocol == "federated"
        assert len(merged.operations) == 3

    async def test_single_protocol_preserved(self) -> None:
        ir1 = _make_ir("svc_a", "openapi", [_make_operation("op_a")])
        ir2 = _make_ir("svc_b", "openapi", [_make_operation("op_b")])

        merged = compose_irs([ir1, ir2])

        assert merged.protocol == "openapi"


class TestOperationMerging:
    """Operations from each IR are merged with prefixed IDs."""

    async def test_prefixed_operation_ids(self) -> None:
        ir1 = _make_ir("alpha", "openapi", [_make_operation("list")])
        ir2 = _make_ir("beta", "graphql", [_make_operation("search")])

        merged = compose_irs([ir1, ir2])

        op_ids = {op.id for op in merged.operations}
        assert "alpha_list" in op_ids
        assert "beta_search" in op_ids

    async def test_no_prefix_strategy(self) -> None:
        ir1 = _make_ir("alpha", "openapi", [_make_operation("list")])
        ir2 = _make_ir("beta", "graphql", [_make_operation("search")])

        strategy = CompositionStrategy(prefix_operation_ids=False)
        merged = compose_irs([ir1, ir2], strategy=strategy)

        op_ids = {op.id for op in merged.operations}
        assert "list" in op_ids
        assert "search" in op_ids


class TestEventResourcePromptMerging:
    """Events, resources, prompts are merged from all source IRs."""

    async def test_events_merged(self) -> None:
        op1 = _make_operation("ws_op")
        evt1 = EventDescriptor(
            id="evt1",
            name="stream",
            transport=EventTransport.websocket,
            support=EventSupportLevel.unsupported,
        )
        ir1 = _make_ir("ws_svc", "openapi", [op1], events=[evt1])

        op2 = _make_operation("sse_op")
        evt2 = EventDescriptor(
            id="evt2",
            name="feed",
            transport=EventTransport.sse,
            support=EventSupportLevel.unsupported,
        )
        ir2 = _make_ir("sse_svc", "openapi", [op2], events=[evt2])

        merged = compose_irs([ir1, ir2])
        event_ids = {e.id for e in merged.event_descriptors}
        assert "ws_svc_evt1" in event_ids
        assert "sse_svc_evt2" in event_ids

    async def test_resources_merged(self) -> None:
        op1 = _make_operation("read_op")
        res1 = ResourceDefinition(
            id="res1",
            name="schema",
            uri="service://alpha/schema",
            content="{}",
        )
        ir1 = _make_ir("alpha", "openapi", [op1], resources=[res1])

        op2 = _make_operation("read_op2")
        res2 = ResourceDefinition(
            id="res2",
            name="schema2",
            uri="service://beta/schema",
            content="{}",
        )
        ir2 = _make_ir("beta", "openapi", [op2], resources=[res2])

        merged = compose_irs([ir1, ir2])
        assert len(merged.resource_definitions) == 2

    async def test_prompts_merged(self) -> None:
        op1 = _make_operation("op1")
        prompt1 = PromptDefinition(
            id="p1",
            name="helper1",
            template="Use {tool}",
            tool_ids=["op1"],
        )
        ir1 = _make_ir("alpha", "openapi", [op1], prompts=[prompt1])

        op2 = _make_operation("op2")
        prompt2 = PromptDefinition(
            id="p2",
            name="helper2",
            template="Query {table}",
            tool_ids=["op2"],
        )
        ir2 = _make_ir("beta", "graphql", [op2], prompts=[prompt2])

        merged = compose_irs([ir1, ir2])
        assert len(merged.prompt_definitions) == 2

    async def test_chains_and_groups_merged(self) -> None:
        ops1 = [_make_operation("step1"), _make_operation("step2")]
        chain1 = OperationChain(id="c1", name="workflow", steps=["step1", "step2"])
        group1 = ToolGroup(id="g1", label="Admin", operation_ids=["step1", "step2"])
        ir1 = _make_ir("alpha", "openapi", ops1, chains=[chain1], groups=[group1])

        ops2 = [_make_operation("fetch")]
        ir2 = _make_ir("beta", "graphql", ops2)

        merged = compose_irs([ir1, ir2])
        assert len(merged.operation_chains) == 1
        assert "alpha_step1" in merged.operation_chains[0].steps
        assert len(merged.tool_grouping) == 1
        assert "alpha_step1" in merged.tool_grouping[0].operation_ids


class TestConflictDetection:
    """Duplicate operation IDs should raise or be skipped based on strategy."""

    async def test_duplicate_ids_raise_by_default(self) -> None:
        ir1 = _make_ir("alpha", "openapi", [_make_operation("dup")])
        ir2 = _make_ir("alpha", "openapi", [_make_operation("dup")])

        with pytest.raises(CompositionConflictError, match="Duplicate operation ID"):
            compose_irs([ir1, ir2])

    async def test_duplicate_ids_no_prefix_raise(self) -> None:
        ir1 = _make_ir("svc1", "openapi", [_make_operation("shared")])
        ir2 = _make_ir("svc2", "openapi", [_make_operation("shared")])

        strategy = CompositionStrategy(prefix_operation_ids=False, fail_on_conflict=True)
        with pytest.raises(CompositionConflictError):
            compose_irs([ir1, ir2], strategy=strategy)

    async def test_duplicate_ids_skipped_when_allowed(self) -> None:
        ir1 = _make_ir("svc1", "openapi", [_make_operation("shared")])
        ir2 = _make_ir("svc2", "openapi", [_make_operation("shared")])

        strategy = CompositionStrategy(prefix_operation_ids=False, fail_on_conflict=False)
        merged = compose_irs([ir1, ir2], strategy=strategy)
        # First instance kept, second skipped
        assert len(merged.operations) == 1


class TestComposedIRPassesValidation:
    """The merged IR must pass schema validation."""

    async def test_composed_ir_validates(self) -> None:
        ir1 = _make_ir("petstore", "openapi", [_make_operation("list_pets")])
        ir2 = _make_ir("catalog", "graphql", [_make_operation("search_catalog")])

        merged = compose_irs([ir1, ir2])

        async with PreDeployValidator() as validator:
            report = await validator.validate(merged)

        schema_result = report.get_result("schema")
        assert schema_result.passed, schema_result.details


class TestMetadataInComposedIR:
    """Verify metadata on the federated IR."""

    async def test_metadata_populated(self) -> None:
        ir1 = _make_ir("svc_a", "openapi", [_make_operation("op_a")])
        ir2 = _make_ir("svc_b", "grpc", [_make_operation("op_b")])

        merged = compose_irs([ir1, ir2])

        assert merged.metadata["federated"] is True
        assert merged.metadata["source_count"] == 2
        assert set(merged.metadata["source_protocols"]) == {"openapi", "grpc"}
        assert set(merged.metadata["source_services"]) == {"svc_a", "svc_b"}

    async def test_custom_service_name(self) -> None:
        ir1 = _make_ir("a", "openapi", [_make_operation("op_a")])
        ir2 = _make_ir("b", "openapi", [_make_operation("op_b")])

        strategy = CompositionStrategy(merged_service_name="my-federated-svc")
        merged = compose_irs([ir1, ir2], strategy=strategy)

        assert merged.service_name == "my-federated-svc"


class TestEdgeCases:
    """Edge cases for compose_irs."""

    async def test_empty_list_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot compose empty"):
            compose_irs([])

    async def test_single_ir_returns_itself(self) -> None:
        ir = _make_ir("only", "openapi", [_make_operation("op")])
        result = compose_irs([ir])
        assert result is ir
