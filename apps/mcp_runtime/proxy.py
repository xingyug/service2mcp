"""Upstream HTTP proxy — thin dispatcher over protocol-specific modules."""

from __future__ import annotations

import asyncio
import importlib
from time import perf_counter
from typing import Any

import httpx
import websockets  # noqa: F401 — re-exported for mock.patch compatibility
from mcp.server.fastmcp.exceptions import ToolError

from apps.mcp_runtime.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from apps.mcp_runtime.event_bridge import EventBridgeClient
from apps.mcp_runtime.observability import RuntimeObservability
from apps.mcp_runtime.proxy_enterprise import (
    jsonrpc_error_message,
    odata_error_message,
    prepare_jsonrpc_payload,
    scim_error_message,
)
from apps.mcp_runtime.proxy_graphql import (
    graphql_error_message,
    prepare_graphql_payload,  # noqa: F401
)
from apps.mcp_runtime.proxy_http import (
    perform_request,
    poll_async_job,
    prepare_request_payload,
    select_body_argument_name,
    split_query_and_body,
)
from apps.mcp_runtime.proxy_soap import prepare_soap_payload, soap_fault_message
from apps.mcp_runtime.proxy_streaming import (
    _collect_sse_events,  # noqa: F401 — re-exported for mock.patch compat
    _collect_websocket_messages,  # noqa: F401 — re-exported for mock.patch compat
    consume_grpc_stream,
    consume_sse_stream,
    consume_websocket_stream,
    perform_stream_session,
    prepare_websocket_session,
    stream_descriptor_for_operation,
)
from apps.mcp_runtime.proxy_utils import (
    GrpcStreamExecutor,
    GrpcUnaryExecutor,
    PreparedRequestPayload,  # noqa: F401 — re-exported
    SqlExecutor,
    build_request_kwargs,
    check_protocol_errors,
    obs_fail,
    obs_success,
    resolve_secret_ref_for_operation,
    resolve_secret_value,
    resolve_url,
    sanitize_response,
)
from apps.mcp_runtime.proxy_utils import (
    get_client as _get_client_impl,
)
from apps.mcp_runtime.webhook_adapter import WebhookAdapter
from libs.ir.models import (
    EventDescriptor,
    EventDirection,
    EventSupportLevel,
    EventTransport,
    Operation,
    ServiceIR,
)
from libs.observability.tracing import trace_span

_BROKER_TRANSPORTS = {
    EventTransport.kafka,
    EventTransport.rabbitmq,
    EventTransport.mqtt,
    EventTransport.amqp,
    EventTransport.pulsar,
    EventTransport.async_event,
}
_WEBHOOK_TRANSPORTS = {EventTransport.webhook, EventTransport.callback}

# Lazy re-exports for ``from apps.mcp_runtime.proxy import <any_old_name>``.
_REEXPORT_MODULES = [
    "apps.mcp_runtime.proxy_utils",
    "apps.mcp_runtime.proxy_graphql",
    "apps.mcp_runtime.proxy_soap",
    "apps.mcp_runtime.proxy_enterprise",
    "apps.mcp_runtime.proxy_http",
    "apps.mcp_runtime.proxy_streaming",
]

__all__ = [
    "RuntimeProxy",
    "GrpcStreamExecutor",
    "GrpcUnaryExecutor",
    "SqlExecutor",
    "PreparedRequestPayload",
    "_collect_sse_events",
    "_collect_websocket_messages",
    "websockets",
]


