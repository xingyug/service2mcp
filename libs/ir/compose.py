"""IR composition: merge multiple ServiceIR artifacts into a federated tool server."""

from __future__ import annotations

import hashlib
import logging

from libs.ir.models import ServiceIR

logger = logging.getLogger(__name__)


class CompositionConflictError(Exception):
    """Raised when IR composition encounters unresolvable conflicts."""

    def __init__(self, conflicts: list[str]) -> None:
        self.conflicts = conflicts
        super().__init__(f"IR composition conflicts: {'; '.join(conflicts)}")


class CompositionStrategy:
    """Configuration for how to handle conflicts during composition."""

    def __init__(
        self,
        *,
        prefix_operation_ids: bool = True,
        fail_on_conflict: bool = True,
        merged_service_name: str | None = None,
        merged_description: str | None = None,
    ) -> None:
        self.prefix_operation_ids = prefix_operation_ids
        self.fail_on_conflict = fail_on_conflict
        self.merged_service_name = merged_service_name
        self.merged_description = merged_description


def compose_irs(
    irs: list[ServiceIR],
    *,
    strategy: CompositionStrategy | None = None,
) -> ServiceIR:
    """Merge multiple ServiceIR artifacts into a single federated ServiceIR.

    Parameters
    ----------
    irs:
        List of ServiceIR objects to merge (must be non-empty).
    strategy:
        Configuration for conflict resolution. Uses defaults if None.

    Returns
    -------
    ServiceIR:
        A single merged IR containing all operations, resources, prompts, and events.

    Raises
    ------
    ValueError:
        If ``irs`` is empty.
    CompositionConflict:
        If unresolvable conflicts exist and ``strategy.fail_on_conflict`` is True.
    """
    if not irs:
        raise ValueError("Cannot compose empty list of ServiceIRs")

    if len(irs) == 1:
        return irs[0]

    strat = strategy or CompositionStrategy()
    conflicts: list[str] = []

    # Determine merged identity
    service_name = strat.merged_service_name or "-".join(ir.service_name for ir in irs)
    description = strat.merged_description or f"Federated service from {len(irs)} sources"
    base_url = irs[0].base_url

    # Merge operations
    all_operations = []
    seen_op_ids: set[str] = set()
    for ir in irs:
        prefix = f"{ir.service_name}_" if strat.prefix_operation_ids else ""
        for op in ir.operations:
            new_id = f"{prefix}{op.id}" if prefix else op.id
            if new_id in seen_op_ids:
                conflicts.append(f"Duplicate operation ID: {new_id}")
                continue
            seen_op_ids.add(new_id)
            updated_op = op.model_copy(
                update={
                    "id": new_id,
                    "name": f"{prefix}{op.name}" if prefix else op.name,
                }
            )
            all_operations.append(updated_op)

    # Merge event descriptors
    all_events = []
    seen_event_ids: set[str] = set()
    for ir in irs:
        prefix = f"{ir.service_name}_" if strat.prefix_operation_ids else ""
        for evt in ir.event_descriptors:
            new_id = f"{prefix}{evt.id}" if prefix else evt.id
            if new_id in seen_event_ids:
                conflicts.append(f"Duplicate event descriptor ID: {new_id}")
                continue
            seen_event_ids.add(new_id)
            new_op_id = (
                f"{prefix}{evt.operation_id}" if prefix and evt.operation_id else evt.operation_id
            )
            all_events.append(evt.model_copy(update={"id": new_id, "operation_id": new_op_id}))

    # Merge resource definitions
    all_resources = []
    seen_resource_ids: set[str] = set()
    seen_resource_uris: set[str] = set()
    for ir in irs:
        prefix = f"{ir.service_name}_" if strat.prefix_operation_ids else ""
        for res in ir.resource_definitions:
            new_id = f"{prefix}{res.id}" if prefix else res.id
            if new_id in seen_resource_ids:
                conflicts.append(f"Duplicate resource definition ID: {new_id}")
                continue
            # Prefix URI to avoid uniqueness violations across services
            new_uri = f"{ir.service_name}/{res.uri}" if prefix else res.uri
            if new_uri in seen_resource_uris:
                conflicts.append(f"Duplicate resource URI: {new_uri}")
                continue
            seen_resource_ids.add(new_id)
            seen_resource_uris.add(new_uri)
            update: dict[str, object] = {"id": new_id, "uri": new_uri}
            # Rewrite dynamic resource operation_id references
            if res.operation_id and prefix:
                update["operation_id"] = f"{prefix}{res.operation_id}"
            all_resources.append(res.model_copy(update=update))

    # Merge prompt definitions
    all_prompts = []
    seen_prompt_ids: set[str] = set()
    seen_prompt_names: set[str] = set()
    for ir in irs:
        prefix = f"{ir.service_name}_" if strat.prefix_operation_ids else ""
        for prompt in ir.prompt_definitions:
            new_id = f"{prefix}{prompt.id}" if prefix else prompt.id
            new_name = f"{prefix}{prompt.name}" if prefix else prompt.name
            if new_id in seen_prompt_ids or new_name in seen_prompt_names:
                conflicts.append(f"Duplicate prompt definition: {new_id} / {new_name}")
                continue
            seen_prompt_ids.add(new_id)
            seen_prompt_names.add(new_name)
            new_tool_ids = [f"{prefix}{tid}" if prefix else tid for tid in prompt.tool_ids]
            all_prompts.append(
                prompt.model_copy(update={"id": new_id, "name": new_name, "tool_ids": new_tool_ids})
            )

    # Merge operation chains — steps is list[str] of operation IDs
    all_chains = []
    for ir in irs:
        prefix = f"{ir.service_name}_" if strat.prefix_operation_ids else ""
        for chain in ir.operation_chains:
            new_steps = [f"{prefix}{step}" if prefix else step for step in chain.steps]
            all_chains.append(chain.model_copy(update={"steps": new_steps}))

    # Merge tool grouping
    all_groups = []
    for ir in irs:
        prefix = f"{ir.service_name}_" if strat.prefix_operation_ids else ""
        for group in ir.tool_grouping:
            new_ops = [f"{prefix}{oid}" if prefix else oid for oid in group.operation_ids]
            all_groups.append(group.model_copy(update={"operation_ids": new_ops}))

    if conflicts and strat.fail_on_conflict:
        raise CompositionConflictError(conflicts)

    # Build combined source hash
    combined_hash = hashlib.sha256("|".join(ir.source_hash for ir in irs).encode()).hexdigest()

    # Collect unique protocols
    protocols = sorted({ir.protocol for ir in irs})

    merged = ServiceIR(
        source_url=irs[0].source_url,
        source_hash=combined_hash,
        protocol=protocols[0] if len(protocols) == 1 else "federated",
        service_name=service_name,
        service_description=description,
        base_url=base_url,
        auth=irs[0].auth,
        operations=all_operations,
        operation_chains=all_chains,
        tool_grouping=all_groups,
        event_descriptors=all_events,
        resource_definitions=all_resources,
        prompt_definitions=all_prompts,
        metadata={
            "federated": True,
            "source_count": len(irs),
            "source_protocols": protocols,
            "source_services": [ir.service_name for ir in irs],
        },
    )

    logger.info(
        "Composed %d IRs into federated service '%s': "
        "%d operations, %d events, %d resources, %d prompts",
        len(irs),
        service_name,
        len(all_operations),
        len(all_events),
        len(all_resources),
        len(all_prompts),
    )

    return merged
