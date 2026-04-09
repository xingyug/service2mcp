"""Unit tests for proxy.py utility functions — pure/near-pure helpers."""

from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from apps.mcp_runtime.proxy import (
    _apply_array_limit,
    _apply_field_filter,
    _apply_truncation,
    _build_signing_payload,
    _build_soap_envelope,
    _candidate_env_names,
    _coerce_xml_text,
    _extract_nested_value,
    _normalize_query_value,
    _parse_response_payload,
    _parse_stream_payload,
    _set_nested,
    _soap_body_element,
    _soap_scalar_to_text,
    _split_url_query,
    _to_websocket_url,
    _xml_element_to_value,
    _xml_local_name,
)

# --- URL & Query helpers ---


class TestToWebsocketUrl:
    def test_http_to_ws(self) -> None:
        result = _to_websocket_url("http://example.com/ws", {})
        assert result.startswith("ws://")

    def test_https_to_wss(self) -> None:
        result = _to_websocket_url("https://example.com/ws", {})
        assert result.startswith("wss://")

    def test_query_params_sorted(self) -> None:
        result = _to_websocket_url("http://example.com/ws", {"b": "2", "a": "1"})
        assert "a=1&b=2" in result

    def test_preserves_path(self) -> None:
        result = _to_websocket_url("https://example.com/api/v1/ws", {})
        assert "/api/v1/ws" in result

    def test_wss_preserved(self) -> None:
        """wss:// must not be downgraded to ws://."""
        result = _to_websocket_url("wss://example.com/ws", {})
        assert result.startswith("wss://")

    def test_ws_stays_ws(self) -> None:
        result = _to_websocket_url("ws://example.com/ws", {})
        assert result.startswith("ws://")
        assert not result.startswith("wss://")


class TestSplitUrlQuery:
    def test_no_query(self) -> None:
        base, params = _split_url_query("https://example.com/api")
        assert base == "https://example.com/api"
        assert params == {}

    def test_with_query(self) -> None:
        base, params = _split_url_query("https://example.com/api?foo=bar&baz=1")
        assert base == "https://example.com/api"
        assert params == {"foo": "bar", "baz": "1"}


class TestNormalizeQueryValue:
    def test_bool_true(self) -> None:
        assert _normalize_query_value(True) == "true"

    def test_bool_false(self) -> None:
        assert _normalize_query_value(False) == "false"

    def test_int(self) -> None:
        assert _normalize_query_value(42) == "42"

    def test_string(self) -> None:
        assert _normalize_query_value("hello") == "hello"


class TestCandidateEnvNames:
    def test_simple(self) -> None:
        names = _candidate_env_names("my-secret")
        assert "my-secret" in names
        assert "MY_SECRET" in names

    def test_already_upper(self) -> None:
        names = _candidate_env_names("MY_SECRET")
        assert names == ["MY_SECRET"]

    def test_special_chars(self) -> None:
        names = _candidate_env_names("secret.ref/value")
        assert "secret.ref/value" in names
        assert "SECRET_REF_VALUE" in names


# --- Signing ---