def __getattr__(name: str) -> Any:
    for mod_path in _REEXPORT_MODULES:
        mod = importlib.import_module(mod_path)
        try:
            return getattr(mod, name)
        except AttributeError:
            continue
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class RuntimeProxy:
    """Executes IR operations against the upstream API."""

    def __init__(
        self,
        service_ir: ServiceIR,
        *,
        observability: RuntimeObservability,
        client: httpx.AsyncClient | None = None,
        sql_executor: SqlExecutor | None = None,
        grpc_unary_executor: GrpcUnaryExecutor | None = None,
        grpc_stream_executor: GrpcStreamExecutor | None = None,
        event_bridge_client: EventBridgeClient | None = None,
        webhook_adapter: WebhookAdapter | None = None,
        timeout: float = 10.0,
        failure_threshold: int = 5,
    ) -> None:
        self._service_ir = service_ir
        self._observability = observability
        self._timeout = timeout
        self._client = client
        self._sql_executor = sql_executor
        self._grpc_unary_executor = grpc_unary_executor
        self._grpc_stream_executor = grpc_stream_executor
        self._event_bridge_client = event_bridge_client
        self._webhook_adapter = webhook_adapter
        self._owns_client = client is None
        self._failure_threshold = failure_threshold
        self.breakers: dict[str, CircuitBreaker] = {}
        self._oauth_token_cache: dict[str, tuple[str, float | None]] = {}
        self._oauth_lock = asyncio.Lock()

    def _ctx(self) -> dict[str, Any]:
        """Common state passed to module-level proxy functions."""
        return dict(
            service_ir=self._service_ir,
            oauth_token_cache=self._oauth_token_cache,
            oauth_lock=self._oauth_lock,
            get_client=self._get_client,
            timeout=self._timeout,
        )

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
        sql_close = getattr(self._sql_executor, "aclose", None)
        if callable(sql_close):
            await sql_close()  # pyright: ignore[reportGeneralTypeIssues]
        if self._event_bridge_client is not None:
            await self._event_bridge_client.disconnect()

    async def invoke(self, operation: Operation, arguments: dict[str, Any]) -> dict[str, Any]:
        start = perf_counter()
        breaker = self.breakers.setdefault(
            operation.id,
            CircuitBreaker(operation_id=operation.id, failure_threshold=self._failure_threshold),
        )
        obs, oid = self._observability, operation.id
        with trace_span(
            "mcp_runtime.tool_call",
            attributes={
                "tool.operation_id": oid,
                "tool.method": operation.method or "",
                "tool.path": operation.path or "",
            },
        ) as span:
            obs.logger.info(
                "runtime tool invocation started",
                extra={
                    "extra_fields": {
                        "operation_id": oid,
                        "method": operation.method,
                        "path": operation.path,
                    }
                },
            )
            try:
                breaker.before_request()
            except CircuitBreakerOpenError as exc:
                obs.record_tool_call(oid, "error")
                obs.set_circuit_breaker_state(oid, True)
                span.record_exception(exc)
                obs.logger.warning(
                    "runtime tool invocation blocked by circuit breaker",
                    extra={"extra_fields": {"operation_id": oid}},
                )
                raise ToolError(str(exc)) from exc
            _failure_recorded = False
            _reached_http_path = False
            try:
                early = await self._dispatch_native(operation, arguments)
                if early is not None:
                    obs_success(breaker, obs, oid)
                    return early
                _reached_http_path = True
                response = await self._perform_request(operation, arguments)
                check_protocol_errors(
                    response, operation, breaker, span, obs, self._service_ir.protocol
                )
                result, truncated = sanitize_response(
                    response, operation, protocol=self._service_ir.protocol
                )
            except httpx.TimeoutException as exc:
                _failure_recorded = True
                obs_fail(breaker, obs, oid, "timeout", span, exc, "timed out")
                raise ToolError(f"Upstream timeout for operation {oid}.") from exc
            except httpx.HTTPError as exc:
                _failure_recorded = True
                obs_fail(
                    breaker, obs, oid, "http_error", span, exc, "encountered an HTTP client error"
                )
                resp = getattr(exc, "response", None)
                status = resp.status_code if resp is not None else "unknown"
                raise ToolError(f"Upstream request failed for {oid} with status {status}.") from exc
            except ToolError:
                # check_protocol_errors already records failure for HTTP paths;
                # only record for native dispatch (SQL/gRPC/stream) ToolErrors.
                if not _failure_recorded and not _reached_http_path:
                    breaker.record_failure()
                    obs.record_tool_call(oid, "error")
                    obs.set_circuit_breaker_state(oid, breaker.is_open)
                raise
            else:
                obs_success(breaker, obs, oid)
                obs.logger.info(
                    "runtime tool invocation completed",
                    extra={
                        "extra_fields": {
                            "operation_id": oid,
                            "status_code": response.status_code,
                            "truncated": truncated,
                        }
                    },
                )
                return {
                    "status": "ok",
                    "operation_id": oid,
                    "upstream_status": response.status_code,
                    "result": result,
                    "truncated": truncated,
                }
            finally:
                elapsed_seconds = perf_counter() - start
                obs.record_latency(oid, elapsed_seconds)
                # SLA breach check
                if operation.sla and operation.sla.latency_budget_ms:
                    obs.check_sla(oid, elapsed_seconds, operation.sla.latency_budget_ms)

    # -- native/streaming dispatch ---------------------------------------

    async def _dispatch_native(self, op: Operation, args: dict[str, Any]) -> dict[str, Any] | None:
        obs, oid = self._observability, op.id
        if op.sql is not None:
            r = await self._perform_sql(op, args)
            obs.logger.info(
                "runtime sql tool invocation completed",
                extra={"extra_fields": {"operation_id": oid}},
            )
            return {"status": "ok", "operation_id": oid, "result": r, "truncated": False}
        if op.grpc_unary is not None:
            r = await self._perform_grpc_unary(op, args)
            obs.logger.info(
                "runtime grpc unary tool invocation completed",
                extra={"extra_fields": {"operation_id": oid}},
            )
            return {"status": "ok", "operation_id": oid, "result": r, "truncated": False}
        if op.cli is not None:
            r = await self._perform_cli(op, args)
            obs.logger.info(
                "runtime cli tool invocation completed",
                extra={"extra_fields": {"operation_id": oid}},
            )
            return {"status": "ok", "operation_id": oid, "result": r, "truncated": False}
        ed = self._find_event_descriptor_for_operation(op)
        if ed is not None and ed.support is not EventSupportLevel.unsupported:
            if ed.transport in _BROKER_TRANSPORTS:
                r = await self._perform_event_bridge(op, args, ed)
                obs.logger.info(
                    "runtime event bridge invocation completed",
                    extra={
                        "extra_fields": {
                            "operation_id": oid,
                            "transport": ed.transport.value,
                        }
                    },
                )
                return {
                    "status": "ok",
                    "operation_id": oid,
                    "transport": ed.transport.value,
                    "result": r,
                    "truncated": False,
                }
            if ed.transport in _WEBHOOK_TRANSPORTS:
                r = self._perform_webhook(op, args, ed)
                obs.logger.info(
                    "runtime webhook invocation completed",
                    extra={"extra_fields": {"operation_id": oid}},
                )
                return {
                    "status": "ok",
                    "operation_id": oid,
                    "transport": ed.transport.value,
                    "result": r,
                    "truncated": False,
                }
        sd = self._stream_descriptor_for_operation(op)
        if sd is not None:
            sr = await self._perform_stream_session(op, args, sd)
            obs.logger.info(
                "runtime streaming tool invocation completed",
                extra={
                    "extra_fields": {
                        "operation_id": oid,
                        "transport": sd.transport.value,
                        "termination_reason": sr["lifecycle"]["termination_reason"],
                    }
                },
            )
            return {
                "status": "ok",
                "operation_id": oid,
                "transport": sd.transport.value,
                "result": sr,
                "truncated": False,
            }
        return None

    async def _perform_cli(self, op: Operation, args: dict[str, Any]) -> dict[str, Any]:
        from apps.mcp_runtime.cli_executor import execute_cli_tool

        if op.cli is None:
            raise ToolError(f"Operation {op.id} is missing cli metadata.")
        result = await execute_cli_tool(op.cli, args)
        exit_code = result.get("exit_code", -1)
        if exit_code != 0:
            stderr = result.get("stderr", "")
            raise ToolError(
                f"CLI command for operation {op.id} exited with code {exit_code}: {stderr}"
            )
        return result

    async def _perform_grpc_unary(self, op: Operation, args: dict[str, Any]) -> dict[str, Any]:
        if op.grpc_unary is None:
            raise ToolError(f"Operation {op.id} is missing grpc_unary metadata.")
        if self._grpc_unary_executor is None:
            raise ToolError(
                f"Native grpc unary transport for operation {op.id} "
                "requires a configured grpc unary executor."
            )
        r = await self._grpc_unary_executor.invoke(
            operation=op, arguments=args, config=op.grpc_unary
        )
        if not isinstance(r, dict):
            raise ToolError(
                f"Native grpc unary executor for operation {op.id} returned a non-dict result."
            )
        return r

    async def _perform_sql(self, op: Operation, args: dict[str, Any]) -> dict[str, Any]:
        if op.sql is None:
            raise ToolError(f"Operation {op.id} is missing sql metadata.")
        if self._sql_executor is None:
            raise ToolError(
                f"Native SQL transport for operation {op.id} requires a configured sql executor."
            )
        r = await self._sql_executor.invoke(operation=op, arguments=args, config=op.sql)
        if not isinstance(r, dict):
            raise ToolError(
                f"Native SQL executor for operation {op.id} returned a non-dict result."
            )
        return r

    def _find_event_descriptor_for_operation(self, op: Operation) -> EventDescriptor | None:
        for desc in self._service_ir.event_descriptors:
            if desc.operation_id == op.id:
                return desc
        return None

    async def _perform_event_bridge(
        self,
        op: Operation,
        args: dict[str, Any],
        descriptor: EventDescriptor,
    ) -> dict[str, Any]:
        if self._event_bridge_client is None:
            raise ToolError(
                f"Event bridge operation {op.id} requires a configured event bridge client."
            )
        topic = (
            descriptor.channel
            or (descriptor.event_bridge.topic if descriptor.event_bridge else None)
            or op.id
        )
        try:
            if descriptor.direction in (EventDirection.outbound, EventDirection.bidirectional):
                return await self._event_bridge_client.publish(topic, args)
            return {
                "messages": await self._event_bridge_client.observe(
                    topic,
                    max_messages=args.get("max_messages", 10),
                    timeout=args.get("timeout", 5.0),
                )
            }
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"Event bridge error for {op.id} on topic {topic}: {exc}") from exc

    def _perform_webhook(
        self,
        op: Operation,
        args: dict[str, Any],
        descriptor: EventDescriptor,
    ) -> dict[str, Any]:
        if self._webhook_adapter is None:
            raise ToolError(f"Webhook operation {op.id} requires a configured webhook adapter.")
        channel = descriptor.channel or op.id
        action = args.get("action", "observe")
        if action == "register":
            return self._webhook_adapter.register(channel, args.get("target_url"))
        if action == "deregister":
            return self._webhook_adapter.deregister(channel)
        raw_max = args.get("max_count", 10)
        max_count = max(1, min(int(raw_max) if isinstance(raw_max, int | float) else 10, 100))
        return {"payloads": self._webhook_adapter.get_payloads(channel, max_count=max_count)}

    async def _perform_request(self, op: Operation, args: dict[str, Any]) -> httpx.Response:
        return await perform_request(op, args, **self._ctx())

    # -- thin delegation wrappers ----------------------------------------

    def _stream_descriptor_for_operation(self, op: Operation) -> Any:
        return stream_descriptor_for_operation(op, self._service_ir.event_descriptors)

    async def _perform_stream_session(self, op: Operation, args: dict[str, Any], d: Any) -> Any:
        return await perform_stream_session(
            op, args, d, **self._ctx(), grpc_stream_executor=self._grpc_stream_executor
        )

    async def _consume_grpc_stream(self, op: Operation, args: dict[str, Any], d: Any) -> Any:
        return await consume_grpc_stream(
            op, args, d, grpc_stream_executor=self._grpc_stream_executor
        )

    async def _consume_sse_stream(self, op: Operation, args: dict[str, Any], d: Any) -> Any:
        import apps.mcp_runtime.proxy as _self_mod

        return await consume_sse_stream(
            op,
            args,
            d,
            **self._ctx(),
            collect_fn=_self_mod._collect_sse_events,
        )

    async def _consume_websocket_stream(self, op: Operation, args: dict[str, Any], d: Any) -> Any:
        import apps.mcp_runtime.proxy as _self_mod

        return await consume_websocket_stream(
            op,
            args,
            d,
            **self._ctx(),
            collect_fn=_self_mod._collect_websocket_messages,
            ws_connect_fn=_self_mod.websockets.connect,
        )

    def _prepare_websocket_session(
        self,
        op: Operation,
        args: dict[str, Any],
        *,
        path_argument_names: set[str],
    ) -> tuple[dict[str, Any], list[str | bytes]]:
        return prepare_websocket_session(op, args, path_argument_names=path_argument_names)

    def _resolve_url(self, path: str, args: dict[str, Any]) -> tuple[str, set[str]]:
        return resolve_url(path, args, self._service_ir)

    def _prepare_request_payload(
        self,
        op: Operation,
        args: dict[str, Any],
        *,
        path_argument_names: set[str],
    ) -> PreparedRequestPayload:
        return prepare_request_payload(
            op,
            args,
            path_argument_names=path_argument_names,
            service_ir=self._service_ir,
            split_query_and_body=self._split_query_and_body,
        )

    def _prepare_graphql_payload(self, op: Operation, remaining: dict[str, Any]) -> Any:
        return prepare_graphql_payload(op, remaining)

    def _prepare_soap_payload(self, op: Operation, remaining: dict[str, Any]) -> Any:
        return prepare_soap_payload(op, remaining)

    def _split_query_and_body(self, op: Operation, remaining: dict[str, Any]) -> Any:
        return split_query_and_body(op, remaining)

    def _select_body_argument_name(self, op: Operation, remaining: dict[str, Any]) -> str:
        return select_body_argument_name(op, remaining)

    def _build_request_kwargs(
        self,
        *,
        headers: dict[str, str],
        params: Any,
        payload: Any,
    ) -> dict[str, Any]:
        return build_request_kwargs(
            headers=headers, params=params, payload=payload, timeout=self._timeout
        )

    async def _build_auth(self, oid: str, **kw: Any) -> tuple[dict[str, str], dict[str, str]]:
        from apps.mcp_runtime.proxy_utils import build_auth

        return await build_auth(oid, **kw, **self._ctx())

    async def _build_primary_auth(self, oid: str) -> tuple[dict[str, str], dict[str, str]]:
        from apps.mcp_runtime.proxy_utils import build_primary_auth

        return await build_primary_auth(
            oid,
            auth=self._service_ir.auth,
            oauth_token_cache=self._oauth_token_cache,
            oauth_lock=self._oauth_lock,
            get_client=self._get_client,
            timeout=self._timeout,
        )

    async def _fetch_oauth2_access_token(self, oid: str, oauth2: Any) -> str:
        from apps.mcp_runtime.proxy_utils import fetch_oauth2_access_token

        return await fetch_oauth2_access_token(
            oid,
            oauth2,
            oauth_token_cache=self._oauth_token_cache,
            oauth_lock=self._oauth_lock,
            get_client=self._get_client,
            timeout=self._timeout,
        )

    async def _send_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None,
        payload: PreparedRequestPayload | None = None,
        follow_redirects: bool = False,
    ) -> httpx.Response:
        from apps.mcp_runtime.proxy_utils import send_request

        return await send_request(
            method,
            url,
            headers=headers,
            params=params,
            payload=payload,
            follow_redirects=follow_redirects,
            client=self._get_client(),
            timeout=self._timeout,
        )

    async def _poll_async_job(self, oid: str, response: httpx.Response, cfg: Any) -> Any:
        return await poll_async_job(oid, response, cfg, **self._ctx(), send_fn=self._send_request)

    # Backward-compatible static method aliases
    _prepare_jsonrpc_payload = staticmethod(prepare_jsonrpc_payload)
    _graphql_error_message = staticmethod(graphql_error_message)
    _odata_error_message = staticmethod(odata_error_message)
    _jsonrpc_error_message = staticmethod(jsonrpc_error_message)
    _scim_error_message = staticmethod(scim_error_message)
    _soap_fault_message = staticmethod(soap_fault_message)
    _resolve_secret_value = staticmethod(resolve_secret_value)
    _resolve_secret_ref = staticmethod(resolve_secret_ref_for_operation)
    _sanitize_response = staticmethod(sanitize_response)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = _get_client_impl(
                existing_client=None,
                service_ir=self._service_ir,
            )
        return self._client
