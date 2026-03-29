# Bug Sweep – Round 4

Manual deep-dive into extractors, validator, enhancer sub-modules, and web-UI.

> **Methodology**: every file listed was read in full (or to the stated range);
> candidate bugs were verified against the actual source with Python `re` /
> import checks or TypeScript type inspection.  Only confirmed issues appear
> below.

---

## BUG-133 — gRPC: Nested message regex matches to first `}`, corrupts parse

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `libs/extractors/grpc.py` |
| **Lines** | 43-46 (`_MESSAGE_PATTERN`) |

**Description**

```python
_MESSAGE_PATTERN = re.compile(
    r'message\s+(?P<name>\w+)\s*\{(?P<body>.*?)\}', re.DOTALL
)
```

The non-greedy `.*?` with `re.DOTALL` matches to the **first** closing brace,
not the matching one.  For a proto like:

```proto
message Outer {
  int32 x = 1;
  message Inner { int32 y = 1; }
  int32 z = 2;
}
```

The regex produces `Outer` with body ending at the first `}` inside `Inner`,
so `z` is lost and `Inner` is never parsed as a separate message.

**Evidence** — reproduced with `re.finditer`:

```
Match: Outer
  Body: '\n  int32 x = 1;\n  message Inner {\n    int32 y = 1;\n  '
```

Only one match instead of two; `Outer.z` and the entire `Inner` message
definition are silently dropped.

The same pattern is used for `_SERVICE_PATTERN` and `_ENUM_PATTERN`, so
nested enums and services with option-block RPCs are equally affected.

**Impact**: Any `.proto` file with nested messages, nested enums, or services
containing RPCs with option blocks will produce an incomplete or incorrect IR.

---

## BUG-134 — gRPC: RPCs with option blocks silently dropped

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `libs/extractors/grpc.py` |
| **Lines** | 51-56 (`_RPC_PATTERN`) |

**Description**

```python
_RPC_PATTERN = re.compile(
    r'rpc\s+(?P<name>\w+)\s*'
    r'\(\s*(?P<request_stream>stream\s+)?(?P<request>[\w.]+)\s*\)\s*'
    r'returns\s*\(\s*(?P<response_stream>stream\s+)?(?P<response>[\w.]+)\s*\)\s*;',
)
```

The pattern ends with `\)\s*;`, requiring a semicolon.  RPCs that have option
blocks (very common in real-world protos — e.g. `google.api.http` annotations)
use `{ … }` instead of `;`:

```proto
rpc GetUser(GetUserRequest) returns (GetUserResponse) {
  option (google.api.http) = { get: "/v1/user" };
}
```

These RPCs are silently dropped and never appear in the extracted IR.

**Evidence** — reproduced:

```
With semicolon: MATCH
With options block: NO MATCH
```

**Impact**: gRPC services that use HTTP transcoding annotations or any other
RPC options will have those RPCs silently omitted from the compilation.

---

## BUG-135 — OData: Composite keys silently truncated to first key only

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `libs/extractors/odata.py` |
| **Lines** | ~318 (`_build_entity_set_operations`) |

**Description**

The function uses `key_props[0]` to build entity paths, but OData entities can
have composite keys (multiple `PropertyRef` elements under `Key`).  Only the
first key property is used; all others are silently dropped.

**Evidence** — reproduced with an entity having `(OrderId, ItemId)` composite
key:

```
get_orderitems_by_key: path=/OrderItems({OrderId}), required_params=['OrderId']
update_orderitems:     path=/OrderItems({OrderId}), required_params=['OrderId']
delete_orderitems:     path=/OrderItems({OrderId}), required_params=['OrderId']
```

`ItemId` is completely absent.  The generated paths are wrong and will return
404 or wrong entities at runtime.

**Impact**: Any OData entity with a composite key produces invalid CRUD
operations.

---

## BUG-136 — SCIM: Naive pluralization produces incorrect resource paths

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `libs/extractors/scim.py` |
| **Lines** | 199 |

**Description**

```python
plural = f"{name}s"
```

Naive string concatenation produces incorrect plurals for common English
nouns: "Entry" → "Entrys" (should be "Entries"), "Person" → "Persons"
(acceptable but inconsistent), "Policy" → "Policys" (wrong).

The REST extractor has a proper `_pluralize_resource_name()` that handles
`-y → -ies` and other edge cases, but SCIM doesn't use it.

**Impact**: SCIM resource paths may not match the actual server endpoints,
causing 404 errors for resources whose names end in -y, -s, -x, etc.

---

