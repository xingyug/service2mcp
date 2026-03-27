Current pause-point note:
- Read `agent.md` first for the latest status (all backlogs complete; repository DTO transformers & audit service unit tests added — 1080 tests).
- Then read `devlog.md`: latest entry is `Slice 7 — Repository DTO transformers & audit service` (3 new test files, 23 new tests).
- All backlogs complete: T-001–T-033, H-001–H-008, R-001–R-003, P-001–P-006, L-001–L-006, B-001, B-002, B-003 (including P1 pipeline integration).
- Quality gates at last verification: **1080** tests, ruff/mypy clean (see `devlog.md`).
- The project has also been synced to the private GitHub repo `xingyug/service2mcp` on `main`; if a public open-source release happens later, treat it as a fresh export into a new public repo without carrying over this private/internal history.
- Before every `git push`, run `make gitleaks` (mandatory policy; see `agent.md` Git Conventions and `scripts/git-hooks/pre-push.sample`).

Core documentation:
- `tool-compiler-v2-sdd.md`: `../tool-compiler-v2-sdd.md`
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

ADRs:
- `001-ir-as-first-class-artifact.md`: `./docs/adr/001-ir-as-first-class-artifact.md`
- `002-generic-runtime-default.md`: `./docs/adr/002-generic-runtime-default.md`
- `003-pipeline-orchestration-abstraction.md`: `./docs/adr/003-pipeline-orchestration-abstraction.md`
- `004-oidc-jwt-auth-and-pats.md`: `./docs/adr/004-oidc-jwt-auth-and-pats.md`
- `005-semantic-risk-classification.md`: `./docs/adr/005-semantic-risk-classification.md`

