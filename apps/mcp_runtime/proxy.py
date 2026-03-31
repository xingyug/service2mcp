"""Upstream HTTP proxy for generic MCP runtime tools."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

import httpx
import websockets
from mcp.server.fastmcp.exceptions import ToolError

from apps.mcp_runtime.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from apps.mcp_runtime.observability import RuntimeObservability
from libs.ir.models import (
    AsyncJobConfig,
    AuthConfig,
    AuthType,
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    GrpcStreamRuntimeConfig,
    GrpcUnaryRuntimeConfig,
    JsonRpcOperationConfig,
    OAuth2ClientCredentialsConfig,
    Operation,
    RequestBodyMode,
    ResponseStrategy,
    ServiceIR,
    SoapOperationConfig,
    SqlOperationConfig,
    TruncationPolicy,
)
from libs.observability.tracing import trace_span
from libs.secret_refs import candidate_env_names, resolve_secret_ref

logger = logging.getLogger(__name__)

_PATH_PARAM_PATTERN = re.compile(r"{([^{}]+)}")
_WRITE_METHODS = {"POST", "PUT", "PATCH"}
_BODY_PARAM_NAMES = {"body", "payload", "data"}
_STREAM_MESSAGE_PARAM_NAMES = ("messages", "payload", "body", "data")
_TEXTUAL_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/x-www-form-urlencoded",
)
_SUPPORTED_STREAM_TRANSPORTS = {EventTransport.sse, EventTransport.websocket}
_SOAP_ENVELOPE_NS = "http://schemas.xmlsoap.org/soap/envelope/"
_DEFAULT_STREAM_IDLE_TIMEOUT_SECONDS = 15.0


class GrpcStreamExecutor(Protocol):
    """Dedicated native executor for grpc_stream event descriptors."""

    async def invoke(
        self,
        *,
        operation: Operation,
        arguments: dict[str, Any],
        descriptor: EventDescriptor,
        config: GrpcStreamRuntimeConfig,
    ) -> dict[str, Any]:
        """Execute a native grpc_stream tool invocation."""
        ...


class GrpcUnaryExecutor(Protocol):
    """Dedicated native executor for grpc unary operations."""

    async def invoke(
        self,
        *,
        operation: Operation,
        arguments: dict[str, Any],
        config: GrpcUnaryRuntimeConfig,
    ) -> dict[str, Any]:
        """Execute a native grpc unary tool invocation."""
        ...


class SqlExecutor(Protocol):
    """Dedicated native executor for reflected SQL operations."""

    async def invoke(
        self,
        *,
        operation: Operation,
        arguments: dict[str, Any],
        config: SqlOperationConfig,
    ) -> dict[str, Any]:
        """Execute a native SQL tool invocation."""
        ...


@dataclass(slots=True)
class PreparedRequestPayload:
    """Normalized request payload emitted from IR tool arguments."""

    query_params: dict[str, Any]
    json_body: dict[str, Any] | list[Any] | None = None
    form_data: dict[str, str] | None = None
    files: dict[str, tuple[str, bytes, str | None]] | None = None
    raw_body: bytes | str | None = None
    content_type: str | None = None
    signable_body: Any | None = None


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
        self._owns_client = client is None
        self._failure_threshold = failure_threshold
        self.breakers: dict[str, CircuitBreaker] = {}
        self._oauth_token_cache: dict[str, tuple[str, float | None]] = {}
        self._oauth_lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
        sql_close = getattr(self._sql_executor, "aclose", None)
        if callable(sql_close):
            await sql_close()  # pyright: ignore[reportGeneralTypeIssues]

    async def invoke(self, operation: Operation, arguments: dict[str, Any]) -> dict[str, Any]:
        start_time = perf_counter()
        breaker = self.breakers.setdefault(
            operation.id,
            CircuitBreaker(operation_id=operation.id, failure_threshold=self._failure_threshold),
        )
        attributes = {
            "tool.operation_id": operation.id,
            "tool.method": operation.method or "",
            "tool.path": operation.path or "",
        }

        with trace_span("mcp_runtime.tool_call", attributes=attributes) as span:
            self._observability.logger.info(
                "runtime tool invocation started",
                extra={
                    "extra_fields": {
                        "operation_id": operation.id,
                        "method": operation.method,
                        "path": operation.path,
                    }
                },
            )
            try:
                breaker.before_request()
            except CircuitBreakerOpenError as exc:
                self._observability.record_tool_call(operation.id, "error")
                self._observability.set_circuit_breaker_state(operation.id, True)
                span.record_exception(exc)
                self._observability.logger.warning(
                    "runtime tool invocation blocked by circuit breaker",
                    extra={"extra_fields": {"operation_id": operation.id}},
                )
                raise ToolError(str(exc)) from exc

            _failure_recorded = False
            try:
                if operation.sql is not None:
                    sql_result = await self._perform_sql(operation, arguments)
                    breaker.record_success()
                    self._observability.record_tool_call(operation.id, "success")
                    self._observability.set_circuit_breaker_state(operation.id, False)
                    self._observability.logger.info(
                        "runtime sql tool invocation completed",
                        extra={"extra_fields": {"operation_id": operation.id}},
                    )
                    return {
                        "status": "ok",
                        "operation_id": operation.id,
                        "result": sql_result,
                        "truncated": False,
                    }

                if operation.grpc_unary is not None:
                    unary_result = await self._perform_grpc_unary(operation, arguments)
                    breaker.record_success()
                    self._observability.record_tool_call(operation.id, "success")
                    self._observability.set_circuit_breaker_state(operation.id, False)
                    self._observability.logger.info(
                        "runtime grpc unary tool invocation completed",
                        extra={"extra_fields": {"operation_id": operation.id}},
                    )
                    return {
                        "status": "ok",
                        "operation_id": operation.id,
                        "result": unary_result,
                        "truncated": False,
                    }

                stream_descriptor = self._stream_descriptor_for_operation(operation)
                if stream_descriptor is not None:
                    stream_result = await self._perform_stream_session(
                        operation,
                        arguments,
                        stream_descriptor,
                    )
                    breaker.record_success()
                    self._observability.record_tool_call(operation.id, "success")
                    self._observability.set_circuit_breaker_state(operation.id, False)
                    self._observability.logger.info(
                        "runtime streaming tool invocation completed",
                        extra={
                            "extra_fields": {
                                "operation_id": operation.id,
                                "transport": stream_descriptor.transport.value,
                                "termination_reason": stream_result["lifecycle"][
                                    "termination_reason"
                                ],
                            }
                        },
                    )
                    return {
                        "status": "ok",
                        "operation_id": operation.id,
                        "transport": stream_descriptor.transport.value,
                        "result": stream_result,
                        "truncated": False,
                    }

                response = await self._perform_request(operation, arguments)
                soap_fault = self._soap_fault_message(response, operation)
                if soap_fault is not None:
                    _failure_recorded = True
                    breaker.record_failure()
                    self._observability.record_tool_call(operation.id, "error")
                    self._observability.record_upstream_error(operation.id, "soap_fault")
                    self._observability.set_circuit_breaker_state(operation.id, breaker.is_open)
                    tool_error = ToolError(soap_fault)
                    span.record_exception(tool_error)
                    self._observability.logger.warning(
                        "runtime soap tool invocation failed",
                        extra={"extra_fields": {"operation_id": operation.id}},
                    )
                    raise tool_error

                if response.is_error:
                    _failure_recorded = True
                    breaker.record_failure()
                    self._observability.record_tool_call(operation.id, "error")
                    self._observability.record_upstream_error(operation.id, "upstream_status")
                    self._observability.set_circuit_breaker_state(operation.id, breaker.is_open)
                    error_message = (
                        f"Upstream request failed for {operation.id} "
                        f"with status {response.status_code}."
                    )
                    tool_error = ToolError(error_message)
                    span.record_exception(tool_error)
                    self._observability.logger.warning(
                        "runtime tool invocation failed",
                        extra={
                            "extra_fields": {
                                "operation_id": operation.id,
                                "status_code": response.status_code,
                            }
                        },
                    )
                    raise tool_error

                graphql_error = self._graphql_error_message(response, operation)
                if graphql_error is not None:
                    _failure_recorded = True
                    breaker.record_failure()
                    self._observability.record_tool_call(operation.id, "error")
                    self._observability.record_upstream_error(operation.id, "graphql_error")
                    self._observability.set_circuit_breaker_state(operation.id, breaker.is_open)
                    tool_error = ToolError(graphql_error)
                    span.record_exception(tool_error)
                    self._observability.logger.warning(
                        "runtime graphql tool invocation failed",
                        extra={"extra_fields": {"operation_id": operation.id}},
                    )
                    raise tool_error

                odata_error = self._odata_error_message(
                    response,
                    self._service_ir.protocol,
                )
                if odata_error is not None:
                    _failure_recorded = True
                    breaker.record_failure()
                    self._observability.record_tool_call(operation.id, "error")
                    self._observability.record_upstream_error(operation.id, "odata_error")
                    self._observability.set_circuit_breaker_state(operation.id, breaker.is_open)
                    tool_error = ToolError(odata_error)
                    span.record_exception(tool_error)
                    self._observability.logger.warning(
                        "runtime odata tool invocation failed",
                        extra={"extra_fields": {"operation_id": operation.id}},
                    )
                    raise tool_error

                jsonrpc_error = self._jsonrpc_error_message(response, operation)
                if jsonrpc_error is not None:
                    _failure_recorded = True
                    breaker.record_failure()
                    self._observability.record_tool_call(operation.id, "error")
                    self._observability.record_upstream_error(operation.id, "jsonrpc_error")
                    self._observability.set_circuit_breaker_state(operation.id, breaker.is_open)
                    tool_error = ToolError(jsonrpc_error)
                    span.record_exception(tool_error)
                    self._observability.logger.warning(
                        "runtime jsonrpc tool invocation failed",
                        extra={"extra_fields": {"operation_id": operation.id}},
                    )
                    raise tool_error

                scim_error = self._scim_error_message(
                    response,
                    self._service_ir.protocol,
                )
                if scim_error is not None:
                    _failure_recorded = True
                    breaker.record_failure()
                    self._observability.record_tool_call(operation.id, "error")
                    self._observability.record_upstream_error(operation.id, "scim_error")
                    self._observability.set_circuit_breaker_state(operation.id, breaker.is_open)
                    tool_error = ToolError(scim_error)
                    span.record_exception(tool_error)
                    self._observability.logger.warning(
                        "runtime scim tool invocation failed",
                        extra={"extra_fields": {"operation_id": operation.id}},
                    )
                    raise tool_error

                result, truncated = self._sanitize_response(
                    response,
                    operation,
                    protocol=self._service_ir.protocol,
                )
            except httpx.TimeoutException as exc:
                _failure_recorded = True
                breaker.record_failure()
                self._observability.record_tool_call(operation.id, "error")
                self._observability.record_upstream_error(operation.id, "timeout")
                self._observability.set_circuit_breaker_state(operation.id, breaker.is_open)
                span.record_exception(exc)
                self._observability.logger.warning(
                    "runtime tool invocation timed out",
                    extra={"extra_fields": {"operation_id": operation.id}},
                )
                raise ToolError(f"Upstream timeout for operation {operation.id}.") from exc
            except httpx.HTTPError as exc:
                _failure_recorded = True
                breaker.record_failure()
                self._observability.record_tool_call(operation.id, "error")
                self._observability.record_upstream_error(operation.id, "http_error")
                self._observability.set_circuit_breaker_state(operation.id, breaker.is_open)
                span.record_exception(exc)
                self._observability.logger.warning(
                    "runtime tool invocation encountered an HTTP client error",
                    extra={"extra_fields": {"operation_id": operation.id}},
                )
                raise ToolError(f"Upstream request failed for {operation.id}: {exc}") from exc
            except ToolError:
                # ToolErrors from the HTTP response path (soap_fault, is_error,
                # graphql_error, timeout, httpx.HTTPError) already call
                # breaker.record_failure() before raising.
                # ToolErrors from SQL/gRPC/stream paths need failure accounting.
                if not _failure_recorded:
                    breaker.record_failure()
                raise
            else:
                breaker.record_success()
                self._observability.record_tool_call(operation.id, "success")
                self._observability.set_circuit_breaker_state(operation.id, False)
                self._observability.logger.info(
                    "runtime tool invocation completed",
                    extra={
                        "extra_fields": {
                            "operation_id": operation.id,
                            "status_code": response.status_code,
                            "truncated": truncated,
                        }
                    },
                )
                return {
                    "status": "ok",
                    "operation_id": operation.id,
                    "upstream_status": response.status_code,
                    "result": result,
                    "truncated": truncated,
                }
            finally:
                self._observability.record_latency(operation.id, perf_counter() - start_time)

    async def _perform_grpc_unary(
        self,
        operation: Operation,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if operation.grpc_unary is None:
            raise ToolError(f"Operation {operation.id} is missing grpc_unary metadata.")
        if self._grpc_unary_executor is None:
            raise ToolError(
                f"Native grpc unary transport for operation {operation.id} requires "
                "a configured grpc unary executor."
            )
        result = await self._grpc_unary_executor.invoke(
            operation=operation,
            arguments=arguments,
            config=operation.grpc_unary,
        )
        if not isinstance(result, dict):
            raise ToolError(
                f"Native grpc unary executor for operation {operation.id} returned "
                "a non-dict result."
            )
        return result

    async def _perform_sql(
        self,
        operation: Operation,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if operation.sql is None:
            raise ToolError(f"Operation {operation.id} is missing sql metadata.")
        if self._sql_executor is None:
            raise ToolError(
                f"Native SQL transport for operation {operation.id} requires "
                "a configured sql executor."
            )
        result = await self._sql_executor.invoke(
            operation=operation,
            arguments=arguments,
            config=operation.sql,
        )
        if not isinstance(result, dict):
            raise ToolError(
                f"Native SQL executor for operation {operation.id} returned a non-dict result."
            )
        return result

    async def _perform_request(
        self,
        operation: Operation,
        arguments: dict[str, Any],
    ) -> httpx.Response:
        if not operation.method or not operation.path:
            raise ToolError(f"Operation {operation.id} is missing method or path metadata.")

        url, path_arguments = self._resolve_url(operation.path, arguments)
        payload = self._prepare_request_payload(
            operation,
            arguments,
            path_argument_names=path_arguments,
        )
        headers, auth_query_params = await self._build_auth(
            operation.id,
            method=operation.method.upper(),
            url=url,
            query_params=payload.query_params,
            body_for_signing=payload.signable_body,
        )
        if operation.soap is not None:
            headers.setdefault("Accept", "text/xml, application/xml")
            if operation.soap.soap_action:
                headers.setdefault("SOAPAction", f'"{operation.soap.soap_action}"')
        query_params = dict(payload.query_params)
        query_params.update(auth_query_params)

        response = await self._send_request(
            operation.method.upper(),
            url,
            headers=headers,
            params=query_params or None,
            payload=payload,
        )
        if operation.async_job is not None:
            return await self._poll_async_job(operation.id, response, operation.async_job)
        return response

    def _stream_descriptor_for_operation(self, operation: Operation) -> EventDescriptor | None:
        descriptors = [
            descriptor
            for descriptor in self._service_ir.event_descriptors
            if descriptor.operation_id == operation.id
        ]
        if not descriptors:
            return None
        supported_descriptors = [
            descriptor
            for descriptor in descriptors
            if descriptor.support is EventSupportLevel.supported
        ]
        if len(supported_descriptors) > 1:
            raise ToolError(
                f"Operation {operation.id} has multiple streaming descriptors and "
                "cannot be invoked unambiguously."
            )
        if not supported_descriptors:
            declared_transports = ", ".join(
                sorted({descriptor.transport.value for descriptor in descriptors})
            )
            raise ToolError(
                f"Streaming transport(s) {declared_transports} for operation "
                f"{operation.id} are declared but not enabled."
            )
        descriptor = supported_descriptors[0]
        if descriptor.support is not EventSupportLevel.supported:
            raise ToolError(
                f"Streaming transport {descriptor.transport.value} for operation "
                f"{operation.id} is declared but not enabled."
            )
        if descriptor.transport is EventTransport.grpc_stream:
            if descriptor.grpc_stream is None:
                raise ToolError(
                    f"Native grpc_stream transport for operation {operation.id} is "
                    "missing grpc_stream runtime configuration."
                )
            return descriptor
        if descriptor.transport not in _SUPPORTED_STREAM_TRANSPORTS:
            raise ToolError(
                f"Streaming transport {descriptor.transport.value} for operation "
                f"{operation.id} is not supported by the runtime."
            )
        return descriptor

    async def _perform_stream_session(
        self,
        operation: Operation,
        arguments: dict[str, Any],
        descriptor: EventDescriptor,
    ) -> dict[str, Any]:
        if descriptor.transport is EventTransport.sse:
            return await self._consume_sse_stream(operation, arguments, descriptor)
        if descriptor.transport is EventTransport.websocket:
            return await self._consume_websocket_stream(operation, arguments, descriptor)
        if descriptor.transport is EventTransport.grpc_stream:
            return await self._consume_grpc_stream(operation, arguments, descriptor)
        raise ToolError(
            f"Streaming transport {descriptor.transport.value} is not supported by the runtime."
        )

    async def _consume_grpc_stream(
        self,
        operation: Operation,
        arguments: dict[str, Any],
        descriptor: EventDescriptor,
    ) -> dict[str, Any]:
        if descriptor.grpc_stream is None:
            raise ToolError(
                f"Native grpc_stream transport for operation {operation.id} is "
                "missing grpc_stream runtime configuration."
            )
        if self._grpc_stream_executor is None:
            raise ToolError(
                f"Native grpc_stream transport for operation {operation.id} requires "
                "a configured grpc stream executor."
            )
        result = await self._grpc_stream_executor.invoke(
            operation=operation,
            arguments=arguments,
            descriptor=descriptor,
            config=descriptor.grpc_stream,
        )
        if not isinstance(result, dict):
            raise ToolError(
                f"Native grpc_stream executor for operation {operation.id} returned "
                "a non-dict result."
            )
        return result

    def _resolve_url(self, raw_path: str, arguments: dict[str, Any]) -> tuple[str, set[str]]:
        path_argument_names = set(_PATH_PARAM_PATTERN.findall(raw_path))
        resolved_path = raw_path
        for path_argument in path_argument_names:
            value = arguments.get(path_argument)
            if value is None:
                raise ToolError(f"Missing path parameter {path_argument}.")
            resolved_path = resolved_path.replace(
                f"{{{path_argument}}}",
                quote(str(value), safe=""),
            )

        original_base_url = self._service_ir.base_url
        base_parts = urlsplit(original_base_url)
        resolved_parts = urlsplit(resolved_path if resolved_path else "/")
        if resolved_parts.path in {"", "/"}:
            return (
                urlunsplit(
                    (
                        base_parts.scheme,
                        base_parts.netloc,
                        base_parts.path or "/",
                        resolved_parts.query or base_parts.query,
                        resolved_parts.fragment,
                    )
                ),
                path_argument_names,
            )

        base_path = (base_parts.path or "").rstrip("/")
        path_suffix = (
            resolved_parts.path
            if resolved_parts.path.startswith("/")
            else f"/{resolved_parts.path}"
        )
        if (
            base_parts.path == path_suffix
            and not resolved_parts.query
            and not resolved_parts.fragment
        ):
            return original_base_url, path_argument_names
        return (
            urlunsplit(
                (
                    base_parts.scheme,
                    base_parts.netloc,
                    f"{base_path}{path_suffix}",
                    resolved_parts.query,
                    resolved_parts.fragment,
                )
            ),
            path_argument_names,
        )

    def _prepare_request_payload(
        self,
        operation: Operation,
        arguments: dict[str, Any],
        *,
        path_argument_names: set[str],
    ) -> PreparedRequestPayload:
        remaining = {
            key: value
            for key, value in arguments.items()
            if key not in path_argument_names and value is not None
        }
        if operation.soap is not None:
            return self._prepare_soap_payload(operation, remaining)
        if operation.graphql is not None:
            return self._prepare_graphql_payload(operation, remaining)
        if operation.jsonrpc is not None:
            return self._prepare_jsonrpc_payload(operation.jsonrpc, remaining)
        if self._service_ir.protocol == "odata":
            return self._prepare_odata_payload(operation, remaining)

        if not remaining:
            return PreparedRequestPayload(query_params={})

        if operation.request_body_mode == RequestBodyMode.multipart:
            body_key = self._select_body_argument_name(operation, remaining)
            body_value = remaining[body_key]
            query_params = {key: value for key, value in remaining.items() if key != body_key}
            form_data, files, signable_body = _build_multipart_request_body(
                operation.id,
                body_value,
            )
            return PreparedRequestPayload(
                query_params=query_params,
                form_data=form_data or None,
                files=files or None,
                signable_body=signable_body,
            )

        if operation.request_body_mode == RequestBodyMode.raw:
            body_key = self._select_body_argument_name(operation, remaining)
            body_value = remaining[body_key]
            query_params = {key: value for key, value in remaining.items() if key != body_key}
            raw_body, content_type, signable_body = _build_raw_request_body(
                operation.id,
                body_value,
            )
            return PreparedRequestPayload(
                query_params=query_params,
                raw_body=raw_body,
                content_type=content_type,
                signable_body=signable_body,
            )

        query_params, json_body = self._split_query_and_body(operation, remaining)
        return PreparedRequestPayload(
            query_params=query_params,
            json_body=json_body,
            signable_body=json_body,
        )

    def _prepare_graphql_payload(
        self,
        operation: Operation,
        remaining: dict[str, Any],
    ) -> PreparedRequestPayload:
        if operation.graphql is None:
            raise ToolError(f"Operation {operation.id} is missing GraphQL runtime metadata.")

        variable_names = (
            operation.graphql.variable_names
            if operation.graphql.variable_names
            else list(remaining.keys())
        )
        variables = {
            variable_name: remaining[variable_name]
            for variable_name in variable_names
            if variable_name in remaining
        }
        json_body: dict[str, Any] = {
            "query": operation.graphql.document,
            "variables": variables,
        }
        if operation.graphql.operation_name is not None:
            json_body["operationName"] = operation.graphql.operation_name
        return PreparedRequestPayload(
            query_params={},
            json_body=json_body,
            signable_body=json_body,
        )

    def _prepare_soap_payload(
        self,
        operation: Operation,
        remaining: dict[str, Any],
    ) -> PreparedRequestPayload:
        if operation.soap is None:
            raise ToolError(f"Operation {operation.id} is missing SOAP runtime metadata.")

        envelope = _build_soap_envelope(operation.soap, remaining)
        return PreparedRequestPayload(
            query_params={},
            raw_body=envelope,
            content_type="text/xml; charset=utf-8",
            signable_body=envelope,
        )

    def _prepare_odata_payload(
        self,
        operation: Operation,
        remaining: dict[str, Any],
    ) -> PreparedRequestPayload:
        """Prepare request payload for OData v4 operations.

        FastMCP strips the ``$`` prefix from OData system query parameters
        (e.g. ``$filter`` → ``filter``).  This method re-adds the prefix so
        the upstream OData service receives the correct query option names.
        """
        dollar_params: set[str] = set()
        for param in operation.params:
            if param.name.startswith("$"):
                dollar_params.add(param.name[1:])

        odata_query: dict[str, Any] = {}
        non_odata: dict[str, Any] = {}
        for key, value in remaining.items():
            if key in dollar_params:
                odata_query[f"${key}"] = value
            else:
                non_odata[key] = value

        query_params, json_body = self._split_query_and_body(operation, non_odata)
        query_params.update(odata_query)
        return PreparedRequestPayload(
            query_params=query_params,
            json_body=json_body,
            signable_body=json_body,
        )

    @staticmethod
    def _prepare_jsonrpc_payload(
        config: JsonRpcOperationConfig,
        remaining: dict[str, Any],
    ) -> PreparedRequestPayload:
        """Wrap tool arguments in a JSON-RPC 2.0 request envelope."""
        if config.params_type == "positional":
            params: Any = [remaining.get(n) for n in config.params_names]
        else:
            params = {
                name: remaining[name] for name in config.params_names if name in remaining
            } or remaining
        json_body: dict[str, Any] = {
            "jsonrpc": config.jsonrpc_version,
            "method": config.method_name,
            "params": params,
            "id": 1,
        }
        return PreparedRequestPayload(
            query_params={},
            json_body=json_body,
            signable_body=json_body,
        )

    def _split_query_and_body(
        self,
        operation: Operation,
        remaining: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | list[Any] | None]:
        if not remaining:
            return {}, None

        if (operation.method or "").upper() in _WRITE_METHODS:
            if operation.body_param_name and operation.body_param_name in remaining:
                body = remaining[operation.body_param_name]
                query_params = {
                    key: value
                    for key, value in remaining.items()
                    if key != operation.body_param_name
                }
                return query_params, body
            if len(remaining) == 1:
                key, value = next(iter(remaining.items()))
                param = next((param for param in operation.params if param.name == key), None)
                is_object_like = param is not None and param.type in {"object", "array"}
                if key in _BODY_PARAM_NAMES or is_object_like:
                    return {}, value
            return {}, remaining

        return remaining, None

    def _select_body_argument_name(
        self,
        operation: Operation,
        remaining: dict[str, Any],
    ) -> str:
        if operation.body_param_name:
            if operation.body_param_name not in remaining:
                raise ToolError(
                    f"Operation {operation.id} expects body parameter "
                    f"{operation.body_param_name!r}."
                )
            return operation.body_param_name

        for candidate in _BODY_PARAM_NAMES:
            if candidate in remaining:
                return candidate

        object_like_keys = [
            param.name
            for param in operation.params
            if param.name in remaining and param.type in {"object", "array"}
        ]
        if len(object_like_keys) == 1:
            return object_like_keys[0]

        if len(remaining) == 1:
            return next(iter(remaining))

        raise ToolError(
            f"Operation {operation.id} could not determine which argument should be "
            f"used as the {operation.request_body_mode.value} request body."
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
        client = self._get_client()
        return await client.request(
            method,
            url,
            follow_redirects=follow_redirects,
            **self._build_request_kwargs(
                headers=headers,
                params=params,
                payload=payload,
            ),
        )

    def _build_request_kwargs(
        self,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None,
        payload: PreparedRequestPayload | None,
    ) -> dict[str, Any]:
        request_headers = dict(headers)
        request_kwargs: dict[str, Any] = {
            "headers": request_headers,
            "params": params,
            "timeout": self._timeout,
        }
        if payload is None:
            return request_kwargs

        if payload.content_type and "Content-Type" not in request_headers:
            request_headers["Content-Type"] = payload.content_type
        if payload.json_body is not None:
            request_kwargs["json"] = payload.json_body
        elif payload.files is not None:
            request_kwargs["data"] = payload.form_data
            request_kwargs["files"] = payload.files
        elif payload.raw_body is not None:
            request_kwargs["content"] = payload.raw_body
        elif payload.form_data is not None:
            request_kwargs["data"] = payload.form_data
        return request_kwargs

    async def _build_auth(
        self,
        operation_id: str,
        *,
        method: str,
        url: str,
        query_params: dict[str, Any],
        body_for_signing: Any | None,
    ) -> tuple[dict[str, str], dict[str, str]]:
        headers, query = await self._build_primary_auth(operation_id)
        signed_query = dict(query_params)
        signed_query.update(query)
        headers.update(
            self._build_request_signing(
                operation_id,
                method=method,
                url=url,
                query_params=signed_query,
                body_for_signing=body_for_signing,
            )
        )
        return headers, query

    async def _build_primary_auth(self, operation_id: str) -> tuple[dict[str, str], dict[str, str]]:
        auth = self._service_ir.auth
        if auth.type == AuthType.none:
            return {}, {}

        headers: dict[str, str] = {}
        query_params: dict[str, str] = {}

        if auth.type == AuthType.oauth2 and auth.oauth2 is not None:
            access_token = await self._fetch_oauth2_access_token(operation_id, auth.oauth2)
            header_name = auth.header_name or "Authorization"
            header_prefix = auth.header_prefix or "Bearer"
            headers[header_name] = f"{header_prefix} {access_token}".strip()
        elif auth.type in {AuthType.bearer, AuthType.oauth2}:
            secret = self._resolve_secret_value(auth, operation_id)
            header_name = auth.header_name or "Authorization"
            header_prefix = auth.header_prefix or "Bearer"
            headers[header_name] = f"{header_prefix} {secret}".strip()
        elif auth.type == AuthType.basic:
            if auth.basic_username and auth.basic_password_ref:
                password = self._resolve_secret_ref(
                    auth.basic_password_ref,
                    operation_id,
                    purpose="basic auth password",
                )
                secret = f"{auth.basic_username}:{password}"
            else:
                secret = self._resolve_secret_value(auth, operation_id)
            encoded = base64.b64encode(secret.encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {encoded}"
        elif auth.type == AuthType.api_key:
            secret = self._resolve_secret_value(auth, operation_id)
            api_key_name = auth.api_key_param or "api_key"
            if auth.api_key_location == "query":
                query_params[api_key_name] = secret
            else:
                headers[api_key_name] = secret
        elif auth.type == AuthType.custom_header:
            secret = self._resolve_secret_value(auth, operation_id)
            if not auth.header_name:
                raise ToolError(
                    f"Operation {operation_id} uses custom_header auth without header_name."
                )
            if auth.header_prefix:
                headers[auth.header_name] = f"{auth.header_prefix} {secret}".strip()
            else:
                headers[auth.header_name] = secret
        else:
            raise ToolError(f"Unsupported auth type {auth.type} for operation {operation_id}.")

        return headers, query_params

    async def _fetch_oauth2_access_token(
        self,
        operation_id: str,
        oauth2: OAuth2ClientCredentialsConfig,
    ) -> str:
        cache_key = "|".join(
            [
                oauth2.token_url,
                oauth2.client_id or oauth2.client_id_ref or "",
                ",".join(sorted(oauth2.scopes)),
                oauth2.audience or "",
            ]
        )
        cached = self._oauth_token_cache.get(cache_key)
        now = time.time()
        if cached is not None:
            token, expires_at = cached
            if expires_at is None or expires_at > now + 30:
                return token

        async with self._oauth_lock:
            # Re-check after acquiring lock (another coroutine may have refreshed)
            cached = self._oauth_token_cache.get(cache_key)
            now = time.time()
            if cached is not None:
                token, expires_at = cached
                if expires_at is None or expires_at > now + 30:
                    return token

            client_id = (
                oauth2.client_id
                if oauth2.client_id is not None
                else self._resolve_secret_ref(
                    oauth2.client_id_ref or "",
                    operation_id,
                    purpose="oauth2 client id",
                )
            )
            client_secret = self._resolve_secret_ref(
                oauth2.client_secret_ref,
                operation_id,
                purpose="oauth2 client secret",
            )
            form_payload: dict[str, str] = {"grant_type": "client_credentials"}
            if oauth2.scopes:
                form_payload["scope"] = " ".join(oauth2.scopes)
            if oauth2.audience:
                form_payload["audience"] = oauth2.audience

            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            if oauth2.client_auth_method == "client_secret_basic":
                encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")
                headers["Authorization"] = f"Basic {encoded}"
            else:
                form_payload["client_id"] = client_id
                form_payload["client_secret"] = client_secret

            response = await self._get_client().post(
                oauth2.token_url,
                content=urlencode(form_payload),
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                raise ToolError(
                    f"OAuth2 token endpoint returned non-JSON response for {operation_id}."
                ) from exc
            if not isinstance(payload, dict):
                raise ToolError(
                    f"OAuth2 token endpoint returned non-object JSON for {operation_id}."
                )
            access_token = payload.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise ToolError(
                    f"OAuth2 token endpoint did not return an access_token for {operation_id}."
                )

            expires_at = None
            expires_in = payload.get("expires_in")
            if isinstance(expires_in, int | float) and expires_in >= 0:
                expires_at = now + float(expires_in)
            self._oauth_token_cache[cache_key] = (access_token, expires_at)
            return access_token

    async def _poll_async_job(
        self,
        operation_id: str,
        response: httpx.Response,
        async_job: AsyncJobConfig,
    ) -> httpx.Response:
        if response.status_code not in async_job.initial_status_codes:
            return response

        try:
            status_url = _extract_async_status_url(async_job, response)
        except _InvalidJsonPayloadError as exc:
            raise ToolError(
                f"Async kickoff for operation {operation_id} received invalid JSON: {exc}"
            ) from exc
        if not status_url:
            raise ToolError(
                f"Async job operation {operation_id} did not provide a pollable status URL."
            )

        deadline = time.monotonic() + async_job.timeout_seconds
        pending_states = {value.lower() for value in async_job.pending_status_values}
        success_states = {value.lower() for value in async_job.success_status_values}
        failure_states = {value.lower() for value in async_job.failure_status_values}

        while True:
            if time.monotonic() > deadline:
                raise ToolError(f"Async job polling timed out for operation {operation_id}.")

            request_url, query_params = _split_url_query(status_url)
            headers, auth_query_params = await self._build_auth(
                operation_id,
                method="GET",
                url=request_url,
                query_params=query_params,
                body_for_signing=None,
            )
            query_params.update(auth_query_params)
            poll_response = await self._send_request(
                "GET",
                request_url,
                headers=headers,
                params=query_params or None,
                follow_redirects=True,
            )

            try:
                status_value = _extract_async_status_value(async_job, poll_response)
            except _InvalidJsonPayloadError as exc:
                raise ToolError(
                    f"Async poll for operation {operation_id} received invalid JSON: {exc}"
                ) from exc
            normalized_status = status_value.lower() if status_value is not None else None
            if normalized_status in success_states:
                return poll_response
            if normalized_status in failure_states:
                raise ToolError(
                    f"Async job polling failed for operation {operation_id} "
                    f"with terminal status {status_value!r}."
                )

            if (
                poll_response.status_code in async_job.initial_status_codes
                or normalized_status in pending_states
            ):
                await asyncio.sleep(async_job.poll_interval_seconds)
                continue

            return poll_response

    async def _consume_sse_stream(
        self,
        operation: Operation,
        arguments: dict[str, Any],
        descriptor: EventDescriptor,
    ) -> dict[str, Any]:
        if not operation.method or not operation.path:
            raise ToolError(f"Operation {operation.id} is missing method or path metadata.")

        url, path_arguments = self._resolve_url(operation.path, arguments)
        payload = self._prepare_request_payload(
            operation,
            arguments,
            path_argument_names=path_arguments,
        )
        if payload.files is not None:
            raise ToolError(
                f"SSE streaming does not support multipart payloads for operation {operation.id}."
            )

        headers, auth_query_params = await self._build_auth(
            operation.id,
            method=operation.method.upper(),
            url=url,
            query_params=payload.query_params,
            body_for_signing=payload.signable_body,
        )
        headers.setdefault("Accept", "text/event-stream")
        query_params = dict(payload.query_params)
        query_params.update(auth_query_params)
        request_kwargs = self._build_request_kwargs(
            headers=headers,
            params=query_params or None,
            payload=payload,
        )

        async with self._get_client().stream(
            operation.method.upper(),
            url,
            **request_kwargs,
        ) as response:
            if response.status_code >= 400:
                raise ToolError(
                    f"Upstream SSE request failed for {operation.id} "
                    f"with status {response.status_code}."
                )
            content_type = response.headers.get("content-type", "").lower()
            if "text/event-stream" not in content_type:
                raise ToolError(
                    f"Upstream SSE request for {operation.id} returned unsupported "
                    f"content type {content_type or '<missing>'}."
                )

            max_events = _descriptor_positive_int(descriptor, "max_events", default=10)
            idle_timeout = _descriptor_positive_float(
                descriptor,
                "idle_timeout_seconds",
                default=_DEFAULT_STREAM_IDLE_TIMEOUT_SECONDS,
            )
            events, termination_reason = await _collect_sse_events(
                response,
                max_events=max_events,
                idle_timeout_seconds=idle_timeout,
            )
            return {
                "transport": descriptor.transport.value,
                "upstream_status": response.status_code,
                "events": events,
                "lifecycle": {
                    "termination_reason": termination_reason,
                    "events_collected": len(events),
                    "max_events": max_events,
                    "idle_timeout_seconds": idle_timeout,
                },
            }

    async def _consume_websocket_stream(
        self,
        operation: Operation,
        arguments: dict[str, Any],
        descriptor: EventDescriptor,
    ) -> dict[str, Any]:
        if not operation.path:
            raise ToolError(f"Operation {operation.id} is missing path metadata.")

        url, path_arguments = self._resolve_url(operation.path, arguments)
        query_params, outbound_messages = self._prepare_websocket_session(
            operation,
            arguments,
            path_argument_names=path_arguments,
        )
        headers, auth_query_params = await self._build_auth(
            operation.id,
            method=(operation.method or "GET").upper(),
            url=url,
            query_params=query_params,
            body_for_signing=outbound_messages,
        )
        query_params.update(auth_query_params)

        max_messages = _descriptor_positive_int(descriptor, "max_messages", default=10)
        idle_timeout = _descriptor_positive_float(
            descriptor,
            "idle_timeout_seconds",
            default=_DEFAULT_STREAM_IDLE_TIMEOUT_SECONDS,
        )
        websocket_url = _to_websocket_url(url, query_params)

        try:
            async with websockets.connect(
                websocket_url,
                additional_headers=headers or None,
                open_timeout=self._timeout,
                close_timeout=self._timeout,
                max_queue=max_messages,
                write_limit=32768,
            ) as websocket:
                for message in outbound_messages:
                    await websocket.send(message)
                events, termination_reason = await _collect_websocket_messages(
                    websocket,
                    max_messages=max_messages,
                    idle_timeout_seconds=idle_timeout,
                )
        except websockets.exceptions.WebSocketException as ws_exc:
            raise ToolError(f"WebSocket communication failed: {ws_exc}") from ws_exc

        return {
            "transport": descriptor.transport.value,
            "events": events,
            "lifecycle": {
                "termination_reason": termination_reason,
                "events_collected": len(events),
                "max_messages": max_messages,
                "idle_timeout_seconds": idle_timeout,
                "messages_sent": len(outbound_messages),
            },
        }

    def _prepare_websocket_session(
        self,
        operation: Operation,
        arguments: dict[str, Any],
        *,
        path_argument_names: set[str],
    ) -> tuple[dict[str, Any], list[str | bytes]]:
        remaining = {
            key: value
            for key, value in arguments.items()
            if key not in path_argument_names and value is not None
        }
        if not remaining:
            return {}, []

        body_param_name = operation.body_param_name
        body_value: Any | None = None
        if body_param_name and body_param_name in remaining:
            body_value = remaining.pop(body_param_name)
        else:
            for candidate in _STREAM_MESSAGE_PARAM_NAMES:
                if candidate in remaining:
                    body_value = remaining.pop(candidate)
                    break

        return remaining, _normalize_websocket_messages(operation.id, body_value)

    def _build_request_signing(
        self,
        operation_id: str,
        *,
        method: str,
        url: str,
        query_params: dict[str, Any],
        body_for_signing: Any | None,
    ) -> dict[str, str]:
        signing = self._service_ir.auth.request_signing
        if signing is None:
            return {}

        secret = self._resolve_secret_ref(
            signing.secret_ref,
            operation_id,
            purpose="request signing secret",
        )
        timestamp = str(int(time.time()))
        signature = hmac.new(
            secret.encode("utf-8"),
            _build_signing_payload(
                method=method,
                url=url,
                query_params=query_params,
                body_for_signing=body_for_signing,
                timestamp=timestamp,
            ).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        headers = {
            signing.signature_header_name: signature,
            signing.timestamp_header_name: timestamp,
        }
        if signing.key_id and signing.key_id_header_name:
            headers[signing.key_id_header_name] = signing.key_id
        return headers

    @staticmethod
    def _resolve_secret_value(auth: AuthConfig, operation_id: str) -> str:
        if not auth.runtime_secret_ref:
            raise ToolError(
                f"Operation {operation_id} requires auth but runtime_secret_ref is not configured."
            )

        return RuntimeProxy._resolve_secret_ref(
            auth.runtime_secret_ref,
            operation_id,
            purpose="runtime auth secret",
        )

    @staticmethod
    def _resolve_secret_ref(secret_ref: str, operation_id: str, *, purpose: str) -> str:
        try:
            return resolve_secret_ref(
                secret_ref,
                purpose=purpose,
                context=f"operation {operation_id}",
            )
        except LookupError as exc:
            raise ToolError(str(exc)) from exc

    @staticmethod
    def _graphql_error_message(
        response: httpx.Response,
        operation: Operation,
    ) -> str | None:
        if operation.graphql is None:
            return None

        payload = _parse_response_payload(response)
        if not isinstance(payload, dict):
            return f"GraphQL operation {operation.id} returned a non-object response body."

        raw_errors = payload.get("errors")
        if not isinstance(raw_errors, list) or not raw_errors:
            return None

        messages = [
            error.get("message", "unknown GraphQL error") if isinstance(error, dict) else str(error)
            for error in raw_errors
        ]
        return f"GraphQL operation {operation.id} failed: {'; '.join(messages)}"

    @staticmethod
    def _odata_error_message(
        response: httpx.Response,
        protocol: str,
    ) -> str | None:
        """Extract an OData JSON error message if present."""
        if protocol != "odata":
            return None
        payload = _parse_response_payload(response)
        if not isinstance(payload, dict):
            return None
        if "error" not in payload:
            return None
        error = payload.get("error")
        if not isinstance(error, dict):
            return "OData response returned a malformed error envelope."
        code = error.get("code", "")
        message = error.get("message", "OData error")
        if isinstance(message, dict):
            message = message.get("value", str(message))
        return f"OData error ({code}): {message}" if code else f"OData error: {message}"

    @staticmethod
    def _jsonrpc_error_message(
        response: httpx.Response,
        operation: Operation,
    ) -> str | None:
        """Extract a JSON-RPC 2.0 error message if present."""
        if operation.jsonrpc is None:
            return None
        payload = _parse_response_payload(response)
        if not isinstance(payload, dict):
            return None
        if "error" not in payload:
            return None
        error = payload.get("error")
        if not isinstance(error, dict):
            return f"JSON-RPC operation {operation.id} returned a malformed error envelope."
        code = error.get("code", "")
        message = error.get("message", "JSON-RPC error")
        return f"JSON-RPC error ({code}): {message}" if code else f"JSON-RPC error: {message}"

    @staticmethod
    def _scim_error_message(
        response: httpx.Response,
        protocol: str,
    ) -> str | None:
        """Extract a SCIM 2.0 error message if present."""
        if protocol != "scim":
            return None
        payload = _parse_response_payload(response)
        if not isinstance(payload, dict):
            return None
        schemas = payload.get("schemas")
        if not isinstance(schemas, list):
            if schemas is not None and (
                isinstance(schemas, str)
                or "detail" in payload
                or "status" in payload
                or "scimType" in payload
            ):
                return "SCIM response returned a malformed error envelope."
            return None
        if not any("Error" in s for s in schemas if isinstance(s, str)):
            return None
        detail = payload.get("detail", "SCIM error")
        status = payload.get("status", "")
        scim_type = payload.get("scimType", "")
        parts = ["SCIM error"]
        if status:
            parts[0] = f"SCIM error (status {status})"
        if scim_type:
            parts.append(scim_type)
        parts.append(str(detail))
        return ": ".join(parts)

    @staticmethod
    def _soap_fault_message(
        response: httpx.Response,
        operation: Operation,
    ) -> str | None:
        if operation.soap is None:
            return None

        try:
            root = ET.fromstring(response.text)
        except ET.ParseError:
            return None

        body = _soap_body_element(root)
        if body is None:
            return None

        fault = next(
            (child for child in body if _xml_local_name(child.tag) == "Fault"),
            None,
        )
        if fault is None:
            return None

        for tag_name in ("faultstring", "Text"):
            node = next(
                (item for item in fault.iter() if _xml_local_name(item.tag) == tag_name),
                None,
            )
            if node is not None and (node.text or "").strip():
                detail = (node.text or "").strip()
                return f"SOAP operation {operation.id} failed: {detail}"

        detail = _xml_element_to_value(fault)
        return f"SOAP operation {operation.id} failed: {detail}"

    @staticmethod
    def _sanitize_response(
        response: httpx.Response,
        operation: Operation,
        *,
        protocol: str = "",
    ) -> tuple[Any, bool]:
        payload = _parse_response_payload(response)
        if operation.soap is not None:
            payload = _unwrap_soap_payload(response, operation)
        if operation.graphql is not None:
            payload = _unwrap_graphql_payload(payload, operation)
        if protocol == "odata":
            payload = _unwrap_odata_payload(payload)
        if protocol == "scim":
            payload = _unwrap_scim_payload(payload)
        if operation.jsonrpc is not None:
            payload = _unwrap_jsonrpc_payload(payload, operation)
        payload = _apply_field_filter(payload, operation.response_strategy.field_filter)
        payload = _apply_array_limit(payload, operation.response_strategy.max_array_items)
        return _apply_truncation(payload, operation.response_strategy)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            client_kwargs: dict[str, Any] = {}
            mtls = self._service_ir.auth.mtls
            if mtls is not None:
                client_kwargs["cert"] = (
                    self._resolve_secret_ref(
                        mtls.cert_ref,
                        "__runtime__",
                        purpose="mTLS client certificate",
                    ),
                    self._resolve_secret_ref(
                        mtls.key_ref,
                        "__runtime__",
                        purpose="mTLS client key",
                    ),
                )
                if mtls.ca_ref:
                    client_kwargs["verify"] = self._resolve_secret_ref(
                        mtls.ca_ref,
                        "__runtime__",
                        purpose="mTLS CA bundle",
                    )
            self._client = httpx.AsyncClient(**client_kwargs)
        return self._client


def _descriptor_positive_int(descriptor: EventDescriptor, key: str, *, default: int) -> int:
    value = descriptor.metadata.get(key, default)
    if isinstance(value, int) and value > 0:
        return value
    return default


def _descriptor_positive_float(
    descriptor: EventDescriptor,
    key: str,
    *,
    default: float,
) -> float:
    value = descriptor.metadata.get(key, default)
    if isinstance(value, int | float) and float(value) > 0:
        return float(value)
    return default


async def _collect_sse_events(
    response: httpx.Response,
    *,
    max_events: int,
    idle_timeout_seconds: float,
) -> tuple[list[dict[str, Any]], str]:
    lines = response.aiter_lines()
    event_type = "message"
    data_lines: list[str] = []
    event_id: str | None = None
    events: list[dict[str, Any]] = []

    while len(events) < max_events:
        try:
            line = await asyncio.wait_for(anext(lines), timeout=idle_timeout_seconds)
        except StopAsyncIteration:
            break
        except TimeoutError:
            return events, "idle_timeout"
        except (httpx.ReadError, httpx.RemoteProtocolError) as exc:
            logger.warning("SSE connection error: %s", exc)
            return events, "connection_error"

        if line == "":
            event = _build_sse_event(event_type, data_lines, event_id)
            if event is not None:
                events.append(event)
                if len(events) >= max_events:
                    return events, "max_events"
            event_type = "message"
            data_lines = []
            event_id = None
            continue

        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            raw = line.partition(":")[2]
            event_type = raw[1:] if raw.startswith(" ") else raw
            event_type = event_type or "message"
            continue
        if line.startswith("data:"):
            raw = line.partition(":")[2]
            data_lines.append(raw[1:] if raw.startswith(" ") else raw)
            continue
        if line.startswith("id:"):
            raw = line.partition(":")[2]
            event_id = raw[1:] if raw.startswith(" ") else raw

    trailing_event = _build_sse_event(event_type, data_lines, event_id)
    if trailing_event is not None and len(events) < max_events:
        events.append(trailing_event)
        if len(events) >= max_events:
            return events, "max_events"
    return events, "eof"


def _build_sse_event(
    event_type: str,
    data_lines: list[str],
    event_id: str | None,
) -> dict[str, Any] | None:
    if not data_lines and event_id is None:
        return None
    payload = "\n".join(data_lines)
    event: dict[str, Any] = {
        "event": event_type,
        "data": payload,
    }
    if event_id is not None:
        event["id"] = event_id
    parsed_payload = _parse_stream_payload(payload)
    if parsed_payload is not payload:
        event["parsed_data"] = parsed_payload
    return event


def _normalize_websocket_messages(operation_id: str, value: Any | None) -> list[str | bytes]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_normalize_websocket_message(item) for item in value]
    return [_normalize_websocket_message(value)]


def _normalize_websocket_message(value: Any) -> str | bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "bytes_base64" in value and isinstance(value["bytes_base64"], str):
            try:
                return base64.b64decode(value["bytes_base64"], validate=True)
            except ValueError as exc:
                raise ToolError("WebSocket bytes_base64 contains invalid base64 data.") from exc
        if "text" in value and isinstance(value["text"], str):
            return value["text"]
        if "json" in value:
            return json.dumps(value["json"], ensure_ascii=True, separators=(",", ":"))
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


async def _collect_websocket_messages(
    websocket: websockets.ClientConnection,
    *,
    max_messages: int,
    idle_timeout_seconds: float,
) -> tuple[list[dict[str, Any]], str]:
    events: list[dict[str, Any]] = []
    while len(events) < max_messages:
        try:
            message = await asyncio.wait_for(websocket.recv(), timeout=idle_timeout_seconds)
        except TimeoutError:
            return events, "idle_timeout"
        except websockets.ConnectionClosed:
            return events, "connection_closed"

        if isinstance(message, bytes):
            events.append(
                {
                    "message_type": "bytes",
                    "content_base64": base64.b64encode(message).decode("ascii"),
                    "size_bytes": len(message),
                }
            )
        else:
            event: dict[str, Any] = {
                "message_type": "text",
                "text": message,
            }
            parsed_payload = _parse_stream_payload(message)
            if parsed_payload is not message:
                event["parsed_data"] = parsed_payload
            events.append(event)

        if len(events) >= max_messages:
            return events, "max_messages"

    return events, "connection_closed"


def _parse_stream_payload(payload: str) -> Any:
    stripped = payload.strip()
    if not stripped:
        return payload
    if stripped[0] not in {"{", "["}:
        return payload
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return payload


def _to_websocket_url(url: str, query_params: dict[str, Any]) -> str:
    parts = urlsplit(url)
    scheme = "wss" if parts.scheme in ("https", "wss") else "ws"
    normalized_query = urlencode(
        sorted((str(key), _normalize_query_value(value)) for key, value in query_params.items())
    )
    return urlunsplit((scheme, parts.netloc, parts.path, normalized_query, ""))


def _candidate_env_names(secret_ref: str) -> list[str]:
    return candidate_env_names(secret_ref)


def _build_signing_payload(
    *,
    method: str,
    url: str,
    query_params: dict[str, Any],
    body_for_signing: Any | None,
    timestamp: str,
) -> str:
    path = urlsplit(url).path or "/"
    normalized_query = urlencode(
        sorted((str(key), _normalize_query_value(value)) for key, value in query_params.items())
    )
    if body_for_signing is None:
        normalized_body = ""
    elif isinstance(body_for_signing, str):
        normalized_body = body_for_signing
    elif isinstance(body_for_signing, bytes):
        normalized_body = base64.b64encode(body_for_signing).decode("ascii")
    else:
        normalized_body = json.dumps(body_for_signing, ensure_ascii=True, separators=(",", ":"))
    return "\n".join([method.upper(), path, normalized_query, normalized_body, timestamp])


def _normalize_query_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _parse_response_payload(response: httpx.Response) -> Any:
    content_type = response.headers.get("content-type", "")
    normalized_content_type = content_type.lower()
    if "json" in normalized_content_type:
        try:
            return response.json()
        except ValueError:
            return response.text
    if any(
        normalized_content_type.startswith(textual) or textual in normalized_content_type
        for textual in _TEXTUAL_CONTENT_TYPES
    ):
        return response.text
    return {
        "binary": True,
        "content_type": content_type or "application/octet-stream",
        "content_base64": base64.b64encode(response.content).decode("ascii"),
        "size_bytes": len(response.content),
    }


def _build_soap_envelope(config: SoapOperationConfig, arguments: dict[str, Any]) -> str:
    ET.register_namespace("soapenv", _SOAP_ENVELOPE_NS)
    ET.register_namespace("tns", config.target_namespace)

    envelope = ET.Element(f"{{{_SOAP_ENVELOPE_NS}}}Envelope")
    body = ET.SubElement(envelope, f"{{{_SOAP_ENVELOPE_NS}}}Body")
    request_root = ET.SubElement(
        body,
        f"{{{config.target_namespace}}}{config.request_element}",
    )

    child_namespace = config.target_namespace if config.child_element_form == "qualified" else None
    for key, value in arguments.items():
        _append_soap_argument(
            request_root,
            key,
            value,
            namespace=child_namespace,
        )

    return ET.tostring(envelope, encoding="unicode")


def _append_soap_argument(
    parent: ET.Element,
    name: str,
    value: Any,
    *,
    namespace: str | None,
) -> None:
    if value is None:
        return
    if isinstance(value, list):
        for item in value:
            _append_soap_argument(parent, name, item, namespace=namespace)
        return

    child = ET.SubElement(parent, f"{{{namespace}}}{name}" if namespace else name)
    if isinstance(value, dict):
        for nested_key, nested_value in value.items():
            _append_soap_argument(child, str(nested_key), nested_value, namespace=namespace)
        return

    child.text = _soap_scalar_to_text(value)


def _soap_scalar_to_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _soap_body_element(root: ET.Element) -> ET.Element | None:
    return next(
        (element for element in root.iter() if _xml_local_name(element.tag) == "Body"),
        None,
    )


def _xml_local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _xml_element_to_value(element: ET.Element) -> Any:
    children = list(element)
    if not children:
        return _coerce_xml_text(element.text)

    grouped: dict[str, list[Any]] = {}
    for child in children:
        key = _xml_local_name(child.tag)
        grouped.setdefault(key, []).append(_xml_element_to_value(child))

    payload: dict[str, Any] = {}
    for key, values in grouped.items():
        payload[key] = values if len(values) > 1 else values[0]
    return payload


def _coerce_xml_text(text: str | None) -> Any:
    if text is None:
        return ""
    stripped = text.strip()
    if not stripped:
        return ""
    lowered = stripped.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if re.fullmatch(r"-?[0-9]+", stripped):
        try:
            return int(stripped)
        except ValueError:
            return stripped
    if re.fullmatch(r"-?[0-9]+\.[0-9]+", stripped):
        try:
            return float(stripped)
        except ValueError:
            return stripped
    return stripped


def _split_escaped_dot_path(path: str) -> list[str]:
    r"""Split a dot-delimited path, treating ``\.`` as a literal dot.

    >>> _split_escaped_dot_path(r"Address\.City")
    ['Address.City']
    >>> _split_escaped_dot_path("user.name")
    ['user', 'name']
    """
    segments: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(path):
        if path[i] == "\\" and i + 1 < len(path) and path[i + 1] == ".":
            current.append(".")
            i += 2
        elif path[i] == ".":
            segments.append("".join(current))
            current = []
            i += 1
        else:
            current.append(path[i])
            i += 1
    segments.append("".join(current))
    return segments


def _has_unescaped_dot(path: str) -> bool:
    r"""Return True if *path* contains at least one unescaped dot."""
    i = 0
    while i < len(path):
        if path[i] == "\\" and i + 1 < len(path) and path[i + 1] == ".":
            i += 2
        elif path[i] == ".":
            return True
        else:
            i += 1
    return False


def _apply_field_filter(payload: Any, field_filter: list[str] | None) -> Any:
    r"""Filter response fields by allowlist.

    Supports three path styles:
    - ``"name"`` — top-level key
    - ``"user.name"`` — nested key via dot notation
    - ``"items[].id"`` — key inside each element of a top-level array

    Use ``\.`` to represent a literal dot inside a field name
    (e.g. ``r"Address\.City"`` selects the key ``Address.City``).
    """
    if not field_filter:
        return payload

    # Separate plain top-level keys from dot/bracket paths.
    top_keys: set[str] = set()
    nested_paths: list[tuple[str, list[str]]] = []  # (root, remaining segments)
    array_paths: dict[str, list[str]] = {}  # root[] -> inner field names

    for path in field_filter:
        if "[]." in path:
            root, rest = path.split("[].", 1)
            array_paths.setdefault(root, []).append(rest)
        elif _has_unescaped_dot(path):
            segments = _split_escaped_dot_path(path)
            root = segments[0]
            nested_paths.append((root, segments[1:]))
        else:
            # Either a plain key or a key that only contains escaped dots.
            top_keys.add(_split_escaped_dot_path(path)[0])

    if isinstance(payload, dict):
        return _filter_dict(payload, top_keys, nested_paths, array_paths)

    if isinstance(payload, list):
        # When the payload itself is a list, apply filters to each dict item.
        all_inner_fields = top_keys | {p for paths in array_paths.values() for p in paths}
        if not nested_paths and not array_paths:
            # Simple flat filter on list items.
            return [
                {k: v for k, v in item.items() if k in top_keys} if isinstance(item, dict) else item
                for item in payload
            ]
        return [
            _filter_dict(item, all_inner_fields, nested_paths, array_paths)
            if isinstance(item, dict)
            else item
            for item in payload
        ]

    return payload


def _filter_dict(
    d: dict[str, Any],
    top_keys: set[str],
    nested_paths: list[tuple[str, list[str]]],
    array_paths: dict[str, list[str]],
) -> dict[str, Any]:
    """Build a filtered copy of *d* keeping only requested paths."""
    result: dict[str, Any] = {}

    # Top-level keys.
    for key in top_keys:
        if key in d:
            result[key] = d[key]

    # Nested dot-paths — drill into sub-dicts.
    for root, segments in nested_paths:
        if root not in d:
            continue
        _set_nested(result, root, segments, d[root])

    # Array bracket paths — filter items inside a top-level list field.
    for root, inner_fields in array_paths.items():
        if root not in d:
            continue
        value = d[root]
        if isinstance(value, list):
            inner_set = set(inner_fields)
            result[root] = [
                {k: v for k, v in item.items() if k in inner_set}
                if isinstance(item, dict)
                else item
                for item in value
            ]
        else:
            result[root] = value

    return result


def _set_nested(
    target: dict[str, Any],
    root: str,
    segments: list[str],
    source: Any,
) -> None:
    """Copy a nested value from *source* into *target[root]* following *segments*."""
    if not segments:
        return
    node = source
    for seg in segments:
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        else:
            return  # path not present — skip silently

    # Build / merge nested structure in *target*.
    cur = target
    if root not in cur:
        cur[root] = {}
    cur = cur[root]
    for seg in segments[:-1]:
        if not isinstance(cur, dict):
            return
        if seg not in cur:
            cur[seg] = {}
        cur = cur[seg]
    if isinstance(cur, dict):
        cur[segments[-1]] = node


def _apply_array_limit(payload: Any, max_items: int | None) -> Any:
    """Truncate top-level list payloads (or list values inside a dict) to *max_items*."""
    if max_items is None:
        return payload
    if isinstance(payload, list):
        return payload[:max_items]
    if isinstance(payload, dict):
        return {k: v[:max_items] if isinstance(v, list) else v for k, v in payload.items()}
    return payload


def _truncate_utf8_prefix(payload_bytes: bytes, max_bytes: int) -> tuple[str, bool]:
    """Return the largest valid UTF-8 prefix not exceeding ``max_bytes`` bytes."""

    candidate = payload_bytes[:max_bytes]
    try:
        return candidate.decode("utf-8"), False
    except UnicodeDecodeError:
        end = len(candidate)
        while end > 0:
            try:
                return candidate[:end].decode("utf-8"), True
            except UnicodeDecodeError:
                end -= 1
        return "", True


def _apply_truncation(payload: Any, strategy: ResponseStrategy) -> tuple[Any, bool]:
    if strategy.max_response_bytes is None or strategy.max_response_bytes <= 0:
        return payload, False

    serialized = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=True)
    payload_bytes = serialized.encode("utf-8")
    if len(payload_bytes) <= strategy.max_response_bytes:
        return payload, False

    if strategy.truncation_policy == TruncationPolicy.none:
        return payload, False

    truncated, utf8_boundary_trimmed = _truncate_utf8_prefix(
        payload_bytes,
        strategy.max_response_bytes,
    )
    result = {
        "content": truncated,
        "original_type": type(payload).__name__,
        "truncated": True,
    }
    if utf8_boundary_trimmed:
        result["utf8_boundary_trimmed"] = True
    return result, True


def _unwrap_graphql_payload(payload: Any, operation: Operation) -> Any:
    if operation.graphql is None:
        return payload
    if not isinstance(payload, dict):
        raise ToolError(f"GraphQL operation {operation.id} returned a non-object response body.")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ToolError(f"GraphQL operation {operation.id} returned no data object.")
    return data.get(operation.graphql.operation_name, data)


def _unwrap_odata_payload(payload: Any) -> Any:
    """Unwrap OData collection responses.

    OData services wrap collection results in ``{"value": [...]}`` with optional
    ``@odata.count`` and ``@odata.nextLink`` metadata.  This function extracts
    the ``value`` array while preserving single-entity responses as-is.
    """
    if not isinstance(payload, dict):
        return payload
    if "value" in payload and isinstance(payload["value"], list):
        result: dict[str, Any] = {"items": payload["value"]}
        count = payload.get("@odata.count")
        if count is not None:
            result["total_count"] = count
        next_link = payload.get("@odata.nextLink")
        if next_link is not None:
            result["next_link"] = next_link
        return result
    return payload


def _unwrap_scim_payload(payload: Any) -> Any:
    """Unwrap SCIM 2.0 list responses.

    SCIM services wrap collection results in
    ``{"Resources": [...], "totalResults": N, "startIndex": N, "itemsPerPage": N}``.
    This function extracts the ``Resources`` array while preserving single-resource
    responses as-is.
    """
    if not isinstance(payload, dict):
        return payload
    resources = payload.get("Resources")
    if isinstance(resources, list):
        result: dict[str, Any] = {"items": resources}
        total = payload.get("totalResults")
        if total is not None:
            result["total_count"] = total
        start = payload.get("startIndex")
        if start is not None:
            result["start_index"] = start
        per_page = payload.get("itemsPerPage")
        if per_page is not None:
            result["items_per_page"] = per_page
        return result
    return payload


def _unwrap_jsonrpc_payload(payload: Any, operation: Operation) -> Any:
    """Unwrap JSON-RPC 2.0 response envelope.

    Extracts ``result`` from ``{"jsonrpc": "2.0", "result": ..., "id": N}``.
    """
    if operation.jsonrpc is None:
        return payload
    if not isinstance(payload, dict):
        return payload
    if "result" in payload:
        return payload["result"]
    return payload


def _unwrap_soap_payload(response: httpx.Response, operation: Operation) -> Any:
    if operation.soap is None:
        return _parse_response_payload(response)

    try:
        root = ET.fromstring(response.text)
    except ET.ParseError as exc:
        raise ToolError(f"SOAP operation {operation.id} returned invalid XML: {exc}") from exc

    body = _soap_body_element(root)
    if body is None:
        raise ToolError(f"SOAP operation {operation.id} returned no SOAP Body element.")

    fault = next(
        (child for child in body if _xml_local_name(child.tag) == "Fault"),
        None,
    )
    if fault is not None:
        raise ToolError(
            RuntimeProxy._soap_fault_message(response, operation)
            or f"SOAP operation {operation.id} returned a SOAP Fault."
        )

    payload_element: ET.Element | None = None
    if operation.soap.response_element:
        payload_element = next(
            (
                child
                for child in body
                if _xml_local_name(child.tag) == operation.soap.response_element
            ),
            None,
        )
    if payload_element is None:
        payload_element = next(iter(body), None)
    if payload_element is None:
        raise ToolError(f"SOAP operation {operation.id} returned an empty SOAP Body.")

    return _xml_element_to_value(payload_element)


def _build_multipart_request_body(
    operation_id: str,
    value: Any,
) -> tuple[dict[str, str], dict[str, tuple[str, bytes, str | None]], dict[str, Any]]:
    if not isinstance(value, dict):
        raise ToolError(
            f"Multipart operation {operation_id} requires an object payload with form/files."
        )

    raw_form = value.get("form", {})
    raw_files = value.get("files", {})
    if not isinstance(raw_form, dict):
        raise ToolError(f"Multipart form payload must be an object for operation {operation_id}.")
    if not isinstance(raw_files, dict):
        raise ToolError(f"Multipart files payload must be an object for operation {operation_id}.")

    form_data = {
        str(key): _normalize_form_value(field_value) for key, field_value in raw_form.items()
    }
    files: dict[str, tuple[str, bytes, str | None]] = {}
    signable_files: dict[str, dict[str, str]] = {}
    for field_name, file_value in raw_files.items():
        filename, content, content_type = _normalize_multipart_file_part(
            operation_id,
            field_name,
            file_value,
        )
        files[str(field_name)] = (filename, content, content_type)
        signable_files[str(field_name)] = {
            "filename": filename,
            "content_base64": base64.b64encode(content).decode("ascii"),
            "content_type": content_type or "application/octet-stream",
        }

    return form_data, files, {"form": form_data, "files": signable_files}


def _normalize_multipart_file_part(
    operation_id: str,
    field_name: str,
    value: Any,
) -> tuple[str, bytes, str | None]:
    if isinstance(value, str):
        return field_name, value.encode("utf-8"), "text/plain"

    if not isinstance(value, dict):
        raise ToolError(
            f"Multipart file field {field_name!r} must be a string or object "
            f"for operation {operation_id}."
        )

    filename = value.get("filename")
    if filename is None:
        filename = field_name
    if not isinstance(filename, str) or not filename:
        raise ToolError(
            f"Multipart file field {field_name!r} has an invalid filename "
            f"for operation {operation_id}."
        )

    content_type = value.get("content_type")
    if content_type is not None and not isinstance(content_type, str):
        raise ToolError(
            f"Multipart file field {field_name!r} has an invalid content_type "
            f"for operation {operation_id}."
        )

    if "content_base64" in value:
        encoded = value["content_base64"]
        if not isinstance(encoded, str):
            raise ToolError(
                f"Multipart file field {field_name!r} has a non-string content_base64 "
                f"for operation {operation_id}."
            )
        try:
            decoded_content = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ToolError(
                f"Multipart file field {field_name!r} contains invalid base64 "
                f"for operation {operation_id}."
            ) from exc
        return filename, decoded_content, content_type

    content = value.get("content")
    if not isinstance(content, str):
        raise ToolError(
            f"Multipart file field {field_name!r} requires string content or content_base64 "
            f"for operation {operation_id}."
        )
    return filename, content.encode("utf-8"), content_type or "text/plain"


def _build_raw_request_body(
    operation_id: str,
    value: Any,
) -> tuple[bytes | str, str | None, Any]:
    if isinstance(value, str):
        return value, None, value

    if not isinstance(value, dict):
        raise ToolError(f"Raw-body operation {operation_id} requires a string or object payload.")

    content_type = value.get("content_type")
    if content_type is not None and not isinstance(content_type, str):
        raise ToolError(f"Raw-body operation {operation_id} received a non-string content_type.")

    if "content_base64" in value:
        encoded = value["content_base64"]
        if not isinstance(encoded, str):
            raise ToolError(
                f"Raw-body operation {operation_id} received a non-string content_base64."
            )
        try:
            decoded_content = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ToolError(
                f"Raw-body operation {operation_id} received invalid base64 content."
            ) from exc
        return (
            decoded_content,
            content_type,
            {"content_base64": encoded, "content_type": content_type},
        )

    content = value.get("content")
    if not isinstance(content, str):
        raise ToolError(
            f"Raw-body operation {operation_id} requires string content or content_base64."
        )
    return content, content_type, {"content": content, "content_type": content_type}


def _normalize_form_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    return str(value)


def _is_same_origin(base_url: str, resolved_url: str) -> bool:
    """Check that resolved_url shares the same scheme+host+port as base_url."""
    base = urlsplit(base_url)
    resolved = urlsplit(resolved_url)
    return base.scheme == resolved.scheme and base.netloc == resolved.netloc


def _extract_async_status_url(async_job: AsyncJobConfig, response: httpx.Response) -> str | None:
    if async_job.status_url_source == "location_header":
        location_value = response.headers.get("Location") or response.headers.get(
            "Content-Location"
        )
        if not isinstance(location_value, str) or not location_value:
            return None
        request_url = str(response.request.url) if response.request is not None else ""
        resolved = urljoin(request_url, location_value)
        if request_url and not _is_same_origin(request_url, resolved):
            logger.warning(
                "Async poll URL %r resolved to different origin than request %r; "
                "blocking potential SSRF",
                resolved,
                request_url,
            )
            return None
        return resolved

    payload = _maybe_parse_json_payload(response)
    if payload is None or async_job.status_url_field is None:
        return None
    url_value = _extract_nested_value(payload, async_job.status_url_field)
    if not isinstance(url_value, str) or not url_value:
        return None
    request_url = str(response.request.url) if response.request is not None else ""
    resolved = urljoin(request_url, url_value)
    if request_url and not _is_same_origin(request_url, resolved):
        logger.warning(
            "Async poll URL %r resolved to different origin than request %r; "
            "blocking potential SSRF",
            resolved,
            request_url,
        )
        return None
    return resolved


def _extract_async_status_value(async_job: AsyncJobConfig, response: httpx.Response) -> str | None:
    payload = _maybe_parse_json_payload(response)
    if payload is None:
        return None
    status_value = _extract_nested_value(payload, async_job.status_field)
    if isinstance(status_value, str):
        return status_value
    return None


class _InvalidJsonPayloadError(Exception):
    """Raised when a response claims JSON content-type but the body is not valid JSON."""


def _maybe_parse_json_payload(response: httpx.Response) -> Any | None:
    """Parse a JSON response body.

    Returns ``None`` when the content-type is not JSON.
    Raises :class:`_InvalidJsonPayloadError` when the content-type indicates JSON
    but the body cannot be decoded — callers should treat this as a protocol
    error rather than silently degrading.
    """
    content_type = response.headers.get("content-type", "")
    if "json" not in content_type.lower():
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise _InvalidJsonPayloadError(
            f"Response declared content-type {content_type!r} "
            f"but body is not valid JSON (HTTP {response.status_code})"
        ) from exc


def _extract_nested_value(payload: Any, dotted_path: str) -> Any | None:
    current = payload
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _split_url_query(url: str) -> tuple[str, dict[str, str]]:
    parsed = urlsplit(url)
    base_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", parsed.fragment))
    return base_url, dict(parse_qsl(parsed.query, keep_blank_values=True))