## BUG-137 — JSON-RPC: Unguarded `method["name"]` raises KeyError

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `libs/extractors/jsonrpc.py` |
| **Lines** | ~193 in `_method_to_operation` |

**Description**

```python
method_name: str = method["name"]
```

If a method dict in the `"methods"` array lacks a `"name"` key, this raises
an unguarded `KeyError`, crashing the entire extraction.  The calling code
in `extract()` iterates over `data.get("methods", [])` with no validation
that each entry has `"name"`.

All other extractors use defensive `.get()` patterns with fallback defaults
or explicit `continue` on missing required fields.

**Impact**: A single malformed method entry in a JSON-RPC specification
crashes the entire extraction instead of skipping the bad entry.

---

## BUG-138 — REST: `_probe_allowed_methods` sends no auth headers

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `libs/extractors/rest.py` |
| **Lines** | ~710-741 |

**Description**

```python
def _probe_allowed_methods(self, endpoint: _ObservedEndpoint) -> None:
    try:
        response = self._client.options(endpoint.absolute_url)
    except httpx.HTTPError:
        return
```

The OPTIONS request is sent with no authentication headers.  However, the
companion method `_probe_and_register` (L580-626) does pass `auth_headers`.

On APIs that require authentication, the OPTIONS probe returns 401/403,
and the method falls through to `return` on `status_code >= 400`, losing
all method discovery data for that endpoint.

**Impact**: On authenticated REST APIs, method discovery via OPTIONS is
silently disabled.  Endpoints will use only the speculative methods from
link discovery, which may be incomplete or wrong.

---

## BUG-139 — OpenAPI: `$ref` with sibling properties never resolved (3.1 spec)

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `libs/extractors/openapi.py` |
| **Lines** | ~185 |

**Description**

```python
if "$ref" in node and len(node) == 1:
    # resolve $ref ...
```

This guard only resolves `$ref` when it is the sole key in the node.  OpenAPI
3.1 (JSON Schema 2020-12 compatible) allows `$ref` alongside sibling
properties such as `description`, `summary`, or `default`:

```json
{ "$ref": "#/components/schemas/Pet", "description": "A pet override" }
```

Because `len(node) == 2`, the `$ref` is never resolved.  The node retains
the unresolved `$ref` string, and downstream code that expects the schema
content gets an incomplete definition.

**Evidence**: `len({"$ref": "...", "description": "..."}) == 2`, so `len==1`
is `False`.

**Impact**: OpenAPI 3.1 specs that use `$ref` with sibling properties will
have unresolved schema references, producing tools with missing parameter
types or descriptions.

---

## BUG-140 — OpenAPI: Multiple security schemes silently discard ALL auth

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `libs/extractors/openapi.py` |
| **Lines** | 275-281 |

**Description**

```python
if len(parsed_schemes) > 1:
    logger.info(
        "OpenAPI auth inference skipped because "
        "multiple security schemes were declared: %s",
        [name for name, _ in parsed_schemes],
    )
    return AuthConfig(type=AuthType.none)
```

When a spec declares multiple security schemes (extremely common — e.g., both
Bearer token and API key), **all** authentication is silently discarded and
`AuthType.none` is returned.  The logger only writes an `info`-level message.

**Impact**: Many real-world OpenAPI specs declare 2+ security schemes.  The
compiled tools will make unauthenticated requests, receiving 401/403 errors
at runtime.  This is functionally equivalent to ignoring the spec's auth
entirely.

---

## BUG-141 — Web-UI: `auditApi.get()` fetches full list to find one entry

| Field | Value |
|---|---|
| **Severity** | Low |
| **File** | `apps/web-ui/src/lib/api-client.ts` |
| **Lines** | 697-705 |

**Description**

```ts
get(entryId: string) {
  return auditApi.list().then((response) => {
    const entry = response.entries.find((item) => item.id === entryId);
    if (!entry) {
      throw new ApiError(404, `Audit entry ${entryId} not found`);
    }
    return entry;
  });
},
```

To fetch a single audit log entry, the code calls `auditApi.list()` which
retrieves **all** audit entries, then filters client-side with `.find()`.

**Impact**: On systems with large audit logs (thousands+ entries), every
single-entry lookup transfers the entire audit history.  This is an O(n)
network transfer for an O(1) lookup.  No server-side endpoint for a single
entry is used.

---

## BUG-142 — Web-UI: `loadWorkflow` caches fake fallback, masking real state

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `apps/web-ui/src/stores/workflow-store.ts` |
| **Lines** | 114-115, 126-138 |

**Description**

