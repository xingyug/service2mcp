# service2mcp

Compile services into governed MCP tool servers.

> Status: alpha. The core pipeline, local dev stack, and live proof harnesses are working, but some scripts and defaults still reflect an internal environment and are being cleaned up for broader external use.

## What It Does

`service2mcp` takes a service definition or live endpoint and turns it into an MCP-compatible tool runtime:

- detect the source protocol
- extract and normalize it into a shared IR
- enrich operation metadata with LLM-assisted descriptions
- generate deployable runtime artifacts
- validate and deploy the runtime
- expose governed MCP tools for agent use

Today the repository covers OpenAPI, REST discovery, GraphQL, gRPC, SOAP/WSDL, and SQL reflection.

## Main Components

- `apps/compiler_api`: submission, artifact, and service APIs
- `apps/compiler_worker`: queue-backed compilation and rollback workflows
- `apps/mcp_runtime`: generated runtime that serves MCP tools
- `apps/access_control`: authn/authz, gateway binding, and audit services
- `libs/extractors`: protocol-specific extraction pipeline
- `libs/validator`: pre-deploy and post-deploy validation

## Quick Start

From the repository root:

```bash
./scripts/setup-dev.sh
make dev-up
make dev-smoke
.venv/bin/pytest -q tests/e2e/test_full_compilation_flow.py
```

Useful next references:

- `docs/quickstart.md`: local and GKE-oriented walkthroughs
- `agent.md`: current project status and handoff snapshot
- `devlog.md`: chronological implementation and verification log

## Current Project State

- Original SDD implementation backlog is complete
- Post-SDD expansion and protocol-completion tracks are complete
- Cross-protocol live proof track is complete
- `B-002` REST black-box hardening is complete and live-validated
- `B-003` large-surface black-box pilot is complete, including the paper-informed P1 slice (LLM seed mutation, semantic tool grouping, discovery/action bifurcation, LLM-as-a-Judge)
- **Next follow-on work** (see `docs/post-sdd-modular-expansion-plan.md`): extend **B-001** generated-tool audit across protocols and reporting surfaces; **B-002** backlog items (audit summary in validator/reporting, skip-policy refinement); **B-003** remaining discovery research (OPTIONS-heavy probing, large-spec pilot, regression thresholds)

The current clean audit-enabled GKE baseline recorded:

- `discovered=13`
- `generated=13`
- `audited=7`
- `passed=7`
- `failed=0`
- `skipped=6`

## Current Caveats

- Some live harnesses still assume private images or environment-specific defaults
- The repository has not yet been fully polished for public open-source onboarding
- Top-level project governance files are still minimal and will need expansion before a public launch
- The current collaboration/baseline repo is private; if this project is later open-sourced, the preferred path is a fresh public repo without carrying over the full private/internal history

## Development

Common commands:

```bash
make test
make contract-test
make test-integration
make lint
make typecheck
```

Before every commit that may be pushed, and **before every `git push`**, run a secrets scan (required project policy):

```bash
make gitleaks
```

Optional: install the repo-provided pre-push hook so `gitleaks` runs automatically on push:

```bash
cp scripts/git-hooks/pre-push.sample .git/hooks/pre-push && chmod +x .git/hooks/pre-push
```

## Naming

The external/project name is `service2mcp`. Some internal paths and historical docs still refer to `tool-compiler-v2`; those are being renamed incrementally to avoid unnecessary churn while the system is still under active hardening.
