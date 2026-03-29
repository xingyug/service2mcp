"""SOAP / WSDL extractor foundation for WSDL 1.1 document-literal services."""

from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlparse

import httpx

from libs.extractors.base import SourceConfig
from libs.ir.models import (
    AuthConfig,
    AuthType,
    ErrorSchema,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SoapOperationConfig,
    SourceType,
)

logger = logging.getLogger(__name__)

WSDL_NS = "http://schemas.xmlsoap.org/wsdl/"
SOAP_NS = "http://schemas.xmlsoap.org/wsdl/soap/"
XSD_NS = "http://www.w3.org/2001/XMLSchema"
NS = {"wsdl": WSDL_NS, "soap": SOAP_NS, "xsd": XSD_NS}

XSD_TYPE_MAP: dict[str, str] = {
    "string": "string",
    "boolean": "boolean",
    "int": "integer",
    "integer": "integer",
    "long": "integer",
    "short": "integer",
    "decimal": "number",
    "float": "number",
    "double": "number",
    "date": "string",
    "dateTime": "string",
}
SAFE_OPERATION_PREFIXES = ("Get", "List", "Read", "Fetch", "Lookup", "Describe")
DANGEROUS_OPERATION_PREFIXES = ("Delete", "Remove", "Cancel", "Purge")


@dataclass(frozen=True)
class XSDField:
    """Normalized XML Schema field."""

    name: str
    type_name: str
    required: bool
    repeated: bool


