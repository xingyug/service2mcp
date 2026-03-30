# Quickstart

## Goal

Bring up the local Tool Compiler v2 environment, compile a sample OpenAPI spec, and verify the runtime/tooling path with the repository's end-to-end harness.

## Prerequisites

- Python `3.12+`
- Docker with the Compose plugin
- GNU `make`
- Free local ports: `5432`, `6379`, `7233`, `8000`, `8001`, `8002`, `8003`

## 1. Prepare The Environment

From the repository root:

```bash
./scripts/setup-dev.sh
```

This creates `.venv`, installs `.[all]`, and validates `deploy/docker-compose.yaml`.

## 2. Start Local Services

```bash
make dev-up
make dev-smoke
```

`make dev-up` starts:

- PostgreSQL
- Redis
- Temporal dev server
- Compiler API
- Access Control service
- Compiler Worker shell
- Gateway Admin Mock
- Generic MCP Runtime shell

Useful local endpoints:

- Compiler API: `http://localhost:8000/healthz`
- Access Control: `http://localhost:8001/healthz`
- Compiler Worker: `http://localhost:8002/readyz`
- MCP Runtime: `http://localhost:8003/tools`
- Gateway Admin Mock: `http://localhost:8004/healthz`

## 3. Compile The Sample OpenAPI Spec

Use the compiler API to submit the bundled Petstore fixture:

```bash
python3 - <<'PY'
from pathlib import Path
import httpx

spec_path = Path("tests/fixtures/openapi_specs/petstore_3_0.yaml")
payload = {
    "source_url": "https://example.com/petstore.yaml",
    "source_content": spec_path.read_text(encoding="utf-8"),
    "created_by": "quickstart-user",
    "service_name": "petstore-api",
}

response = httpx.post("http://127.0.0.1:8000/api/v1/compilations", json=payload, timeout=60)
response.raise_for_status()
print(response.json())
PY
```

Then inspect the active compiled services:

```bash
curl -s http://127.0.0.1:8000/api/v1/services | python3 -m json.tool
```

## 4. Invoke A Tool

The repository includes an end-to-end harness that compiles the Petstore spec, deploys the generic runtime in-memory, and performs a real MCP tool invocation:

```bash
.venv/bin/pytest -q tests/e2e/test_full_compilation_flow.py
```

That test proves the full path:

1. Compiler API accepts the Petstore submission.
2. The compilation workflow completes successfully.
3. The generic runtime is deployed from the generated IR.
4. An MCP tool call returns a valid upstream response.

You can also inspect the local runtime shell directly:

```bash
curl -s http://127.0.0.1:8003/tools | python3 -m json.tool
```

## 5. Smoke The Route-Publication Path

The repository also includes a smoke script for the route-publication path. By default it runs in `artifact` mode: it creates an artifact-registry version with `route_config`, syncs routes through Access Control, deletes the stable route in the Gateway Admin Mock to simulate drift, then runs reconciliation and checks that the route is restored. If you want to exercise the full compile path instead, run it with `SMOKE_MODE=compile`.

```bash
make gateway-smoke
```

For a live GKE smoke that avoids the full compiler/runtime stack, use the minimal harness below. It creates a temporary namespace with PostgreSQL, runs migrations, deploys Access Control plus Gateway Admin Mock, starts two lightweight versioned runtime services, inserts a service version directly into `registry.service_versions`, then verifies route sync, drift reconciliation, and a real data-plane request through the gateway entrypoint.

```bash
ACCESS_CONTROL_IMAGE=us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/access-control:20260325-h008-r13 \
COMPILER_API_IMAGE=us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/compiler-api:20260325-b0e27e6-r4 \
make gke-gateway-smoke
```

If you want the same live harness to exercise rollout semantics as well, run it in `SMOKE_MODE=rollout`. That mode seeds `v1`, verifies the stable route points at `v1`, rolls forward to `v2` and verifies the stable route target changes, then activates `v1` again and verifies rollback of the stable route target. It also checks the gateway data plane directly: the active route must switch to `v2`, pinned `x-tool-compiler-version` requests must continue to reach the correct versioned runtime, and rollback must return the stable route to `v1` without breaking pinned `v2`.

```bash
SMOKE_MODE=rollout \
ACCESS_CONTROL_IMAGE=us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/access-control:20260325-h008-r13 \
COMPILER_API_IMAGE=us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/compiler-api:20260325-b0e27e6-r4 \
make gke-gateway-smoke
```

## 6. Smoke Native gRPC Streaming On GKE

The repository also includes a live GKE smoke for the native `grpc_stream` server-stream path. It generates a generic runtime manifest from a hand-authored `ServiceIR`, deploys a reflection-enabled gRPC upstream mock, waits for the runtime to become ready, then runs `PostDeployValidator` plus a direct MCP tool invocation from inside the cluster. The smoke succeeds only if the runtime returns `transport="grpc_stream"` and a single reflected protobuf event for `watchInventory`.

Build and push a fresh runtime image first, then run:

```bash
RUNTIME_IMAGE=us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/mcp-runtime:20260325-b0e27e6-r19 \
KEEP_NAMESPACE=1 \
make gke-grpc-stream-smoke
```

That harness leaves behind a temporary namespace only when `KEEP_NAMESPACE=1`; otherwise it cleans up automatically after the validator job completes.

## 7. Run Cross-Protocol LLM-Enabled Proofs On GKE

