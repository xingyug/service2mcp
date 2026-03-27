# Stream A: CLI Support

## Goal

Compile CLI tools into governed MCP capabilities.  An agent should be able to call `kubectl get pods`, `aws s3 ls`, or `terraform plan` through MCP — with risk classification, output parsing, and execution sandboxing.

## Scope

- **In scope:** CLI discovery, command tree extraction, output parsing (JSON/table/text), local/container/sandbox execution, risk classification for CLI ops.
- **Out of scope:** Browser/GUI automation, interactive TUI programs, long-running daemon processes.

---

## Architecture

```
CLI binary/man page/help output
        │
        ▼
┌───────────────────┐
│ CLIExtractor      │  libs/extractors/cli.py
│ protocol_name="cli"│
│ detect() → 0.0-1.0│
│ extract() → ServiceIR│
└───────┬───────────┘
        │
        ▼ ServiceIR (protocol="cli")
        │   Operations have method=None, path=None
        │   Each op has cli_command: CliOperationConfig
        │
┌───────────────────┐
│ CLIRuntimeAdapter │  apps/mcp_runtime/cli.py
│ Executes via      │
│ subprocess/docker  │
└───────────────────┘
```

## IR Extension

Add to `Operation` model:

```python
class CliOperationConfig(BaseModel):
    """Typed CLI execution contract for one command."""

    binary: str = Field(min_length=1, description="CLI binary name or path")
    subcommand: list[str] = Field(default_factory=list, description="Subcommand parts, e.g. ['get', 'pods']")
    fixed_args: list[str] = Field(default_factory=list, description="Always-present arguments")
    output_format: Literal["json", "table", "text", "yaml"] = "text"
    timeout_seconds: float = Field(default=30.0, gt=0.0)
    execution_context: Literal["local", "container", "sandbox"] = "local"
    container_image: str | None = None  # required when execution_context="container"
```

On `Operation`:
```python
cli: CliOperationConfig | None = None
```

Add mutual exclusion validator (like existing grpc_unary/soap/sql/graphql pattern).

## New Files

| File | Purpose |
|---|---|
| `libs/extractors/cli.py` | CLI extractor: parses `--help` output, man pages, or CLI spec files |
| `libs/extractors/cli_output_parser.py` | Structured output parser for JSON/table/text/YAML |
| `apps/mcp_runtime/cli.py` | CLI execution adapter (subprocess, Docker, sandbox) |
| `tests/fixtures/cli_mocks/` | Mock CLI binaries (shell scripts) for testing |
| `libs/extractors/tests/test_cli.py` | Unit tests for CLI extractor |
| `libs/extractors/tests/test_cli_output_parser.py` | Unit tests for output parser |
| `tests/integration/test_mcp_runtime_cli.py` | Integration test: extract → compile → runtime |

## Modified Files

| File | Change |
|---|---|
| `libs/ir/models.py` | Add `CliOperationConfig`, `cli` field on `Operation`, mutual exclusion validator |
| `libs/extractors/__init__.py` | Register CLI extractor in default registry |
| `libs/validator/capability_matrix.py` | Add `cli` row |

---

## Task Backlog

### CLI-001: CliOperationConfig model
**File:** `libs/ir/models.py`
**What:** Add `CliOperationConfig` Pydantic model and `cli: CliOperationConfig | None = None` field on `Operation`. Add mutual exclusion validator with graphql/sql/grpc_unary/soap.
**Tests:** Unit tests in `libs/ir/tests/test_models.py` — valid CLI config, mutual exclusion errors, container_image required when context=container.
**Exit:** `pytest libs/ir/tests/ -q` green, `mypy libs/ir/models.py` clean.

### CLI-002: CLI help output parser
**File:** `libs/extractors/cli_output_parser.py`
**What:** Parse `--help` / `-h` text output into structured command trees. Extract:
- Subcommands and their descriptions
- Flags (name, type, required, default, description)
- Positional arguments
- Output format hints (e.g., `--output json` availability)

Support patterns: GNU-style (`--flag VALUE`), POSIX short (`-f`), Go-style (`-flag=value`).
**Tests:** `libs/extractors/tests/test_cli_output_parser.py` — test with `kubectl`, `aws`, `git`, `curl` help text fixtures.
**Exit:** 100% line coverage on parser module.

### CLI-003: CLI extractor detect()
**File:** `libs/extractors/cli.py`
**What:** Implement `CLIExtractor.detect(source: SourceConfig) -> float`.
- If `source.hints.get("protocol") == "cli"` → 1.0
- If `source.file_path` ends in `.cli.yaml` or `.cli.json` (CLI spec) → 0.9
- If `source.file_content` looks like `--help` output → 0.7
- Otherwise → 0.0
**Tests:** `libs/extractors/tests/test_cli.py::TestDetect`
**Exit:** All detection scenarios covered.

