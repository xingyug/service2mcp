Current pause-point note:
- Read `agent.md` first for the latest status: B-003 complete, all P1 paper-informed features implemented.
- Then read the latest `devlog.md` section `B-003 Third Slice: P1 Paper-Informed Features` for implementation details.
- B-003 P1 features: LLM-driven seed mutation (RESTSpecIT-style), semantic tool grouping (LLM-ITL intent clustering), Discovery/Action tool bifurcation, LLM-as-a-Judge evaluation pipeline.
- All four features are opt-in (LLM client parameter or function call), with mock-based tests. 410 tests, ruff/mypy clean.
- Paper-informed next steps (documented in `post-sdd-modular-expansion-plan.md` B-003 section): all P0 and P1 items complete.
- The project has also been synced to the private GitHub repo `xingyug/service2mcp` on `main`; if a public open-source release happens later, treat it as a fresh export into a new public repo without carrying over this private/internal history.

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

