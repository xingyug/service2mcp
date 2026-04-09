# IR Composition & Transformation Guide

This guide covers how to **compose** multiple ServiceIR artifacts into a
single federated IR and how to **transform** an IR using declarative rules.
Both capabilities live in `libs/ir/` and operate on the standard
`ServiceIR` model defined in `libs/ir/models.py`.

---

## 1. When to use composition

Composition merges several independently-extracted ServiceIR artifacts into
one **federated** ServiceIR.  Common scenarios:

| Scenario | Example |
|----------|---------|
| Multi-protocol gateway | Combine an OpenAPI spec, a GraphQL endpoint, and a gRPC service into one MCP tool server. |
| Micro-service federation | Merge the IRs of `orders`, `inventory`, and `payments` so a single runtime serves all tools. |
| Incremental enrichment | Start with the extractor IR, then compose it with a manually-curated supplement IR that adds SLA configs or extra operations. |

---

## 2. compose_irs API

```python
from libs.ir.compose import compose_irs, CompositionStrategy

merged_ir = compose_irs(
    [ir_a, ir_b, ir_c],
    strategy=CompositionStrategy(
        prefix_operation_ids=True,   # Prefix op IDs with service name
        fail_on_conflict=True,       # Raise on unresolvable conflicts
        merged_service_name="my-gateway",
        merged_description="Federated gateway for orders + inventory",
    ),
)
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `irs` | `list[ServiceIR]` | *(required)* | Two or more ServiceIR artifacts to merge. |
| `strategy` | `CompositionStrategy \| None` | `None` | Conflict-resolution configuration.  When `None`, defaults are used. |

### CompositionStrategy fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `prefix_operation_ids` | `bool` | `True` | Prefix each operation ID with its source `service_name` to avoid collisions. |
| `fail_on_conflict` | `bool` | `True` | Raise `CompositionConflictError` if duplicate operation IDs remain after prefixing. Set to `False` to silently keep the first occurrence. |
| `merged_service_name` | `str \| None` | `None` | Override `service_name` on the merged IR.  When `None`, names are joined with `+`. |
| `merged_description` | `str \| None` | `None` | Override `service_description`.  When `None`, descriptions are concatenated. |

### What gets merged

- **Operations** — concatenated (with optional ID prefixing).
- **Resources, prompts, event descriptors** — unioned; duplicates by ID are
  kept from the first IR that defined them.
- **Operation chains & tool grouping** — concatenated with step/op-ID
  references updated to match prefixed IDs.
- **Metadata** — shallow-merged (`dict.update` order matches `irs` list
  order).

### Error handling

```python
from libs.ir.compose import CompositionConflictError

try:
    merged = compose_irs([ir_a, ir_b], strategy=strategy)
except CompositionConflictError as exc:
    print("Conflicts:", exc.conflicts)  # list[str] of colliding IDs
```

---

## 3. Transformation rules

Transformations let operators **reshape** an IR after extraction without
modifying the extractor or the source spec.  Rules are applied in order and
use glob-style patterns to select operations.

### Quick example

```python
from libs.ir.transform import apply_transforms, TransformAction, TransformRule

rules = [
    # Tag every operation
    TransformRule(action=TransformAction.add_tag, target="*", value="production"),

    # Disable all DELETE operations
    TransformRule(action=TransformAction.disable_operation, target="*delete*"),

    # Override risk on a specific op
    TransformRule(
        action=TransformAction.override_risk,
        target="orders_cancelOrder",
        value="dangerous",
    ),

    # Rename a service (applies to service_name, not individual ops)
    TransformRule(action=TransformAction.rename_service, value="my-service-v2"),
]

