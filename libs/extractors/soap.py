"""SOAP / WSDL extractor foundation for WSDL 1.1 services (document and RPC style)."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Literal, cast
from urllib.parse import urlparse

from libs.extractors.base import SourceConfig
from libs.extractors.utils import (
    compute_content_hash,
    get_auth_headers,
    get_content,
    slugify,
)
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

        source_hash = compute_content_hash(content)
        target_namespace = root.attrib.get("targetNamespace", "")
        messages = _parse_messages(root)
        elements, complex_types, child_element_forms, simple_types, simple_type_enums = (
            _parse_schema_types(root)
        )
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
            or f"https://{slugify(service_name, camel_case=True)}"
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
        if binding_style not in ("document", "rpc"):
            raise ValueError(f"Unsupported SOAP binding style: {binding_style!r}")
        rpc_message_parts = _parse_rpc_message_parts(root) if binding_style == "rpc" else {}
        operations = [
            _build_operation(
                operation=operation,
                messages=messages,
                elements=elements,
                complex_types=complex_types,
                simple_types=simple_types,
                simple_type_enums=simple_type_enums,
                child_element_forms=child_element_forms,
                soap_actions=soap_actions,
                target_namespace=target_namespace,
                binding_style=cast(Literal["document", "rpc"], binding_style),
                body_uses=body_uses,
                endpoint_path=_endpoint_path(base_url),
                rpc_message_parts=rpc_message_parts,
            )
            for operation in port_type.findall("wsdl:operation", NS)
        ]

        return ServiceIR(
            source_url=source.url,
            source_hash=source_hash,
            protocol="soap",
            service_name=slugify(service_name, camel_case=True),
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
        return get_content(source)

    def _auth_headers(self, source: SourceConfig) -> dict[str, str]:
        return get_auth_headers(source)

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


def _parse_rpc_message_parts(root: ET.Element) -> dict[str, list[XSDField]]:
    """Parse RPC-style message parts where each ``<part>`` is a direct parameter."""
    result: dict[str, list[XSDField]] = {}
    for message in root.findall("wsdl:message", NS):
        name = message.attrib.get("name")
        if not name:
            continue
        parts: list[XSDField] = []
        for part in message.findall("wsdl:part", NS):
            part_name = part.attrib.get("name")
            if not part_name:
                continue
            type_qname = part.attrib.get("type") or part.attrib.get("element")
            if not type_qname:
                continue
            parts.append(
                XSDField(
                    name=part_name,
                    type_name=_qname_local(type_qname),
                    required=True,
                    repeated=False,
                )
            )
        result[name] = parts
    return result


def _parse_schema_types(
    root: ET.Element,
) -> tuple[
    dict[str, list[XSDField]],
    dict[str, list[XSDField]],
    dict[str, str],
    dict[str, str],
    dict[str, list[str]],
]:
    """Parse XSD types from WSDL ``<types>`` section.

    Returns ``(elements, complex_types, child_element_forms, simple_types, simple_type_enums)``
    where *simple_types* maps named ``xsd:simpleType`` definitions to their
    resolved XSD base type (e.g. ``"Priority"`` → ``"string"``), and
    *simple_type_enums* captures restriction/enumeration values.
    """
    elements: dict[str, list[XSDField]] = {}
    complex_types: dict[str, list[XSDField]] = {}
    child_element_forms: dict[str, str] = {}
    simple_types: dict[str, str] = {}
    simple_type_enums: dict[str, list[str]] = {}
    for schema in root.findall("wsdl:types/xsd:schema", NS):
        child_element_form = _schema_child_element_form(schema)
        for simple_type in schema.findall("xsd:simpleType", NS):
            name = simple_type.attrib.get("name")
            if not name:
                continue
            restriction = simple_type.find("xsd:restriction", NS)
            if restriction is not None:
                base = _qname_local(restriction.attrib.get("base", "string"))
                simple_types[name] = base
                enum_values = [
                    e.attrib["value"]
                    for e in restriction.findall("xsd:enumeration", NS)
                    if "value" in e.attrib
                ]
                if enum_values:
                    simple_type_enums[name] = enum_values
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
    return elements, complex_types, child_element_forms, simple_types, simple_type_enums


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
    simple_types: dict[str, str],
    simple_type_enums: dict[str, list[str]],
    child_element_forms: dict[str, str],
    soap_actions: dict[str, str],
    target_namespace: str,
    binding_style: Literal["document", "rpc"],
    body_uses: dict[str, str],
    endpoint_path: str,
    rpc_message_parts: dict[str, list[XSDField]] | None = None,
) -> Operation:
    operation_name = operation.attrib.get("name")
    if not operation_name:
        raise ValueError("Encountered WSDL operation without a name.")
    input_tag = operation.find("wsdl:input", NS)
    if input_tag is None:
        raise ValueError(f"WSDL operation '{operation_name}' has no <wsdl:input> child element.")
    input_message_name = _qname_local(input_tag.attrib.get("message", ""))
    if not input_message_name:
        logger.warning(
            "WSDL operation '%s' <wsdl:input> has no 'message' attribute, using empty params.",
            operation_name,
        )
    output_element_name = ""
    output_tag = operation.find("wsdl:output", NS)
    output_message_name = ""
    if output_tag is not None:
        output_message_name = _qname_local(output_tag.attrib.get("message", ""))
        if not output_message_name:
            logger.warning(
                "WSDL operation '%s' <wsdl:output> has no 'message' attribute, "
                "using empty response.",
                operation_name,
            )
        else:
            output_element_name = messages.get(output_message_name, "")

    if binding_style == "rpc" and rpc_message_parts is not None:
        input_fields = rpc_message_parts.get(input_message_name, []) if input_message_name else []
        output_fields = (
            rpc_message_parts.get(output_message_name, []) if output_message_name else []
        )
        request_element = operation_name
    else:
        input_element_name = messages.get(input_message_name, "") if input_message_name else ""
        input_fields = (
            _resolve_wsdl_fields(
                input_element_name,
                elements=elements,
                complex_types=complex_types,
            )
            if input_element_name
            else []
        )
        output_fields = (
            _resolve_wsdl_fields(
                output_element_name,
                elements=elements,
                complex_types=complex_types,
            )
            if output_element_name
            else []
        )
        request_element = (
            messages.get(input_message_name, "") if input_message_name else ""
        ) or operation_name

    body_use = body_uses.get(operation_name, "literal")

    return Operation(
        id=operation_name,
        name=_humanize_identifier(operation_name),
        description=f"SOAP operation {operation_name}.",
        method="POST",
        path=endpoint_path,
        params=[
            Param(
                name=field.name,
                type=(
                    "array"
                    if field.repeated
                    else _ir_type_for_xsd(field.type_name, complex_types, simple_types)
                ),
                required=field.required,
                json_schema=_json_schema_for_field(
                    field, complex_types, simple_types, simple_type_enums
                ),
                source=SourceType.extractor,
                confidence=1.0,
            )
            for field in input_fields
        ],
        response_schema=_response_schema(output_fields, complex_types, simple_types),
        risk=_risk_for_operation(operation_name),
        soap=SoapOperationConfig(
            target_namespace=target_namespace,
            request_element=request_element,
            response_element=output_element_name or None,
            soap_action=soap_actions.get(operation_name),
            binding_style=binding_style,
            body_use=cast(Literal["literal", "encoded"], body_use),
            child_element_form=cast(
                Literal["qualified", "unqualified"],
                child_element_forms.get(request_element, "qualified"),
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
    simple_types: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    if not fields:
        return None
    properties: dict[str, Any] = {}
    for field in fields:
        if field.repeated:
            item_type = _ir_type_for_xsd(field.type_name, complex_types, simple_types)
            properties[field.name] = {"type": "array", "items": {"type": item_type}}
        else:
            properties[field.name] = {
                "type": _ir_type_for_xsd(field.type_name, complex_types, simple_types)
            }
    required = [field.name for field in fields if field.required]
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _ir_type_for_xsd(
    type_name: str,
    complex_types: dict[str, list[XSDField]],
    simple_types: dict[str, str] | None = None,
) -> str:
    if type_name in XSD_TYPE_MAP:
        return XSD_TYPE_MAP[type_name]
    if simple_types and type_name in simple_types:
        base = simple_types[type_name]
        return XSD_TYPE_MAP.get(base, "string")
    if type_name in complex_types:
        return "object"
    return "object"


def _json_schema_for_field(
    field: XSDField,
    complex_types: dict[str, list[XSDField]],
    simple_types: dict[str, str] | None = None,
    simple_type_enums: dict[str, list[str]] | None = None,
) -> dict[str, Any] | None:
    """Build a JSON Schema dict for a field when it references a complex type or enum.

    Returns ``None`` when the field is a plain scalar (no extra schema needed).
    For arrays of complex items, the schema describes the ``items`` sub-structure.
    For simpleType restrictions with enumerations, includes ``"enum"`` constraint.
    """
    if simple_type_enums and field.type_name in simple_type_enums:
        base = (simple_types or {}).get(field.type_name, "string")
        json_type = {"string": "string", "integer": "integer", "int": "integer"}.get(base, "string")
        return {"type": json_type, "enum": simple_type_enums[field.type_name]}

    if field.repeated:
        item_type = _ir_type_for_xsd(field.type_name, complex_types, simple_types)
        if item_type == "object" and field.type_name in complex_types:
            item_schema = _complex_type_to_schema(
                field.type_name, complex_types, simple_types, simple_type_enums=simple_type_enums
            )
            return {"type": "array", "items": item_schema}
        # Scalar items — still emit json_schema so loader knows item type
        return {"type": "array", "items": {"type": item_type}}

    if field.type_name in complex_types:
        return _complex_type_to_schema(
            field.type_name, complex_types, simple_types, simple_type_enums=simple_type_enums
        )

    return None


def _complex_type_to_schema(
    type_name: str,
    complex_types: dict[str, list[XSDField]],
    simple_types: dict[str, str] | None = None,
    _seen: set[str] | None = None,
    *,
    simple_type_enums: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Convert a named XSD complexType into a JSON Schema object definition."""
    if _seen is None:
        _seen = set()
    if type_name in _seen:
        return {"type": "object"}
    _seen.add(type_name)

    fields = complex_types.get(type_name, [])
    properties: dict[str, Any] = {}
    required: list[str] = []
    for f in fields:
        if simple_type_enums and f.type_name in simple_type_enums:
            base = (simple_types or {}).get(f.type_name, "string")
            json_type = {"string": "string", "integer": "integer", "int": "integer"}.get(
                base, "string"
            )
            prop: dict[str, Any] = {"type": json_type, "enum": simple_type_enums[f.type_name]}
        elif f.type_name in complex_types:
            prop = _complex_type_to_schema(
                f.type_name,
                complex_types,
                simple_types,
                _seen,
                simple_type_enums=simple_type_enums,
            )
        else:
            prop_type = _ir_type_for_xsd(f.type_name, complex_types, simple_types)
            prop = {"type": prop_type}

        if f.repeated:
            item_type = _ir_type_for_xsd(f.type_name, complex_types, simple_types)
            if f.type_name in complex_types:
                prop = {
                    "type": "array",
                    "items": _complex_type_to_schema(
                        f.type_name,
                        complex_types,
                        simple_types,
                        _seen,
                        simple_type_enums=simple_type_enums,
                    ),
                }
            else:
                prop = {"type": "array", "items": {"type": item_type}}
        if f.required:
            required.append(f.name)
        properties[f.name] = prop

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


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


__all__ = ["SOAPWSDLExtractor"]
