"""B-003 large-surface pilot integration tests.

Measures three coverage numbers against a 62-endpoint REST mock:
1. Endpoint discovery coverage
2. Generated MCP-tool coverage
3. Audited invocation pass rate

Includes both a black-box REST discovery pilot and a spec-first OpenAPI
pilot for side-by-side comparison.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import httpx
import pytest

from apps.mcp_runtime import create_app
from apps.proof_runner.live_llm_e2e import (
    ToolIntentCounts,
    _compute_tool_intent_counts,
)
from libs.enhancer.tool_grouping import ToolGrouper, apply_grouping
from libs.enhancer.tool_intent import bifurcate_descriptions, derive_tool_intents
from libs.extractors.base import SourceConfig
from libs.extractors.openapi import OpenAPIExtractor
from libs.extractors.rest import RESTExtractor
from libs.ir.models import ToolIntent
from libs.ir.schema import serialize_ir
from libs.validator.audit import (
    AuditPolicy,
    AuditThresholds,
    LargeSurfacePilotReport,
    ToolAuditSummary,
    check_thresholds,
)
from libs.validator.llm_judge import JudgeEvaluation, LLMJudge
from libs.validator.post_deploy import PostDeployValidator
from tests.fixtures.large_surface_rest_mock import (
    GROUND_TRUTH,
    build_large_surface_transport,
)

# Regression thresholds for the large-surface pilot baseline.
# Future extractor/enhancer changes must not regress below these values.
# Updated after iterative sub-resource inference + OPTIONS-authoritative probing.
PILOT_BASELINE_THRESHOLDS = AuditThresholds(
    min_audited_ratio=0.40,   # At least 40% of generated tools auditable
    max_failed=0,             # Zero audit failures after OPTIONS fix
    min_passed=10,            # At least 10 passing tools (was 1)
)

# Coverage baselines — minimum acceptable discovery and generation rates.
# Updated after iterative inference raised discovery from ~25% to ~64%.
PILOT_MIN_DISCOVERY_COVERAGE = 0.50   # 50% of ground-truth endpoints discovered
PILOT_MIN_GENERATION_COVERAGE = 0.40  # 40% of discovered endpoints get tools
PILOT_MIN_AUDIT_PASS_RATE = 0.90      # 90% of audited tools pass (was 50%)

# Spec-first pilot thresholds — stricter because spec extraction is deterministic.
SPEC_FIRST_THRESHOLDS = AuditThresholds(
    min_audited_ratio=0.40,   # At least 40% of generated tools auditable
    max_failed=0,             # Zero audit failures for spec-first
    min_passed=5,             # At least 5 passing tools
)
SPEC_FIRST_MIN_GENERATION_RATIO = 1.0   # Every spec op should produce a tool
SPEC_FIRST_MIN_AUDIT_PASS_RATE = 0.90   # 90% of audited tools pass

# Path to the large-surface OpenAPI spec fixture.
_LARGE_SURFACE_SPEC = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "openapi_specs"
    / "large_surface_api.yaml"
)


def _write_service_ir(tmp_path: Path, ir_json: str) -> Path:
    output_path = tmp_path / "large_surface_ir.json"
    output_path.write_text(ir_json, encoding="utf-8")
    return output_path


def _identify_unsupported_patterns(
    ground_truth_count: int,
    discovered_paths: set[str],
    audit_summary: ToolAuditSummary,
) -> list[str]:
    """Identify unsupported patterns from the pilot run."""
    patterns: list[str] = []

    # Discovery gaps
    all_ground_truth_paths = {path for _, path in GROUND_TRUTH}
    undiscovered = all_ground_truth_paths - discovered_paths
    if undiscovered:
        # Classify undiscovered endpoints by pattern
        nested_count = sum(1 for p in undiscovered if p.count("/") >= 5)
        write_count = sum(
            1
            for p in undiscovered
            for m, gp in GROUND_TRUTH
            if gp == p and m in {"POST", "PUT", "DELETE"}
        )
        if nested_count:
            patterns.append(
                f"Deeply nested resources not fully discovered ({nested_count} endpoints)"
            )
        if write_count:
            patterns.append(
                f"Write/mutation endpoints not discoverable via GET crawl ({write_count} endpoints)"
            )

    # Audit skip patterns
    skipped_results = [r for r in audit_summary.results if r.outcome == "skipped"]
    skip_reasons: dict[str, int] = {}
    for r in skipped_results:
        skip_reasons[r.reason] = skip_reasons.get(r.reason, 0) + 1
    for reason, count in sorted(skip_reasons.items(), key=lambda x: -x[1]):
        patterns.append(f"Skipped by policy: {reason} ({count} tools)")

    # Audit failure patterns
    failed_results = [r for r in audit_summary.results if r.outcome == "failed"]
    if failed_results:
        patterns.append(
            f"Audit invocation failures ({len(failed_results)} tools): "
            + ", ".join(r.tool_name for r in failed_results[:5])
        )

    return patterns


@pytest.mark.asyncio
async def test_large_surface_rest_pilot_measures_three_coverage_numbers(
    tmp_path: Path,
) -> None:
    """B-003 pilot: measure discovery, generation, and audit coverage on 62 endpoints."""

    # --- Phase 1: Discovery + Extraction ---
    mock_transport = build_large_surface_transport()
    discovery_client = httpx.Client(transport=mock_transport, follow_redirects=True)
    extractor = RESTExtractor(client=discovery_client, max_pages=20)

    try:
        service_ir = extractor.extract(
            SourceConfig(
                url="https://large-surface.example.com/api",
                hints={"protocol": "rest", "service_name": "large-surface-pilot"},
            )
        )
    finally:
        extractor.close()

    discovered_paths = set(service_ir.metadata.get("discovered_paths", []))
    ground_truth_paths = {path for _, path in GROUND_TRUTH}
    ground_truth_count = len(ground_truth_paths)

    # Count unique ground-truth paths that are covered by discovered paths.
    # A discovered path "covers" a ground-truth path if they match after
    # normalizing path parameters.
    matched_ground_truth = set()
    for gt_path in ground_truth_paths:
        for disc_path in discovered_paths:
            # Exact match
            if disc_path == gt_path:
                matched_ground_truth.add(gt_path)
                break
            # Template match (discovered paths may have concrete values)
            if gt_path.replace("{", "").replace("}", "") in disc_path:
                matched_ground_truth.add(gt_path)
                break

    discovery_coverage = len(matched_ground_truth) / ground_truth_count if ground_truth_count else 0

    # --- Phase 2: Runtime Boot ---
    ir_path = _write_service_ir(tmp_path, serialize_ir(service_ir))

    # Use the same mock transport for upstream requests
    upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: build_large_surface_transport().handle_request(req)
        ),
        follow_redirects=True,
    )

    try:
        app = create_app(service_ir_path=ir_path, upstream_client=upstream_client)
        runtime_transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(
            transport=runtime_transport, base_url="http://testserver"
        ) as client:
            # --- Phase 3: Validation + Audit ---
            async def tool_invoker(
                tool_name: str,
                arguments: dict[str, object],
            ) -> dict[str, object]:
                _, structured = await app.state.runtime_state.mcp_server.call_tool(
                    tool_name,
                    arguments,
                )
                return cast(dict[str, object], structured)

            # Build sample invocations from actual generated operations
            # so tool names match what the runtime exposes.
            _param_values: dict[str, str] = {
                "user_id": "usr-1",
                "post_id": "post-1",
                "product_id": "prod-1",
                "order_id": "ord-1",
                "item_id": "item-1",
                "category_id": "cat-1",
                "sku": "sku-1",
                "notification_id": "notif-1",
                "report_id": "rpt-1",
                "webhook_id": "wh-1",
                "id": "test-1",
            }
            sample_invocations: dict[str, dict[str, object]] = {}
            for op in service_ir.operations:
                args: dict[str, object] = {}
                for param in op.params:
                    if param.name == "payload":
                        args["payload"] = {"test": True}
                    elif param.default is not None:
                        args[param.name] = param.default
                    else:
                        args[param.name] = _param_values.get(
                            param.name, f"test-{param.name}"
                        )
                sample_invocations[op.id] = args

            validator = PostDeployValidator(client=client, tool_invoker=tool_invoker)
            report, audit_summary = await validator.validate_with_audit(
                "http://testserver",
                service_ir,
                sample_invocations=sample_invocations,
                audit_policy=AuditPolicy(),
            )
    finally:
        await upstream_client.aclose()

    # --- Phase 4: Coverage Calculation ---
    generated_tools = len(service_ir.operations)
    generation_coverage = generated_tools / len(discovered_paths) if discovered_paths else 0
    audit_pass_rate = (
        audit_summary.passed / audit_summary.audited_tools
        if audit_summary.audited_tools > 0
        else 0
    )

    unsupported_patterns = _identify_unsupported_patterns(
        ground_truth_count, discovered_paths, audit_summary,
    )

    pilot_report = LargeSurfacePilotReport(
        ground_truth_endpoints=ground_truth_count,
        discovered_endpoints=len(discovered_paths),
        generated_tools=generated_tools,
        audited_tools=audit_summary.audited_tools,
        passed=audit_summary.passed,
        failed=audit_summary.failed,
        skipped=audit_summary.skipped,
        discovery_coverage=discovery_coverage,
        generation_coverage=generation_coverage,
        audit_pass_rate=audit_pass_rate,
        unsupported_patterns=unsupported_patterns,
    )

    # --- Phase 5: Assertions ---
    # Standard validation should pass (health + tool listing)
    assert report.overall_passed is True, (
        "Standard validation failed: "
        + "; ".join(
            f"{r.stage}={r.passed} ({r.details})"
            for r in report.results
            if not r.passed
        )
    )

    # We must discover a meaningful fraction of the surface
    assert pilot_report.discovered_endpoints >= 10, (
        f"Only discovered {pilot_report.discovered_endpoints} endpoints, expected ≥ 10"
    )

    # At least some tools must be generated
    assert pilot_report.generated_tools >= 5, (
        f"Only generated {pilot_report.generated_tools} tools, expected ≥ 5"
    )

    # At least some tools must pass audit
    assert pilot_report.passed >= 1, (
        "No tools passed audit — expected at least 1 passing tool"
    )

    # Audit pass rate: audited tools should mostly pass
    if pilot_report.audited_tools > 0:
        assert pilot_report.audit_pass_rate >= 0.50, (
            f"Audit pass rate {pilot_report.audit_pass_rate:.2f} is below 0.50"
        )

    # Unsupported patterns should be captured (we expect at least write-safety skips)
    assert len(pilot_report.unsupported_patterns) >= 1, (
        "Expected at least one unsupported pattern to be captured"
    )

    # --- Phase 5b: Regression Threshold Checks ---
    # Build a ToolAuditSummary from pilot_report for threshold checking.
    threshold_summary = ToolAuditSummary(
        discovered_operations=pilot_report.discovered_endpoints,
        generated_tools=pilot_report.generated_tools,
        audited_tools=pilot_report.audited_tools,
        passed=pilot_report.passed,
        failed=pilot_report.failed,
        skipped=pilot_report.skipped,
        results=[],
    )
    violations = check_thresholds(
        threshold_summary, PILOT_BASELINE_THRESHOLDS
    )
    assert not violations, (
        "Pilot regression thresholds violated: "
        + "; ".join(violations)
    )

    # Coverage regression checks
    assert pilot_report.discovery_coverage >= PILOT_MIN_DISCOVERY_COVERAGE, (
        f"Discovery coverage {pilot_report.discovery_coverage:.2f} "
        f"below minimum {PILOT_MIN_DISCOVERY_COVERAGE:.2f}"
    )
    assert pilot_report.generation_coverage >= PILOT_MIN_GENERATION_COVERAGE, (
        f"Generation coverage {pilot_report.generation_coverage:.2f} "
        f"below minimum {PILOT_MIN_GENERATION_COVERAGE:.2f}"
    )
    if pilot_report.audited_tools > 0:
        assert pilot_report.audit_pass_rate >= PILOT_MIN_AUDIT_PASS_RATE, (
            f"Audit pass rate {pilot_report.audit_pass_rate:.2f} "
            f"below minimum {PILOT_MIN_AUDIT_PASS_RATE:.2f}"
        )

    # Print the report for visibility in CI output
    print(f"\n{'='*60}")
    print("B-003 LARGE-SURFACE PILOT REPORT")
    print(f"{'='*60}")
    print(f"Ground truth endpoints: {pilot_report.ground_truth_endpoints}")
    print(f"Discovered endpoints:   {pilot_report.discovered_endpoints}")
    print(f"Generated tools:        {pilot_report.generated_tools}")
    print(f"Audited tools:          {pilot_report.audited_tools}")
    print(f"  Passed:               {pilot_report.passed}")
    print(f"  Failed:               {pilot_report.failed}")
    print(f"  Skipped:              {pilot_report.skipped}")
    print(f"Discovery coverage:     {pilot_report.discovery_coverage:.1%}")
    print(f"Generation coverage:    {pilot_report.generation_coverage:.1%}")
    print(f"Audit pass rate:        {pilot_report.audit_pass_rate:.1%}")
    print(f"Unsupported patterns ({len(pilot_report.unsupported_patterns)}):")
    for pattern in pilot_report.unsupported_patterns:
        print(f"  - {pattern}")
    print(f"{'='*60}\n")


class _MockPilotLLMClient:
    """Mock LLM client for pilot P1 feature tests."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, prompt: str, max_tokens: int = 4096) -> object:
        self.calls.append(prompt)

        # Detect which kind of prompt this is and return appropriate mock response
        if "cluster them into logical business-intent groups" in prompt:
            return self._grouping_response(prompt)
        if "Rate the quality of each tool description" in prompt:
            return self._judge_response(prompt)
        if "generate additional endpoint paths" in prompt:
            return self._seed_mutation_response(prompt)

        class _Resp:
            content = "[]"
        return _Resp()

    def _grouping_response(self, prompt: str) -> object:
        # Parse operation IDs from the prompt to build realistic groups
        import re
        op_ids = re.findall(r'"operation_id":\s*"([^"]+)"', prompt)

        # Group by path prefix heuristic
        groups: dict[str, list[str]] = {}
        for oid in op_ids:
            # Extract resource name from operation ID pattern like "get_api_users"
            parts = (
                oid.replace("get_", "").replace("post_", "")
                .replace("put_", "").replace("delete_", "")
            )
            resource = parts.split("_")[1] if "_" in parts else "general"
            groups.setdefault(resource, []).append(oid)

        result = [
            {
                "id": f"{resource}-ops",
                "label": f"{resource.title()} Operations",
                "intent": f"Operations related to {resource}",
                "operation_ids": ids,
                "confidence": 0.8,
            }
            for resource, ids in groups.items()
        ]

        class _Resp:
            content = json.dumps(result)
        return _Resp()

    def _judge_response(self, prompt: str) -> object:
        import re
        op_ids = re.findall(r'"operation_id":\s*"([^"]+)"', prompt)
        result = [
            {
                "operation_id": oid,
                "accuracy": 0.75,
                "completeness": 0.70,
                "clarity": 0.80,
                "feedback": "Adequate description for discovered endpoint.",
            }
            for oid in op_ids
        ]

        class _Resp:
            content = json.dumps(result)
        return _Resp()

    def _seed_mutation_response(self, prompt: str) -> object:
        # Suggest a few plausible additional endpoints
        result = [
            {
                "path": "/api/users/{id}/activity", "methods": ["GET"],
                "rationale": "User activity log", "confidence": 0.7,
            },
            {
                "path": "/api/products/{id}/pricing", "methods": ["GET"],
                "rationale": "Product pricing", "confidence": 0.65,
            },
            {
                "path": "/api/orders/{id}/tracking", "methods": ["GET"],
                "rationale": "Order tracking", "confidence": 0.7,
            },
        ]

        class _Resp:
            content = json.dumps(result)
        return _Resp()


