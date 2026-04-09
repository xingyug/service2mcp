# service2mcp — Agent Briefing

> This file is the authoritative context document for any AI coding agent working on this project.
> Read this first before making changes.

## 1. What This Project Is

**service2mcp** compiles APIs (OpenAPI, REST, GraphQL, gRPC, SOAP/WSDL, OData, SQL, JSON-RPC, SCIM) into governed, observable [MCP](https://modelcontextprotocol.io/) tool servers.

**Pipeline:** detect → extract → **ServiceIR** → validate → deploy/runtime → gateway/access control.

**Central contract:** `libs/ir/models.py` — the IR is the product. Extractors produce it; runtime, validators, and generators consume it.

## 2. Architecture

Three planes:

- **Control plane** — Compiler API, service registry, access control
- **Build plane** — extractors, LLM enhancer, validators, generator
- **Runtime plane** — generic MCP runtime, gateway, observability

Do **not** introduce a second pipeline shape for one protocol family, or let runtime assumptions leak into extractors.

## 3. Directory Map

| Area | Path |
|------|------|
| IR | `libs/ir/` |
| Extractors | `libs/extractors/` |
| Enhancer / LLM | `libs/enhancer/` |
| Validators | `libs/validator/` |
| Compiler API | `apps/compiler_api/` |
| Worker | `apps/compiler_worker/` |
| MCP Runtime | `apps/mcp_runtime/` |
| Access Control | `apps/access_control/` |
| Web UI | `apps/web-ui/` |
| Deploy | `deploy/` |
| Tests | `tests/` (integration, contract, e2e, security) |

## 4. Non-Negotiable Code Conventions

- **Extractor purity:** Code under `libs/extractors/` must **not** call LLMs. Extractor output uses `source: "extractor"`. LLM work lives in `libs/enhancer/` or explicit opt-in helpers.
- **Source tracking:** Any LLM- or user-derived field uses `source` and `confidence` where applicable.
- **Risk metadata:** Semantic `RiskMetadata` (`writes_state`, `destructive`, `external_side_effect`, `idempotent`, `risk_level`) — not HTTP-method guessing alone.
- **Unsupported behavior:** Must fail **explicitly and observably** — no silent degradation.
- **IR versioning:** `ir_version` is semver; breaking IR changes → major bump.
- **Scope:** Change only what the task requires; match surrounding style, types, and test patterns.

## 5. Quality Gates

```bash
# Lint
ruff check .
ruff format --check .

# Type check
mypy libs/ apps/ tests/integration tests/contract tests/e2e
basedpyright

# Tests
pytest -q

# Or use Makefile targets:
make lint
make typecheck
make test
```

Use `nox` when available: `uv run nox -s lint typecheck test`.

## 6. Testing Expectations

- New logic needs **unit tests** next to modules (`libs/*/tests/`, `apps/*/tests/`).
- **Integration tests** when crossing service boundaries (`tests/integration/`, `tests/e2e/`, `tests/contract/`).
- Reuse fixtures under `tests/fixtures/`.
- Property-based tests (`hypothesis`) where they add value.
- Frontend tests: `cd apps/web-ui && npm test` (Vitest).

## 7. Git and Secrets

- **Before every push:** run `make gitleaks`. Never push secrets.
- **Never** commit API keys, tokens, or key files. Secrets are environment-only.
- Use conventional commit prefixes: `fix:`, `feat:`, `docs:`, `test:`, `refactor:`.

## 8. High-Risk Areas (Extra Care)

- Database migrations (`migrations/`)
- AuthN/AuthZ, JWT, PATs (`apps/access_control/`)
- Gateway binding, routes, rollback (`apps/access_control/gateway_binding/`)
- Compiler worker workflows (`apps/compiler_worker/`)
- Kubernetes/Helm (`deploy/helm/`)

## 9. Key Documentation

| Document | Description |
|----------|-------------|
| `README.md` | Project overview and quick start |
| `docs/architecture.md` | Full architecture deep-dive |
| `docs/quickstart.md` | Local dev environment setup |
| `docs/extractor-developer-guide.md` | Writing new extractors |
| `docs/ir-composition-guide.md` | IR merging and composition |
| `docs/api-reference.md` | REST API documentation |
| `docs/troubleshooting.md` | Common issues and solutions |
| `docs/adr/` | Architecture Decision Records |
| `CONTRIBUTING.md` | Contribution guidelines |

## 10. Agent Behavior

- Prefer **small, test-backed diffs**.
- If a request conflicts with **extractor purity**, **IR contracts**, or **explicit unsupported semantics**, flag it and propose a compliant design.
- Run quality gates before claiming changes are complete.
- Update this file if architecture or conventions change materially.
