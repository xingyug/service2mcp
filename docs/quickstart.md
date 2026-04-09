# Quickstart

## Goal

Bring up the local service2mcp environment, compile a sample OpenAPI spec, and verify the runtime/tooling path with the repository's end-to-end harness.

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
pytest -q tests/e2e/test_full_compilation_flow.py
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

The repository includes a smoke script for the route-publication path:

```bash
make gateway-smoke
```

This creates an artifact-registry version with `route_config`, syncs routes through Access Control, simulates drift, then runs reconciliation and checks that the route is restored.

## 6. LLM Enhancement (Optional)

To enable LLM-enhanced operation descriptions, set these environment variables for the compiler worker:

```bash
export LLM_PROVIDER=deepseek           # or: openai
export LLM_API_KEY=your-api-key-here
export LLM_MODEL=deepseek-chat         # or: gpt-4o-mini
```

Any OpenAI-compatible provider works. The enhancer runs after extraction and enriches operation descriptions, parameter documentation, and tool names.

## 7. Run Quality Gates

```bash
make test
make contract-test
make test-integration
make lint
make typecheck
```

## 8. Shut Everything Down

```bash
make dev-down
```

## Kubernetes Deployment

For Kubernetes deployment, the Helm chart lives under `deploy/helm/tool-compiler/`. See `deploy/` for manifests and configuration examples. You will need to:

1. Build and push container images (see `deploy/docker/Dockerfile.app` for reference)
2. Configure `values.yaml` with your registry, image tags, and secrets
3. Deploy with `helm install`

See [production-deployment.md](production-deployment.md) for detailed instructions.

## Notes

- The local `compiler-worker` service exposes workflow health and metrics endpoints; the tested end-to-end execution path is the in-process workflow harness in `tests/e2e/test_full_compilation_flow.py`.
- The Helm chart lives under `deploy/helm/tool-compiler/`.
- Grafana dashboard templates live under `observability/grafana/`.
