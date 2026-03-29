"""Extended tests for apps/mcp_runtime/proxy.py — covers missed lines."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from apps.mcp_runtime.observability import RuntimeObservability
from apps.mcp_runtime.proxy import (
    PreparedRequestPayload,
    RuntimeProxy,
    _append_soap_argument,
    _apply_field_filter,
    _apply_truncation,
    _build_multipart_request_body,
    _build_raw_request_body,
    _build_sse_event,
    _coerce_xml_text,
    _collect_sse_events,
    _collect_websocket_messages,
    _descriptor_positive_float,
    _descriptor_positive_int,
    _extract_async_status_url,
    _extract_async_status_value,
    _filter_dict,
    _is_same_origin,
    _maybe_parse_json_payload,
    _normalize_form_value,
    _normalize_websocket_message,
    _normalize_websocket_messages,
    _set_nested,
    _unwrap_graphql_payload,
    _unwrap_soap_payload,
)
from libs.ir.models import (
    AsyncJobConfig,
    AuthConfig,
    AuthType,
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    GraphQLOperationConfig,
    GraphQLOperationType,
    GrpcStreamMode,
    GrpcStreamRuntimeConfig,
    GrpcUnaryRuntimeConfig,
    OAuth2ClientCredentialsConfig,
    Operation,
    Param,
    RequestBodyMode,
    ResponseStrategy,
    ServiceIR,
    SoapOperationConfig,
    TruncationPolicy,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _observability() -> RuntimeObservability:
    return RuntimeObservability()


def _make_request(method: str = "GET", url: str = "https://api.example.com/test") -> httpx.Request:
    return httpx.Request(method, url)


def _minimal_ir(**overrides: Any) -> ServiceIR:
    defaults: dict[str, Any] = {
        "service_name": "Test",
        "base_url": "https://api.example.com",
        "source_hash": "sha256:test",
        "protocol": "openapi",
        "operations": [],
    }
    defaults.update(overrides)
    # Auto-create stub operations for any event_descriptors referencing unknown op IDs
    if "event_descriptors" in defaults and defaults["event_descriptors"]:
        existing_op_ids = {
            op.id if hasattr(op, "id") else op["id"] for op in defaults.get("operations", [])
        }
        for ed in defaults["event_descriptors"]:
            op_id = ed.operation_id if hasattr(ed, "operation_id") else ed.get("operation_id")
            if op_id and op_id not in existing_op_ids:
                defaults.setdefault("operations", []).append(
                    Operation(id=op_id, name=op_id, method="GET", path=f"/{op_id}")
                )
                existing_op_ids.add(op_id)
    return ServiceIR(**defaults)


def _op(**overrides: Any) -> Operation:
    defaults: dict[str, Any] = {
        "id": "test_op",
        "name": "Test Op",
        "method": "GET",
        "path": "/test",
    }
    defaults.update(overrides)
    return Operation(**defaults)


def _proxy(
    ir: ServiceIR | None = None,
    client: httpx.AsyncClient | None = None,
    **kwargs: Any,
) -> RuntimeProxy:
    return RuntimeProxy(
        service_ir=ir or _minimal_ir(),
        observability=_observability(),
        client=client,
        **kwargs,
    )


# ===================================================================
# invoke() error branches
# ===================================================================


class TestInvokeHTTPError:
    """Lines 320-330: httpx.HTTPError branch in invoke()."""

    async def test_httpx_http_error_raises_tool_error(self) -> None:
        op = _op()
        p = _proxy()
        with patch.object(
            p,
            "_perform_request",
            side_effect=httpx.ConnectError("connection refused"),
        ):
            with pytest.raises(ToolError, match="Upstream request failed"):
                await p.invoke(op, {})


# ===================================================================
# _perform_grpc_unary
# ===================================================================


class TestPerformGrpcUnary:
    """Lines 369, 371, 381."""

    async def test_missing_grpc_unary_metadata(self) -> None:
        op = _op(grpc_unary=None, method="POST")
        p = _proxy()
        with pytest.raises(ToolError, match="missing grpc_unary metadata"):
            await p._perform_grpc_unary(op, {})

    async def test_no_executor_configured(self) -> None:
        config = GrpcUnaryRuntimeConfig(rpc_path="/test")
        op = _op(grpc_unary=config, method="POST")
        p = _proxy(grpc_unary_executor=None)
        with pytest.raises(ToolError, match="configured grpc unary executor"):
            await p._perform_grpc_unary(op, {})

    async def test_non_dict_result(self) -> None:
        config = GrpcUnaryRuntimeConfig(rpc_path="/test")
        op = _op(grpc_unary=config, method="POST")
        executor = AsyncMock()
        executor.invoke.return_value = "not a dict"
        p = _proxy(grpc_unary_executor=executor)
        with pytest.raises(ToolError, match="non-dict result"):
            await p._perform_grpc_unary(op, {})


# ===================================================================
# _perform_sql
# ===================================================================


class TestPerformSql:
    """Lines 393, 395, 405."""

    async def test_missing_sql_metadata(self) -> None:
        op = _op(sql=None)
        p = _proxy()
        with pytest.raises(ToolError, match="missing sql metadata"):
            await p._perform_sql(op, {})

    async def test_no_executor_configured(self) -> None:
        from libs.ir.models import SqlOperationConfig, SqlOperationType, SqlRelationKind

        sql_config = SqlOperationConfig(
            action=SqlOperationType.query,
            schema_name="public",
            relation_name="users",
            relation_kind=SqlRelationKind.table,
            filterable_columns=["id"],
        )
        op = _op(sql=sql_config)
        p = _proxy(sql_executor=None)
        with pytest.raises(ToolError, match="configured sql executor"):
            await p._perform_sql(op, {})

    async def test_non_dict_result(self) -> None:
        from libs.ir.models import SqlOperationConfig, SqlOperationType, SqlRelationKind

        sql_config = SqlOperationConfig(
            action=SqlOperationType.query,
            schema_name="public",
            relation_name="users",
            relation_kind=SqlRelationKind.table,
            filterable_columns=["id"],
        )
        op = _op(sql=sql_config)
        executor = AsyncMock()
        executor.invoke.return_value = [1, 2, 3]
        p = _proxy(sql_executor=executor)
        with pytest.raises(ToolError, match="non-dict result"):
            await p._perform_sql(op, {})


# ===================================================================
# _perform_request
# ===================================================================


class TestPerformRequest:
    """Line 417: missing method or path."""

    async def test_missing_method_raises(self) -> None:
        op = _op(method=None, path="/test")
        p = _proxy()
        with pytest.raises(ToolError, match="missing method or path"):
            await p._perform_request(op, {})

    async def test_missing_path_raises(self) -> None:
        op = _op(method="GET", path=None)
        p = _proxy()
        with pytest.raises(ToolError, match="missing method or path"):
            await p._perform_request(op, {})


# ===================================================================
# _stream_descriptor_for_operation
# ===================================================================


class TestStreamDescriptorForOperation:
    """Lines 459, 465, 471, 477."""

    def test_multiple_descriptors_raises(self) -> None:
        op = _op(id="dup_op")
        d1 = EventDescriptor(
            id="d1",
            name="D1",
            transport=EventTransport.sse,
            support=EventSupportLevel.supported,
            operation_id="dup_op",
        )
        d2 = EventDescriptor(
            id="d2",
            name="D2",
            transport=EventTransport.sse,
            support=EventSupportLevel.supported,
            operation_id="dup_op",
        )
        ir = _minimal_ir(event_descriptors=[d1, d2])
        p = _proxy(ir=ir)
        with pytest.raises(ToolError, match="multiple streaming descriptors"):
            p._stream_descriptor_for_operation(op)

    def test_unsupported_descriptor_raises(self) -> None:
        op = _op(id="unsup_op")
        d = EventDescriptor(
            id="d1",
            name="D1",
            transport=EventTransport.sse,
            support=EventSupportLevel.planned,
            operation_id="unsup_op",
        )
        ir = _minimal_ir(event_descriptors=[d])
        p = _proxy(ir=ir)
        with pytest.raises(ToolError, match="not enabled"):
            p._stream_descriptor_for_operation(op)

    def test_grpc_stream_missing_config_raises(self) -> None:
        op = _op(id="grpc_op")
        d = EventDescriptor.model_construct(
            id="d1",
            name="D1",
            transport=EventTransport.grpc_stream,
            support=EventSupportLevel.supported,
            operation_id="grpc_op",
            grpc_stream=None,
            metadata={},
        )
        # Bypass ServiceIR validation too - construct IR then inject descriptor
        ir = _minimal_ir(operations=[op])
        ir.event_descriptors = [d]
        p = _proxy(ir=ir)
        with pytest.raises(ToolError, match="missing grpc_stream runtime configuration"):
            p._stream_descriptor_for_operation(op)

    def test_unsupported_transport_raises(self) -> None:
        op = _op(id="cb_op")
        d = EventDescriptor(
            id="d1",
            name="D1",
            transport=EventTransport.callback,
            support=EventSupportLevel.supported,
            operation_id="cb_op",
        )
        ir = _minimal_ir(event_descriptors=[d])
        p = _proxy(ir=ir)
        with pytest.raises(ToolError, match="not supported by the runtime"):
            p._stream_descriptor_for_operation(op)

    def test_grpc_stream_returns_descriptor(self) -> None:
        op = _op(id="grpc_ok")
        grpc_cfg = GrpcStreamRuntimeConfig(rpc_path="/svc/Stream", mode=GrpcStreamMode.server)
        d = EventDescriptor(
            id="d1",
            name="D1",
            transport=EventTransport.grpc_stream,
            support=EventSupportLevel.supported,
            operation_id="grpc_ok",
            grpc_stream=grpc_cfg,
        )
        ir = _minimal_ir(event_descriptors=[d])
        p = _proxy(ir=ir)
        assert p._stream_descriptor_for_operation(op) is d


# ===================================================================
# _perform_stream_session
# ===================================================================


class TestPerformStreamSession:
    """Line 495: unsupported transport in _perform_stream_session."""

    async def test_unsupported_transport_raises(self) -> None:
        op = _op()
        d = EventDescriptor(
            id="d1",
            name="D1",
            transport=EventTransport.webhook,
            support=EventSupportLevel.supported,
        )
        p = _proxy()
        with pytest.raises(ToolError, match="not supported by the runtime"):
            await p._perform_stream_session(op, {}, d)


# ===================================================================
# _consume_grpc_stream
# ===================================================================


class TestConsumeGrpcStream:
    """Lines 506, 522."""

    async def test_missing_grpc_stream_config_raises(self) -> None:
        op = _op()
        d = EventDescriptor.model_construct(
            id="d1",
            name="D1",
            transport=EventTransport.grpc_stream,
            support=EventSupportLevel.supported,
            operation_id="test_op",
            grpc_stream=None,
            metadata={},
        )
        p = _proxy()
        with pytest.raises(ToolError, match="missing grpc_stream runtime configuration"):
            await p._consume_grpc_stream(op, {}, d)

    async def test_non_dict_result_raises(self) -> None:
        grpc_cfg = GrpcStreamRuntimeConfig(rpc_path="/svc/S", mode=GrpcStreamMode.server)
        op = _op()
        d = EventDescriptor(
            id="d1",
            name="D1",
            transport=EventTransport.grpc_stream,
            support=EventSupportLevel.supported,
            grpc_stream=grpc_cfg,
        )
        executor = AsyncMock()
        executor.invoke.return_value = "bad"
        p = _proxy(grpc_stream_executor=executor)
        with pytest.raises(ToolError, match="non-dict result"):
            await p._consume_grpc_stream(op, {}, d)


# ===================================================================
# _resolve_url — line 534 (missing path param)
# ===================================================================


class TestResolveUrl:
    def test_missing_path_param_raises(self) -> None:
        p = _proxy()
        with pytest.raises(ToolError, match="Missing path parameter"):
            p._resolve_url("/users/{userId}", {})

    def test_root_path_preserves_collection_base_url_without_trailing_slash(self) -> None:
        p = _proxy(ir=_minimal_ir(base_url="https://api.example.com/items/products"))
        url, path_params = p._resolve_url("/", {})

        assert url == "https://api.example.com/items/products"
        assert path_params == set()


# ===================================================================
# _prepare_graphql_payload — line 610
# ===================================================================


class TestPrepareGraphqlPayload:
    def test_missing_graphql_metadata_raises(self) -> None:
        op = _op(graphql=None)
        p = _proxy()
        with pytest.raises(ToolError, match="missing GraphQL runtime metadata"):
            p._prepare_graphql_payload(op, {})


# ===================================================================
# _prepare_soap_payload — line 640
# ===================================================================


class TestPrepareSoapPayload:
    def test_missing_soap_metadata_raises(self) -> None:
        op = _op(method="POST", soap=None)
        p = _proxy()
        with pytest.raises(ToolError, match="missing SOAP runtime metadata"):
            p._prepare_soap_payload(op, {})


# ===================================================================
# _split_query_and_body — line 656, complex body logic
# ===================================================================


class TestSplitQueryAndBody:
    def test_empty_remaining_returns_empty(self) -> None:
        op = _op(method="POST")
        p = _proxy()
        q, b = p._split_query_and_body(op, {})
        assert q == {}
        assert b is None

    def test_write_method_single_object_param_goes_to_body(self) -> None:
        op = _op(
            method="POST",
            params=[Param(name="data", type="object")],
        )
        p = _proxy()
        q, b = p._split_query_and_body(op, {"data": {"key": "val"}})
        assert q == {}
        assert b == {"key": "val"}

    def test_write_method_multiple_params_all_go_to_body(self) -> None:
        op = _op(method="POST")
        p = _proxy()
        q, b = p._split_query_and_body(op, {"a": 1, "b": 2})
        assert q == {}
        assert b == {"a": 1, "b": 2}

    def test_get_method_goes_to_query(self) -> None:
        op = _op(method="GET")
        p = _proxy()
        q, b = p._split_query_and_body(op, {"q": "search"})
        assert q == {"q": "search"}
        assert b is None


# ===================================================================
# _select_body_argument_name — lines 684, 690-705
# ===================================================================


class TestSelectBodyArgumentName:
    def test_body_param_name_missing_raises(self) -> None:
        op = _op(body_param_name="payload")
        p = _proxy()
        with pytest.raises(ToolError, match="expects body parameter"):
            p._select_body_argument_name(op, {"data": "val"})

    def test_falls_back_to_body_param_names(self) -> None:
        op = _op(body_param_name=None)
        p = _proxy()
        result = p._select_body_argument_name(op, {"body": "val", "other": "x"})
        assert result == "body"

    def test_object_like_single_key(self) -> None:
        op = _op(
            body_param_name=None,
            params=[Param(name="content", type="object")],
        )
        p = _proxy()
        result = p._select_body_argument_name(op, {"content": {}, "limit": 10})
        assert result == "content"

    def test_single_remaining_key(self) -> None:
        op = _op(body_param_name=None)
        p = _proxy()
        result = p._select_body_argument_name(op, {"custom_field": "val"})
        assert result == "custom_field"

    def test_ambiguous_raises(self) -> None:
        op = _op(body_param_name=None)
        p = _proxy()
        with pytest.raises(ToolError, match="could not determine"):
            p._select_body_argument_name(op, {"a": 1, "b": 2})


# ===================================================================
# _build_request_kwargs — line 756 (form_data without files)
# ===================================================================


class TestBuildRequestKwargs:
    def test_form_data_only(self) -> None:
        p = _proxy()
        payload = PreparedRequestPayload(
            query_params={},
            form_data={"field": "value"},
        )
        result = p._build_request_kwargs(
            headers={},
            params=None,
            payload=payload,
        )
        assert result["data"] == {"field": "value"}
        assert "json" not in result
        assert "files" not in result


# ===================================================================
# _build_primary_auth — lines 800-822
# ===================================================================


class TestBuildPrimaryAuth:
    async def test_basic_auth(self) -> None:
        ir = _minimal_ir(
            auth=AuthConfig(
                type=AuthType.basic,
                runtime_secret_ref="MY_SECRET",
            ),
        )
        p = _proxy(ir=ir)
        with patch.dict("os.environ", {"MY_SECRET": "user:pass"}):
            headers, query = await p._build_primary_auth("op1")
        encoded = base64.b64encode(b"user:pass").decode("ascii")
        assert headers["Authorization"] == f"Basic {encoded}"
        assert query == {}

    async def test_api_key_in_query(self) -> None:
        ir = _minimal_ir(
            auth=AuthConfig(
                type=AuthType.api_key,
                runtime_secret_ref="MY_KEY",
                api_key_param="apiKey",
                api_key_location="query",
            ),
        )
        p = _proxy(ir=ir)
        with patch.dict("os.environ", {"MY_KEY": "secret123"}):
            headers, query = await p._build_primary_auth("op1")
        assert query == {"apiKey": "secret123"}
        assert not headers

    async def test_api_key_in_header(self) -> None:
        ir = _minimal_ir(
            auth=AuthConfig(
                type=AuthType.api_key,
                runtime_secret_ref="MY_KEY",
                api_key_param="X-Api-Key",
                api_key_location="header",
            ),
        )
        p = _proxy(ir=ir)
        with patch.dict("os.environ", {"MY_KEY": "secret123"}):
            headers, query = await p._build_primary_auth("op1")
        assert headers == {"X-Api-Key": "secret123"}
        assert not query

    async def test_custom_header_with_prefix(self) -> None:
        ir = _minimal_ir(
            auth=AuthConfig(
                type=AuthType.custom_header,
                runtime_secret_ref="MY_SECRET",
                header_name="X-Custom",
                header_prefix="Token",
            ),
        )
        p = _proxy(ir=ir)
        with patch.dict("os.environ", {"MY_SECRET": "tok123"}):
            headers, query = await p._build_primary_auth("op1")
        assert headers["X-Custom"] == "Token tok123"

    async def test_custom_header_without_prefix(self) -> None:
        ir = _minimal_ir(
            auth=AuthConfig(
                type=AuthType.custom_header,
                runtime_secret_ref="MY_SECRET",
                header_name="X-Custom",
            ),
        )
        p = _proxy(ir=ir)
        with patch.dict("os.environ", {"MY_SECRET": "raw_val"}):
            headers, query = await p._build_primary_auth("op1")
        assert headers["X-Custom"] == "raw_val"

    async def test_custom_header_missing_header_name_raises(self) -> None:
        auth = AuthConfig.model_construct(
            type=AuthType.custom_header,
            runtime_secret_ref="MY_SECRET",
            header_name=None,
        )
        ir = _minimal_ir()
        ir.auth = auth
        p = _proxy(ir=ir)
        with patch.dict("os.environ", {"MY_SECRET": "val"}):
            with pytest.raises(ToolError, match="custom_header auth without header_name"):
                await p._build_primary_auth("op1")

    async def test_unsupported_auth_type_raises(self) -> None:
        ir = _minimal_ir()
        p = _proxy(ir=ir)
        # Force an unexpected auth type
        p._service_ir.auth.type = "unknown_type"  # type: ignore[assignment]
        with pytest.raises(ToolError, match="Unsupported auth type"):
            await p._build_primary_auth("op1")


# ===================================================================
# _fetch_oauth2_access_token — lines 842-905
# ===================================================================


class TestFetchOAuth2AccessToken:
    async def test_cached_token_returned(self) -> None:
        oauth2 = OAuth2ClientCredentialsConfig(
            token_url="https://auth.example.com/token",
            client_id_ref="CID",
            client_secret_ref="CSEC",
            scopes=["read"],
        )
        p = _proxy()
        cache_key = f"{oauth2.token_url}|{oauth2.client_id_ref}|read|"
        p._oauth_token_cache[cache_key] = ("cached_tok", None)
        token = await p._fetch_oauth2_access_token("op1", oauth2)
        assert token == "cached_tok"

    async def test_expired_token_refetched(self) -> None:
        oauth2 = OAuth2ClientCredentialsConfig(
            token_url="https://auth.example.com/token",
            client_id_ref="CID",
            client_secret_ref="CSEC",
            scopes=[],
        )
        p = _proxy()
        cache_key = f"{oauth2.token_url}|{oauth2.client_id_ref}||"
        p._oauth_token_cache[cache_key] = ("old_tok", time.time() - 100)

        mock_response = httpx.Response(
            200,
            json={"access_token": "new_tok", "expires_in": 3600},
            headers={"content-type": "application/json"},
            request=_make_request("POST", oauth2.token_url),
        )
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        p._client = mock_client

        with patch.dict("os.environ", {"CID": "id_val", "CSEC": "sec_val"}):
            token = await p._fetch_oauth2_access_token("op1", oauth2)
        assert token == "new_tok"

    async def test_client_secret_post_method(self) -> None:
        oauth2 = OAuth2ClientCredentialsConfig(
            token_url="https://auth.example.com/token",
            client_id_ref="CID",
            client_secret_ref="CSEC",
            scopes=["write"],
            audience="https://api.example.com",
            client_auth_method="client_secret_post",
        )
        p = _proxy()

        mock_response = httpx.Response(
            200,
            json={"access_token": "tok_post", "expires_in": 600},
            headers={"content-type": "application/json"},
            request=_make_request("POST", oauth2.token_url),
        )
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        p._client = mock_client

        with patch.dict("os.environ", {"CID": "cid", "CSEC": "csec"}):
            token = await p._fetch_oauth2_access_token("op1", oauth2)
        assert token == "tok_post"
        call_kwargs = mock_client.post.call_args
        content = call_kwargs.kwargs.get("content") or call_kwargs[1].get("content", "")
        assert "client_id=cid" in content
        assert "client_secret=csec" in content

    async def test_non_json_response_raises(self) -> None:
        oauth2 = OAuth2ClientCredentialsConfig(
            token_url="https://auth.example.com/token",
            client_id_ref="CID",
            client_secret_ref="CSEC",
        )
        p = _proxy()

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.raise_for_status = MagicMock()
        mock_response.json.side_effect = ValueError("not json")
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        p._client = mock_client

        with patch.dict("os.environ", {"CID": "id", "CSEC": "sec"}):
            with pytest.raises(ToolError, match="non-JSON response"):
                await p._fetch_oauth2_access_token("op1", oauth2)

    async def test_non_object_json_raises(self) -> None:
        oauth2 = OAuth2ClientCredentialsConfig(
            token_url="https://auth.example.com/token",
            client_id_ref="CID",
            client_secret_ref="CSEC",
        )
        p = _proxy()

        mock_response = httpx.Response(
            200,
            json=["not", "an", "object"],
            headers={"content-type": "application/json"},
            request=_make_request("POST", oauth2.token_url),
        )
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        p._client = mock_client

        with patch.dict("os.environ", {"CID": "id", "CSEC": "sec"}):
            with pytest.raises(ToolError, match="non-object JSON"):
                await p._fetch_oauth2_access_token("op1", oauth2)

    async def test_missing_access_token_raises(self) -> None:
        oauth2 = OAuth2ClientCredentialsConfig(
            token_url="https://auth.example.com/token",
            client_id_ref="CID",
            client_secret_ref="CSEC",
        )
        p = _proxy()

        mock_response = httpx.Response(
            200,
            json={"token_type": "bearer"},
            headers={"content-type": "application/json"},
            request=_make_request("POST", oauth2.token_url),
        )
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        p._client = mock_client

        with patch.dict("os.environ", {"CID": "id", "CSEC": "sec"}):
            with pytest.raises(ToolError, match="did not return an access_token"):
                await p._fetch_oauth2_access_token("op1", oauth2)

    async def test_expires_in_sets_expiry(self) -> None:
        oauth2 = OAuth2ClientCredentialsConfig(
            token_url="https://auth.example.com/token",
            client_id_ref="CID",
            client_secret_ref="CSEC",
        )
        p = _proxy()

        mock_response = httpx.Response(
            200,
            json={"access_token": "tok", "expires_in": 7200},
            headers={"content-type": "application/json"},
            request=_make_request("POST", oauth2.token_url),
        )
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        p._client = mock_client

        with patch.dict("os.environ", {"CID": "id", "CSEC": "sec"}):
            token = await p._fetch_oauth2_access_token("op1", oauth2)
        assert token == "tok"
        cache_key = f"{oauth2.token_url}|{oauth2.client_id_ref}||"
        _, expires_at = p._oauth_token_cache[cache_key]
        assert expires_at is not None
        assert expires_at > time.time()


# ===================================================================
# _poll_async_job — lines 916, 920, 931, 961
# ===================================================================


class TestPollAsyncJob:
    async def test_non_initial_status_returns_immediately(self) -> None:
        config = AsyncJobConfig(initial_status_codes=[202])
        response = httpx.Response(200, text="ok", request=_make_request())
        p = _proxy()
        result = await p._poll_async_job("op1", response, config)
        assert result is response

    async def test_no_status_url_raises(self) -> None:
        config = AsyncJobConfig(
            initial_status_codes=[202],
            status_url_source="location_header",
        )
        response = httpx.Response(202, text="", request=_make_request())
        p = _proxy()
        with pytest.raises(ToolError, match="pollable status URL"):
            await p._poll_async_job("op1", response, config)

    async def test_timeout_raises(self) -> None:
        config = AsyncJobConfig(
            initial_status_codes=[202],
            status_url_source="location_header",
            timeout_seconds=0.01,
            poll_interval_seconds=0.001,
        )
        response = httpx.Response(
            202,
            text="",
            headers={"Location": "https://api.example.com/status/1"},
            request=_make_request(),
        )
        p = _proxy()
        # Mock _send_request to return pending status forever
        pending_resp = httpx.Response(
            202,
            json={"status": "pending"},
            headers={"content-type": "application/json"},
            request=_make_request(),
        )
        with patch.object(p, "_send_request", return_value=pending_resp):
            with pytest.raises(ToolError, match="timed out"):
                await p._poll_async_job("op1", response, config)

    async def test_unrecognized_status_returns_response(self) -> None:
        config = AsyncJobConfig(
            initial_status_codes=[202],
            status_url_source="location_header",
            timeout_seconds=5.0,
        )
        response = httpx.Response(
            202,
            text="",
            headers={"Location": "https://api.example.com/status/1"},
            request=_make_request(),
        )
        p = _proxy()
        poll_resp = httpx.Response(
            200,
            json={"status": "unknown_state"},
            headers={"content-type": "application/json"},
            request=_make_request(),
        )
        with patch.object(p, "_send_request", return_value=poll_resp):
            result = await p._poll_async_job("op1", response, config)
        assert result is poll_resp


# ===================================================================
# _consume_sse_stream — lines 970, 979, 1005, 1011, 1046
# ===================================================================


class TestConsumeSSEStream:
    async def test_missing_method_raises(self) -> None:
        op = _op(method=None)
        d = EventDescriptor(
            id="d1",
            name="D1",
            transport=EventTransport.sse,
            support=EventSupportLevel.supported,
        )
        p = _proxy()
        with pytest.raises(ToolError, match="missing method or path"):
            await p._consume_sse_stream(op, {}, d)


# ===================================================================
# _consume_websocket_stream — line 1046
# ===================================================================


class TestConsumeWebsocketStream:
    async def test_missing_path_raises(self) -> None:
        op = _op(path=None)
        d = EventDescriptor(
            id="d1",
            name="D1",
            transport=EventTransport.websocket,
            support=EventSupportLevel.supported,
        )
        p = _proxy()
        with pytest.raises(ToolError, match="missing path metadata"):
            await p._consume_websocket_stream(op, {}, d)


# ===================================================================
# _prepare_websocket_session — lines 1112, 1119-1122
# ===================================================================


class TestPrepareWebsocketSession:
    def test_empty_remaining(self) -> None:
        op = _op()
        p = _proxy()
        query, messages = p._prepare_websocket_session(op, {}, path_argument_names=set())
        assert query == {}
        assert messages == []

    def test_uses_body_param_name(self) -> None:
        op = _op(body_param_name="payload")
        p = _proxy()
        query, messages = p._prepare_websocket_session(
            op,
            {"payload": "hello", "extra": "q"},
            path_argument_names=set(),
        )
        assert query == {"extra": "q"}
        assert messages == ["hello"]

    def test_falls_back_to_stream_message_names(self) -> None:
        op = _op(body_param_name=None)
        p = _proxy()
        query, messages = p._prepare_websocket_session(
            op,
            {"messages": ["m1", "m2"], "q": "val"},
            path_argument_names=set(),
        )
        assert query == {"q": "val"}
        assert messages == ["m1", "m2"]


# ===================================================================
# _resolve_secret_value / _resolve_secret_ref — line 1167, 1184
# ===================================================================


class TestResolveSecretValue:
    def test_missing_secret_ref_raises(self) -> None:
        auth = AuthConfig(type=AuthType.bearer, runtime_secret_ref=None)
        with pytest.raises(ToolError, match="runtime_secret_ref is not configured"):
            RuntimeProxy._resolve_secret_value(auth, "op1")

    def test_missing_env_var_raises(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ToolError, match="Missing"):
                RuntimeProxy._resolve_secret_ref("NONEXISTENT_VAR", "op1", purpose="test")


# ===================================================================
# _graphql_error_message — line 1196
# ===================================================================


class TestGraphqlErrorMessage:
    def test_non_dict_payload_returns_error(self) -> None:
        response = httpx.Response(
            200,
            text='"just a string"',
            headers={"content-type": "application/json"},
            request=_make_request(),
        )
        op = _op(
            graphql=GraphQLOperationConfig(
                operation_type=GraphQLOperationType.query,
                operation_name="getUsers",
                document="query getUsers { users { id } }",
            ),
        )
        result = RuntimeProxy._graphql_error_message(response, op)
        assert result is not None
        assert "non-object" in result


# ===================================================================
# _soap_fault_message — lines 1222-1246
# ===================================================================


class TestSoapFaultMessage:
    def test_no_body_returns_none(self) -> None:
        xml_text = '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"><soapenv:Header/></soapenv:Envelope>'
        response = httpx.Response(
            500,
            text=xml_text,
            headers={"content-type": "text/xml"},
            request=_make_request(),
        )
        soap_cfg = SoapOperationConfig(
            target_namespace="http://example.com/api",
            request_element="GetItem",
        )
        op = _op(method="POST", soap=soap_cfg)
        result = RuntimeProxy._soap_fault_message(response, op)
        assert result is None

    def test_fault_without_faultstring_uses_element(self) -> None:
        xml_text = (
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
            "<soapenv:Body>"
            "<soapenv:Fault>"
            "<faultcode>Server</faultcode>"
            "</soapenv:Fault>"
            "</soapenv:Body>"
            "</soapenv:Envelope>"
        )
        response = httpx.Response(
            500,
            text=xml_text,
            headers={"content-type": "text/xml"},
            request=_make_request(),
        )
        soap_cfg = SoapOperationConfig(
            target_namespace="http://example.com/api",
            request_element="GetItem",
        )
        op = _op(method="POST", soap=soap_cfg)
        result = RuntimeProxy._soap_fault_message(response, op)
        assert result is not None
        assert "SOAP operation" in result


# ===================================================================
# _descriptor_positive_int / _descriptor_positive_float — lines 1293, 1305
# ===================================================================


class TestDescriptorHelpers:
    def test_positive_int_invalid_returns_default(self) -> None:
        d = EventDescriptor(
            id="d1",
            name="D1",
            transport=EventTransport.sse,
            metadata={"max_events": -5},
        )
        assert _descriptor_positive_int(d, "max_events", default=10) == 10

    def test_positive_int_non_int_returns_default(self) -> None:
        d = EventDescriptor(
            id="d1",
            name="D1",
            transport=EventTransport.sse,
            metadata={"max_events": "abc"},
        )
        assert _descriptor_positive_int(d, "max_events", default=10) == 10

    def test_positive_float_invalid_returns_default(self) -> None:
        d = EventDescriptor(
            id="d1",
            name="D1",
            transport=EventTransport.sse,
            metadata={"idle_timeout_seconds": -1.0},
        )
        assert _descriptor_positive_float(d, "idle_timeout_seconds", default=1.0) == 1.0

    def test_positive_float_non_number_returns_default(self) -> None:
        d = EventDescriptor(
            id="d1",
            name="D1",
            transport=EventTransport.sse,
            metadata={"idle_timeout_seconds": "nope"},
        )
        assert _descriptor_positive_float(d, "idle_timeout_seconds", default=1.0) == 1.0


# ===================================================================
# _collect_sse_events — lines 1323-1359
# ===================================================================


class TestCollectSSEEvents:
    async def test_timeout_returns_idle_timeout(self) -> None:
        async def slow_lines():
            yield "data: first"
            yield ""
            await asyncio.sleep(10)

        response = MagicMock()
        response.aiter_lines.return_value = slow_lines()
        events, reason = await _collect_sse_events(
            response, max_events=10, idle_timeout_seconds=0.01
        )
        assert reason == "idle_timeout"
        assert len(events) == 1

    async def test_max_events_reached(self) -> None:
        async def lines():
            for i in range(5):
                yield f"data: msg{i}"
                yield ""

        response = MagicMock()
        response.aiter_lines.return_value = lines()
        events, reason = await _collect_sse_events(response, max_events=2, idle_timeout_seconds=5.0)
        assert reason == "max_events"
        assert len(events) == 2

    async def test_eof(self) -> None:
        async def lines():
            yield "data: only"
            yield ""

        response = MagicMock()
        response.aiter_lines.return_value = lines()
        events, reason = await _collect_sse_events(
            response, max_events=10, idle_timeout_seconds=5.0
        )
        assert reason == "eof"
        assert len(events) == 1

    async def test_comment_lines_ignored(self) -> None:
        async def lines():
            yield ": this is a comment"
            yield "data: real"
            yield ""

        response = MagicMock()
        response.aiter_lines.return_value = lines()
        events, reason = await _collect_sse_events(
            response, max_events=10, idle_timeout_seconds=5.0
        )
        assert len(events) == 1
        assert events[0]["data"] == "real"

    async def test_event_type_line(self) -> None:
        async def lines():
            yield "event: custom"
            yield "data: payload"
            yield ""

        response = MagicMock()
        response.aiter_lines.return_value = lines()
        events, reason = await _collect_sse_events(
            response, max_events=10, idle_timeout_seconds=5.0
        )
        assert events[0]["event"] == "custom"

    async def test_id_line(self) -> None:
        async def lines():
            yield "id: 42"
            yield "data: payload"
            yield ""

        response = MagicMock()
        response.aiter_lines.return_value = lines()
        events, reason = await _collect_sse_events(
            response, max_events=10, idle_timeout_seconds=5.0
        )
        assert events[0]["id"] == "42"

    async def test_trailing_event_with_max_events(self) -> None:
        async def lines():
            yield "data: msg1"
            yield ""
            yield "data: trailing"

        response = MagicMock()
        response.aiter_lines.return_value = lines()
        events, reason = await _collect_sse_events(response, max_events=2, idle_timeout_seconds=5.0)
        assert len(events) == 2
        assert reason == "max_events"

    async def test_event_type_empty_resets_to_message(self) -> None:
        async def lines():
            yield "event:"
            yield "data: payload"
            yield ""

        response = MagicMock()
        response.aiter_lines.return_value = lines()
        events, _ = await _collect_sse_events(response, max_events=10, idle_timeout_seconds=5.0)
        assert events[0]["event"] == "message"


# ===================================================================
# _build_sse_event — lines 1368, 1375
# ===================================================================


class TestBuildSSEEvent:
    def test_no_data_no_id_returns_none(self) -> None:
        assert _build_sse_event("message", [], None) is None

    def test_with_id(self) -> None:
        event = _build_sse_event("message", ["hello"], "42")
        assert event is not None
        assert event["id"] == "42"

    def test_json_data_parsed(self) -> None:
        event = _build_sse_event("message", ['{"key":"value"}'], None)
        assert event is not None
        assert event["parsed_data"] == {"key": "value"}

    def test_id_only_event(self) -> None:
        event = _build_sse_event("message", [], "42")
        assert event is not None
        assert event["id"] == "42"


# ===================================================================
# _normalize_websocket_message(s) — lines 1384-1402
# ===================================================================


class TestNormalizeWebsocketMessages:
    def test_none_returns_empty(self) -> None:
        assert _normalize_websocket_messages("op1", None) == []

    def test_list_input(self) -> None:
        result = _normalize_websocket_messages("op1", ["a", "b"])
        assert result == ["a", "b"]

    def test_single_value_wrapped(self) -> None:
        result = _normalize_websocket_messages("op1", "hello")
        assert result == ["hello"]


class TestNormalizeWebsocketMessage:
    def test_bytes_passthrough(self) -> None:
        assert _normalize_websocket_message(b"\x00\x01") == b"\x00\x01"

    def test_string_passthrough(self) -> None:
        assert _normalize_websocket_message("hello") == "hello"

    def test_dict_bytes_base64(self) -> None:
        encoded = base64.b64encode(b"binary").decode("ascii")
        result = _normalize_websocket_message({"bytes_base64": encoded})
        assert result == b"binary"

    def test_dict_text(self) -> None:
        result = _normalize_websocket_message({"text": "hello"})
        assert result == "hello"

    def test_dict_json(self) -> None:
        result = _normalize_websocket_message({"json": {"key": "val"}})
        assert isinstance(result, str)
        assert json.loads(result) == {"key": "val"}

    def test_fallback_json_dumps(self) -> None:
        result = _normalize_websocket_message(42)
        assert result == "42"

    def test_dict_without_known_keys_json_dumps(self) -> None:
        result = _normalize_websocket_message({"unknown": "val"})
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed == {"unknown": "val"}


# ===================================================================
# _collect_websocket_messages — lines 1415-1441
# ===================================================================


class TestCollectWebsocketMessages:
    async def test_timeout_returns_idle_timeout(self) -> None:
        ws = AsyncMock()
        ws.recv.side_effect = TimeoutError()
        events, reason = await _collect_websocket_messages(
            ws, max_messages=10, idle_timeout_seconds=0.01
        )
        assert reason == "idle_timeout"
        assert events == []

    async def test_connection_closed(self) -> None:
        import websockets

        ws = AsyncMock()
        ws.recv.side_effect = websockets.ConnectionClosed(None, None)
        events, reason = await _collect_websocket_messages(
            ws, max_messages=10, idle_timeout_seconds=5.0
        )
        assert reason == "connection_closed"

    async def test_bytes_message(self) -> None:
        ws = AsyncMock()
        ws.recv.side_effect = [b"\x00\x01", TimeoutError()]
        events, reason = await _collect_websocket_messages(
            ws, max_messages=10, idle_timeout_seconds=0.01
        )
        assert len(events) == 1
        assert events[0]["message_type"] == "bytes"
        assert events[0]["content_base64"] == base64.b64encode(b"\x00\x01").decode()

    async def test_text_message_with_json(self) -> None:
        ws = AsyncMock()
        ws.recv.side_effect = ['{"key":"val"}', TimeoutError()]
        events, reason = await _collect_websocket_messages(
            ws, max_messages=10, idle_timeout_seconds=0.01
        )
        assert events[0]["message_type"] == "text"
        assert events[0]["parsed_data"] == {"key": "val"}

    async def test_max_messages_reached(self) -> None:
        call_count = 0

        async def recv_side_effect():
            nonlocal call_count
            call_count += 1
            return f"msg{call_count}"

        ws = AsyncMock()
        ws.recv = recv_side_effect
        events, reason = await _collect_websocket_messages(
            ws, max_messages=2, idle_timeout_seconds=5.0
        )
        assert reason == "max_messages"
        assert len(events) == 2

    async def test_empty_loop_returns_connection_closed(self) -> None:
        """Cover line 1441: loop ends without any messages collected (max_messages=0)."""
        ws = AsyncMock()
        events, reason = await _collect_websocket_messages(
            ws, max_messages=0, idle_timeout_seconds=5.0
        )
        assert reason == "connection_closed"
        assert events == []


# ===================================================================
# _parse_stream_payload — lines 1447, 1452-1453
# ===================================================================


class TestParseStreamPayload:
    def test_empty_string_returns_original(self) -> None:
        from apps.mcp_runtime.proxy import _parse_stream_payload

        result = _parse_stream_payload("   ")
        assert result == "   "

    def test_non_json_start_returns_original(self) -> None:
        from apps.mcp_runtime.proxy import _parse_stream_payload

        result = _parse_stream_payload("not json")
        assert result == "not json"

    def test_invalid_json_returns_original(self) -> None:
        from apps.mcp_runtime.proxy import _parse_stream_payload

        result = _parse_stream_payload("{invalid json")
        assert result == "{invalid json"


# ===================================================================
# _parse_response_payload — lines 1498, 1509-1510
# ===================================================================


class TestParseResponsePayloadExtended:
    def test_none_query_value(self) -> None:
        from apps.mcp_runtime.proxy import _normalize_query_value

        assert _normalize_query_value(None) == ""

    def test_json_parse_error_returns_text(self) -> None:
        response = httpx.Response(
            200,
            text="not valid json",
            headers={"content-type": "application/json"},
            request=_make_request(),
        )
        from apps.mcp_runtime.proxy import _parse_response_payload

        result = _parse_response_payload(response)
        assert result == "not valid json"


# ===================================================================
# _append_soap_argument — lines 1556, 1558-1560, 1564-1566
# ===================================================================


class TestAppendSoapArgument:
    def test_none_value_skipped(self) -> None:
        import xml.etree.ElementTree as ET

        parent = ET.Element("root")
        _append_soap_argument(parent, "field", None, namespace="http://example.com")
        assert len(list(parent)) == 0

    def test_list_value(self) -> None:
        import xml.etree.ElementTree as ET

        parent = ET.Element("root")
        _append_soap_argument(parent, "item", ["a", "b"], namespace="http://example.com")
        children = list(parent)
        assert len(children) == 2

    def test_dict_value(self) -> None:
        import xml.etree.ElementTree as ET

        parent = ET.Element("root")
        _append_soap_argument(
            parent,
            "address",
            {"city": "NYC", "zip": "10001"},
            namespace="http://example.com",
        )
        children = list(parent)
        assert len(children) == 1
        grandchildren = list(children[0])
        assert len(grandchildren) == 2


# ===================================================================
# _coerce_xml_text — lines 1620-1626
# ===================================================================


class TestCoerceXmlTextExtended:
    def test_float_value(self) -> None:
        result = _coerce_xml_text("3.14")
        assert result == 3.14
        assert isinstance(result, float)

    def test_negative_float(self) -> None:
        result = _coerce_xml_text("-2.5")
        assert result == -2.5


# ===================================================================
# _apply_field_filter extended — lines 1673, 1700, 1706, 1717, 1730, 1745
# ===================================================================


class TestApplyFieldFilterExtended:
    def test_list_payload_with_nested_paths(self) -> None:
        data = [
            {"id": 1, "user": {"name": "Alice"}},
            {"id": 2, "user": {"name": "Bob"}},
        ]
        result = _apply_field_filter(data, ["id", "user.name"])
        assert len(result) == 2
        assert result[0]["id"] == 1

    def test_nested_path_missing_root_skipped(self) -> None:
        data = {"existing": "val"}
        result = _apply_field_filter(data, ["missing.deep.key"])
        assert result == {}

    def test_array_path_missing_root_skipped(self) -> None:
        data = {"existing": "val"}
        result = _apply_field_filter(data, ["missing[].id"])
        assert result == {}

    def test_array_path_non_list_value(self) -> None:
        data = {"items": "not_a_list"}
        result = _apply_field_filter(data, ["items[].id"])
        assert result == {"items": "not_a_list"}


# ===================================================================
# _set_nested extended — line 1730, 1745
# ===================================================================


class TestSetNestedExtended:
    def test_empty_segments_no_op(self) -> None:
        target: dict[str, Any] = {}
        _set_nested(target, "root", [], {"val": 1})
        assert target == {}

    def test_non_dict_intermediate_skipped(self) -> None:
        target: dict[str, Any] = {"root": "not_a_dict"}
        _set_nested(target, "root", ["deep", "val"], {"deep": {"val": 42}})
        # The existing string value shouldn't be overwritten to a dict for drilling
        assert target == {"root": "not_a_dict"}


# ===================================================================
# _apply_truncation extended — line 1777
# ===================================================================


class TestApplyTruncationExtended:
    def test_truncation_policy_none_returns_untouched(self) -> None:
        strategy = ResponseStrategy(
            max_response_bytes=5,
            truncation_policy=TruncationPolicy.none,
        )
        result, truncated = _apply_truncation("a" * 100, strategy)
        assert truncated is False
        assert result == "a" * 100


# ===================================================================
# _unwrap_graphql_payload — lines 1791, 1796
# ===================================================================


class TestUnwrapGraphqlPayload:
    def test_non_dict_raises(self) -> None:
        op = _op(
            graphql=GraphQLOperationConfig(
                operation_type=GraphQLOperationType.query,
                operation_name="getUsers",
                document="query getUsers { users { id } }",
            ),
        )
        with pytest.raises(ToolError, match="non-object"):
            _unwrap_graphql_payload("not a dict", op)

    def test_no_data_key_raises(self) -> None:
        op = _op(
            graphql=GraphQLOperationConfig(
                operation_type=GraphQLOperationType.query,
                operation_name="getUsers",
                document="query getUsers { users { id } }",
            ),
        )
        with pytest.raises(ToolError, match="returned no data object"):
            _unwrap_graphql_payload({"errors": []}, op)

    def test_extracts_named_field(self) -> None:
        op = _op(
            graphql=GraphQLOperationConfig(
                operation_type=GraphQLOperationType.query,
                operation_name="getUsers",
                document="query getUsers { users { id } }",
            ),
        )
        result = _unwrap_graphql_payload(
            {"data": {"getUsers": [{"id": 1}]}},
            op,
        )
        assert result == [{"id": 1}]


# ===================================================================
# _unwrap_soap_payload — lines 1802, 1806-1807, 1813, 1820, 1836, 1838
# ===================================================================


class TestUnwrapSoapPayload:
    def test_no_soap_config_returns_parsed(self) -> None:
        response = httpx.Response(
            200,
            json={"key": "value"},
            headers={"content-type": "application/json"},
            request=_make_request(),
        )
        op = _op(method="POST", soap=None)
        result = _unwrap_soap_payload(response, op)
        assert result == {"key": "value"}

    def test_invalid_xml_raises(self) -> None:
        soap_cfg = SoapOperationConfig(
            target_namespace="http://example.com/api",
            request_element="GetItem",
        )
        response = httpx.Response(
            200,
            text="<not valid xml",
            headers={"content-type": "text/xml"},
            request=_make_request(),
        )
        op = _op(method="POST", soap=soap_cfg)
        with pytest.raises(ToolError, match="invalid XML"):
            _unwrap_soap_payload(response, op)

    def test_no_body_element_raises(self) -> None:
        soap_cfg = SoapOperationConfig(
            target_namespace="http://example.com/api",
            request_element="GetItem",
        )
        xml_text = '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"><soapenv:Header/></soapenv:Envelope>'
        response = httpx.Response(
            200,
            text=xml_text,
            headers={"content-type": "text/xml"},
            request=_make_request(),
        )
        op = _op(method="POST", soap=soap_cfg)
        with pytest.raises(ToolError, match="no SOAP Body"):
            _unwrap_soap_payload(response, op)

    def test_fault_in_body_raises(self) -> None:
        soap_cfg = SoapOperationConfig(
            target_namespace="http://example.com/api",
            request_element="GetItem",
        )
        xml_text = (
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
            "<soapenv:Body>"
            "<soapenv:Fault>"
            "<faultstring>Server error</faultstring>"
            "</soapenv:Fault>"
            "</soapenv:Body>"
            "</soapenv:Envelope>"
        )
        response = httpx.Response(
            500,
            text=xml_text,
            headers={"content-type": "text/xml"},
            request=_make_request(),
        )
        op = _op(method="POST", soap=soap_cfg)
        with pytest.raises(ToolError, match="SOAP"):
            _unwrap_soap_payload(response, op)

    def test_empty_body_raises(self) -> None:
        soap_cfg = SoapOperationConfig(
            target_namespace="http://example.com/api",
            request_element="GetItem",
        )
        xml_text = (
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
            "<soapenv:Body>"
            "</soapenv:Body>"
            "</soapenv:Envelope>"
        )
        response = httpx.Response(
            200,
            text=xml_text,
            headers={"content-type": "text/xml"},
            request=_make_request(),
        )
        op = _op(method="POST", soap=soap_cfg)
        with pytest.raises(ToolError, match="empty SOAP Body"):
            _unwrap_soap_payload(response, op)

    def test_response_element_extraction(self) -> None:
        soap_cfg = SoapOperationConfig(
            target_namespace="http://example.com/api",
            request_element="GetItem",
            response_element="GetItemResponse",
        )
        xml_text = (
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
            "<soapenv:Body>"
            "<GetItemResponse><result>42</result></GetItemResponse>"
            "</soapenv:Body>"
            "</soapenv:Envelope>"
        )
        response = httpx.Response(
            200,
            text=xml_text,
            headers={"content-type": "text/xml"},
            request=_make_request(),
        )
        op = _op(method="POST", soap=soap_cfg)
        result = _unwrap_soap_payload(response, op)
        assert result == {"result": 42}

    def test_first_child_fallback(self) -> None:
        soap_cfg = SoapOperationConfig(
            target_namespace="http://example.com/api",
            request_element="GetItem",
            response_element="NotMatching",
        )
        xml_text = (
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
            "<soapenv:Body>"
            "<SomeResponse><value>hello</value></SomeResponse>"
            "</soapenv:Body>"
            "</soapenv:Envelope>"
        )
        response = httpx.Response(
            200,
            text=xml_text,
            headers={"content-type": "text/xml"},
            request=_make_request(),
        )
        op = _op(method="POST", soap=soap_cfg)
        result = _unwrap_soap_payload(response, op)
        assert result == {"value": "hello"}


# ===================================================================
# _build_multipart_request_body — lines 1848-1877
# ===================================================================


class TestBuildMultipartRequestBody:
    def test_non_dict_raises(self) -> None:
        with pytest.raises(ToolError, match="object payload"):
            _build_multipart_request_body("op1", "not_dict")

    def test_non_dict_form_raises(self) -> None:
        with pytest.raises(ToolError, match="form payload must be an object"):
            _build_multipart_request_body("op1", {"form": "bad", "files": {}})

    def test_non_dict_files_raises(self) -> None:
        with pytest.raises(ToolError, match="files payload must be an object"):
            _build_multipart_request_body("op1", {"form": {}, "files": "bad"})

    def test_valid_multipart(self) -> None:
        value = {
            "form": {"field1": "val1"},
            "files": {"file1": "text content"},
        }
        form_data, files, signable = _build_multipart_request_body("op1", value)
        assert form_data == {"field1": "val1"}
        assert "file1" in files
        assert files["file1"][0] == "file1"
        assert files["file1"][1] == b"text content"

    def test_file_with_base64_content(self) -> None:
        encoded = base64.b64encode(b"binary data").decode("ascii")
        value = {
            "form": {},
            "files": {
                "upload": {
                    "filename": "test.bin",
                    "content_base64": encoded,
                    "content_type": "application/octet-stream",
                },
            },
        }
        form_data, files, signable = _build_multipart_request_body("op1", value)
        assert files["upload"][0] == "test.bin"
        assert files["upload"][1] == b"binary data"
        assert files["upload"][2] == "application/octet-stream"


# ===================================================================
# _normalize_multipart_file_part — lines 1887-1933
# ===================================================================


class TestNormalizeMultipartFilePart:
    def test_string_value(self) -> None:
        from apps.mcp_runtime.proxy import _normalize_multipart_file_part

        filename, content, ct = _normalize_multipart_file_part("op1", "field", "hello")
        assert filename == "field"
        assert content == b"hello"
        assert ct == "text/plain"

    def test_non_string_non_dict_raises(self) -> None:
        from apps.mcp_runtime.proxy import _normalize_multipart_file_part

        with pytest.raises(ToolError, match="string or object"):
            _normalize_multipart_file_part("op1", "field", 42)

    def test_invalid_filename_raises(self) -> None:
        from apps.mcp_runtime.proxy import _normalize_multipart_file_part

        with pytest.raises(ToolError, match="invalid filename"):
            _normalize_multipart_file_part("op1", "field", {"filename": "", "content": "x"})

    def test_invalid_content_type_raises(self) -> None:
        from apps.mcp_runtime.proxy import _normalize_multipart_file_part

        with pytest.raises(ToolError, match="invalid content_type"):
            _normalize_multipart_file_part(
                "op1",
                "field",
                {"content": "x", "content_type": 123},
            )

    def test_non_string_base64_raises(self) -> None:
        from apps.mcp_runtime.proxy import _normalize_multipart_file_part

        with pytest.raises(ToolError, match="non-string content_base64"):
            _normalize_multipart_file_part(
                "op1",
                "field",
                {"content_base64": 123},
            )

    def test_invalid_base64_raises(self) -> None:
        from apps.mcp_runtime.proxy import _normalize_multipart_file_part

        with pytest.raises(ToolError, match="invalid base64"):
            _normalize_multipart_file_part(
                "op1",
                "field",
                {"content_base64": "!!!not base64!!!"},
            )

    def test_missing_content_raises(self) -> None:
        from apps.mcp_runtime.proxy import _normalize_multipart_file_part

        with pytest.raises(ToolError, match="requires string content"):
            _normalize_multipart_file_part("op1", "field", {"filename": "f.txt"})

    def test_content_string_with_content_type(self) -> None:
        from apps.mcp_runtime.proxy import _normalize_multipart_file_part

        filename, content, ct = _normalize_multipart_file_part(
            "op1",
            "field",
            {"filename": "doc.txt", "content": "hello", "content_type": "text/html"},
        )
        assert filename == "doc.txt"
        assert content == b"hello"
        assert ct == "text/html"

    def test_content_without_content_type_defaults(self) -> None:
        from apps.mcp_runtime.proxy import _normalize_multipart_file_part

        filename, content, ct = _normalize_multipart_file_part(
            "op1",
            "field",
            {"filename": "doc.txt", "content": "hello"},
        )
        assert ct == "text/plain"

    def test_filename_defaults_to_field_name(self) -> None:
        from apps.mcp_runtime.proxy import _normalize_multipart_file_part

        filename, content, ct = _normalize_multipart_file_part(
            "op1",
            "myfield",
            {"content": "data"},
        )
        assert filename == "myfield"


# ===================================================================
# _build_raw_request_body — lines 1941-1977
# ===================================================================


class TestBuildRawRequestBody:
    def test_string_value(self) -> None:
        body, ct, signable = _build_raw_request_body("op1", "raw text")
        assert body == "raw text"
        assert ct is None
        assert signable == "raw text"

    def test_non_dict_non_string_raises(self) -> None:
        with pytest.raises(ToolError, match="string or object"):
            _build_raw_request_body("op1", 42)

    def test_non_string_content_type_raises(self) -> None:
        with pytest.raises(ToolError, match="non-string content_type"):
            _build_raw_request_body("op1", {"content": "x", "content_type": 123})

    def test_non_string_base64_raises(self) -> None:
        with pytest.raises(ToolError, match="non-string content_base64"):
            _build_raw_request_body("op1", {"content_base64": 123})

    def test_invalid_base64_raises(self) -> None:
        with pytest.raises(ToolError, match="invalid base64"):
            _build_raw_request_body("op1", {"content_base64": "!!!bad!!!"})

    def test_valid_base64(self) -> None:
        encoded = base64.b64encode(b"binary").decode("ascii")
        body, ct, signable = _build_raw_request_body(
            "op1",
            {"content_base64": encoded, "content_type": "application/octet-stream"},
        )
        assert body == b"binary"
        assert ct == "application/octet-stream"

    def test_content_string(self) -> None:
        body, ct, signable = _build_raw_request_body(
            "op1",
            {"content": "hello", "content_type": "text/plain"},
        )
        assert body == "hello"
        assert ct == "text/plain"

    def test_missing_content_raises(self) -> None:
        with pytest.raises(ToolError, match="requires string content"):
            _build_raw_request_body("op1", {"content_type": "text/plain"})


# ===================================================================
# _normalize_form_value — lines 1984, 1986
# ===================================================================


class TestNormalizeFormValue:
    def test_bool_true(self) -> None:
        assert _normalize_form_value(True) == "true"

    def test_bool_false(self) -> None:
        assert _normalize_form_value(False) == "false"

    def test_none(self) -> None:
        assert _normalize_form_value(None) == ""

    def test_dict(self) -> None:
        result = _normalize_form_value({"key": "val"})
        assert json.loads(result) == {"key": "val"}

    def test_list(self) -> None:
        result = _normalize_form_value([1, 2])
        assert json.loads(result) == [1, 2]

    def test_int(self) -> None:
        assert _normalize_form_value(42) == "42"


# ===================================================================
# _is_same_origin — line 1999-2014
# ===================================================================


class TestIsSameOrigin:
    def test_same_origin(self) -> None:
        assert _is_same_origin("https://api.example.com", "https://api.example.com/status/1")

    def test_different_origin(self) -> None:
        assert not _is_same_origin("https://api.example.com", "https://evil.com/status/1")

    def test_different_scheme(self) -> None:
        assert not _is_same_origin("https://api.example.com", "http://api.example.com/status/1")


# ===================================================================
# _extract_async_status_url — lines 1999-2031
# ===================================================================


class TestExtractAsyncStatusUrl:
    def test_location_header_with_location(self) -> None:
        config = AsyncJobConfig(status_url_source="location_header")
        response = httpx.Response(
            202,
            headers={"Location": "https://api.example.com/status/123"},
            request=_make_request(),
        )
        result = _extract_async_status_url(config, response)
        assert result == "https://api.example.com/status/123"

    def test_location_header_no_header(self) -> None:
        config = AsyncJobConfig(status_url_source="location_header")
        response = httpx.Response(202, request=_make_request())
        result = _extract_async_status_url(config, response)
        assert result is None

    def test_location_header_different_origin_blocked(self) -> None:
        config = AsyncJobConfig(status_url_source="location_header")
        response = httpx.Response(
            202,
            headers={"Location": "https://evil.com/status/123"},
            request=_make_request(),
        )
        result = _extract_async_status_url(config, response)
        assert result is None

    def test_response_body_extracts_url(self) -> None:
        config = AsyncJobConfig(
            status_url_source="response_body",
            status_url_field="links.status",
        )
        response = httpx.Response(
            202,
            json={"links": {"status": "https://api.example.com/status/1"}},
            headers={"content-type": "application/json"},
            request=_make_request(),
        )
        result = _extract_async_status_url(config, response)
        assert result == "https://api.example.com/status/1"

    def test_response_body_no_field(self) -> None:
        config = AsyncJobConfig.model_construct(
            status_url_source="response_body",
            status_url_field=None,
        )
        response = httpx.Response(
            202,
            json={"data": "val"},
            headers={"content-type": "application/json"},
            request=_make_request(),
        )
        result = _extract_async_status_url(config, response)
        assert result is None

    def test_response_body_non_string_url(self) -> None:
        config = AsyncJobConfig(
            status_url_source="response_body",
            status_url_field="url",
        )
        response = httpx.Response(
            202,
            json={"url": 42},
            headers={"content-type": "application/json"},
            request=_make_request(),
        )
        result = _extract_async_status_url(config, response)
        assert result is None

    def test_response_body_different_origin_blocked(self) -> None:
        config = AsyncJobConfig(
            status_url_source="response_body",
            status_url_field="url",
        )
        response = httpx.Response(
            202,
            json={"url": "https://evil.com/status/1"},
            headers={"content-type": "application/json"},
            request=_make_request(),
        )
        result = _extract_async_status_url(config, response)
        assert result is None


# ===================================================================
# _extract_async_status_value — line 2038, 2042
# ===================================================================


class TestExtractAsyncStatusValue:
    def test_extracts_string_status(self) -> None:
        config = AsyncJobConfig()
        response = httpx.Response(
            200,
            json={"status": "completed"},
            headers={"content-type": "application/json"},
            request=_make_request(),
        )
        result = _extract_async_status_value(config, response)
        assert result == "completed"

    def test_non_json_returns_none(self) -> None:
        config = AsyncJobConfig()
        response = httpx.Response(
            200,
            text="not json",
            headers={"content-type": "text/plain"},
            request=_make_request(),
        )
        result = _extract_async_status_value(config, response)
        assert result is None

    def test_non_string_status_returns_none(self) -> None:
        config = AsyncJobConfig()
        response = httpx.Response(
            200,
            json={"status": 42},
            headers={"content-type": "application/json"},
            request=_make_request(),
        )
        result = _extract_async_status_value(config, response)
        assert result is None


# ===================================================================
# _maybe_parse_json_payload — lines 2048, 2051-2052
# ===================================================================


class TestMaybeParseJsonPayload:
    def test_non_json_content_type(self) -> None:
        response = httpx.Response(
            200,
            text="plain",
            headers={"content-type": "text/plain"},
            request=_make_request(),
        )
        assert _maybe_parse_json_payload(response) is None

    def test_invalid_json_returns_none(self) -> None:
        response = httpx.Response(
            200,
            text="not valid json",
            headers={"content-type": "application/json"},
            request=_make_request(),
        )
        assert _maybe_parse_json_payload(response) is None

    def test_valid_json(self) -> None:
        response = httpx.Response(
            200,
            json={"key": "val"},
            headers={"content-type": "application/json"},
            request=_make_request(),
        )
        result = _maybe_parse_json_payload(response)
        assert result == {"key": "val"}


# ===================================================================
# Full invoke() integration-like tests
# ===================================================================


class TestInvokeIntegration:
    async def test_invoke_sql_success(self) -> None:
        from libs.ir.models import SqlOperationConfig, SqlOperationType, SqlRelationKind

        sql_config = SqlOperationConfig(
            action=SqlOperationType.query,
            schema_name="public",
            relation_name="users",
            relation_kind=SqlRelationKind.table,
            filterable_columns=["id"],
        )
        op = _op(sql=sql_config)
        executor = AsyncMock()
        executor.invoke.return_value = {"rows": []}
        p = _proxy(sql_executor=executor)
        result = await p.invoke(op, {})
        assert result["status"] == "ok"
        assert result["result"] == {"rows": []}

    async def test_invoke_grpc_unary_success(self) -> None:
        config = GrpcUnaryRuntimeConfig(rpc_path="/test")
        op = _op(grpc_unary=config, method="POST")
        executor = AsyncMock()
        executor.invoke.return_value = {"data": "val"}
        p = _proxy(grpc_unary_executor=executor)
        result = await p.invoke(op, {})
        assert result["status"] == "ok"
        assert result["result"] == {"data": "val"}

    async def test_invoke_tool_error_from_sql_records_failure(self) -> None:
        from libs.ir.models import SqlOperationConfig, SqlOperationType, SqlRelationKind

        sql_config = SqlOperationConfig(
            action=SqlOperationType.query,
            schema_name="public",
            relation_name="users",
            relation_kind=SqlRelationKind.table,
            filterable_columns=["id"],
        )
        op = _op(sql=sql_config)
        executor = AsyncMock()
        executor.invoke.side_effect = ToolError("SQL failed")
        p = _proxy(sql_executor=executor)
        with pytest.raises(ToolError, match="SQL failed"):
            await p.invoke(op, {})
        # The breaker should have recorded a failure
        assert op.id in p.breakers

    async def test_invoke_graphql_error(self) -> None:
        gql_config = GraphQLOperationConfig(
            operation_type=GraphQLOperationType.query,
            operation_name="getUsers",
            document="query getUsers { users { id } }",
        )
        op = _op(method="POST", path="/graphql", graphql=gql_config)
        p = _proxy()

        response = httpx.Response(
            200,
            json={"errors": [{"message": "Bad request"}]},
            headers={"content-type": "application/json"},
            request=_make_request("POST", "https://api.example.com/graphql"),
        )
        with patch.object(p, "_perform_request", return_value=response):
            with pytest.raises(ToolError, match="GraphQL"):
                await p.invoke(op, {})

    async def test_invoke_soap_fault(self) -> None:
        soap_cfg = SoapOperationConfig(
            target_namespace="http://example.com/api",
            request_element="GetItem",
        )
        op = _op(method="POST", path="/soap", soap=soap_cfg)
        p = _proxy()

        xml_text = (
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
            "<soapenv:Body>"
            "<soapenv:Fault>"
            "<faultstring>Server error</faultstring>"
            "</soapenv:Fault>"
            "</soapenv:Body>"
            "</soapenv:Envelope>"
        )
        response = httpx.Response(
            500,
            text=xml_text,
            headers={"content-type": "text/xml"},
            request=_make_request("POST", "https://api.example.com/soap"),
        )
        with patch.object(p, "_perform_request", return_value=response):
            with pytest.raises(ToolError, match="SOAP"):
                await p.invoke(op, {})

    async def test_invoke_upstream_error_status(self) -> None:
        op = _op()
        p = _proxy()

        response = httpx.Response(
            500,
            text="Internal Server Error",
            request=_make_request(),
        )
        with patch.object(p, "_perform_request", return_value=response):
            with pytest.raises(ToolError, match="Upstream request failed"):
                await p.invoke(op, {})

    async def test_invoke_timeout(self) -> None:
        op = _op()
        p = _proxy()
        with patch.object(p, "_perform_request", side_effect=httpx.ReadTimeout("timeout")):
            with pytest.raises(ToolError, match="Upstream timeout"):
                await p.invoke(op, {})


# ===================================================================
# _filter_dict extended — array_paths with non-list value
# ===================================================================


class TestFilterDictExtended:
    def test_array_path_with_non_list_passthrough(self) -> None:
        result = _filter_dict(
            {"items": "scalar_value"},
            set(),
            [],
            {"items": ["id"]},
        )
        assert result == {"items": "scalar_value"}

    def test_nested_path_root_missing(self) -> None:
        result = _filter_dict(
            {"other": "val"},
            set(),
            [("missing", ["deep"])],
            {},
        )
        assert result == {}

    def test_mixed_top_nested_array(self) -> None:
        result = _filter_dict(
            {
                "id": 1,
                "user": {"name": "Alice", "age": 30},
                "items": [{"a": 1, "b": 2}, {"a": 3, "b": 4}],
            },
            {"id"},
            [("user", ["name"])],
            {"items": ["a"]},
        )
        assert result["id"] == 1
        assert result["user"] == {"name": "Alice"}
        assert result["items"] == [{"a": 1}, {"a": 3}]


# ===================================================================
# request_body_mode multipart / raw via _prepare_request_payload
# ===================================================================


class TestPrepareRequestPayloadModes:
    def test_multipart_mode(self) -> None:
        op = _op(
            method="POST",
            request_body_mode=RequestBodyMode.multipart,
            body_param_name="upload",
        )
        p = _proxy()
        args = {"upload": {"form": {"field": "val"}, "files": {}}, "q": "search"}
        payload = p._prepare_request_payload(op, args, path_argument_names=set())
        assert payload.form_data == {"field": "val"}
        assert payload.query_params == {"q": "search"}

    def test_raw_mode(self) -> None:
        op = _op(
            method="POST",
            request_body_mode=RequestBodyMode.raw,
            body_param_name="body",
        )
        p = _proxy()
        args = {"body": "raw content"}
        payload = p._prepare_request_payload(op, args, path_argument_names=set())
        assert payload.raw_body == "raw content"


# ===================================================================
# aclose
# ===================================================================


class TestAclose:
    async def test_aclose_with_owned_client(self) -> None:
        p = _proxy()
        p._client = AsyncMock()
        p._owns_client = True
        await p.aclose()
        p._client.aclose.assert_awaited_once()

    async def test_aclose_with_sql_executor(self) -> None:
        executor = MagicMock()
        executor.aclose = AsyncMock()
        p = _proxy(sql_executor=executor)
        await p.aclose()
        executor.aclose.assert_awaited_once()

    async def test_aclose_without_owned_client(self) -> None:
        client = AsyncMock()
        p = _proxy(client=client)
        await p.aclose()
        client.aclose.assert_not_awaited()
