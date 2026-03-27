# Stream D: IR Evolution (MCP Resources + Prompts)

## Goal

Extend `ServiceIR` to produce MCP **resources** and **prompts** alongside tools.  Currently our IR only generates MCP tools.  MCP's full capability model includes:
- **Tools:** callable functions (what we have)
- **Resources:** read-only data the agent can reference as context (what we're adding)
- **Prompts:** reusable prompt templates for interacting with tools (what we're adding)

## Scope

- **In scope:** ResourceDefinition and PromptDefinition models in IR, generation of resource/prompt registration manifests, runtime registration of MCP resources and prompts.
- **Out of scope:** Dynamic resource content fetching (Phase 2), prompt chaining/orchestration.

---

## Architecture

```
ServiceIR
  ├── operations: list[Operation]         ← existing (MCP tools)
  ├── resource_definitions: list[ResourceDefinition]  ← NEW (MCP resources)
  └── prompt_definitions: list[PromptDefinition]      ← NEW (MCP prompts)
         │
         ▼
┌────────────────────────┐
│ Generator / Loader     │
│ Produces:              │
│  - tool registrations  │  (existing)
│  - resource registrations│  (NEW)
│  - prompt registrations │  (NEW)
└────────┬───────────────┘
         │
         ▼
┌────────────────────────┐
│ MCP Runtime            │
│ FastMCP server         │
│ .add_resource()        │  (NEW)
│ .add_prompt()          │  (NEW)
└────────────────────────┘
```

## IR Extension

### ResourceDefinition

```python
class ResourceDefinition(BaseModel):
    """A read-only data resource the agent can access as context."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    uri: str = Field(min_length=1, description="MCP resource URI, e.g. 'service://petstore/schema'")
    mime_type: str = "application/json"
    content_type: Literal["static", "dynamic"] = "static"
    content: str | None = None  # for static resources, inline content
    operation_id: str | None = None  # for dynamic resources, links to an operation that fetches it
    tags: list[str] = Field(default_factory=list)
```

### PromptDefinition

```python
class PromptDefinition(BaseModel):
    """A reusable prompt template for interacting with the service's tools."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    template: str = Field(min_length=1, description="Prompt template text with {placeholder} variables")
    arguments: list[PromptArgument] = Field(default_factory=list)
    tool_ids: list[str] = Field(default_factory=list, description="Operations this prompt is designed for")
    tags: list[str] = Field(default_factory=list)

class PromptArgument(BaseModel):
    """An argument for a prompt template."""

    name: str = Field(min_length=1)
    description: str = ""
    required: bool = False
    default: str | None = None
```

### ServiceIR additions

```python
class ServiceIR(BaseModel):
    # ... existing fields ...
    resource_definitions: list[ResourceDefinition] = Field(default_factory=list)
    prompt_definitions: list[PromptDefinition] = Field(default_factory=list)
```

Plus validators:
- `resource_definitions` IDs must be unique
- `prompt_definitions` IDs must be unique
- `prompt_definitions.tool_ids` must reference valid operation IDs
- `resource_definitions.operation_id` must reference valid operation IDs

## Modified Files

| File | Change |
|---|---|
| `libs/ir/models.py` | Add `ResourceDefinition`, `PromptDefinition`, `PromptArgument`, fields on `ServiceIR`, validators |
| `libs/ir/schema.py` | Schema generation for new fields |
| `libs/generator/generic_mode.py` | Generate resource/prompt registration manifests |
| `apps/mcp_runtime/loader.py` | Load and register resources + prompts |
| `apps/mcp_runtime/main.py` | Wire resource/prompt registration into startup |

## New Files

| File | Purpose |
|---|---|
| `libs/ir/tests/test_resource_prompt_models.py` | Unit tests for new IR models |
| `tests/integration/test_mcp_runtime_resources_prompts.py` | Integration test for resource/prompt registration |

---

## Task Backlog

### IRX-001: ResourceDefinition and PromptDefinition models
**File:** `libs/ir/models.py`
**What:** Add `PromptArgument`, `PromptDefinition`, `ResourceDefinition` Pydantic models. Add `resource_definitions` and `prompt_definitions` fields to `ServiceIR` with `default_factory=list`. Both fields are optional/additive — existing IRs with no resources/prompts continue to work unchanged.
**Tests:** `libs/ir/tests/test_resource_prompt_models.py`:
- Valid resource creation with all fields
- Valid prompt creation with arguments
- ResourceDefinition with static content
- ResourceDefinition with dynamic operation_id link
- Default factory produces empty lists (backward compat)
**Exit:** `pytest libs/ir/tests/ -q` green, `mypy` clean, existing tests unchanged.

### IRX-002: ServiceIR validators for new fields
**File:** `libs/ir/models.py`
**What:** Add validators to `ServiceIR`:
- `resource_definition_ids_must_be_unique` — like existing `operation_ids_must_be_unique`
- `prompt_definition_ids_must_be_unique`
- `prompt_tool_ids_must_reference_valid_operations` — all `tool_ids` in prompts must exist in `operations`
- `resource_operation_ids_must_reference_valid_operations` — `operation_id` on resources must exist
**Tests:** Validator error cases for duplicates, invalid refs.
**Exit:** All validators fire correctly.

### IRX-003: Auto-generate resources from extractors
**File:** `libs/enhancer/enhancer.py` (or new `libs/enhancer/resource_generator.py`)
**What:** Automatically generate common resources during enhancement:
- `service:///{service_name}/schema` — the API schema summary (static, from IR metadata)
- `service:///{service_name}/operations` — list of available operations with descriptions (static)
- `service:///{service_name}/auth-requirements` — authentication requirements (static)
These give the agent context about the service before calling tools.
**Tests:** Enhance an IR, verify 3 auto-generated resources.
**Exit:** Resources generated with correct URIs and content.

### IRX-004: Auto-generate prompts from extractors
**File:** `libs/enhancer/enhancer.py` (or new `libs/enhancer/prompt_generator.py`)
**What:** Auto-generate common prompt templates:
- `explore_{service}` — "List available operations for {service_name} and their risk levels"
- `safe_discovery_{service}` — "Only use discovery (read-only) tools to explore {service_name}. Available safe tools: {safe_tool_list}"
- For CRUD services: `manage_{entity}` — "Create, read, update, or delete {entity}. Available tools: {crud_tool_list}"
**Tests:** Verify prompts generated for an OpenAPI service with CRUD operations.
**Exit:** Prompts have correct tool_ids linking to operations.

### IRX-005: Schema generation for resources + prompts
**File:** `libs/ir/schema.py`
**What:** Ensure JSON Schema export of `ServiceIR` includes `resource_definitions` and `prompt_definitions` arrays with full nested schemas.
**Tests:** Generate schema, verify new fields present with correct types.
**Exit:** Schema is valid JSON Schema, includes all new types.

### IRX-006: Generator manifest output
**File:** `libs/generator/generic_mode.py`
**What:** Extend the generator to emit resource and prompt registration data alongside tool manifests. Output format:
```json
{
  "tools": [...],
  "resources": [{"uri": "...", "name": "...", "mime_type": "..."}],
  "prompts": [{"name": "...", "description": "...", "arguments": [...]}]
}
```
**Tests:** Generate from an IR with resources and prompts, verify output structure.
**Exit:** Generator output includes all three sections.

### IRX-007: Runtime registration of resources and prompts
**File:** `apps/mcp_runtime/loader.py`
**What:** Extend `register_ir_tools` (or add `register_ir_resources` + `register_ir_prompts`):
- For each `ResourceDefinition` with `content_type="static"`: register with FastMCP using `server.add_resource()` with inline content.
- For each `PromptDefinition`: register with FastMCP using `server.add_prompt()` with template and arguments.
**Tests:** Integration test: load IR with resources/prompts → verify `mcp.list_resources()` and `mcp.list_prompts()` return them.
**Exit:** Resources and prompts appear in MCP server listings.

### IRX-008: End-to-end integration test
**File:** `tests/integration/test_mcp_runtime_resources_prompts.py`
**What:** Full path test:
1. Create an IR with 2 operations, 1 resource, 1 prompt
2. Load into runtime
3. Verify `list_tools()` returns 2 tools
4. Verify `list_resources()` returns 1 resource with correct URI
5. Verify `list_prompts()` returns 1 prompt with correct arguments
6. Verify `read_resource(uri)` returns static content
7. Verify `get_prompt(name, args)` returns rendered template
**Exit:** All assertions pass.

---

## Key Design Decisions

1. **Additive, backward-compatible.** Both new fields default to empty lists. Existing IRs and tests are unaffected.
2. **Static resources first.** Dynamic resource fetching (calling an operation to get content) is Phase 2. Static content is sufficient for service metadata, schemas, and documentation.
3. **Auto-generation in enhancer.** Resources and prompts are auto-generated during the enhance stage — extractors don't need to know about them. This keeps extractors simple.
4. **URI scheme:** `service:///{service_name}/{resource}` — follows MCP resource URI conventions.
5. **Prompts reference tools by ID.** This maintains the explicit contract between prompts and the operations they're designed for.
