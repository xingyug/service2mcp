"""Tests for IR composition."""

from __future__ import annotations

import pytest

from libs.ir.compose import CompositionConflictError, CompositionStrategy, compose_irs
from libs.ir.models import (
    AuthConfig,
    AuthType,
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    Operation,
    OperationChain,
    PromptDefinition,
    ResourceDefinition,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
    ToolGroup,
)


def _make_op(op_id: str, *, name: str | None = None, method: str = "GET") -> Operation:
    """Create a minimal Operation for testing."""
    return Operation(
        id=op_id,
        name=name or op_id,
        description=f"Test operation {op_id}",
        method=method,
        path=f"/{op_id}",
        risk=RiskMetadata(risk_level=RiskLevel.safe, source=SourceType.extractor),
    )


def _make_ir(
    name: str,
    *,
    protocol: str = "openapi",
    operations: list[Operation] | None = None,
    event_descriptors: list[EventDescriptor] | None = None,
    resource_definitions: list[ResourceDefinition] | None = None,
    prompt_definitions: list[PromptDefinition] | None = None,
    operation_chains: list[OperationChain] | None = None,
    tool_grouping: list[ToolGroup] | None = None,
) -> ServiceIR:
    """Create a test ServiceIR with reasonable defaults."""
    return ServiceIR(
        source_url=f"https://{name}.example.com/spec",
        source_hash=f"hash_{name}",
        protocol=protocol,
        service_name=name,
        service_description=f"Service {name}",
        base_url=f"https://{name}.example.com",
        auth=AuthConfig(type=AuthType.none),
        operations=operations or [],
        event_descriptors=event_descriptors or [],
        resource_definitions=resource_definitions or [],
        prompt_definitions=prompt_definitions or [],
        operation_chains=operation_chains or [],
        tool_grouping=tool_grouping or [],
    )


# ── Basic validation ──────────────────────────────────────────────────────


class TestComposeValidation:
    def test_compose_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot compose empty"):
            compose_irs([])

    def test_compose_single_ir_returns_same(self) -> None:
        ir = _make_ir("alpha", operations=[_make_op("op1")])
        result = compose_irs([ir])
        assert result is ir


# ── Operation merging ─────────────────────────────────────────────────────


class TestOperationMerging:
    def test_compose_two_irs_merges_operations(self) -> None:
        ir_a = _make_ir("alpha", operations=[_make_op("listUsers")])
        ir_b = _make_ir("beta", operations=[_make_op("getItems")])
        merged = compose_irs([ir_a, ir_b])

        op_ids = {op.id for op in merged.operations}
        assert "alpha_listUsers" in op_ids
        assert "beta_getItems" in op_ids
        assert len(merged.operations) == 2

    def test_compose_prefixes_operation_ids(self) -> None:
        ir_a = _make_ir("svcA", operations=[_make_op("op1", name="op1")])
        ir_b = _make_ir("svcB", operations=[_make_op("op2", name="op2")])
        merged = compose_irs([ir_a, ir_b])

        op = next(o for o in merged.operations if o.id == "svcA_op1")
        assert op.name == "svcA_op1"

    def test_compose_no_prefix_option(self) -> None:
        ir_a = _make_ir("alpha", operations=[_make_op("op1")])
        ir_b = _make_ir("beta", operations=[_make_op("op2")])
        strat = CompositionStrategy(prefix_operation_ids=False)
        merged = compose_irs([ir_a, ir_b], strategy=strat)

        op_ids = {op.id for op in merged.operations}
        assert op_ids == {"op1", "op2"}

    def test_compose_duplicate_ops_with_fail_on_conflict(self) -> None:
        ir_a = _make_ir("alpha", operations=[_make_op("shared")])
        ir_b = _make_ir("beta", operations=[_make_op("shared")])
        strat = CompositionStrategy(prefix_operation_ids=False, fail_on_conflict=True)

        with pytest.raises(CompositionConflictError, match="Duplicate operation ID: shared"):
            compose_irs([ir_a, ir_b], strategy=strat)

    def test_compose_duplicate_ops_skip_when_no_fail(self) -> None:
        ir_a = _make_ir("alpha", operations=[_make_op("shared")])
        ir_b = _make_ir("beta", operations=[_make_op("shared")])
        strat = CompositionStrategy(prefix_operation_ids=False, fail_on_conflict=False)
        merged = compose_irs([ir_a, ir_b], strategy=strat)

        assert len(merged.operations) == 1
        assert merged.operations[0].id == "shared"


# ── Event descriptor merging ──────────────────────────────────────────────