```ts
// Line 114-115: early return if cached
const existing = get().workflows[key];
if (existing) return existing;

// Line 126-138: on ANY error, create and cache a fake "draft" record
} catch {
  const fallback: WorkflowRecord = {
    serviceId, versionNumber: version,
    state: "draft", reviewNotes: null, history: [],
  };
  set((s) => ({ workflows: { ...s.workflows, [key]: fallback } }));
  return fallback;
}
```

On any API failure (network error, 500, timeout), a fake "draft"
`WorkflowRecord` is created and cached.  Because the early-return on line
114-115 checks the cache first, **all subsequent calls** to `loadWorkflow`
for the same service/version return the fake record without ever retrying
the API.

**Impact**: A single transient API failure permanently masks the real workflow
state (which could be "published", "deployed", etc.) until the user does a
full page refresh.  The UI shows "draft" for a workflow that might actually
be in production.

---

## BUG-143 — Web-UI: Publish/deploy side-effects fire-and-forget with inconsistent state

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `apps/web-ui/src/components/review/approval-workflow.tsx` |
| **Lines** | 235-249 |

**Description**

```ts
await transition(serviceId, versionNumber, confirmDialog.targetState, actor, comment || undefined);

// Side-effects for publish / deploy
if (confirmDialog.targetState === "published") {
  try {
    await artifactApi.activateVersion(serviceId, versionNumber);
  } catch {
    toast.error("Workflow transitioned to Published but artifact activation failed.");
  }
}
```

The workflow state transition (`transition()`) is awaited and succeeds first.
Then, as a side-effect, `artifactApi.activateVersion` is called.  If it fails,
only a toast is shown — but the workflow has **already transitioned** to
"published".

**Impact**: The system enters an inconsistent state where the workflow says
"published" but the artifact isn't actually activated.  Same pattern applies
for "deployed" → `gatewayApi.syncRoutes`.  There is no rollback mechanism.

---

## BUG-144 — Web-UI: Wizard collects auth config but never sends it

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `apps/web-ui/src/components/compilations/compilation-wizard.tsx` |
| **Lines** | 128-155 (`buildRequest`), step 3 UI (679-847) |

**Description**

The compilation wizard has an entire step (Step 3 – "Auth Configuration")
where users select an auth type and fill in credentials (bearer token refs,
basic auth username/password, API key headers, OAuth2 client credentials).

However, `buildRequest()` **never reads any auth fields** from the form data.
The resulting `CompilationCreateRequest` only includes `created_by`, `options`
(protocol, runtime mode, enhancement, tenant, environment), and source
URL/content.

Additionally, the `CompilationCreateRequest` type (both frontend
`types/api.ts:34` and backend `apps/compiler_api/models.py:24`) has **no auth
field** at all — `options` is a flat dict with no auth slot.

**Evidence**: Full `buildRequest` function reads: `runtimeMode`,
`forceProtocol`, `skipEnhancement`, `tenant`, `environment`, `sourceMode`,
`sourceUrl`, `sourceContent`, `serviceName`, `createdBy`.  Auth fields
(`bearerSecretRef`, `basicUsername`, `basicPasswordRef`, `apiKeyHeaderName`,
`apiKeySecretRef`, etc.) are never referenced.

**Impact**: Users go through a multi-field auth configuration step, enter
sensitive credential references, and submit.  All auth data is silently
discarded.  Every compilation is created with no auth configuration,
regardless of what the user entered.

---

## Summary

| ID | Severity | Component | One-line |
|---|---|---|---|
| BUG-133 | High | gRPC extractor | Nested message regex matches first `}` |
| BUG-134 | High | gRPC extractor | RPCs with option blocks silently dropped |
| BUG-135 | High | OData extractor | Composite keys truncated to first key |
| BUG-136 | Medium | SCIM extractor | Naive `f"{name}s"` pluralization |
| BUG-137 | Medium | JSON-RPC extractor | Unguarded `method["name"]` KeyError |
| BUG-138 | Medium | REST extractor | OPTIONS probe sends no auth headers |
| BUG-139 | Medium | OpenAPI extractor | `$ref` + sibling properties unresolved |
| BUG-140 | High | OpenAPI extractor | Multiple security schemes → AuthType.none |
| BUG-141 | Low | Web-UI api-client | auditApi.get() fetches entire list |
| BUG-142 | High | Web-UI workflow store | Fake fallback cached permanently |
| BUG-143 | Medium | Web-UI approval workflow | Publish side-effects fire-and-forget |
| BUG-144 | High | Web-UI compilation wizard | Auth config collected but never sent |

**Tally**: 5 High, 6 Medium, 1 Low  
**Cumulative**: BUG-109 – BUG-144 (36 new bugs across rounds 2-4)