@pytest.mark.asyncio
async def test_large_surface_pilot_p1_features(tmp_path: Path) -> None:
    """B-003 P1: exercise LLM seed mutation, grouping, bifurcation, and judge on pilot IR."""

    # --- Phase 1: Discovery + Extraction (with LLM seed mutation) ---
    mock_llm = _MockPilotLLMClient()
    mock_transport = build_large_surface_transport()
    discovery_client = httpx.Client(transport=mock_transport, follow_redirects=True)
    extractor = RESTExtractor(
        client=discovery_client,
        max_pages=20,
        llm_client=mock_llm,
    )

    try:
        service_ir = extractor.extract(
            SourceConfig(
                url="https://large-surface.example.com/api",
                hints={"protocol": "rest", "service_name": "large-surface-p1"},
            )
        )
    finally:
        extractor.close()

    # Seed mutation should have been attempted
    assert service_ir.metadata.get("llm_seed_mutation") is True

    # --- Phase 2: Discovery/Action bifurcation ---
    service_ir = derive_tool_intents(service_ir)
    service_ir = bifurcate_descriptions(service_ir)

    # All operations should now have tool_intent set
    for op in service_ir.operations:
        assert op.tool_intent is not None, f"Operation {op.id} missing tool_intent"

    # GET operations should be discovery, others should be action
    discovery_ops = [op for op in service_ir.operations if op.tool_intent == ToolIntent.discovery]
    action_ops = [op for op in service_ir.operations if op.tool_intent == ToolIntent.action]
    assert len(discovery_ops) > 0, "Expected at least some discovery tools"
    assert len(action_ops) > 0, "Expected at least some action tools"

    # Descriptions should be prefixed
    for op in discovery_ops:
        assert op.description.startswith("[DISCOVERY] "), f"Missing prefix on {op.id}"
    for op in action_ops:
        assert op.description.startswith("[ACTION] "), f"Missing prefix on {op.id}"

    # --- Phase 3: Semantic tool grouping ---
    grouper = ToolGrouper(mock_llm)
    grouping_result = grouper.group(service_ir)
    service_ir = apply_grouping(service_ir, grouping_result)

    assert len(service_ir.tool_grouping) > 0, "Expected at least one tool group"
    # All grouped operations should reference valid IDs
    op_ids = {op.id for op in service_ir.operations}
    for group in service_ir.tool_grouping:
        for oid in group.operation_ids:
            assert oid in op_ids, f"Group {group.id} references unknown op {oid}"

    # --- Phase 4: LLM-as-a-Judge evaluation ---
    judge = LLMJudge(mock_llm)
    evaluation = judge.evaluate(service_ir)

    assert evaluation.tools_evaluated > 0, "Expected judge to evaluate at least some tools"
    assert evaluation.average_overall > 0, "Expected non-zero quality scores"
    assert len(evaluation.scores) > 0, "Expected per-tool scores"

    # --- Phase 5: Report ---
    print(f"\n{'='*60}")
    print("B-003 P1 FEATURES PILOT REPORT")
    print(f"{'='*60}")
    print(f"Total operations:       {len(service_ir.operations)}")
    print(f"Discovery tools:        {len(discovery_ops)}")
    print(f"Action tools:           {len(action_ops)}")
    print(f"Tool groups:            {len(service_ir.tool_grouping)}")
    for g in service_ir.tool_grouping:
        print(f"  - {g.label}: {len(g.operation_ids)} ops")
    print(f"Ungrouped operations:   {len(grouping_result.ungrouped_operations)}")
    print(f"Judge evaluations:      {evaluation.tools_evaluated}")
    print(f"Average quality:        {evaluation.average_overall:.2f}")
    print(f"  Accuracy:             {evaluation.average_accuracy:.2f}")
    print(f"  Completeness:         {evaluation.average_completeness:.2f}")
    print(f"  Clarity:              {evaluation.average_clarity:.2f}")
    print(f"Low quality tools:      {len(evaluation.low_quality_tools)}")
    print(f"LLM calls (total):      {len(mock_llm.calls)}")
    print(f"{'='*60}\n")


