"""Tests for the JSON-RPC 2.0 extractor."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from libs.extractors.base import SourceConfig
from libs.extractors.jsonrpc import JsonRpcExtractor, _resolve_params_type
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


def test_extract_preserves_param_defaults_and_positional_mode() -> None:
    extractor = JsonRpcExtractor()
    source = SourceConfig(
        file_content=json.dumps(
            {
                "jsonrpc_service": True,
                "endpoint": "https://downloads.example.com/jsonrpc",
                "methods": [
                    {
                        "name": "aria2.getVersion",
                        "params_type": "positional",
                        "params": [
                            {
                                "name": "token",
                                "required": True,
                                "default": "token:test-secret",
                                "schema": {"type": "string"},
                            }
                        ],
                    }
                ],
            }
        )
    )

    ir = extractor.extract(source)

    assert len(ir.operations) == 1
    operation = ir.operations[0]
    assert operation.jsonrpc is not None
    assert operation.jsonrpc.params_type == "positional"
    assert operation.params[0].default == "token:test-secret"


# ── additional detection edge cases ────────────────────────────────────────


def test_detect_returns_zero_when_no_content() -> None:
    """Line 115: _get_content returns None → 0.0."""
    extractor = JsonRpcExtractor()
    source = SourceConfig(url="https://example.com/rpc")
    with patch.object(extractor, "_get_content", return_value=None):
        assert extractor.detect(source) == 0.0


def test_detect_returns_zero_for_invalid_json() -> None:
    """Lines 119-120: invalid JSON → 0.0."""
    extractor = JsonRpcExtractor()
    source = SourceConfig(file_content="not json at all")
    assert extractor.detect(source) == 0.0


def test_detect_returns_zero_for_non_dict_json() -> None:
    """Line 123: valid JSON but not a dict → 0.0."""
    extractor = JsonRpcExtractor()
    source = SourceConfig(file_content="[1, 2, 3]")
    assert extractor.detect(source) == 0.0


def test_detect_methods_with_params_returns_07() -> None:
    """Line 132: methods list with params but no openrpc/jsonrpc_service keys."""
    extractor = JsonRpcExtractor()
    content = json.dumps(
        {
            "methods": [
                {"name": "doSomething", "params": [{"name": "x"}]},
            ]
        }
    )
    source = SourceConfig(file_content=content)
    assert extractor.detect(source) == 0.7


def test_detect_methods_without_params_returns_zero() -> None:
    """Line 132 else branch: methods list but no entry has params."""
    extractor = JsonRpcExtractor()
    content = json.dumps(
        {
            "methods": [
                {"name": "noParams"},
            ]
        }
    )
    source = SourceConfig(file_content=content)
    assert extractor.detect(source) == 0.0


# ── extract error case ────────────────────────────────────────────────────


def test_extract_raises_when_no_content() -> None:
    """Line 140: extract raises ValueError when _get_content returns None."""
    extractor = JsonRpcExtractor()
    source = SourceConfig(url="https://example.com/rpc")
    with patch.object(extractor, "_get_content", return_value=None):
        with pytest.raises(ValueError, match="Could not read source content"):
            extractor.extract(source)


# ── _resolve_base_url edge cases ──────────────────────────────────────────


def test_resolve_base_url_non_openrpc_with_endpoint() -> None:
    """Line 252-254: non-openrpc spec with explicit endpoint."""
    content = _load_fixture("manual_user_service.json")
    data = json.loads(content)
    source = SourceConfig(file_content=content)
    url = JsonRpcExtractor._resolve_base_url(data, source, is_openrpc=False)
    assert url == "https://users.example.com/api/jsonrpc"


def test_resolve_base_url_falls_back_to_source_url() -> None:
    """Lines 256-257: no server/endpoint → falls back to source.url."""
    data = {"methods": []}
    source = SourceConfig(url="https://fallback.example.com/rpc")
    url = JsonRpcExtractor._resolve_base_url(data, source, is_openrpc=False)
    assert url == "https://fallback.example.com/rpc"


def test_resolve_base_url_default_when_nothing() -> None:
    """Lines 258-259: no server, no endpoint, no source URL → default."""
    data = {"methods": []}
    source = SourceConfig(file_content="{}")
    url = JsonRpcExtractor._resolve_base_url(data, source, is_openrpc=False)
    assert url == "http://localhost:8080/rpc"


# ── _get_content edge cases ───────────────────────────────────────────────


def test_get_content_from_file_path() -> None:
    """Lines 264-265: _get_content reads from file_path."""
    extractor = JsonRpcExtractor()
    fixture_path = str(FIXTURES_DIR / "openrpc_calculator.json")
    source = SourceConfig(file_path=fixture_path)
    content = extractor._get_content(source)
    assert content is not None
    assert "openrpc" in content


def test_get_content_from_url_success() -> None:
    """Lines 266-274: _get_content fetches from URL."""
    extractor = JsonRpcExtractor()
    mock_response = MagicMock()
    mock_response.text = '{"openrpc": "1.0"}'
    mock_response.raise_for_status = MagicMock()

    with patch("libs.extractors.utils.httpx.get", return_value=mock_response):
        source = SourceConfig(url="https://example.com/spec.json")
        content = extractor._get_content(source)
    assert content == '{"openrpc": "1.0"}'


def test_get_content_from_url_failure_returns_none() -> None:
    """Lines 275-281: _get_content returns None on HTTP error."""
    extractor = JsonRpcExtractor()
    err = httpx.ConnectError("connection error")
    with patch("libs.extractors.utils.httpx.get", side_effect=err):
        source = SourceConfig(url="https://example.com/spec.json")
        content = extractor._get_content(source)
    assert content is None


def test_get_content_no_source_returns_none() -> None:
    """Line 282: _get_content returns None when all sources are exhausted."""
    extractor = JsonRpcExtractor()
    # Use a source where file_content is truthy but empty-ish won't work with SourceConfig
    # So test via mocking _get_content returning None for detect
    source = SourceConfig(url="https://example.com/rpc")
    with patch("libs.extractors.utils.httpx.get", side_effect=httpx.ConnectError("fail")):
        content = extractor._get_content(source)
    assert content is None


# ── _auth_headers ─────────────────────────────────────────────────────────


def test_auth_headers_with_auth_header() -> None:
    """Lines 286-288: auth_header is passed through."""
    source = SourceConfig(file_content="{}", auth_header="Bearer tok")
    headers = JsonRpcExtractor._auth_headers(source)
    assert headers == {"Authorization": "Bearer tok"}


def test_auth_headers_with_auth_token() -> None:
    """Lines 289-290: auth_token formatted as Bearer."""
    source = SourceConfig(file_content="{}", auth_token="mytoken")
    headers = JsonRpcExtractor._auth_headers(source)
    assert headers == {"Authorization": "Bearer mytoken"}


def test_auth_headers_no_auth() -> None:
    """Line 291: no auth → empty dict."""
    source = SourceConfig(file_content="{}")
    headers = JsonRpcExtractor._auth_headers(source)
    assert headers == {}


# ── _default_openrpc_endpoint tests ───────────────────────────────────────


def test_resolve_base_url_openrpc_falls_back_to_default_endpoint() -> None:
    """Line 261: OpenRPC with source.url but no servers → _default_openrpc_endpoint."""
    data: dict = {"openrpc": "1.0", "methods": []}
    source = SourceConfig(url="https://example.com/openrpc.json")
    url = JsonRpcExtractor._resolve_base_url(data, source, is_openrpc=True)
    assert url == "https://example.com/rpc"


def test_default_openrpc_endpoint_strips_json_suffix() -> None:
    """Lines 268-279: strips /openrpc.json and appends /rpc."""
    result = JsonRpcExtractor._default_openrpc_endpoint("https://example.com/api/openrpc.json")
    assert result == "https://example.com/api/rpc"


def test_default_openrpc_endpoint_strips_yaml_suffix() -> None:
    """Lines 268-279: strips /openrpc.yaml and appends /rpc."""
    result = JsonRpcExtractor._default_openrpc_endpoint("https://example.com/openrpc.yaml")
    assert result == "https://example.com/rpc"


def test_default_openrpc_endpoint_strips_yml_suffix() -> None:
    """Lines 268-279: strips /openrpc.yml and appends /rpc."""
    result = JsonRpcExtractor._default_openrpc_endpoint("https://example.com/openrpc.yml")
    assert result == "https://example.com/rpc"


def test_default_openrpc_endpoint_no_known_suffix() -> None:
    """Line 279: no recognized suffix → returns source_url unchanged."""
    result = JsonRpcExtractor._default_openrpc_endpoint("https://example.com/api/v1")
    assert result == "https://example.com/api/v1"


# ── _get_content returns None with no source ──────────────────────────────


def test_get_content_returns_none_when_all_sources_empty() -> None:
    """Line 302: _get_content returns None when source has no content, file, or URL."""
    extractor = JsonRpcExtractor()
    source = SourceConfig(file_content="placeholder")
    # Bypass __post_init__ validation by overriding after construction
    source.file_content = ""
    source.file_path = None
    source.url = None
    content = extractor._get_content(source)
    assert content is None


# ── _resolve_params_type with paramStructure ──────────────────────────────


def test_resolve_params_type_by_position() -> None:
    """Line 321: paramStructure='by-position' → 'positional'."""
    assert _resolve_params_type({"name": "m", "paramStructure": "by-position"}) == "positional"


def test_resolve_params_type_by_name() -> None:
    """Line 323: paramStructure='by-name' → 'named'."""
    assert _resolve_params_type({"name": "m", "paramStructure": "by-name"}) == "named"


def test_resolve_params_type_either() -> None:
    """Line 323: paramStructure='either' → 'named'."""
    assert _resolve_params_type({"name": "m", "paramStructure": "either"}) == "named"
