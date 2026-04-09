# GitHub Copilot — service2mcp Instructions

Use this document as **binding project context** for all suggestions, completions, and edits.

---

## 1. What this repository is

- **Product name:** `service2mcp`
- **Purpose:** Compile APIs (OpenAPI, REST, GraphQL, gRPC, SOAP/WSDL, OData, SQL, JSON-RPC, SCIM) into a **governed, observable MCP tool server**: detect → extract → **ServiceIR** → validate → deploy/runtime → gateway/access control.
- **Central contract:** **`libs/ir/models.py`** — the IR is the product; extractors produce it; runtime, validator, and generator consume it.

---

## 2. Read first (authoritative docs)

| File | Role |
|------|------|
| `agent.md` | Architecture, conventions, quality gates |
| `docs/architecture.md` | Full system design deep-dive |
| `docs/quickstart.md` | Local onboarding |
| `docs/extractor-developer-guide.md` | Writing new extractors |
| `docs/adr/*.md` | Architecture decisions |

---

## 3. Architecture (do not fork per protocol)

- **Three planes:** Control (compiler API, registry, access control) · Build (extractors, enhancer, validators, generator) · Runtime (generic MCP runtime, gateway, observability).
- **Pipeline shape:** `detect → extract → normalize to ServiceIR → validate → deploy/runtime → publish`.
- **Do not** introduce a second pipeline shape for one protocol family, or let runtime assumptions leak into extractors.

---

## 4. Non-negotiable code conventions

- **Extractor purity:** Code under `libs/extractors/` must **not** call LLMs. Extractor output uses `source: "extractor"`. LLM work lives in `libs/enhancer/` or explicit opt-in helpers (e.g. REST seed mutation via injected `llm_client`) — follow existing patterns.
- **Source tracking:** Any LLM- or user-derived field uses `source` and `confidence` where applicable.
- **Risk:** Semantic **RiskMetadata** (`writes_state`, `destructive`, `external_side_effect`, `idempotent`, `risk_level`) — not HTTP-method guessing alone.
- **Unsupported behavior:** Must fail **explicitly and observably** — no silent degradation to wrong compiled output.
- **IR versioning:** `ir_version` semver; breaking IR changes → major bump.
- **Scope:** Change only what the task requires; match surrounding style, types, and test patterns.

---

## 5. Quality gates (must pass before merging)

```bash
ruff check .
ruff format --check .
mypy libs/ apps/ tests/integration tests/contract tests/e2e
basedpyright
pytest -q
```

Use `Makefile` targets where defined (`make lint`, `make typecheck`, `make test`, etc.). Do not claim green results without running the gates.

---

## 6. Git and secrets (mandatory)

- **Before every `git push`:** run **`make gitleaks`**. Fix or properly allowlist findings; never push secrets.
- Optional: install `scripts/git-hooks/pre-push.sample` as `.git/hooks/pre-push` for automatic scans.
- **Never** commit API keys, tokens, or key files. Treat secrets as environment-only.
- Use conventional commit prefixes: `fix:`, `feat:`, `docs:`, `test:`, `refactor:`.

---

## 7. Testing expectations

- New logic needs **unit tests** next to modules (`libs/*/tests/`, `apps/*/tests/` as applicable) and **integration tests** when crossing service boundaries (`tests/integration/`, `tests/e2e/`, `tests/contract/`).
- Reuse fixtures under `tests/fixtures/`; keep conformance and regression cases for messy specs.
- Property-based tests (`hypothesis`) where it adds value.

---

## 8. High-risk areas (extra care + human review)

- Database migrations (`migrations/`)
- AuthN/AuthZ, JWT, PATs (`apps/access_control/`)
- Gateway binding, routes, rollback (`apps/access_control/gateway_binding/`, `apps/gateway_admin_mock/`)
- Compiler worker workflows, Celery, deployment activities (`apps/compiler_worker/`)
- Kubernetes/Helm (`deploy/helm/`)

---

## 9. Quick directory map

| Area | Path |
|------|------|
| IR | `libs/ir/` |
| Extractors | `libs/extractors/` |
| Enhancer / LLM | `libs/enhancer/` |
| Validators | `libs/validator/` (incl. `audit.py`, `capability_matrix.py`) |
| Compiler API | `apps/compiler_api/` |
| Worker | `apps/compiler_worker/` |
| MCP runtime | `apps/mcp_runtime/` |
| Access control | `apps/access_control/` |
| Deploy | `deploy/` |

---

## 10. Copilot behavior

- Prefer **small, test-backed diffs**.
- If a request conflicts with **extractor purity**, **IR contracts**, or **explicit unsupported semantics**, flag it and propose a compliant design.
