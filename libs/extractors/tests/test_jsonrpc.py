"""Tests for the JSON-RPC 2.0 extractor."""

from __future__ import annotations

from pathlib import Path

from libs.extractors.base import SourceConfig
from libs.extractors.jsonrpc import JsonRpcExtractor
from libs.ir.models import RiskLevel

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "jsonrpc_specs"


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


# ── detection tests ────────────────────────────────────────────────────────


def test_detect_with_protocol_hint() -> None:
    extractor = JsonRpcExtractor()
    source = SourceConfig(file_content="{}", hints={"protocol": "jsonrpc"})
    assert extractor.detect(source) == 1.0


def test_detect_with_openrpc_content() -> None:
    extractor = JsonRpcExtractor()
    content = _load_fixture("openrpc_calculator.json")
    source = SourceConfig(file_content=content)
    assert extractor.detect(source) == 0.95


def test_detect_with_manual_spec() -> None:
    extractor = JsonRpcExtractor()
    content = _load_fixture("manual_user_service.json")
    source = SourceConfig(file_content=content)
    assert extractor.detect(source) == 0.9


def test_detect_non_jsonrpc() -> None:
    extractor = JsonRpcExtractor()
    source = SourceConfig(file_content='{"openapi": "3.0.0", "paths": {}}')
    assert extractor.detect(source) == 0.0


# ── OpenRPC calculator extraction tests ───────────────────────────────────


def test_extract_openrpc_calculator() -> None:
    extractor = JsonRpcExtractor()
    content = _load_fixture("openrpc_calculator.json")
    source = SourceConfig(file_content=content)

    ir = extractor.extract(source)

    assert ir.protocol == "jsonrpc"
    assert len(ir.operations) == 4

    op_ids = {op.id for op in ir.operations}
    assert op_ids == {"add", "subtract", "get_history", "delete_history"}

    # All operations use POST
    assert all(op.method == "POST" for op in ir.operations)

    # All operations have jsonrpc config with correct method_name
    for op in ir.operations:
        assert op.jsonrpc is not None
        assert op.jsonrpc.method_name == op.name

    # Risk classification
    get_history = next(op for op in ir.operations if op.id == "get_history")
    assert get_history.risk.risk_level is RiskLevel.safe

    delete_history = next(op for op in ir.operations if op.id == "delete_history")
    assert delete_history.risk.risk_level is RiskLevel.dangerous

    add_op = next(op for op in ir.operations if op.id == "add")
    assert add_op.risk.risk_level is RiskLevel.cautious

    # Params for add
    assert len(add_op.params) == 2
    param_map = {p.name: p for p in add_op.params}
    assert "a" in param_map and "b" in param_map
    assert param_map["a"].required is True
    assert param_map["b"].required is True
    assert param_map["a"].type == "number"
    assert param_map["b"].type == "number"

    # jsonrpc config params_names
    assert add_op.jsonrpc is not None
    assert add_op.jsonrpc.params_names == ["a", "b"]


# ── manual user service extraction tests ──────────────────────────────────


def test_extract_manual_user_service() -> None:
    extractor = JsonRpcExtractor()
    content = _load_fixture("manual_user_service.json")
    source = SourceConfig(file_content=content)

    ir = extractor.extract(source)

    assert len(ir.operations) == 6

    op_map = {op.id: op for op in ir.operations}

    # user.getById → user_getById
    assert "user_getById" in op_map
    get_by_id = op_map["user_getById"]
    assert len(get_by_id.params) == 1
    assert get_by_id.params[0].name == "userId"
    assert get_by_id.params[0].required is True

    # Risk classification using last segment of dotted name
    assert op_map["user_delete"].risk.risk_level is RiskLevel.dangerous
    assert op_map["user_list"].risk.risk_level is RiskLevel.safe
    assert op_map["user_query"].risk.risk_level is RiskLevel.safe

    # All operations share the same path
    paths = {op.path for op in ir.operations}
    assert len(paths) == 1
