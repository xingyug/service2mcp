#!/usr/bin/env python3
"""Call every enabled tool on an MCP runtime via streamable HTTP (matches worker post-deploy path)."""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
import traceback
from pathlib import Path

from apps.compiler_worker.activities.production import (
    build_sample_invocations,
    build_streamable_http_tool_invoker,
)
from libs.ir.models import ServiceIR


async def _call_one(
    invoker,
    tool: str,
    arguments: dict,
    *,
    timeout: float,
) -> tuple[str, bool, str]:
    try:
        result = await asyncio.wait_for(invoker(tool, arguments), timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return tool, False, f"exception: {exc!s}\n{traceback.format_exc()}"
    if isinstance(result, dict) and result.get("status") == "error":
        return tool, False, json.dumps(result, default=str)[:2000]
    return tool, True, json.dumps(result, default=str)[:500]


async def run_service(base_url: str, ir_path: Path, *, per_call_timeout: float) -> dict:
    raw = gzip.decompress(ir_path.read_bytes())
    ir = ServiceIR.model_validate_json(raw)
    samples = build_sample_invocations(ir)
    invoker = build_streamable_http_tool_invoker(base_url)
    ok = fail = 0
    failures: list[tuple[str, str]] = []
    for tool_name, arguments in samples.items():
        t_ok, success, detail = await _call_one(
            invoker, tool_name, arguments, timeout=per_call_timeout
        )
        if success:
            ok += 1
        else:
            fail += 1
            failures.append((t_ok, detail))
    return {
        "base_url": base_url,
        "service_name": ir.service_name,
        "tools": len(samples),
        "ok": ok,
        "fail": fail,
        "failures": failures[:50],
        "failures_truncated": max(0, len(failures) - 50),
    }


async def main_async(args: argparse.Namespace) -> int:
    results: list[dict] = []
    for line in Path(args.manifest).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            print(f"skip bad line: {line}", file=sys.stderr)
            continue
        base_url, ir_path = parts[0], Path(parts[1])
        print(f"=== {base_url} ===", flush=True)
        r = await run_service(base_url, ir_path, per_call_timeout=args.timeout)
        results.append(r)
        print(json.dumps(r, indent=2)[:8000], flush=True)
    total_tools = sum(x["tools"] for x in results)
    total_ok = sum(x["ok"] for x in results)
    total_fail = sum(x["fail"] for x in results)
    print(
        json.dumps(
            {
                "summary": {
                    "services": len(results),
                    "tools": total_tools,
                    "ok": total_ok,
                    "fail": total_fail,
                }
            },
            indent=2,
        ),
        flush=True,
    )
    return 0 if total_fail == 0 else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest", type=Path, help="Lines: <base_url> <path-to-ir.json.gz>")
    ap.add_argument(
        "--timeout",
        type=float,
        default=45.0,
        help="Per-tool asyncio timeout (seconds)",
    )
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