class TestBuildSigningPayload:
    def test_basic(self) -> None:
        result = _build_signing_payload(
            method="GET",
            url="https://example.com/api/items",
            query_params={},
            body_for_signing=None,
            timestamp="1234567890",
        )
        lines = result.split("\n")
        assert lines[0] == "GET"
        assert lines[1] == "/api/items"
        assert lines[2] == ""  # empty query
        assert lines[3] == ""  # empty body
        assert lines[4] == "1234567890"

    def test_origin_only_url_uses_root_path(self) -> None:
        result = _build_signing_payload(
            method="GET",
            url="https://example.com",
            query_params={},
            body_for_signing=None,
            timestamp="1234567890",
        )
        lines = result.split("\n")
        assert lines[1] == "/"

    def test_with_query_params_sorted(self) -> None:
        result = _build_signing_payload(
            method="POST",
            url="https://example.com/api",
            query_params={"b": "2", "a": "1"},
            body_for_signing=None,
            timestamp="0",
        )
        lines = result.split("\n")
        assert "a=1" in lines[2]
        assert lines[2].index("a=1") < lines[2].index("b=2")

    def test_with_string_body(self) -> None:
        result = _build_signing_payload(
            method="POST",
            url="https://example.com/api",
            query_params={},
            body_for_signing="raw body",
            timestamp="0",
        )
        assert "raw body" in result

    def test_with_bytes_body(self) -> None:
        raw = b"\x00\x01\x02"
        result = _build_signing_payload(
            method="POST",
            url="https://example.com/api",
            query_params={},
            body_for_signing=raw,
            timestamp="0",
        )
        expected = base64.b64encode(raw).decode("ascii")
        assert expected in result

    def test_with_dict_body(self) -> None:
        result = _build_signing_payload(
            method="POST",
            url="https://example.com/api",
            query_params={},
            body_for_signing={"key": "value"},
            timestamp="0",
        )
        assert '{"key":"value"}' in result

    def test_method_uppercased(self) -> None:
        result = _build_signing_payload(
            method="post",
            url="https://example.com/api",
            query_params={},
            body_for_signing=None,
            timestamp="0",
        )
        assert result.startswith("POST\n")


# --- XML helpers ---


class TestXmlLocalName:
    def test_with_namespace(self) -> None:
        assert _xml_local_name("{http://example.com}Body") == "Body"

    def test_without_namespace(self) -> None:
        assert _xml_local_name("Body") == "Body"


class TestCoerceXmlText:
    def test_none(self) -> None:
        assert _coerce_xml_text(None) == ""

    def test_empty(self) -> None:
        assert _coerce_xml_text("") == ""

    def test_whitespace(self) -> None:
        assert _coerce_xml_text("   ") == ""

    def test_true(self) -> None:
        assert _coerce_xml_text("true") is True
        assert _coerce_xml_text("True") is True

    def test_false(self) -> None:
        assert _coerce_xml_text("false") is False

    def test_integer(self) -> None:
        assert _coerce_xml_text("42") == 42
        assert _coerce_xml_text("-7") == -7

    def test_string(self) -> None:
        assert _coerce_xml_text("hello world") == "hello world"


class TestXmlElementToValue:
    def test_text_only(self) -> None:
        elem = ET.fromstring("<name>Alice</name>")
        assert _xml_element_to_value(elem) == "Alice"

    def test_nested(self) -> None:
        elem = ET.fromstring("<user><name>Alice</name><age>30</age></user>")
        result = _xml_element_to_value(elem)
        assert result == {"name": "Alice", "age": 30}

    def test_repeated_elements_become_list(self) -> None:
        elem = ET.fromstring("<items><item>A</item><item>B</item></items>")
        result = _xml_element_to_value(elem)
        assert result == {"item": ["A", "B"]}


class TestSoapScalarToText:
    def test_bool_true(self) -> None:
        assert _soap_scalar_to_text(True) == "true"

    def test_bool_false(self) -> None:
        assert _soap_scalar_to_text(False) == "false"

    def test_string(self) -> None:
        assert _soap_scalar_to_text("hello") == "hello"

    def test_int(self) -> None:
        assert _soap_scalar_to_text(42) == "42"

    def test_control_chars_stripped(self) -> None:
        assert _soap_scalar_to_text("hello\x1fworld") == "helloworld"
        assert _soap_scalar_to_text("\x00\x01\x08") == ""
        # Tab, newline, carriage return are allowed
        assert _soap_scalar_to_text("a\tb\nc\r") == "a\tb\nc\r"


class TestSoapBodyElement:
    def test_finds_body(self) -> None:
        xml = '<Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body><data/></soap:Body></Envelope>'
        root = ET.fromstring(xml)
        body = _soap_body_element(root)
        assert body is not None

    def test_no_body(self) -> None:
        root = ET.fromstring("<root><child/></root>")
        assert _soap_body_element(root) is None


