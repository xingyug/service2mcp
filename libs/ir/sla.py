"""SLA baseline computation and recommendation for ServiceIR operations.

Generates SLA configuration recommendations from operational latency data,
validates feasibility, and produces human-readable SLA reports.
"""

from __future__ import annotations

import math
from typing import Any

from libs.ir.models import Operation, RetryConfig, ServiceIR, SlaConfig


def compute_sla_from_latencies(
    latencies: list[float],
    percentile: float = 99.0,
) -> SlaConfig:
    """Compute an SLA configuration from observed latency measurements.

    Args:
        latencies: Latency measurements in milliseconds.
        percentile: Target percentile for the latency budget (default p99).

    Returns:
        SlaConfig with latency_budget_ms derived from the specified percentile,
        timeout_ms at 2× the budget, and sensible retry defaults.

    Raises:
        ValueError: If *latencies* is empty or *percentile* is out of range.
    """
    if not latencies:
        raise ValueError("latencies must be non-empty")
    if not (0.0 < percentile <= 100.0):
        raise ValueError(f"percentile must be in (0, 100], got {percentile}")

    sorted_lats = sorted(latencies)
    n = len(sorted_lats)

    if n == 1:
        p_value = sorted_lats[0]
    else:
        # Nearest-rank percentile calculation
        rank = math.ceil(percentile / 100.0 * n) - 1
        rank = max(0, min(rank, n - 1))
        p_value = sorted_lats[rank]

    budget_ms = max(1, int(math.ceil(p_value)))
    timeout_ms = budget_ms * 2

    return SlaConfig(
        latency_budget_ms=budget_ms,
        timeout_ms=timeout_ms,
        retry=RetryConfig(
            max_retries=2,
            backoff_base_ms=100,
            backoff_multiplier=2.0,
        ),
    )


def recommend_sla_for_ir(
    ir: ServiceIR,
    latency_data: dict[str, list[float]],
) -> ServiceIR:
    """Apply SLA recommendations to a ServiceIR based on observed latencies.

    For each operation whose ``id`` appears in *latency_data* (with a
    non-empty list), a new :class:`SlaConfig` is computed and assigned.
    Operations without latency data retain their existing SLA (or ``None``).

    The original *ir* is **not** mutated; a deep copy is returned.
    """
    updated_ops: list[Operation] = []
    for op in ir.operations:
        lats = latency_data.get(op.id)
        if lats:
            sla = compute_sla_from_latencies(lats)
            updated_ops.append(op.model_copy(update={"sla": sla}))
        else:
            updated_ops.append(op.model_copy())

    return ir.model_copy(update={"operations": updated_ops})


def export_sla_report(ir: ServiceIR) -> dict[str, Any]:
    """Generate a human-readable SLA report for all operations in *ir*.

    Returns a dict with ``operations`` (per-operation detail) and ``summary``
    (aggregate counts).
    """
    op_reports: list[dict[str, Any]] = []
    with_sla = 0
    without_sla = 0

    for op in ir.operations:
        has_sla = op.sla is not None
        entry: dict[str, Any] = {
            "operation_id": op.id,
            "has_sla": has_sla,
        }
        if has_sla:
            assert op.sla is not None
            with_sla += 1
            entry["latency_budget_ms"] = op.sla.latency_budget_ms
            entry["timeout_ms"] = op.sla.timeout_ms
            entry["retry"] = {
                "max_retries": op.sla.retry.max_retries,
                "backoff_base_ms": op.sla.retry.backoff_base_ms,
                "backoff_multiplier": op.sla.retry.backoff_multiplier,
            }
        else:
            without_sla += 1
            entry["latency_budget_ms"] = None
            entry["timeout_ms"] = None
            entry["retry"] = None

        op_reports.append(entry)

    return {
        "operations": op_reports,
        "summary": {
            "total_operations": len(ir.operations),
            "with_sla": with_sla,
            "without_sla": without_sla,
        },
    }


def validate_sla_feasibility(sla: SlaConfig) -> list[str]:
    """Return warnings about potentially infeasible or misconfigured SLA.

    Checks performed:
    - Very aggressive latency budget (<10 ms)
    - timeout_ms less than latency_budget_ms
    - Retries with backoff that may exceed the timeout
    """
    warnings: list[str] = []

    budget = sla.latency_budget_ms
    timeout = sla.timeout_ms
    retry = sla.retry

    if budget is not None and budget < 10:
        warnings.append(
            f"latency_budget_ms ({budget}ms) is very aggressive"
            " — p99 latency rarely achievable below 10ms"
        )

    if budget is not None and timeout is not None and timeout < budget:
        warnings.append(f"timeout_ms ({timeout}ms) is less than latency_budget_ms ({budget}ms)")

    if retry.max_retries > 0 and timeout is not None:
        # Estimate total worst-case time: initial attempt + retries with backoff
        total_ms = float(budget or timeout)
        for i in range(retry.max_retries):
            backoff = retry.backoff_base_ms * (retry.backoff_multiplier**i)
            total_ms += backoff + (budget or timeout)
        if total_ms > timeout:
            warnings.append(
                f"max_retries ({retry.max_retries}) with backoff"
                f" may exceed timeout_ms ({timeout}ms)"
            )

    return warnings