@pytest.mark.asyncio
async def test_large_surface_openapi_spec_first_pilot(tmp_path: Path) -> None:
    """B-003 spec-first pilot: measure extraction, generation, and audit coverage
    from an OpenAPI 3.0 spec covering the same 62-endpoint domain.

    This test provides a quantitative comparison baseline against the black-box
    REST discovery pilot.  Spec-first extraction should yield near-100% coverage
    for both discovery and generation, with failures limited to audit-policy
    skips rather than discovery gaps.
    """

    # --- Phase 1: Extraction via OpenAPI spec ---
    extractor = OpenAPIExtractor()
    service_ir = extractor.extract(
        SourceConfig(
            file_path=str(_LARGE_SURFACE_SPEC),
            hints={"service_name": "large-surface-spec-first"},
        )
    )

    # The spec defines all 62 operations; the extractor should capture them all.
    spec_operations = len(service_ir.operations)
    ground_truth_paths = {path for _, path in GROUND_TRUTH}
    ground_truth_count = len(ground_truth_paths)

    # Map spec paths (e.g. /users/{user_id}) to ground-truth paths
    # (e.g. /api/users/{user_id}) by prepending the /api prefix.
    spec_path_set = {f"/api{op.path}" for op in service_ir.operations}

    matched_ground_truth = set()
    for gt_path in ground_truth_paths:
        if gt_path in spec_path_set:
            matched_ground_truth.add(gt_path)

    discovery_coverage = (
        len(matched_ground_truth) / ground_truth_count if ground_truth_count else 0
    )

    # --- Phase 2: Runtime Boot ---
    ir_path = _write_service_ir(tmp_path, serialize_ir(service_ir))

    # The mock transport serves responses for all /api/* paths.
    upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: build_large_surface_transport().handle_request(req)
        ),
        follow_redirects=True,
    )

    try:
        app = create_app(service_ir_path=ir_path, upstream_client=upstream_client)
        runtime_transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(
            transport=runtime_transport, base_url="http://testserver"
        ) as client:
            # --- Phase 3: Validation + Audit ---
            async def tool_invoker(
                tool_name: str,
                arguments: dict[str, object],
            ) -> dict[str, object]:
                _, structured = await app.state.runtime_state.mcp_server.call_tool(
                    tool_name,
                    arguments,
                )
                return cast(dict[str, object], structured)

            # Build sample invocations from the spec-extracted operations.
            _param_values: dict[str, str] = {
                "user_id": "usr-1",
                "post_id": "post-1",
                "product_id": "prod-1",
                "order_id": "ord-1",
                "item_id": "item-1",
                "category_id": "cat-1",
                "sku": "sku-1",
                "notification_id": "notif-1",
                "report_id": "rpt-1",
                "webhook_id": "wh-1",
                "id": "test-1",
            }
            # Type-safe default values for typed params.
            _type_defaults: dict[str, object] = {
                "integer": 10,
                "number": 1.0,
                "boolean": True,
                "array": [],
                "object": {},
            }

            sample_invocations: dict[str, dict[str, object]] = {}
            for op in service_ir.operations:
                args: dict[str, object] = {}
                for param in op.params:
                    if param.name == "payload":
                        args["payload"] = {"test": True}
                    elif param.default is not None:
                        args[param.name] = param.default
                    elif param.name in _param_values:
                        args[param.name] = _param_values[param.name]
                    elif param.type in _type_defaults:
                        args[param.name] = _type_defaults[param.type]
                    else:
                        args[param.name] = f"test-{param.name}"
                sample_invocations[op.id] = args

            validator = PostDeployValidator(client=client, tool_invoker=tool_invoker)
            report, audit_summary = await validator.validate_with_audit(
                "http://testserver",
                service_ir,
                sample_invocations=sample_invocations,
                audit_policy=AuditPolicy(),
            )
    finally:
        await upstream_client.aclose()

    # --- Phase 4: Coverage Calculation ---
    generation_ratio = spec_operations / ground_truth_count if ground_truth_count else 0
    audit_pass_rate = (
        audit_summary.passed / audit_summary.audited_tools
        if audit_summary.audited_tools > 0
        else 0
    )

    spec_unsupported = _identify_unsupported_patterns(
        ground_truth_count, spec_path_set, audit_summary,
    )

    spec_report = LargeSurfacePilotReport(
        ground_truth_endpoints=ground_truth_count,
        discovered_endpoints=len(matched_ground_truth),
        generated_tools=spec_operations,
        audited_tools=audit_summary.audited_tools,
        passed=audit_summary.passed,
        failed=audit_summary.failed,
        skipped=audit_summary.skipped,
        discovery_coverage=discovery_coverage,
        generation_coverage=generation_ratio,
        audit_pass_rate=audit_pass_rate,
        unsupported_patterns=spec_unsupported,
    )

    # --- Phase 5: Assertions ---
    # Standard validation should pass (health + tool listing).
    assert report.overall_passed is True, (
        "Standard validation failed: "
        + "; ".join(
            f"{r.stage}={r.passed} ({r.details})"
            for r in report.results
            if not r.passed
        )
    )

    # Spec-first extraction must capture all ground-truth endpoints.
    assert spec_report.discovered_endpoints >= ground_truth_count - 1, (
        f"Spec-first discovered {spec_report.discovered_endpoints} of "
        f"{ground_truth_count} ground-truth endpoints"
    )

    # Every spec operation must generate a tool (1:1 mapping).
    assert spec_report.generated_tools == spec_operations

    # Spec-first: generation ratio must cover all ground-truth endpoints.
    assert spec_report.generation_coverage >= SPEC_FIRST_MIN_GENERATION_RATIO, (
        f"Generation ratio {spec_report.generation_coverage:.2f} "
        f"below minimum {SPEC_FIRST_MIN_GENERATION_RATIO:.2f}"
    )

    # Zero audit failures for spec-first input.
    assert spec_report.failed == 0, (
        f"Spec-first pilot had {spec_report.failed} audit failures"
    )

    # Audit pass rate: spec-first should be very high.
    if spec_report.audited_tools > 0:
        assert spec_report.audit_pass_rate >= SPEC_FIRST_MIN_AUDIT_PASS_RATE, (
            f"Audit pass rate {spec_report.audit_pass_rate:.1%} "
            f"below minimum {SPEC_FIRST_MIN_AUDIT_PASS_RATE:.1%}"
        )

    # --- Phase 5b: Regression Threshold Checks ---
    threshold_summary = ToolAuditSummary(
        discovered_operations=spec_report.discovered_endpoints,
        generated_tools=spec_report.generated_tools,
        audited_tools=spec_report.audited_tools,
        passed=spec_report.passed,
        failed=spec_report.failed,
        skipped=spec_report.skipped,
        results=[],
    )
    violations = check_thresholds(threshold_summary, SPEC_FIRST_THRESHOLDS)
    assert not violations, (
        "Spec-first regression thresholds violated: " + "; ".join(violations)
    )

    # --- Phase 6: Comparison Report ---
    print(f"\n{'='*60}")
    print("B-003 SPEC-FIRST PILOT REPORT")
    print(f"{'='*60}")
    print(f"Ground truth endpoints:   {spec_report.ground_truth_endpoints}")
    print(f"Matched from spec:        {spec_report.discovered_endpoints}")
    print(f"Generated tools:          {spec_report.generated_tools}")
    print(f"Audited tools:            {spec_report.audited_tools}")
    print(f"  Passed:                 {spec_report.passed}")
    print(f"  Failed:                 {spec_report.failed}")
    print(f"  Skipped:                {spec_report.skipped}")
    print(f"Discovery coverage:       {spec_report.discovery_coverage:.1%}")
    print(f"Generation coverage:      {spec_report.generation_coverage:.1%}")
    print(f"Audit pass rate:          {spec_report.audit_pass_rate:.1%}")
    print(f"Unsupported patterns ({len(spec_report.unsupported_patterns)}):")
    for pattern in spec_report.unsupported_patterns:
        print(f"  - {pattern}")
    print("\n--- Comparison vs. Black-Box REST Discovery ---")
    print(f"  Spec-first discovery:   {spec_report.discovery_coverage:.1%}")
    print(f"  Black-box baseline:     {PILOT_MIN_DISCOVERY_COVERAGE:.1%} (minimum)")
    print(f"  Spec-first generation:  {spec_report.generation_coverage:.1%}")
    print(f"  Black-box baseline:     {PILOT_MIN_GENERATION_COVERAGE:.1%} (minimum)")
    print(f"  Spec-first audit pass:  {spec_report.audit_pass_rate:.1%}")
    print(f"  Black-box baseline:     {PILOT_MIN_AUDIT_PASS_RATE:.1%} (minimum)")
    print(f"{'='*60}\n")