class TestBuildSoapEnvelope:
    def test_basic_envelope(self) -> None:
        from libs.ir.models import SoapOperationConfig

        config = SoapOperationConfig(
            target_namespace="http://example.com/api",
            request_element="GetItem",
            response_element="GetItemResponse",
            soap_action="http://example.com/api/GetItem",
        )
        result = _build_soap_envelope(config, {"id": "42"})
        assert "GetItem" in result
        assert "42" in result
        assert "Envelope" in result

    def test_unqualified_child_elements_when_requested(self) -> None:
        from libs.ir.models import SoapOperationConfig

        config = SoapOperationConfig(
            target_namespace="http://example.com/api",
            request_element="GetItem",
            response_element="GetItemResponse",
            soap_action="http://example.com/api/GetItem",
            child_element_form="unqualified",
        )

        result = _build_soap_envelope(config, {"id": "42"})
        root = ET.fromstring(result)
        body = _soap_body_element(root)
        assert body is not None
        request = next(iter(body))
        children = list(request)

        assert len(children) == 1
        assert children[0].tag == "id"
        assert children[0].text == "42"

    def test_control_chars_produce_valid_xml(self) -> None:
        from libs.ir.models import SoapOperationConfig

        config = SoapOperationConfig(
            target_namespace="http://example.com/api",
            request_element="Req",
            response_element="Resp",
            soap_action="http://example.com/api/Req",
        )
        result = _build_soap_envelope(config, {"name": "hello\x1fworld"})
        root = ET.fromstring(result)  # Must not crash
        assert root is not None

    def test_numeric_dict_key_produces_valid_xml(self) -> None:
        from libs.ir.models import SoapOperationConfig

        config = SoapOperationConfig(
            target_namespace="http://example.com/api",
            request_element="Req",
            response_element="Resp",
            soap_action="http://example.com/api/Req",
        )
        result = _build_soap_envelope(config, {"data": {"0": "value"}})
        root = ET.fromstring(result)  # Must not crash
        assert root is not None

    def test_numeric_request_element_sanitized(self) -> None:
        from libs.ir.models import SoapOperationConfig

        config = SoapOperationConfig(
            target_namespace="http://example.com/api",
            request_element="0abc",
        )
        result = _build_soap_envelope(config, {"x": "v"})
        root = ET.fromstring(result)
        assert root is not None


# --- Stream parsing ---


class TestParseStreamPayload:
    def test_valid_json(self) -> None:
        assert _parse_stream_payload('{"key": "value"}') == {"key": "value"}

    def test_invalid_json_returns_string(self) -> None:
        assert _parse_stream_payload("not json") == "not json"


# --- Response payload parsing ---


class TestParseResponsePayload:
    def test_json_response(self) -> None:
        response = httpx.Response(
            200,
            json={"key": "value"},
            headers={"content-type": "application/json"},
        )
        assert _parse_response_payload(response) == {"key": "value"}

    def test_mixed_case_json_content_type_response(self) -> None:
        response = httpx.Response(
            200,
            json={"key": "value"},
            headers={"content-type": "Application/JSON; Charset=UTF-8"},
        )
        assert _parse_response_payload(response) == {"key": "value"}

    def test_text_response(self) -> None:
        response = httpx.Response(
            200,
            text="hello",
            headers={"content-type": "text/plain"},
        )
        assert _parse_response_payload(response) == "hello"

    def test_binary_response(self) -> None:
        response = httpx.Response(
            200,
            content=b"\x00\x01",
            headers={"content-type": "application/octet-stream"},
        )
        result = _parse_response_payload(response)
        assert result["binary"] is True
        assert result["content_base64"] == base64.b64encode(b"\x00\x01").decode()

    def test_large_binary_response_truncated(self) -> None:
        from apps.mcp_runtime.proxy_utils import _MAX_BINARY_ENCODE_BYTES

        large_content = b"\x00" * (_MAX_BINARY_ENCODE_BYTES + 1)
        response = httpx.Response(
            200,
            content=large_content,
            headers={"content-type": "application/octet-stream"},
        )
        result = _parse_response_payload(response)
        assert result["binary"] is True
        assert result["truncated"] is True
        assert "content_base64" not in result
        assert result["size_bytes"] == len(large_content)

    def test_binary_at_limit_not_truncated(self) -> None:
        from apps.mcp_runtime.proxy_utils import _MAX_BINARY_ENCODE_BYTES

        content = b"\xab" * _MAX_BINARY_ENCODE_BYTES
        response = httpx.Response(
            200,
            content=content,
            headers={"content-type": "application/octet-stream"},
        )
        result = _parse_response_payload(response)
        assert result["binary"] is True
        assert "content_base64" in result
        assert result.get("truncated") is not True


