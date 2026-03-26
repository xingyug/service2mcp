#!/usr/bin/env python3
"""Validate the enhancer against the official DeepSeek endpoint without persisting secrets."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from libs.enhancer import EnhancerConfig, IREnhancer, LLMProvider, create_llm_client
from libs.ir.models import Operation, Param, RiskLevel, RiskMetadata, ServiceIR, SourceType


def _build_validation_ir() -> ServiceIR:
    return ServiceIR(
        source_hash="r001" * 16,
        protocol="openapi",
        service_name="deepseek-validation-api",
        service_description="Minimal IR used for real DeepSeek validation.",
        base_url="https://billing.example.test",
        operations=[
            Operation(
                id="listInvoices",
                name="List Invoices",
                description="",
                method="GET",
                path="/invoices",
                params=[
                    Param(
                        name="customer_id",
                        type="string",
                        required=True,
                        description="",
                        source=SourceType.extractor,
                        confidence=1.0,
                    )
                ],
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            )
        ],
    )


def _read_key(key_file: Path) -> str:
    key = key_file.read_text(encoding="utf-8").strip()
    if not key:
        raise RuntimeError(f"DeepSeek key file is empty: {key_file}")
    return key


def _success_path(key_file: Path, model: str) -> dict[str, Any]:
    config = EnhancerConfig(
        provider=LLMProvider.deepseek,
        model=model,
        api_key=_read_key(key_file),
        api_base_url="https://api.deepseek.com",
        skip_if_description_exists=False,
        batch_size=1,
    )
    ir = _build_validation_ir()
    result = IREnhancer(create_llm_client(config), config=config).enhance(ir)
    operation = result.enhanced_ir.operations[0]
    if result.operations_enhanced < 1:
        raise RuntimeError("DeepSeek success-path validation produced no enhanced operations.")
    if operation.source is not SourceType.llm:
        raise RuntimeError("DeepSeek success-path validation did not tag the operation as llm.")
    if operation.description == ir.operations[0].description:
        raise RuntimeError("DeepSeek success-path validation did not change the description.")

    return {
        "status": "ok",
        "model": config.model,
        "provider": config.provider.value,
        "operations_enhanced": result.operations_enhanced,
        "operations_skipped": result.operations_skipped,
        "token_usage": asdict(result.token_usage),
        "description_preview": operation.description[:120],
    }


def _failure_path(model: str) -> dict[str, Any]:
    config = EnhancerConfig(
        provider=LLMProvider.deepseek,
        model=model,
        api_key="invalid-deepseek-key",
        api_base_url="https://api.deepseek.com",
    )
    client = create_llm_client(config)
    try:
        client.complete("Return [] as plain JSON.", max_tokens=32)
    except Exception as exc:  # pragma: no cover - exercised in manual validation only
        return {
            "status": "expected_failure",
            "provider": config.provider.value,
            "model": config.model,
            "error_type": exc.__class__.__name__,
            "error_message": str(exc)[:200],
        }
    raise RuntimeError("Invalid-key DeepSeek validation unexpectedly succeeded.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--key-file",
        default="/home/guoxy/esoc-agents/.deepseek_api_key",
        help="Path to the DeepSeek API key file.",
    )
    parser.add_argument(
        "--model",
        default="deepseek-chat",
        help="DeepSeek model to validate.",
    )
    parser.add_argument(
        "--skip-failure-check",
        action="store_true",
        help="Skip the explicit invalid-key failure-path validation.",
    )
    args = parser.parse_args()

    summary: dict[str, Any] = {
        "success_path": _success_path(Path(args.key_file), args.model),
    }
    if not args.skip_failure_check:
        summary["failure_path"] = _failure_path(args.model)

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - exercised in manual validation only
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_type": exc.__class__.__name__,
                    "error_message": str(exc),
                },
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
