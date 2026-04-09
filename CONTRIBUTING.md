# Contributing to service2mcp

Thank you for your interest in contributing to **service2mcp**! This
document explains how to get started.

## Development Setup

```bash
# Clone and install
git clone https://github.com/YOUR_ORG/service2mcp.git
cd service2mcp
uv sync --extra all          # or: pip install -e ".[all]"

# Run quality gates
make lint
make typecheck
make test
```

See [docs/quickstart.md](docs/quickstart.md) for full onboarding.

## Quality Gates

Every PR must pass these gates before merge:

1. **Lint** — `ruff check . && ruff format --check .`
2. **Type check** — `mypy libs/ apps/` and `basedpyright`
3. **Tests** — `pytest -q`
4. **Secrets** — `make gitleaks`

CI runs these automatically on every pull request.

## Code Conventions

- **Extractor purity**: Code under `libs/extractors/` must **not** call
  LLMs. LLM work belongs in `libs/enhancer/`.
- **Source tracking**: Any LLM- or user-derived field uses `source` and
  `confidence` where applicable.
- **Risk metadata**: Use semantic `RiskMetadata` — not HTTP-method
  guessing alone.
- **IR versioning**: `ir_version` is semver. Breaking IR changes require
  a major bump.
- Prefer small, test-backed diffs.

## Testing

- Unit tests live next to modules (`libs/*/tests/`, `apps/*/tests/`).
- Integration tests go under `tests/integration/` and `tests/e2e/`.
- Reuse fixtures from `tests/fixtures/`.
- Property-based tests via `hypothesis` where they add value.

## Commit Messages

Use conventional prefixes:

```
fix: handle null parameters in OpenAPI specs
feat: add AsyncAPI extractor
docs: update quickstart guide
test: add property-based tests for IR composition
```

## Pull Request Process

1. Fork the repo and create a feature branch.
2. Make your changes with tests.
3. Ensure all quality gates pass locally.
4. Open a pull request against `main`.
5. Address review feedback.

## Reporting Issues

Use GitHub Issues. Include:

- Steps to reproduce
- Expected vs actual behavior
- Python version and OS
- Relevant log output

## License

By contributing, you agree that your contributions will be licensed
under the [Apache License 2.0](LICENSE).
