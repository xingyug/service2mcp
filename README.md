# service2mcp

Compile any API into a governed [MCP](https://modelcontextprotocol.io/) tool server — automatically.

## What It Does

`service2mcp` takes a service definition (spec URL, live endpoint, or database connection) and turns it into a deployable MCP tool runtime:

1. **Detect** the source protocol
2. **Extract** and normalize into a shared IR (Intermediate Representation)
3. **Enrich** operation metadata with optional LLM-assisted descriptions
4. **Validate** with semantic risk analysis and audit
5. **Generate** deployable runtime artifacts
6. **Deploy** the runtime with governance controls
7. **Expose** governed MCP tools for agent use

### Supported Protocols

| Protocol | Source | Status |
|----------|--------|--------|
| OpenAPI | Spec URL | ✅ Production |
| REST | Live endpoint (black-box) | ✅ Production |
| GraphQL | Introspection | ✅ Production |
| gRPC | Server reflection | ✅ Production |
| SOAP/WSDL | WSDL URL | ✅ Production |
| OData | $metadata | ✅ Production |
| SQL | Database connection | ✅ Production |
| JSON-RPC | system.listMethods | ✅ Production |
| SCIM | RFC 7644 endpoint | ✅ Production |

## Architecture

Three planes:

- **Control plane** — compiler API, service registry, access control
- **Build plane** — extractors, LLM enhancer, validators, generator
- **Runtime plane** — generic MCP runtime, gateway, observability

All protocols normalize to a single **ServiceIR** contract
(`libs/ir/models.py`). The IR is the product — extractors produce it;
runtime, validators, and generators consume it.

## Main Components

| Component | Path | Description |
|-----------|------|-------------|
| Compiler API | `apps/compiler_api/` | Submission, artifact, and service APIs |
| Compiler Worker | `apps/compiler_worker/` | Queue-backed compilation workflows |
| MCP Runtime | `apps/mcp_runtime/` | Generated runtime serving MCP tools |
| Access Control | `apps/access_control/` | AuthN/AuthZ, gateway binding |
| Extractors | `libs/extractors/` | Protocol-specific extraction |
| IR | `libs/ir/` | Intermediate Representation models |
| Enhancer | `libs/enhancer/` | LLM-assisted enrichment |
| Validators | `libs/validator/` | Pre/post-deploy validation, audit |

## Quick Start

```bash
# Install dependencies
uv sync --extra all          # or: pip install -e ".[all]"

# Run quality gates
make lint                     # ruff check + format
make typecheck                # mypy + basedpyright
make test                     # pytest (~4100+ tests)

# Local dev stack
make dev-up                   # docker compose up
make dev-smoke                # smoke test
```

See [docs/quickstart.md](docs/quickstart.md) for full onboarding.

## Development

```bash
make test                     # Full test suite (~4100+ tests)
make contract-test            # Contract tests only
make test-integration         # Integration tests
make lint                     # Ruff linter + formatter
make typecheck                # mypy + basedpyright
```

Before every `git push`, run a secrets scan:

```bash
make gitleaks
```

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture.md) | System design deep-dive |
| [Quickstart](docs/quickstart.md) | Local onboarding |
| [API Reference](docs/api-reference.md) | REST API documentation |
| [IR Composition](docs/ir-composition-guide.md) | IR merging and composition |
| [Extractor Guide](docs/extractor-developer-guide.md) | Writing new extractors |
| [ADRs](docs/adr/) | Architecture decisions |
| [Contributing](CONTRIBUTING.md) | Contribution guidelines |
| [Troubleshooting](docs/troubleshooting.md) | Common issues and solutions |

## Project Highlights

- **9 protocol families** with production-validated extractors
- **4100+ tests** — unit, integration, contract, and e2e
- **Semantic risk analysis** — classifies tools by write/destructive/side-effect risk
- **LLM-enhanced descriptions** — optional enrichment via any OpenAI-compatible provider
- **Gateway binding** with OIDC and PAT authentication
- **Web UI** for compilation management and service monitoring
- **Helm chart** for Kubernetes deployment

## License

[Apache License 2.0](LICENSE)