class SOAPWSDLExtractor:
    """Extract SOAP/WSDL operations into ServiceIR."""

    protocol_name: str = "soap"

    def detect(self, source: SourceConfig) -> float:
        content = self._get_content(source)
        if content is None:
            return 0.0

        lowered_path = (source.file_path or source.url or "").lower()
        if lowered_path.endswith(".wsdl") and self._looks_like_wsdl(content):
            return 0.98
        if self._looks_like_wsdl(content):
            return 0.95
        if "soap:address" in content and "wsdl:definitions" in content:
            return 0.7
        return 0.0

    def extract(self, source: SourceConfig) -> ServiceIR:
        content = self._get_content(source)
        if content is None:
            raise ValueError("Could not read source content")

        root = ET.fromstring(content)
        if _local_name(root.tag) != "definitions":
            raise ValueError("SOAP extractor requires a WSDL definitions document.")

        source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        target_namespace = root.attrib.get("targetNamespace", "")
        messages = _parse_messages(root)
        elements, complex_types, child_element_forms = _parse_schema_types(root)
        services = root.findall("wsdl:service", NS)
        if not services:
            raise ValueError("No wsdl:service definitions found.")
        service = services[0]
        service_name = service.attrib.get("name", root.attrib.get("name", "soap-service"))
        port = service.find("wsdl:port", NS)
        if port is None:
            raise ValueError("WSDL service is missing a port definition.")
        binding_name = _qname_local(port.attrib.get("binding", ""))
        address = port.find("soap:address", NS)
        base_url = (
            (address.attrib.get("location") if address is not None else None)
            or source.url
            or f"https://{_slugify(service_name)}"
        )

        port_type_map = {
            element.attrib["name"]: element
            for element in root.findall("wsdl:portType", NS)
            if "name" in element.attrib
        }
        binding_map = {
            element.attrib["name"]: element
            for element in root.findall("wsdl:binding", NS)
            if "name" in element.attrib
        }
        binding = binding_map.get(binding_name)
        if binding is None:
            raise ValueError(f"WSDL binding {binding_name!r} not found.")
        port_type_name = _qname_local(binding.attrib.get("type", ""))
        port_type = port_type_map.get(port_type_name)
        if port_type is None:
            raise ValueError(f"WSDL portType {port_type_name!r} not found.")

        soap_actions = _parse_soap_actions(binding)
        binding_style = _binding_style(binding)
        body_uses = _parse_body_uses(binding)
        if binding_style != "document":
            raise ValueError("SOAP extractor currently supports document-style bindings only.")
        unsupported_uses = sorted(
            operation_name
            for operation_name, body_use in body_uses.items()
            if body_use != "literal"
        )
        if unsupported_uses:
            raise ValueError(
                "SOAP extractor currently supports literal SOAP bodies only: "
                f"{', '.join(unsupported_uses)}"
            )
        operations = [
            _build_operation(
                operation=operation,
                messages=messages,
                elements=elements,
                complex_types=complex_types,
                child_element_forms=child_element_forms,
                soap_actions=soap_actions,
                target_namespace=target_namespace,
                binding_style=cast(Literal["document"], binding_style),
                body_uses=body_uses,
                endpoint_path=_endpoint_path(base_url),
            )
            for operation in port_type.findall("wsdl:operation", NS)
        ]

        return ServiceIR(
            source_url=source.url,
            source_hash=source_hash,
            protocol="soap",
            service_name=_slugify(service_name),
            service_description=f"SOAP service extracted from WSDL service {service_name}.",
            base_url=base_url,
            auth=AuthConfig(type=AuthType.none),
            operations=operations,
            metadata={
                "wsdl_target_namespace": target_namespace,
                "wsdl_service": service_name,
                "wsdl_port": port.attrib.get("name", ""),
                "wsdl_port_type": port_type_name,
                "wsdl_binding": binding_name,
                "soap_actions": soap_actions,
            },
        )

    def _get_content(self, source: SourceConfig) -> str | None:
        if source.file_content:
            return source.file_content
        if source.file_path:
            return Path(source.file_path).read_text(encoding="utf-8")
        if source.url:
            try:
                response = httpx.get(source.url, timeout=30, headers=self._auth_headers(source))
                response.raise_for_status()
                return response.text
            except Exception:
                logger.warning("Failed to fetch WSDL from %s", source.url, exc_info=True)
                return None
        return None

    def _auth_headers(self, source: SourceConfig) -> dict[str, str]:
        headers: dict[str, str] = {}
        if source.auth_header:
            headers["Authorization"] = source.auth_header
        elif source.auth_token:
            headers["Authorization"] = f"Bearer {source.auth_token}"
        return headers

    def _looks_like_wsdl(self, content: str) -> bool:
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return False
        definitions = _local_name(root.tag) == "definitions"
        has_wsdl_namespace = WSDL_NS in root.tag or any(
            namespace == WSDL_NS for namespace in root.attrib.values()
        )
        has_message = root.find("wsdl:message", NS) is not None
        has_service = root.find("wsdl:service", NS) is not None
        return definitions and (has_wsdl_namespace or (has_message and has_service))


def _parse_messages(root: ET.Element) -> dict[str, str]:
    messages: dict[str, str] = {}
    for message in root.findall("wsdl:message", NS):
        name = message.attrib.get("name")
        if not name:
            continue
        part = message.find("wsdl:part", NS)
        if part is None:
            continue
        element_qname = part.attrib.get("element") or part.attrib.get("type")
        if not element_qname:
            continue
        messages[name] = _qname_local(element_qname)
    return messages


def _parse_schema_types(
    root: ET.Element,
) -> tuple[dict[str, list[XSDField]], dict[str, list[XSDField]], dict[str, str]]:
    elements: dict[str, list[XSDField]] = {}
    complex_types: dict[str, list[XSDField]] = {}
    child_element_forms: dict[str, str] = {}
    for schema in root.findall("wsdl:types/xsd:schema", NS):
        child_element_form = _schema_child_element_form(schema)
        for complex_type in schema.findall("xsd:complexType", NS):
            name = complex_type.attrib.get("name")
            if not name:
                continue
            complex_types[name] = _extract_xsd_fields(complex_type)
            child_element_forms[name] = child_element_form
        for element in schema.findall("xsd:element", NS):
            name = element.attrib.get("name")
            if not name:
                continue
            child_element_forms[name] = child_element_form
            type_name = _qname_local(element.attrib.get("type", ""))
            if type_name and type_name in complex_types:
                elements[name] = complex_types[type_name]
                continue
            inline_complex_type = element.find("xsd:complexType", NS)
            if inline_complex_type is not None:
                elements[name] = _extract_xsd_fields(inline_complex_type)
    return elements, complex_types, child_element_forms