### CLI-004: CLI extractor extract()
**File:** `libs/extractors/cli.py`
**What:** Implement `CLIExtractor.extract(source: SourceConfig) -> ServiceIR`.
- Parse help output via `cli_output_parser`
- OR parse `.cli.yaml` spec file (structured CLI definition)
- Map each subcommand to an `Operation` with:
  - `method=None`, `path=None`
  - `cli=CliOperationConfig(binary=..., subcommand=[...], ...)`
  - Params from flags and positional args
  - Risk: read-only commands → `safe`, write/delete → `cautious`/`dangerous`
- Generate `source_hash` from the help text content
**Tests:** `libs/extractors/tests/test_cli.py::TestExtract` — at least 3 CLI fixtures.
**Exit:** Extracted IR validates against `ServiceIR` model.

### CLI-005: CLI spec file format
**File:** `libs/extractors/cli.py` (part of extract)
**What:** Define and parse a `.cli.yaml` format for declaring CLI tools:
```yaml
binary: kubectl
version: "1.28"
commands:
  - name: get_pods
    subcommand: [get, pods]
    description: List pods in a namespace
    output_format: json
    flags:
      - name: namespace
        short: "n"
        type: string
        required: false
        default: default
      - name: all-namespaces
        short: A
        type: boolean
    risk: safe
```
**Tests:** Parse at least 2 `.cli.yaml` fixtures.
**Exit:** Round-trip: YAML → ServiceIR → serialize → deserialize matches.

### CLI-006: CLI runtime executor (local)
**File:** `apps/mcp_runtime/cli.py`
**What:** `CLIRuntimeExecutor` class:
- Takes `CliOperationConfig` + user params
- Builds subprocess command: `[binary] + subcommand + fixed_args + user_args`
- Runs via `asyncio.create_subprocess_exec` with timeout
- Captures stdout/stderr
- Parses output based on `output_format` (JSON → dict, table → parsed rows, text → raw)
- Returns structured MCP tool result
**Security:** Strict argument escaping. No shell=True. Allowlist of binaries if configured.
**Tests:** `tests/integration/test_mcp_runtime_cli.py` with mock CLIs.
**Exit:** Local execution works for JSON and text output formats.

### CLI-007: CLI runtime executor (container)
**File:** `apps/mcp_runtime/cli.py`
**What:** When `execution_context="container"`:
- Build `docker run --rm <container_image> <binary> <subcommand> <args>`
- Use `asyncio.create_subprocess_exec` to invoke Docker
- Same output parsing pipeline as local
**Tests:** Mock Docker invocation (don't require Docker in unit tests).
**Exit:** Container path produces same parsed output structure as local path.

### CLI-008: Tool registration in runtime
**File:** `apps/mcp_runtime/loader.py`
**What:** Extend `register_ir_tools` to handle operations with `cli` config:
- Use `CLIRuntimeExecutor` instead of `RuntimeProxy` for HTTP
- Wire output parsing into MCP tool response
**Tests:** Integration test proving CLI operation appears in MCP tool list.
**Exit:** `mcp.list_tools()` includes CLI-backed tools.

### CLI-009: Capability matrix update
**File:** `libs/validator/capability_matrix.py`
**What:** Add `cli` row to `_CAPABILITY_ROWS` and `_CAPABILITY_ORDER`.
**Tests:** `libs/validator/tests/test_capability_matrix.py` — matrix includes CLI.
**Exit:** Matrix renders CLI row correctly.

### CLI-010: End-to-end integration test
**Files:** `tests/integration/test_mcp_runtime_cli.py`
**What:** Full path test:
1. Create a mock CLI (shell script that outputs JSON)
2. Create a `.cli.yaml` spec pointing at it
3. Extract → ServiceIR with CLI operations
4. Register in MCP runtime
5. Call via MCP tool → get parsed JSON response
6. Verify risk classification, output parsing, timeout handling
**Exit:** Integration test green, covers happy path + timeout + non-zero exit code.

---

## Key Design Decisions

1. **No shell=True ever.** All CLI execution via `subprocess_exec` with explicit argument lists. This eliminates command injection.
2. **Output parsing is best-effort.** If a CLI claims JSON output but returns garbage, the raw text is returned with a warning — not an error.
3. **Container execution is optional.** Default is local. Container support is for sandboxing untrusted CLIs.
4. **The `.cli.yaml` spec is our own format.** There's no universal CLI spec standard. We define a minimal, practical one.
5. **Method and path are None for CLI ops.** This is a new pattern — CLI tools don't have HTTP routes. The runtime dispatches on `operation.cli is not None`.
