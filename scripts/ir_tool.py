#!/usr/bin/env python3
"""IR manipulation tool: compose and transform ServiceIR artifacts.

Usage:
    python scripts/ir_tool.py compose ir1.json ir2.json -o merged.json [--name NAME] [--no-prefix]
    python scripts/ir_tool.py transform ir.json -r rules.json -o output.json
    python scripts/ir_tool.py inspect ir.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from libs.ir.compose import CompositionConflictError, CompositionStrategy, compose_irs
from libs.ir.models import ServiceIR
from libs.ir.transform import TransformRule, apply_transforms

logger = logging.getLogger(__name__)


def _load_ir(path: str) -> ServiceIR:
    """Load a ServiceIR from a JSON file."""
    data = json.loads(Path(path).read_text())
    return ServiceIR(**data)


def _save_ir(ir: ServiceIR, path: str) -> None:
    """Save a ServiceIR to a JSON file."""
    Path(path).write_text(ir.model_dump_json(indent=2))


def cmd_compose(args: argparse.Namespace) -> int:
    """Compose multiple IR files into one."""
    if len(args.inputs) < 2:
        print("Error: compose requires at least 2 input IR files", file=sys.stderr)
        return 1

    irs = []
    for path in args.inputs:
        try:
            irs.append(_load_ir(path))
        except Exception as exc:
            print(f"Error loading {path}: {exc}", file=sys.stderr)
            return 1

    strategy = CompositionStrategy(
        prefix_operation_ids=not args.no_prefix,
        fail_on_conflict=not args.allow_conflicts,
        merged_service_name=args.name,
        merged_description=args.description,
    )

    try:
        merged = compose_irs(irs, strategy=strategy)
    except CompositionConflictError as exc:
        print(f"Composition conflict: {exc}", file=sys.stderr)
        return 1

    _save_ir(merged, args.output)
    print(
        f"Composed {len(irs)} IRs → {args.output} "
        f"({len(merged.operations)} operations, "
        f"{len(merged.resource_definitions)} resources)"
    )
    return 0


def cmd_transform(args: argparse.Namespace) -> int:
    """Apply transformation rules to an IR."""
    try:
        ir = _load_ir(args.input)
    except Exception as exc:
        print(f"Error loading IR: {exc}", file=sys.stderr)
        return 1

    try:
        rules_data = json.loads(Path(args.rules).read_text())
        if not isinstance(rules_data, list):
            rules_data = [rules_data]
        rules = [TransformRule(**r) for r in rules_data]
    except Exception as exc:
        print(f"Error loading rules: {exc}", file=sys.stderr)
        return 1

    result = apply_transforms(ir, rules)

    _save_ir(result, args.output)
    print(f"Applied {len(rules)} rules → {args.output} ({len(result.operations)} operations)")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    """Inspect an IR file and print summary."""
    try:
        ir = _load_ir(args.input)
    except Exception as exc:
        print(f"Error loading IR: {exc}", file=sys.stderr)
        return 1

    print(f"Service: {ir.service_name}")
    print(f"Protocol: {ir.protocol}")
    print(f"Base URL: {ir.base_url}")
    print(f"Operations: {len(ir.operations)}")
    print(f"Events: {len(ir.event_descriptors)}")
    print(f"Resources: {len(ir.resource_definitions)}")
    print(f"Prompts: {len(ir.prompt_definitions)}")
    print(f"IR version: {ir.ir_version}")

    if args.verbose:
        print("\nOperations:")
        for op in ir.operations:
            risk = op.risk.risk_level.value if op.risk else "unknown"
            enabled = "✓" if op.enabled else "✗"
            sla = ""
            if op.sla and op.sla.latency_budget_ms:
                sla = f" (SLA: {op.sla.latency_budget_ms}ms)"
            print(
                f"  [{enabled}] {op.id} ({op.method or 'N/A'} {op.path or 'N/A'}) risk={risk}{sla}"
            )

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="IR manipulation tool: compose, transform, and inspect ServiceIR artifacts"
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # compose
    compose_parser = subparsers.add_parser("compose", help="Merge multiple IRs into a federated IR")
    compose_parser.add_argument("inputs", nargs="+", help="Input IR JSON files")
    compose_parser.add_argument("-o", "--output", required=True, help="Output file path")
    compose_parser.add_argument("--name", help="Service name for the merged IR")
    compose_parser.add_argument("--description", help="Description for the merged IR")
    compose_parser.add_argument(
        "--no-prefix",
        action="store_true",
        help="Don't prefix operation IDs",
    )
    compose_parser.add_argument(
        "--allow-conflicts",
        action="store_true",
        help="Skip conflicting ops instead of failing",
    )

    # transform
    transform_parser = subparsers.add_parser(
        "transform", help="Apply transformation rules to an IR"
    )
    transform_parser.add_argument("input", help="Input IR JSON file")
    transform_parser.add_argument("-r", "--rules", required=True, help="Rules JSON file")
    transform_parser.add_argument("-o", "--output", required=True, help="Output file path")

    # inspect
    inspect_parser = subparsers.add_parser("inspect", help="Inspect an IR file")
    inspect_parser.add_argument("input", help="Input IR JSON file")
    inspect_parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show per-operation details"
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    if args.command == "compose":
        return cmd_compose(args)
    elif args.command == "transform":
        return cmd_transform(args)
    elif args.command == "inspect":
        return cmd_inspect(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