def _schema_child_element_form(schema: ET.Element) -> str:
    if schema.attrib.get("elementFormDefault", "unqualified").lower() == "qualified":
        return "qualified"
    return "unqualified"


def _extract_xsd_fields(container: ET.Element) -> list[XSDField]:
    sequence = container.find("xsd:sequence", NS)
    if sequence is None:
        return []
    fields: list[XSDField] = []
    for element in sequence.findall("xsd:element", NS):
        name = element.attrib.get("name")
        if not name:
            continue
        has_default = "default" in element.attrib
        fields.append(
            XSDField(
                name=name,
                type_name=_qname_local(element.attrib.get("type", "xsd:string")),
                # Defaulted XSD fields are optional at call time even when minOccurs
                # is omitted, otherwise SOAP runtimes over-require inputs like
                # includeHistory default="false".
                required=element.attrib.get("minOccurs", "1") != "0" and not has_default,
                repeated=element.attrib.get("maxOccurs") not in {None, "1"},
            )
        )
    return fields


def _parse_soap_actions(binding: ET.Element) -> dict[str, str]:
    actions: dict[str, str] = {}
    for operation in binding.findall("wsdl:operation", NS):
        name = operation.attrib.get("name")
        if not name:
            continue
        soap_operation = operation.find("soap:operation", NS)
        if soap_operation is None:
            continue
        action = soap_operation.attrib.get("soapAction")
        if action:
            actions[name] = action
    return actions


def _binding_style(binding: ET.Element) -> str:
    soap_binding = binding.find("soap:binding", NS)
    if soap_binding is None:
        return "document"
    return soap_binding.attrib.get("style", "document").lower()


def _parse_body_uses(binding: ET.Element) -> dict[str, str]:
    uses: dict[str, str] = {}
    for operation in binding.findall("wsdl:operation", NS):
        name = operation.attrib.get("name")
        if not name:
            continue
        soap_body = operation.find("wsdl:input/soap:body", NS)
        if soap_body is None:
            soap_body = operation.find("wsdl:output/soap:body", NS)
        uses[name] = (
            soap_body.attrib.get("use", "literal").lower() if soap_body is not None else "literal"
        )
    return uses


