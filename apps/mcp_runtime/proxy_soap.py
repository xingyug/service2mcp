"""SOAP/WSDL protocol proxy helpers."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from apps.mcp_runtime.proxy_utils import (
    _SOAP_ENVELOPE_NS,
    PreparedRequestPayload,
    _parse_response_payload,
)
from libs.ir.models import Operation, SoapOperationConfig


def prepare_soap_payload(
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


def soap_fault_message(
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


# ---------------------------------------------------------------------------
# SOAP envelope building
# ---------------------------------------------------------------------------


def _build_soap_envelope(config: SoapOperationConfig, arguments: dict[str, Any]) -> str:
    ET.register_namespace("soapenv", _SOAP_ENVELOPE_NS)
    ET.register_namespace("tns", config.target_namespace)

    envelope = ET.Element(f"{{{_SOAP_ENVELOPE_NS}}}Envelope")
    body = ET.SubElement(envelope, f"{{{_SOAP_ENVELOPE_NS}}}Body")
    safe_request_element = _sanitize_xml_name(config.request_element)
    request_root = ET.SubElement(
        body,
        f"{{{config.target_namespace}}}{safe_request_element}",
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

    safe_name = _sanitize_xml_name(name)
    child = ET.SubElement(parent, f"{{{namespace}}}{safe_name}" if namespace else safe_name)
    if isinstance(value, dict):
        for nested_key, nested_value in value.items():
            _append_soap_argument(child, str(nested_key), nested_value, namespace=namespace)
        return

    child.text = _soap_scalar_to_text(value)


def _soap_scalar_to_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    # Strip XML 1.0 illegal control chars (only \t, \n, \r are allowed below \x20)
    return _XML_ILLEGAL_CHARS_RE.sub("", text)


_XML_ILLEGAL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_xml_name(name: str) -> str:
    """Ensure *name* is a valid XML element name.

    Strips control chars, replaces non-alphanumeric chars with ``_``,
    and prepends ``_`` if the name starts with a digit or is empty.
    """
    cleaned = _XML_ILLEGAL_CHARS_RE.sub("", name)
    cleaned = re.sub(r"[^a-zA-Z0-9_.\-]", "_", cleaned)
    if not cleaned or cleaned[0].isdigit() or cleaned[0] in (".", "-"):
        cleaned = f"_{cleaned}"
    return cleaned or "_"


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# SOAP response unwrapping
# ---------------------------------------------------------------------------


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
            soap_fault_message(response, operation)
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
