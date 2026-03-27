# GitHub Copilot — service2mcp (Tool Compiler v2) Instructions

Use this document as **binding project context** for all suggestions, completions, and edits. When in doubt, read the linked files in-repo before changing behavior.

---

## 1. What this repository is

- **Product name:** `service2mcp` (historical folder name: `tool-compiler-v2`).
- **Purpose:** Compile APIs (OpenAPI, REST discovery, GraphQL, gRPC, SOAP/WSDL, SQL, etc.) into a **governed, observable MCP tool server**: detect → extract → **ServiceIR** → validate → deploy/runtime → gateway/access control.
- **Central contract:** **`libs/ir/models.py`** — the IR is the product; extractors produce it; runtime, validator, and generator consume it.

---

## 2. Read first (authoritative docs)

| File | Role |
|------|------|
| `agent.md` | Current status, paths, conventions, git/secrets policy |
| `devlog.md` | Chronological implementation and verification |
| `docs/post-sdd-modular-expansion-plan.md` | Post-SDD roadmap: **B-001…B-003**, exit criteria |
| `docs/context-engineering.md` | **One module slice at a time** — avoid context bleed |
| `../tool-compiler-v2-sdd.md` | Full SDD: architecture, T-001…T-033 definitions |
| `docs/quickstart.md` | Local / GKE onboarding |
| `docs/adr/*.md` | Architecture decisions (IR-first, generic runtime, pipeline abstraction, OIDC/PATs, semantic risk) |

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

## 5. Quality gates (must pass before merging substantial work)

Until `uv`/`nox`/`basedpyright` are fully adopted, the **authoritative** local gates are (from repo root, with `.venv` active):

```bash
ruff check .
ruff format --check .
mypy libs/ apps/ tests/integration tests/contract tests/e2e   # match project convention
pytest -q
```

Use `Makefile` targets where defined (`make lint`, `make typecheck`, `make test`, etc.). Do not claim green results without running the gates.

---

## 6. Git and secrets (mandatory)

- **Before every `git push`:** run **`make gitleaks`** (requires [gitleaks](https://github.com/gitleaks/gitleaks) on `PATH`). Fix or properly allowlist findings; never push secrets.
- Optional: install `scripts/git-hooks/pre-push.sample` as `.git/hooks/pre-push` for automatic scans.
- **Never** commit API keys, tokens, or contents of operator key files (e.g. VM-local `LLM_API_KEY` files). Treat secrets as environment-only.
- **Commits:** Prefix with task IDs when applicable: `T-00X:`, `B-00X:`, `H-00X:`, etc.
- **Remote:** Primary private GitHub repo `xingyug/service2mcp` on `main`. Public releases, if any, should be a **fresh export** without importing private history (see `README.md`).

---

## 7. Completed delivery backlogs (do not re-open as “missing SDD work”)

Treat as **done** unless explicitly extending:

- **SDD:** T-001 … T-033  
- **Post-SDD expansion:** H-001 … H-008  
- **Follow-on:** R-001 … R-003, P-001 … P-006, L-001 … L-006  
- **B-002:** Catalog/audit slice for REST discovery hardening (live-validated); **B-003** pilot + P1 paper-informed features (LLM seed mutation, tool grouping, discovery/action intent, LLM-as-Judge) — see `agent.md` and `post-sdd-modular-expansion-plan.md`
- **Latest joint GKE LLM E2E proof:** image tag **`20260327-75be3a5-r29`** (`PROTOCOL=all`, `AUDIT_ALL_GENERATED_TOOLS=1`), namespace `tool-compiler-llm-b003-032621`, aggregate audit **13/13/7/7/0/6** — confirms B-003 REST OPTIONS / dedup changes against live cluster; details in `agent.md` **Latest verification** and `devlog.md` section **B-003 GKE LLM E2E Live Proof**.

---

## 8. Active and upcoming work (prioritize in this spirit)

### Black-box / audit track (`docs/post-sdd-modular-expansion-plan.md`)

- **B-001 (in progress):** Generated-tool **audit** — `audit_summary` exists and joint **`PROTOCOL=all`** GKE runs have succeeded (e.g. **`r29`**); remaining work is **gate strategy** (representative smoke vs stricter audit), optional **per-protocol** live baselines, and policy for skipped tools.
- **B-002 (remaining):** Continue REST discovery hardening on **messier real-world** targets; refine **AuditPolicy** as needed so more safe tools are auditable without widening risk. (Validator `audit_summary` + fourth-slice policy landed in-tree.)
- **B-003 (follow-on):** OPTIONS deep probing + pilot thresholds + **GKE `r29`** proof are done; next focus is **real targets** beyond fixtures, and product decisions on spec-first vs black-box emphasis.

### Product (planned, not implemented)

- **Operator web UI** with **mandatory human review**: edit IR, recorded decisions, gated promotion (draft → approved → publish/deploy), plus jobs, registry, access control, gateway workflows. Today the platform is API- and script-first.

### Toolchain (aspirational)

- Long-term: `uv`, `nox`, `basedpyright`, pre-commit, security scanners — **do not assume** they are wired; follow `agent.md` transition rules.

---

## 9. Testing expectations

- New logic needs **unit tests** next to modules (`libs/*/tests/`, `apps/*/tests/` as applicable) and **integration tests** when crossing service boundaries (`tests/integration/`, `tests/e2e/`, `tests/contract/`).
- Reuse fixtures under `tests/fixtures/`; keep conformance and regression cases for messy specs.
- Property-based tests (`hypothesis`) where it adds value.

---

## 10. High-risk areas (extra care + human review)

- Database migrations (`migrations/`)
- AuthN/AuthZ, JWT, PATs (`apps/access_control/`)
- Gateway binding, routes, rollback (`apps/access_control/gateway_binding/`, `apps/gateway_admin_mock/`)
- Compiler worker workflows, Celery, deployment activities (`apps/compiler_worker/`)
- Kubernetes/Helm (`deploy/helm/`)

---

## 11. Quick directory map

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

## 12. Copilot behavior

- Prefer **small, test-backed diffs** aligned with `context-engineering.md`.
- When adding features, **update** `agent.md` and `devlog.md` if scope or status changes (per `agent.md` AI Maintenance Requirements).
- If a request conflicts with **extractor purity**, **IR contracts**, or **explicit unsupported semantics**, flag it and propose a compliant design.