# --- Nested value extraction ---


class TestExtractNestedValue:
    def test_simple(self) -> None:
        assert _extract_nested_value({"a": 1}, "a") == 1

    def test_nested(self) -> None:
        assert _extract_nested_value({"a": {"b": {"c": 3}}}, "a.b.c") == 3

    def test_missing(self) -> None:
        assert _extract_nested_value({"a": 1}, "b") is None

    def test_non_dict(self) -> None:
        assert _extract_nested_value("string", "a") is None


# --- Field filtering ---


class TestApplyFieldFilter:
    def test_none_filter(self) -> None:
        data = {"a": 1, "b": 2}
        assert _apply_field_filter(data, None) == data

    def test_empty_filter(self) -> None:
        data = {"a": 1, "b": 2}
        assert _apply_field_filter(data, []) == data

    def test_top_level_keys(self) -> None:
        data = {"a": 1, "b": 2, "c": 3}
        result = _apply_field_filter(data, ["a", "c"])
        assert result == {"a": 1, "c": 3}

    def test_nested_dot_path(self) -> None:
        data = {"user": {"name": "Alice", "age": 30, "email": "a@b.com"}}
        result = _apply_field_filter(data, ["user.name"])
        assert result == {"user": {"name": "Alice"}}

    def test_array_bracket_path(self) -> None:
        data = {"items": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]}
        result = _apply_field_filter(data, ["items[].id"])
        assert result == {"items": [{"id": 1}, {"id": 2}]}

    def test_list_payload_simple(self) -> None:
        data = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
        result = _apply_field_filter(data, ["id"])
        assert result == [{"id": 1}, {"id": 2}]

    def test_escaped_dot_literal_field_name(self) -> None:
        """backslash-dot selects a key with a literal dot."""
        data = {"Address.City": "Berlin", "name": "Alice"}
        result = _apply_field_filter(data, [r"Address\.City"])
        assert result == {"Address.City": "Berlin"}

    def test_escaped_dot_among_nested(self) -> None:
        """Escaped dot and regular nested path coexist."""
        data = {"Address.City": "Berlin", "user": {"name": "Bob"}}
        result = _apply_field_filter(data, [r"Address\.City", "user.name"])
        assert result == {"Address.City": "Berlin", "user": {"name": "Bob"}}


# --- Array limits ---


class TestApplyArrayLimit:
    def test_none_max(self) -> None:
        assert _apply_array_limit([1, 2, 3], None) == [1, 2, 3]

    def test_list_truncated(self) -> None:
        assert _apply_array_limit([1, 2, 3, 4, 5], 3) == [1, 2, 3]

    def test_dict_list_values_truncated(self) -> None:
        data: dict[str, Any] = {"items": [1, 2, 3], "name": "test"}
        result = _apply_array_limit(data, 2)
        assert result == {"items": [1, 2], "name": "test"}

    def test_non_collection_passthrough(self) -> None:
        assert _apply_array_limit("string", 5) == "string"


# --- Truncation ---