def _build_operation(
    *,
    operation: ET.Element,
    messages: dict[str, str],
    elements: dict[str, list[XSDField]],
    complex_types: dict[str, list[XSDField]],
    child_element_forms: dict[str, str],
    soap_actions: dict[str, str],
    target_namespace: str,
    binding_style: Literal["document"],
    body_uses: dict[str, str],
    endpoint_path: str,
) -> Operation:
    operation_name = operation.attrib.get("name")
    if not operation_name:
        raise ValueError("Encountered WSDL operation without a name.")
    input_tag = operation.find("wsdl:input", NS)
    if input_tag is None:
        raise ValueError(f"WSDL operation '{operation_name}' has no <wsdl:input> child element.")
    input_message_name = _qname_local(input_tag.attrib.get("message", ""))
    output_element_name = ""
    output_tag = operation.find("wsdl:output", NS)
    if output_tag is not None:
        output_element_name = messages.get(_qname_local(output_tag.attrib.get("message", "")), "")
    input_element_name = messages.get(input_message_name, "")
    input_fields = _resolve_wsdl_fields(
        input_element_name,
        elements=elements,
        complex_types=complex_types,
    )
    output_fields = _resolve_wsdl_fields(
        output_element_name,
        elements=elements,
        complex_types=complex_types,
    )
    return Operation(
        id=operation_name,
        name=_humanize_identifier(operation_name),
        description=f"SOAP operation {operation_name}.",
        method="POST",
        path=endpoint_path,
        params=[
            Param(
                name=field.name,
                type=_ir_type_for_xsd(field.type_name, complex_types),
                required=field.required,
                source=SourceType.extractor,
                confidence=1.0,
            )
            for field in input_fields
        ],
        response_schema=_response_schema(output_fields, complex_types),
        risk=_risk_for_operation(operation_name),
        soap=SoapOperationConfig(
            target_namespace=target_namespace,
            request_element=input_element_name or operation_name,
            response_element=output_element_name or None,
            soap_action=soap_actions.get(operation_name),
            binding_style=binding_style,
            body_use=cast(Literal["literal"], body_uses.get(operation_name, "literal")),
            child_element_form=cast(
                Literal["qualified", "unqualified"],
                child_element_forms.get(input_element_name or operation_name, "qualified"),
            ),
        ),
        tags=["soap", "wsdl", soap_actions.get(operation_name, "")],
        source=SourceType.extractor,
        confidence=1.0,
        enabled=True,
        error_schema=ErrorSchema(
            default_error_schema={
                "type": "object",
                "properties": {
                    "faultcode": {"type": "string"},
                    "faultstring": {"type": "string"},
                    "detail": {"type": "object"},
                },
                "required": ["faultcode", "faultstring"],
            }
        ),
    )


def _resolve_wsdl_fields(
    name: str,
    *,
    elements: dict[str, list[XSDField]],
    complex_types: dict[str, list[XSDField]],
) -> list[XSDField]:
    if name in elements:
        return elements[name]
    if name in complex_types:
        return complex_types[name]
    return []


def _response_schema(
    fields: list[XSDField],
    complex_types: dict[str, list[XSDField]],
) -> dict[str, Any] | None:
    if not fields:
        return None
    properties = {
        field.name: {"type": _ir_type_for_xsd(field.type_name, complex_types)} for field in fields
    }
    required = [field.name for field in fields if field.required]
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _ir_type_for_xsd(type_name: str, complex_types: dict[str, list[XSDField]]) -> str:
    if type_name in XSD_TYPE_MAP:
        return XSD_TYPE_MAP[type_name]
    if type_name in complex_types:
        return "object"
    return "object"


def _risk_for_operation(operation_name: str) -> RiskMetadata:
    if operation_name.startswith(SAFE_OPERATION_PREFIXES):
        return RiskMetadata(
            writes_state=False,
            destructive=False,
            external_side_effect=False,
            idempotent=True,
            risk_level=RiskLevel.safe,
            confidence=0.9,
            source=SourceType.extractor,
        )
    if operation_name.startswith(DANGEROUS_OPERATION_PREFIXES):
        return RiskMetadata(
            writes_state=True,
            destructive=True,
            external_side_effect=True,
            idempotent=False,
            risk_level=RiskLevel.dangerous,
            confidence=0.9,
            source=SourceType.extractor,
        )
    return RiskMetadata(
        writes_state=True,
        destructive=False,
        external_side_effect=True,
        idempotent=False,
        risk_level=RiskLevel.cautious,
        confidence=0.85,
        source=SourceType.extractor,
    )


def _endpoint_path(base_url: str) -> str:
    path = urlparse(base_url).path
    return path or "/"


def _qname_local(value: str) -> str:
    if "}" in value:
        return value.rsplit("}", 1)[1]
    if ":" in value:
        return value.split(":", 1)[1]
    return value


def _local_name(tag: str) -> str:
    return _qname_local(tag)


def _humanize_identifier(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", " ", value).strip()


def _slugify(text: str) -> str:
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "-", text).lower().strip()
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")


__all__ = ["SOAPWSDLExtractor"]