class TestEventMerging:
    def test_compose_merges_event_descriptors(self) -> None:
        op_a = _make_op("opA")
        evt_a = EventDescriptor(
            id="evt1",
            name="evt1",
            transport=EventTransport.sse,
            support=EventSupportLevel.supported,
            operation_id="opA",
        )
        ir_a = _make_ir("alpha", operations=[op_a], event_descriptors=[evt_a])

        evt_b = EventDescriptor(
            id="evt2",
            name="evt2",
            transport=EventTransport.webhook,
            support=EventSupportLevel.planned,
        )
        ir_b = _make_ir("beta", event_descriptors=[evt_b])

        merged = compose_irs([ir_a, ir_b])

        event_ids = {e.id for e in merged.event_descriptors}
        assert "alpha_evt1" in event_ids
        assert "beta_evt2" in event_ids

        evt1 = next(e for e in merged.event_descriptors if e.id == "alpha_evt1")
        assert evt1.operation_id == "alpha_opA"


# ── Resource merging ──────────────────────────────────────────────────────


class TestResourceMerging:
    def test_compose_merges_resources(self) -> None:
        res_a = ResourceDefinition(
            id="schema",
            name="schema",
            uri="service://alpha/schema",
            content="{}",
            content_type="static",
        )
        ir_a = _make_ir("alpha", resource_definitions=[res_a])

        res_b = ResourceDefinition(
            id="schema",
            name="schema",
            uri="service://beta/schema",
            content="{}",
            content_type="static",
        )
        ir_b = _make_ir("beta", resource_definitions=[res_b])

        merged = compose_irs([ir_a, ir_b])

        resource_ids = {r.id for r in merged.resource_definitions}
        assert "alpha_schema" in resource_ids
        assert "beta_schema" in resource_ids
        assert len(merged.resource_definitions) == 2


# ── Prompt merging ────────────────────────────────────────────────────────


class TestPromptMerging:
    def test_compose_merges_prompts_with_tool_id_rewrite(self) -> None:
        op_a = _make_op("listUsers")
        prompt_a = PromptDefinition(
            id="p1",
            name="list_prompt",
            template="List all users using {tool}",
            tool_ids=["listUsers"],
        )
        ir_a = _make_ir("alpha", operations=[op_a], prompt_definitions=[prompt_a])

        op_b = _make_op("getItems")
        prompt_b = PromptDefinition(
            id="p2",
            name="get_prompt",
            template="Get items using {tool}",
            tool_ids=["getItems"],
        )
        ir_b = _make_ir("beta", operations=[op_b], prompt_definitions=[prompt_b])

        merged = compose_irs([ir_a, ir_b])

        p1 = next(p for p in merged.prompt_definitions if p.id == "alpha_p1")
        assert p1.tool_ids == ["alpha_listUsers"]
        assert p1.name == "alpha_list_prompt"

        p2 = next(p for p in merged.prompt_definitions if p.id == "beta_p2")
        assert p2.tool_ids == ["beta_getItems"]


# ── Protocol handling ─────────────────────────────────────────────────────


class TestProtocolHandling:
    def test_compose_mixed_protocols_sets_federated(self) -> None:
        ir_a = _make_ir("alpha", protocol="openapi")
        ir_b = _make_ir("beta", protocol="graphql")
        merged = compose_irs([ir_a, ir_b])
        assert merged.protocol == "federated"

    def test_compose_same_protocol_preserves_it(self) -> None:
        ir_a = _make_ir("alpha", protocol="openapi")
        ir_b = _make_ir("beta", protocol="openapi")
        merged = compose_irs([ir_a, ir_b])
        assert merged.protocol == "openapi"


# ── Metadata ──────────────────────────────────────────────────────────────


class TestMetadata:
    def test_compose_metadata_includes_source_info(self) -> None:
        ir_a = _make_ir("alpha", protocol="openapi")
        ir_b = _make_ir("beta", protocol="graphql")
        merged = compose_irs([ir_a, ir_b])

        assert merged.metadata["federated"] is True
        assert merged.metadata["source_count"] == 2
        assert sorted(merged.metadata["source_protocols"]) == ["graphql", "openapi"]
        assert merged.metadata["source_services"] == ["alpha", "beta"]


# ── Custom strategy ───────────────────────────────────────────────────────


class TestCustomStrategy:
    def test_compose_custom_service_name(self) -> None:
        ir_a = _make_ir("alpha")
        ir_b = _make_ir("beta")
        strat = CompositionStrategy(
            merged_service_name="my-federated-svc",
            merged_description="My custom description",
        )
        merged = compose_irs([ir_a, ir_b], strategy=strat)
        assert merged.service_name == "my-federated-svc"
        assert merged.service_description == "My custom description"

    def test_compose_default_service_name_joins_sources(self) -> None:
        ir_a = _make_ir("alpha")
        ir_b = _make_ir("beta")
        merged = compose_irs([ir_a, ir_b])
        assert merged.service_name == "alpha-beta"


# ── Chain and group merging ───────────────────────────────────────────────