@pytest.mark.asyncio
async def test_large_surface_pilot_p1_proof_runner_integration(
    tmp_path: Path,
) -> None:
    """B-004: verify tool_intent_counts and judge_evaluation flow through
    the proof runner integration path after P1 transforms are applied.

    This test exercises the same pipeline as `test_large_surface_pilot_p1_features`
    but additionally:
    - Computes tool_intent_counts via the proof runner helper
    - Runs LLM judge evaluation and verifies it produces structured output
    - Confirms both structures are serializable (ProofResult compatibility)
    """
    from dataclasses import asdict

    # --- Phase 1: Discovery + Extraction (with LLM seed mutation) ---
    mock_llm = _MockPilotLLMClient()
    mock_transport = build_large_surface_transport()
    discovery_client = httpx.Client(transport=mock_transport, follow_redirects=True)
    extractor = RESTExtractor(
        client=discovery_client,
        max_pages=20,
        llm_client=mock_llm,
    )

    try:
        service_ir = extractor.extract(
            SourceConfig(
                url="https://large-surface.example.com/api",
                hints={"protocol": "rest", "service_name": "large-surface-b004"},
            )
        )
    finally:
        extractor.close()

    # --- Phase 2: Apply P1 transforms ---
    service_ir = derive_tool_intents(service_ir)
    service_ir = bifurcate_descriptions(service_ir)

    grouper = ToolGrouper(mock_llm)
    grouping_result = grouper.group(service_ir)
    service_ir = apply_grouping(service_ir, grouping_result)

    # --- Phase 3: Verify tool_intent_counts ---
    counts = _compute_tool_intent_counts(service_ir)

    assert isinstance(counts, ToolIntentCounts)
    assert counts.discovery + counts.action + counts.unset > 0, (
        "Expected at least one operation counted"
    )
    # After derive_tool_intents, no enabled ops should be unset.
    assert counts.unset == 0, (
        f"Expected zero unset intents after derivation, got {counts.unset}"
    )
    assert counts.discovery > 0, "Expected at least one discovery tool"
    assert counts.action > 0, "Expected at least one action tool"

    # Serialization check.
    counts_dict = asdict(counts)
    assert set(counts_dict.keys()) == {"discovery", "action", "unset"}

    # --- Phase 4: Verify LLM judge evaluation ---
    judge = LLMJudge(mock_llm)
    evaluation = judge.evaluate(service_ir)

    assert isinstance(evaluation, JudgeEvaluation)
    assert evaluation.tools_evaluated > 0
    assert evaluation.average_overall > 0
    assert evaluation.quality_passed, (
        f"Judge quality below threshold: {evaluation.average_overall:.2f}"
    )

    # Serialization check.
    eval_dict = asdict(evaluation)
    assert "tools_evaluated" in eval_dict
    assert "average_overall" in eval_dict
    assert "scores" in eval_dict

    # --- Phase 5: Report ---
    print(f"\n{'='*60}")
    print("B-004 PROOF RUNNER INTEGRATION REPORT")
    print(f"{'='*60}")
    print("Tool intent counts:")
    print(f"  Discovery:          {counts.discovery}")
    print(f"  Action:             {counts.action}")
    print(f"  Unset:              {counts.unset}")
    print("Judge evaluation:")
    print(f"  Tools evaluated:    {evaluation.tools_evaluated}")
    print(f"  Average overall:    {evaluation.average_overall:.2f}")
    print(f"  Quality passed:     {evaluation.quality_passed}")
    print(f"  Low quality tools:  {len(evaluation.low_quality_tools)}")
    print(f"Tool groups:          {len(service_ir.tool_grouping)}")
    print(f"LLM calls (total):    {len(mock_llm.calls)}")
    print(f"{'='*60}\n")