class TestApplyTruncation:
    def test_no_limit(self) -> None:
        from libs.ir.models import ResponseStrategy

        strategy = ResponseStrategy()
        result, truncated = _apply_truncation({"key": "value"}, strategy)
        assert truncated is False

    def test_within_limit(self) -> None:
        from libs.ir.models import ResponseStrategy

        strategy = ResponseStrategy(max_response_bytes=1000)
        result, truncated = _apply_truncation("short", strategy)
        assert truncated is False

    def test_truncated(self) -> None:
        from libs.ir.models import ResponseStrategy, TruncationPolicy

        strategy = ResponseStrategy(
            max_response_bytes=10,
            truncation_policy=TruncationPolicy.truncate,
        )
        result, truncated = _apply_truncation("a" * 100, strategy)
        assert truncated is True
        assert result["truncated"] is True


# --- Set nested ---


class TestSetNested:
    def test_simple(self) -> None:
        target: dict[str, Any] = {}
        # source is the value at root ("user"), i.e. {"name": "Alice", "age": 30}
        _set_nested(target, "user", ["name"], {"name": "Alice", "age": 30})
        assert target == {"user": {"name": "Alice"}}

    def test_deep_nesting(self) -> None:
        target: dict[str, Any] = {}
        # source is the value at root ("a"), i.e. {"b": {"c": "deep"}}
        _set_nested(target, "a", ["b", "c"], {"b": {"c": "deep"}})
        assert target == {"a": {"b": {"c": "deep"}}}

    def test_missing_path_skipped(self) -> None:
        target: dict[str, Any] = {}
        _set_nested(target, "a", ["missing"], {"other": 1})
        assert target == {}


# --- check_protocol_errors ordering ---


class TestCheckProtocolErrorsOrdering:
    """Verify that protocol-specific error messages take precedence over generic HTTP status."""

    def _make_response(self, status_code: int, json_body: dict[str, Any]) -> httpx.Response:
        import json

        return httpx.Response(
            status_code=status_code,
            content=json.dumps(json_body).encode(),
            headers={"content-type": "application/json"},
            request=httpx.Request("GET", "http://example.com"),
        )

    def _make_operation(self, oid: str = "op1") -> Any:
        from libs.ir.models import (
            Operation,
            RiskLevel,
            RiskMetadata,
            SourceType,
        )

        return Operation(
            id=oid,
            name="Test",
            method="GET",
            path="/test",
            params=[],
            risk=RiskMetadata(
                writes_state=False,
                destructive=False,
                external_side_effect=False,
                idempotent=True,
                risk_level=RiskLevel.safe,
                confidence=0.9,
                source=SourceType.extractor,
            ),
        )

    def _make_mocks(self) -> tuple[Any, Any, Any]:
        from unittest.mock import MagicMock

        breaker = MagicMock()
        breaker.is_open = False
        span = MagicMock()
        obs = MagicMock()
        obs.logger = MagicMock()
        return breaker, span, obs

    def test_odata_error_on_4xx_uses_structured_message(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError

        from apps.mcp_runtime.proxy_utils import check_protocol_errors

        resp = self._make_response(
            400, {"error": {"code": "BadRequest", "message": "Invalid $filter"}}
        )
        op = self._make_operation()
        breaker, span, obs = self._make_mocks()

        with __import__("pytest").raises(ToolError, match="OData error.*Invalid \\$filter"):
            check_protocol_errors(resp, op, breaker, span, obs, "odata")

    def test_scim_error_on_4xx_uses_structured_message(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError

        from apps.mcp_runtime.proxy_utils import check_protocol_errors

        resp = self._make_response(
            409,
            {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "detail": "User already exists",
                "status": "409",
                "scimType": "uniqueness",
            },
        )
        op = self._make_operation()
        breaker, span, obs = self._make_mocks()

        with __import__("pytest").raises(ToolError, match="SCIM error.*User already exists"):
            check_protocol_errors(resp, op, breaker, span, obs, "scim")

    def test_generic_http_error_when_no_structured_body(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError

        from apps.mcp_runtime.proxy_utils import check_protocol_errors

        resp = self._make_response(500, {"detail": "internal error"})
        op = self._make_operation()
        breaker, span, obs = self._make_mocks()

        with __import__("pytest").raises(ToolError, match="Upstream request failed.*500"):
            check_protocol_errors(resp, op, breaker, span, obs, "odata")