transformed_ir = apply_transforms(original_ir, rules)
```

### Available actions

| Action | Target scope | Value | Description |
|--------|-------------|-------|-------------|
| `rename_operation` | single op pattern | new name `str` | Rename matching operations. |
| `filter_by_tag` | IR-wide | tag `str` | Keep **only** operations that have the given tag. |
| `exclude_by_tag` | IR-wide | tag `str` | Remove operations that have the given tag. |
| `add_tag` | op pattern | tag `str` | Append a tag to matching operations. |
| `remove_tag` | op pattern | tag `str` | Remove a tag from matching operations. |
| `override_risk` | op pattern | risk level `str` | Set `risk.risk_level` on matching operations. |
| `disable_operation` | op pattern | — | Set `enabled=False` on matching operations. |
| `enable_operation` | op pattern | — | Set `enabled=True` on matching operations. |
| `set_metadata` | op pattern | any JSON-serializable | Store a value in the IR-level `metadata` dict. |
| `rename_service` | IR-wide | new name `str` | Replace `service_name`. |

### Pattern matching

The `target` field uses shell-style glob patterns:

- `*` — matches everything.
- `orders_*` — matches all ops whose ID starts with `orders_`.
- `*delete*` — matches any op with "delete" in its ID.
- `getUser` — exact match.

---

## 4. Combining composition + transformation

A typical production workflow:

```text
extract(spec_a) ──┐
extract(spec_b) ──┤
extract(spec_c) ──┘
        │
   compose_irs(...)
        │
   apply_transforms(merged, operator_rules)
        │
   validate(transformed)
        │
   deploy / publish
```

### Example: federated gateway with operator overrides

```python
from libs.ir.compose import compose_irs, CompositionStrategy
from libs.ir.transform import apply_transforms, TransformAction, TransformRule
from libs.ir.sla import recommend_sla_for_ir

# 1. Compose
merged = compose_irs(
    [orders_ir, inventory_ir, payments_ir],
    strategy=CompositionStrategy(
        prefix_operation_ids=True,
        merged_service_name="store-gateway",
    ),
)

# 2. Transform — operator policy
rules = [
    TransformRule(action=TransformAction.add_tag, target="*", value="store"),
    TransformRule(action=TransformAction.disable_operation, target="*_debug*"),
    TransformRule(
        action=TransformAction.override_risk,
        target="payments_*charge*",
        value="dangerous",
    ),
]
shaped = apply_transforms(merged, rules)

# 3. Apply SLA from observed latencies
shaped = recommend_sla_for_ir(shaped, latency_data)

# 4. Validate & deploy
# ...
```

---

## 5. Performance characteristics

Both `compose_irs` and `apply_transforms` are designed to be fast for
typical workloads:

| Operation | Workload | Measured baseline |
|-----------|----------|-------------------|
| `compose_irs` | 10 IRs × 20 ops each | < 200 ms |
| `apply_transforms` | 50 ops × 100 rules | < 200 ms |
| `recommend_sla_for_ir` | 100 ops × 200 latency samples each | < 100 ms |

All operations produce **new** ServiceIR instances — the originals are
never mutated.

---

## 6. Drift detection after composition

After deploying a composed IR, you can monitor for source drift:

```python
from libs.validator.drift import detect_drift

report = detect_drift(deployed_ir, freshly_composed_ir)
if report.has_drift:
    print(f"Severity: {report.severity}")
    for detail in report.modified_operations:
        print(f"  {detail.operation_id}: {detail.changes}")
```

The `DriftReport` tracks added/removed operations, per-operation parameter
and schema changes, and classifies each change as `breaking` or
`non_breaking`.

---

## 7. Best practices

1. **Always prefix IDs** when composing IRs from different services to
   avoid collisions.
2. **Validate after transforming** — transformations can make an IR invalid
   (e.g., `filter_by_tag` may remove operations referenced by chains).
3. **Version your rule sets** — store `TransformRule` lists as JSON/YAML
   alongside deployment configs for reproducibility.
4. **Use composition for multi-protocol, not duplication** — if two specs
   describe the same API, deduplicate before composing.
5. **Monitor drift** — schedule periodic `detect_drift` checks after
   deploying composed IRs to catch upstream spec changes early.
