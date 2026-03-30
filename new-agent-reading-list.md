Current pause-point note:
- Read `agent.md` first for the latest status (`B-005` foundation slice complete — black-box validation module, ground truth, integration tests).
- Then read `devlog.md`: latest entries cover Stream C known-issue fixes, B-004 completion, and B-005 foundation slice.
- Backlogs B-001–B-004 complete; Stream C (ENT-001–012) complete; `B-005` (Real External API Black-Box Validation) foundation slice complete — live external API run is the remaining step.
- Quality gates at last verification: **1313** tests, ruff/mypy clean (see `devlog.md`).
- The project has also been synced to the private GitHub repo `xingyug/service2mcp` on `main`; if a public open-source release happens later, treat it as a fresh export into a new public repo without carrying over this private/internal history.
- Before every `git push`, run `make gitleaks` (mandatory policy; see `agent.md` Git Conventions and `scripts/git-hooks/pre-push.sample`).

Next directions (spec tasks defined in `docs/post-sdd-modular-expansion-plan.md`):
- **B-005-T1**: Run live external API validation against real JSONPlaceholder and PetStore APIs
- **B-006** (4 tasks): Enterprise protocol runtime completion — OData/SCIM/JSON-RPC runtime adapters
- **B-007** (5 tasks): Toolchain migration — uv + nox + basedpyright + pre-commit + security gates
- **B-008** (4 tasks): Auth-aware discovery, rate-limit backoff, pagination-aware traversal
- **B-009** (5 tasks): Open-source release preparation — content audit, docs rewrite, license, public CI, fresh export

Core documentation:
- `tool-compiler-v2-sdd.md`: `../tool-compiler-v2-sdd.md`
- `service2mcp-unified-spec.md`: `./specs/service2mcp-unified-spec.md`
- `agent.md`: `./agent.md`
- `devlog.md`: `./devlog.md`
- `context-engineering.md`: `./docs/context-engineering.md`
- `post-sdd-modular-expansion-plan.md`: `./docs/post-sdd-modular-expansion-plan.md`
- `quickstart.md`: `./docs/quickstart.md`

Key implementation files for B-003:
- `libs/extractors/rest.py` — REST extractor with resource hierarchy inference
- `libs/validator/audit.py` — `AuditPolicy`, `AuditThresholds`, `LargeSurfacePilotReport`
- `libs/validator/post_deploy.py` — `validate_with_audit()` for combined validation + audit
- `tests/fixtures/large_surface_rest_mock.py` — 62-endpoint mock with HATEOAS detail responses
- `tests/integration/test_large_surface_pilot.py` — B-003 pilot integration test

Key implementation files for P1 pipeline integration:
- `apps/compiler_worker/activities/production.py` — `_apply_post_enhancement()`, `_tool_grouping_enabled()`, wiring in `enhance_stage`
- `tests/e2e/test_full_compilation_flow.py` — `tool_intent` assertions in E2E tests
- `tests/integration/test_compiler_worker_activities.py` — `test_apply_post_enhancement_sets_tool_intent_and_bifurcates_descriptions`

Key implementation files for B-003 P1:
- `libs/extractors/llm_seed_mutation.py` — LLM-driven seed mutation for REST endpoint discovery
- `libs/enhancer/tool_grouping.py` — Semantic tool grouping via LLM-ITL intent clustering
- `libs/enhancer/tool_intent.py` — Discovery vs Action tool intent derivation and description bifurcation
- `libs/validator/llm_judge.py` — LLM-as-a-Judge evaluation pipeline for tool description quality

Key implementation files for B-004 (P1 Live LLM Proof):
- `apps/proof_runner/live_llm_e2e.py` — `ProofResult` now carries `tool_intent_counts` and `judge_evaluation`; `--enable-llm-judge` CLI flag; `_build_llm_judge_from_env()`
- `scripts/smoke-gke-llm-e2e.sh` — `ENABLE_TOOL_GROUPING`, `ENABLE_LLM_JUDGE` env vars; LLM secret injection for proof runner
- `tests/integration/test_large_surface_pilot.py` — P1 pilot tests with mock LLM

Key implementation files for B-005 (Real External API Black-Box Validation):
- `libs/validator/black_box.py` — `evaluate_black_box()`, `BlackBoxReport`, `EndpointMatch`, `FailurePattern` — core comparison engine
- `tests/fixtures/ground_truth/jsonplaceholder.py` — 21-endpoint ground truth + mock HTTP transport for JSONPlaceholder REST API
- `tests/fixtures/ground_truth/petstore_v3.py` — 19-endpoint ground truth + inline OpenAPI spec + mock transport for PetStore v3
- `libs/validator/tests/test_black_box.py` — 14 unit tests for black-box evaluation module
- `tests/integration/test_black_box_validation.py` — 14 integration tests (REST discovery + OpenAPI spec-first)
- `scripts/smoke-black-box-external.sh` — Operator harness for live external API validation (not CI)

ADRs:
- `001-ir-as-first-class-artifact.md`: `./docs/adr/001-ir-as-first-class-artifact.md`
- `002-generic-runtime-default.md`: `./docs/adr/002-generic-runtime-default.md`
- `003-pipeline-orchestration-abstraction.md`: `./docs/adr/003-pipeline-orchestration-abstraction.md`
- `004-oidc-jwt-auth-and-pats.md`: `./docs/adr/004-oidc-jwt-auth-and-pats.md`
- `005-semantic-risk-classification.md`: `./docs/adr/005-semantic-risk-classification.md`
