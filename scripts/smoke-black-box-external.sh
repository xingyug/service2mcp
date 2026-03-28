#!/usr/bin/env bash
# scripts/smoke-black-box-external.sh
#
# B-005 operator harness: run the full black-box validation pipeline against
# real external APIs and report discovery coverage vs ground truth.
#
# Usage:
#   TARGET=jsonplaceholder ./scripts/smoke-black-box-external.sh
#   TARGET=petstore         ./scripts/smoke-black-box-external.sh
#   TARGET=all              ./scripts/smoke-black-box-external.sh
#
# Environment variables:
#   TARGET              — Target API: jsonplaceholder | petstore | all (default: all)
#   TIMEOUT_SECONDS     — Per-target timeout in seconds (default: 120)
#   RESULTS_DIR         — Output directory for reports (default: /tmp/b005-results)
#   VENV_PYTHON         — Python interpreter (default: .venv/bin/python)
#   MAX_PAGES           — Max discovery crawl pages for REST targets (default: 30)
#   VERBOSE             — Set to 1 for detailed output (default: 0)
#
# Prerequisites:
#   - Active .venv with project deps installed
#   - Network access to target APIs (jsonplaceholder.typicode.com, petstore3.swagger.io)
#
# Out of scope for CI — external APIs are not under our control.
# This script is intended for manual operator validation only.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${TARGET:-all}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-120}"
RESULTS_DIR="${RESULTS_DIR:-/tmp/b005-results}"
VENV_PYTHON="${VENV_PYTHON:-${REPO_ROOT}/.venv/bin/python}"
MAX_PAGES="${MAX_PAGES:-30}"
VERBOSE="${VERBOSE:-0}"

mkdir -p "${RESULTS_DIR}"

echo "═══════════════════════════════════════════════════════════════"
echo "  B-005: Real External API Black-Box Validation"
echo "═══════════════════════════════════════════════════════════════"
echo "  Target:    ${TARGET}"
echo "  Timeout:   ${TIMEOUT_SECONDS}s per target"
echo "  Results:   ${RESULTS_DIR}"
echo "  Max pages: ${MAX_PAGES}"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# Validate python
if [[ ! -x "${VENV_PYTHON}" ]]; then
    echo "ERROR: Python not found at ${VENV_PYTHON}" >&2
    echo "Run: python3 -m venv .venv && pip install -e '.[dev,extractors]'" >&2
    exit 1
fi

run_target() {
    local target_name="$1"
    local target_url="$2"
    local protocol="$3"
    local output_file="${RESULTS_DIR}/${target_name}-report.json"

    echo "──────────────────────────────────────────────────────────"
    echo "  Running: ${target_name} (${protocol}) → ${target_url}"
    echo "──────────────────────────────────────────────────────────"

    timeout "${TIMEOUT_SECONDS}" "${VENV_PYTHON}" -c "
import sys, json, asyncio
sys.path.insert(0, '${REPO_ROOT}')

from libs.extractors.base import SourceConfig
from libs.validator.black_box import evaluate_black_box

target_name = '${target_name}'
target_url = '${target_url}'
protocol = '${protocol}'
max_pages = ${MAX_PAGES}

# Import ground truth
if target_name == 'jsonplaceholder':
    from tests.fixtures.ground_truth.jsonplaceholder import GROUND_TRUTH
    from libs.extractors.rest import RESTExtractor
    import httpx
    client = httpx.Client(follow_redirects=True, timeout=30.0)
    extractor = RESTExtractor(client=client, max_pages=max_pages)
    try:
        ir = extractor.extract(SourceConfig(
            url=target_url,
            hints={'protocol': 'rest', 'service_name': target_name},
        ))
    finally:
        extractor.close()
        client.close()

elif target_name == 'petstore':
    from tests.fixtures.ground_truth.petstore_v3 import GROUND_TRUTH
    from libs.extractors.openapi import OpenAPIExtractor
    import httpx
    client = httpx.Client(follow_redirects=True, timeout=30.0)
    try:
        resp = client.get(target_url)
        resp.raise_for_status()
        spec_content = resp.text
    finally:
        client.close()
    extractor = OpenAPIExtractor()
    ir = extractor.extract(SourceConfig(
        file_content=spec_content,
        hints={'service_name': target_name},
    ))
else:
    print(f'ERROR: Unknown target {target_name}', file=sys.stderr)
    sys.exit(1)

report = evaluate_black_box(ir, GROUND_TRUTH, target_name=target_name, target_base_url=target_url)

result = {
    'target': report.target_name,
    'protocol': report.protocol,
    'base_url': report.target_base_url,
    'ground_truth_count': report.ground_truth_count,
    'discovered_operations': report.discovered_operations,
    'matched_count': report.matched_count,
    'unmatched_count': report.unmatched_count,
    'discovery_coverage': round(report.discovery_coverage, 4),
    'risk_accuracy': round(report.risk_accuracy, 4),
    'resource_groups': report.resource_groups,
    'failure_patterns': [
        {'name': p.pattern_name, 'affected': len(p.affected_endpoints), 'description': p.description}
        for p in report.failure_patterns
    ],
    'unmatched_endpoints': report.unmatched_ground_truth,
    'extra_discovered': [(m, p) for m, p in report.extra_discovered],
}

with open('${output_file}', 'w') as f:
    json.dump(result, f, indent=2)

print(json.dumps(result, indent=2))
print()
print(f'  Coverage: {report.discovery_coverage:.1%} ({report.matched_count}/{report.ground_truth_count})')
print(f'  Risk accuracy: {report.risk_accuracy:.1%}')
print(f'  Failure patterns: {len(report.failure_patterns)}')
if report.failure_patterns:
    for p in report.failure_patterns:
        print(f'    - {p.pattern_name}: {len(p.affected_endpoints)} endpoints')
print(f'  Report saved: ${output_file}')
" 2>&1

    local exit_code=$?
    if [[ ${exit_code} -eq 0 ]]; then
        echo "  ✅ ${target_name}: PASSED"
    elif [[ ${exit_code} -eq 124 ]]; then
        echo "  ⏰ ${target_name}: TIMEOUT (${TIMEOUT_SECONDS}s)" >&2
    else
        echo "  ❌ ${target_name}: FAILED (exit ${exit_code})" >&2
    fi
    echo ""
    return ${exit_code}
}

EXIT_CODE=0

if [[ "${TARGET}" == "jsonplaceholder" || "${TARGET}" == "all" ]]; then
    run_target "jsonplaceholder" "https://jsonplaceholder.typicode.com" "rest" || EXIT_CODE=1
fi

if [[ "${TARGET}" == "petstore" || "${TARGET}" == "all" ]]; then
    run_target "petstore" "https://petstore3.swagger.io/api/v3/openapi.json" "openapi" || EXIT_CODE=1
fi

if [[ "${TARGET}" != "jsonplaceholder" && "${TARGET}" != "petstore" && "${TARGET}" != "all" ]]; then
    echo "ERROR: Unknown target '${TARGET}'. Use: jsonplaceholder | petstore | all" >&2
    exit 1
fi

echo "═══════════════════════════════════════════════════════════════"
if [[ ${EXIT_CODE} -eq 0 ]]; then
    echo "  All targets completed successfully."
else
    echo "  Some targets failed. Check output above."
fi
echo "  Reports in: ${RESULTS_DIR}"
echo "═══════════════════════════════════════════════════════════════"

exit ${EXIT_CODE}