Once the compiler, worker, runtime, and access-control images are built and pushed, the repository exposes a single live harness for the final `LLM-enabled E2E` proof track. It deploys a fresh compiler stack via Helm, injects the local DeepSeek API key as a Kubernetes secret, starts three upstream proof services (`llm-proof-http`, `llm-proof-grpc`, and `llm-proof-sql`), then runs `apps.proof_runner.live_llm_e2e` to prove GraphQL, REST, gRPC, SOAP/WSDL, and SQL end-to-end. The structure-level matrix succeeded earlier in `tool-compiler-llm-all-031802`, and the current authoritative audit-enabled rerun succeeded in `tool-compiler-llm-all-audit-075849` with aggregate `discovered=13`, `generated=13`, `audited=7`, `passed=7`, `failed=0`, `skipped=6`. The harness now also includes a SQL `startupProbe` so Postgres initialization is not interrupted before `init.sql` completes.

```bash
NAMESPACE="tool-compiler-llm-all-audit-$(date +%H%M%S)" \
PROTOCOL=all \
AUDIT_ALL_GENERATED_TOOLS=1 \
AUDIT_MUTATING_TOOLS=1 \
ACCESS_CONTROL_IMAGE="us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/access-control:20260325-b0e27e6-r20" \
COMPILER_API_IMAGE="us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/compiler-api:20260326-b0e27e6-r28" \
COMPILER_WORKER_IMAGE="us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/compiler-worker:20260326-b0e27e6-r28" \
MCP_RUNTIME_IMAGE="us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/mcp-runtime:20260325-b0e27e6-r20" \
PROOF_HELPER_IMAGE="us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/compiler-api:20260326-b0e27e6-r28" \
KEEP_NAMESPACE=1 \
make gke-llm-e2e-smoke
```

The live harness requires the local DeepSeek key file at `/home/guoxy/esoc-agents/.deepseek_api_key` unless you override `LLM_API_KEY_FILE`. It emits one JSON record per protocol, including `job_id`, `operations_enhanced`, `active_version`, `llm_field_count`, and the final runtime tool invocation result. When you set `AUDIT_ALL_GENERATED_TOOLS=1`, the proof runner appends an `audit_summary` block that records per-tool `passed`, `failed`, and `skipped` outcomes for all safe generated tools. If you also set `AUDIT_MUTATING_TOOLS=1`, the post-compile proof audit widens to state-mutating, external-side-effect, and destructive tools too; this does not change extractor-side discovery and does not cause the compile-time exploration step to issue writes. The first audit-enabled REST run in namespace `tool-compiler-llm-rest-audit-041525` intentionally surfaced real discovery drift (`passed=1`, `failed=5`, `skipped=0`), while the current clean cross-protocol audit baseline in `tool-compiler-llm-all-audit-075849` shows those REST false positives are gone and the aggregate audited result is green.

For stepwise hardening, you can now run a single protocol at a time while keeping the same Helm/GKE harness:

```bash
NAMESPACE="tool-compiler-llm-rest-audit-$(date +%H%M%S)" \
PROTOCOL=rest \
AUDIT_ALL_GENERATED_TOOLS=1 \
ACCESS_CONTROL_IMAGE="us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/access-control:20260325-b0e27e6-r20" \
COMPILER_API_IMAGE="us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/compiler-api:20260326-b0e27e6-r28" \
COMPILER_WORKER_IMAGE="us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/compiler-worker:20260326-b0e27e6-r28" \
MCP_RUNTIME_IMAGE="us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/mcp-runtime:20260325-b0e27e6-r20" \
PROOF_HELPER_IMAGE="us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/compiler-api:20260326-b0e27e6-r28" \
KEEP_NAMESPACE=1 \
make gke-llm-e2e-smoke
```

Supported values are `all`, `graphql`, `rest`, `grpc`, `soap`, and `sql` (e.g. `PROTOCOL=graphql`). Single-protocol mode remains the recommended troubleshooting path when the full matrix regresses, because it isolates live proof failures without rerunning all five compiler-managed protocol slices. For current black-box hardening work, use the `PROTOCOL=all` audit run as the clean baseline and switch to the REST + audit command above only when you want to isolate discovery behavior on the catalog fixture.

## 8. Run Local Real DeepSeek Smoke

If you want a minimal local real-provider proof without going through GKE, the repository now exposes a dedicated smoke target that runs the local GraphQL + SQL E2E proofs with `ENABLE_REAL_DEEPSEEK_E2E=1`.

```bash
make e2e-real-deepseek-smoke
```

It reads the VM-local DeepSeek key file at `/home/guoxy/esoc-agents/.deepseek_api_key` by default. You can override the key path, model, API base URL, or the pytest selector when needed:

```bash
LLM_API_KEY_FILE=/path/to/deepseek.key \
DEEPSEEK_MODEL=deepseek-chat \
DEEPSEEK_API_BASE_URL=https://api.deepseek.com \
PYTEST_K_EXPR="graphql_introspection_compiles_to_running_runtime_and_tool_invocation" \
make e2e-real-deepseek-smoke
```

## 9. Run Quality Gates

```bash
make test
make contract-test
make test-integration
.venv/bin/ruff check .
.venv/bin/mypy libs apps tests/integration tests/contract tests/e2e
```

## 10. Shut Everything Down

```bash
make dev-down
```

## Notes

- The local `compiler-worker` service currently exposes workflow health and metrics endpoints; the repository's tested end-to-end execution path is the in-process workflow harness in `tests/e2e/test_full_compilation_flow.py`.
- The Helm chart lives under `deploy/helm/tool-compiler/`.
- Grafana dashboard templates live under `observability/grafana/`.