class TestChainAndGroupMerging:
    def test_compose_chains_rewrite_step_references(self) -> None:
        op_a = _make_op("step1")
        op_b = _make_op("step2")
        chain = OperationChain(id="c1", name="flow", steps=["step1", "step2"])
        ir = _make_ir("alpha", operations=[op_a, op_b], operation_chains=[chain])
        ir2 = _make_ir("beta")

        merged = compose_irs([ir, ir2])
        merged_chain = merged.operation_chains[0]
        assert merged_chain.steps == ["alpha_step1", "alpha_step2"]

    def test_compose_groups_rewrite_operation_ids(self) -> None:
        op = _make_op("op1")
        group = ToolGroup(id="g1", label="grp", operation_ids=["op1"])
        ir = _make_ir("alpha", operations=[op], tool_grouping=[group])
        ir2 = _make_ir("beta")

        merged = compose_irs([ir, ir2])
        merged_group = merged.tool_grouping[0]
        assert merged_group.operation_ids == ["alpha_op1"]


# ── Source hash ───────────────────────────────────────────────────────────


class TestSourceHash:
    def test_compose_produces_deterministic_hash(self) -> None:
        ir_a = _make_ir("alpha")
        ir_b = _make_ir("beta")
        m1 = compose_irs([ir_a, ir_b])
        m2 = compose_irs([ir_a, ir_b])
        assert m1.source_hash == m2.source_hash
        assert m1.source_hash != ir_a.source_hash


# ── Duplicate conflict tracking for events/resources/prompts ─────────────


class TestDuplicateConflictTracking:
    """Verify that duplicate events/resources/prompts are tracked in conflicts."""

    def test_duplicate_event_tracked_in_conflicts(self) -> None:
        evt = EventDescriptor(
            id="evt1",
            name="evt1",
            transport=EventTransport.sse,
            support=EventSupportLevel.supported,
        )
        ir_a = _make_ir("alpha", event_descriptors=[evt])
        ir_b = _make_ir("beta", event_descriptors=[evt])
        strat = CompositionStrategy(prefix_operation_ids=False, fail_on_conflict=False)
        merged = compose_irs([ir_a, ir_b], strategy=strat)
        assert len(merged.event_descriptors) == 1

    def test_duplicate_event_raises_on_fail_on_conflict(self) -> None:
        evt = EventDescriptor(
            id="evt1",
            name="evt1",
            transport=EventTransport.sse,
            support=EventSupportLevel.supported,
        )
        ir_a = _make_ir("alpha", event_descriptors=[evt])
        ir_b = _make_ir("beta", event_descriptors=[evt])
        strat = CompositionStrategy(prefix_operation_ids=False, fail_on_conflict=True)
        with pytest.raises(CompositionConflictError, match="Duplicate event descriptor"):
            compose_irs([ir_a, ir_b], strategy=strat)

    def test_duplicate_resource_tracked_in_conflicts(self) -> None:
        res = ResourceDefinition(
            id="schema",
            name="schema",
            uri="service://x/schema",
            content="{}",
            content_type="static",
        )
        ir_a = _make_ir("alpha", resource_definitions=[res])
        ir_b = _make_ir("beta", resource_definitions=[res])
        strat = CompositionStrategy(prefix_operation_ids=False, fail_on_conflict=False)
        merged = compose_irs([ir_a, ir_b], strategy=strat)
        assert len(merged.resource_definitions) == 1

    def test_duplicate_resource_raises_on_fail_on_conflict(self) -> None:
        res = ResourceDefinition(
            id="schema",
            name="schema",
            uri="service://x/schema",
            content="{}",
            content_type="static",
        )
        ir_a = _make_ir("alpha", resource_definitions=[res])
        ir_b = _make_ir("beta", resource_definitions=[res])
        strat = CompositionStrategy(prefix_operation_ids=False, fail_on_conflict=True)
        with pytest.raises(CompositionConflictError, match="Duplicate resource"):
            compose_irs([ir_a, ir_b], strategy=strat)

    def test_duplicate_prompt_tracked_in_conflicts(self) -> None:
        prompt = PromptDefinition(
            id="p1",
            name="prompt1",
            template="Do something",
            tool_ids=[],
        )
        ir_a = _make_ir("alpha", prompt_definitions=[prompt])
        ir_b = _make_ir("beta", prompt_definitions=[prompt])
        strat = CompositionStrategy(prefix_operation_ids=False, fail_on_conflict=False)
        merged = compose_irs([ir_a, ir_b], strategy=strat)
        assert len(merged.prompt_definitions) == 1

    def test_duplicate_prompt_raises_on_fail_on_conflict(self) -> None:
        prompt = PromptDefinition(
            id="p1",
            name="prompt1",
            template="Do something",
            tool_ids=[],
        )
        ir_a = _make_ir("alpha", prompt_definitions=[prompt])
        ir_b = _make_ir("beta", prompt_definitions=[prompt])
        strat = CompositionStrategy(prefix_operation_ids=False, fail_on_conflict=True)
        with pytest.raises(CompositionConflictError, match="Duplicate prompt"):
            compose_irs([ir_a, ir_b], strategy=strat)
