"""B-003 large-surface black-box pilot integration test.

Measures three coverage numbers against a 62-endpoint REST mock:
1. Endpoint discovery coverage
2. Generated MCP-tool coverage
3. Audited invocation pass rate
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import httpx
import pytest

from apps.mcp_runtime import create_app
from libs.extractors.base import SourceConfig
from libs.extractors.rest import RESTExtractor
from libs.ir.schema import serialize_ir
from libs.validator.audit import (
    AuditPolicy,
    LargeSurfacePilotReport,
    ToolAuditSummary,
)
from libs.validator.post_deploy import PostDeployValidator
from tests.fixtures.large_surface_rest_mock import (
    GROUND_TRUTH,
    build_large_surface_transport,
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
