"""Tests for the SOAP/WSDL extractor foundation."""

from __future__ import annotations

from pathlib import Path

import pytest

from libs.extractors.base import SourceConfig, TypeDetector
from libs.extractors.soap import SOAPWSDLExtractor
from libs.ir.models import RiskLevel

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "tests" / "fixtures"
WSDL_FIXTURE_PATH = FIXTURES_DIR / "wsdl" / "order_service.wsdl"
REAL_TARGET_WSDL_FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "deploy"
    / "k8s"
    / "real-targets"
    / "soap-cxf"
    / "src"
    / "main"
    / "resources"
    / "wsdl"
    / "OrderService.wsdl"
)


def test_detects_wsdl_fixture() -> None:
    extractor = SOAPWSDLExtractor()

    confidence = extractor.detect(SourceConfig(file_path=str(WSDL_FIXTURE_PATH)))

    assert confidence >= 0.9


def test_extracts_document_literal_operations_and_metadata() -> None:
    extractor = SOAPWSDLExtractor()

    service_ir = extractor.extract(SourceConfig(file_path=str(WSDL_FIXTURE_PATH)))

    assert service_ir.protocol == "soap"
    assert service_ir.service_name == "order-service"
    assert service_ir.base_url == "https://orders.example.com/soap/order-service"
    assert service_ir.metadata["wsdl_target_namespace"] == "http://example.com/orders/wsdl"
    assert service_ir.metadata["wsdl_service"] == "OrderService"
    assert service_ir.metadata["wsdl_port_type"] == "OrderServicePortType"
    assert service_ir.metadata["wsdl_binding"] == "OrderServiceBinding"
    assert service_ir.metadata["soap_actions"] == {
        "GetOrderStatus": "http://example.com/orders/GetOrderStatus",
        "SubmitOrder": "http://example.com/orders/SubmitOrder",
    }
    assert len(service_ir.operations) == 2

    get_order_status = next(
        operation for operation in service_ir.operations if operation.id == "GetOrderStatus"
    )
    assert get_order_status.method == "POST"
    assert get_order_status.path == "/soap/order-service"
    assert get_order_status.soap is not None
    assert get_order_status.soap.target_namespace == "http://example.com/orders/wsdl"
    assert get_order_status.soap.request_element == "GetOrderStatusRequest"
    assert get_order_status.soap.response_element == "GetOrderStatusResponse"
    assert get_order_status.soap.soap_action == "http://example.com/orders/GetOrderStatus"
    assert get_order_status.soap.child_element_form == "qualified"
    assert get_order_status.risk.risk_level is RiskLevel.safe
    assert {param.name: param.type for param in get_order_status.params} == {
        "orderId": "string",
        "includeHistory": "boolean",
    }
    assert [param.required for param in get_order_status.params] == [True, False]

    submit_order = next(
        operation for operation in service_ir.operations if operation.id == "SubmitOrder"
    )
    assert submit_order.soap is not None
    assert submit_order.soap.request_element == "SubmitOrderRequest"
    assert submit_order.soap.response_element == "SubmitOrderResponse"
    assert submit_order.risk.risk_level is RiskLevel.cautious
    assert {param.name: param.type for param in submit_order.params} == {
        "customerId": "string",
        "priority": "string",
        "order": "object",
    }


def test_extract_treats_xsd_defaulted_fields_as_optional(tmp_path: Path) -> None:
    extractor = SOAPWSDLExtractor()

    wsdl = """\
<?xml version="1.0" encoding="UTF-8"?>
<wsdl:definitions
  xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
  xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
  xmlns:tns="urn:test"
  xmlns:xsd="http://www.w3.org/2001/XMLSchema"
  targetNamespace="urn:test"
  name="DefaultedFieldService">

  <wsdl:types>
    <xsd:schema targetNamespace="urn:test" elementFormDefault="qualified">
      <xsd:element name="GetOrderStatusRequest">
        <xsd:complexType>
          <xsd:sequence>
            <xsd:element name="orderId" type="xsd:string"/>
            <xsd:element name="includeHistory" type="xsd:boolean" default="false"/>
          </xsd:sequence>
        </xsd:complexType>
      </xsd:element>
      <xsd:element name="GetOrderStatusResponse">
        <xsd:complexType>
          <xsd:sequence>
            <xsd:element name="status" type="xsd:string"/>
          </xsd:sequence>
        </xsd:complexType>
      </xsd:element>
    </xsd:schema>
  </wsdl:types>

  <wsdl:message name="GetOrderStatusInput">
    <wsdl:part name="body" element="tns:GetOrderStatusRequest"/>
  </wsdl:message>
  <wsdl:message name="GetOrderStatusOutput">
    <wsdl:part name="body" element="tns:GetOrderStatusResponse"/>
  </wsdl:message>

  <wsdl:portType name="DefaultedFieldPortType">
    <wsdl:operation name="GetOrderStatus">
      <wsdl:input message="tns:GetOrderStatusInput"/>
      <wsdl:output message="tns:GetOrderStatusOutput"/>
    </wsdl:operation>
  </wsdl:portType>

  <wsdl:binding name="DefaultedFieldBinding" type="tns:DefaultedFieldPortType">
    <soap:binding style="document" transport="http://schemas.xmlsoap.org/soap/http"/>
    <wsdl:operation name="GetOrderStatus">
      <soap:operation soapAction="urn:test/GetOrderStatus"/>
      <wsdl:input><soap:body use="literal"/></wsdl:input>
      <wsdl:output><soap:body use="literal"/></wsdl:output>
    </wsdl:operation>
  </wsdl:binding>

  <wsdl:service name="DefaultedFieldService">
    <wsdl:port name="DefaultedFieldPort" binding="tns:DefaultedFieldBinding">
      <soap:address location="https://example.test/soap"/>
    </wsdl:port>
  </wsdl:service>
</wsdl:definitions>"""
    wsdl_path = tmp_path / "defaulted_field_service.wsdl"
    wsdl_path.write_text(wsdl)

    service_ir = extractor.extract(SourceConfig(file_path=str(wsdl_path)))

    get_order_status = next(
        operation for operation in service_ir.operations if operation.id == "GetOrderStatus"
    )
    params = {param.name: param for param in get_order_status.params}

    assert params["orderId"].required is True
    assert params["includeHistory"].required is False


def test_extract_tracks_unqualified_child_elements_from_schema_default() -> None:
    extractor = SOAPWSDLExtractor()

    service_ir = extractor.extract(SourceConfig(file_path=str(REAL_TARGET_WSDL_FIXTURE_PATH)))

    get_order_status = next(
        operation for operation in service_ir.operations if operation.id == "GetOrderStatus"
    )

    assert get_order_status.soap is not None
    assert get_order_status.soap.child_element_form == "unqualified"


def test_extract_raises_on_operation_missing_wsdl_input(tmp_path: Path) -> None:
    """WSDL operation with no <wsdl:input> must raise, not crash on NoneType."""
    wsdl = """\
<?xml version="1.0" encoding="UTF-8"?>
<wsdl:definitions
  xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
  xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
  xmlns:tns="urn:test"
  xmlns:xsd="http://www.w3.org/2001/XMLSchema"
  targetNamespace="urn:test"
  name="BadService">

  <wsdl:types>
    <xsd:schema targetNamespace="urn:test">
      <xsd:element name="PingRequest"><xsd:complexType/></xsd:element>
    </xsd:schema>
  </wsdl:types>

  <wsdl:message name="PingInput">
    <wsdl:part name="body" element="tns:PingRequest"/>
  </wsdl:message>

  <wsdl:portType name="BadPortType">
    <wsdl:operation name="Ping">
      <!-- intentionally missing <wsdl:input> -->
      <wsdl:output message="tns:PingInput"/>
    </wsdl:operation>
  </wsdl:portType>

  <wsdl:binding name="BadBinding" type="tns:BadPortType">
    <soap:binding style="document" transport="http://schemas.xmlsoap.org/soap/http"/>
    <wsdl:operation name="Ping">
      <wsdl:output><soap:body use="literal"/></wsdl:output>
    </wsdl:operation>
  </wsdl:binding>

  <wsdl:service name="BadService">
    <wsdl:port name="BadPort" binding="tns:BadBinding">
      <soap:address location="https://example.test/soap"/>
    </wsdl:port>
  </wsdl:service>
</wsdl:definitions>"""
    wsdl_path = tmp_path / "bad_service.wsdl"
    wsdl_path.write_text(wsdl)
    extractor = SOAPWSDLExtractor()
    with pytest.raises(ValueError, match="has no <wsdl:input> child element"):
        extractor.extract(SourceConfig(file_path=str(wsdl_path)))


def test_type_detector_can_select_soap_wsdl_extractor() -> None:
    detector = TypeDetector([SOAPWSDLExtractor()])

    detection = detector.detect(SourceConfig(file_path=str(WSDL_FIXTURE_PATH)))

    assert detection.protocol_name == "soap"


def test_soap_operations_have_error_schema() -> None:
    extractor = SOAPWSDLExtractor()

    service_ir = extractor.extract(SourceConfig(file_path=str(WSDL_FIXTURE_PATH)))

    assert len(service_ir.operations) >= 1
    for op in service_ir.operations:
        assert op.error_schema is not None
        schema = op.error_schema.default_error_schema
        assert schema is not None
        assert schema["type"] == "object"
        props = schema["properties"]
        assert "faultcode" in props
        assert "faultstring" in props
        assert "detail" in props
        assert schema["required"] == ["faultcode", "faultstring"]


def test_detect_returns_zero_for_none_content() -> None:
    """Test detection returns 0.0 when content is None."""
    extractor = SOAPWSDLExtractor()

    # Test with SourceConfig that has no content
    confidence = extractor.detect(SourceConfig(url="https://nonexistent.invalid"))

    assert confidence == 0.0


def test_detect_wsdl_by_extension_and_content() -> None:
    """Test detection returns high confidence for .wsdl files with WSDL content."""
    import tempfile

    extractor = SOAPWSDLExtractor()

    wsdl_content = """<?xml version="1.0"?>
    <wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/">
    </wsdl:definitions>"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".wsdl", delete=False) as f:
        f.write(wsdl_content)
        f.flush()

        confidence = extractor.detect(SourceConfig(file_path=f.name))
        assert confidence == 0.98


def test_detect_wsdl_by_content_only() -> None:
    """Test detection for WSDL content without .wsdl extension."""
    extractor = SOAPWSDLExtractor()

    wsdl_content = """<?xml version="1.0"?>
    <wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/">
    </wsdl:definitions>"""

    confidence = extractor.detect(SourceConfig(file_content=wsdl_content))
    assert confidence == 0.95


def test_detect_soap_address_in_wsdl() -> None:
    """Test detection for content with soap:address and wsdl:definitions."""
    extractor = SOAPWSDLExtractor()

    content = """<?xml version="1.0"?>
    <wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/">
        <soap:address location="http://example.com"/>
    </wsdl:definitions>"""

    confidence = extractor.detect(SourceConfig(file_content=content))
    assert confidence == 0.7


def test_extract_raises_for_none_content() -> None:
    """Test extraction raises ValueError when content is None."""
    extractor = SOAPWSDLExtractor()

    with pytest.raises(ValueError, match="Could not read source content"):
        extractor.extract(SourceConfig(url="https://nonexistent.invalid"))


def test_extract_raises_for_non_definitions_root() -> None:
    """Test extraction raises ValueError when root is not wsdl:definitions."""
    extractor = SOAPWSDLExtractor()

    content = """<?xml version="1.0"?>
    <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"/>"""

    with pytest.raises(ValueError, match="SOAP extractor requires a WSDL definitions document"):
        extractor.extract(SourceConfig(file_content=content))


def test_extract_raises_for_no_services() -> None:
    """Test extraction raises ValueError when no wsdl:service definitions found."""
    extractor = SOAPWSDLExtractor()

    content = """<?xml version="1.0"?>
    <wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                      targetNamespace="http://example.com">
    </wsdl:definitions>"""

    with pytest.raises(ValueError, match="No wsdl:service definitions found"):
        extractor.extract(SourceConfig(file_content=content))


def test_extract_raises_for_missing_port() -> None:
    """Test extraction raises ValueError when service is missing port definition."""
    extractor = SOAPWSDLExtractor()

    content = """<?xml version="1.0"?>
    <wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                      targetNamespace="http://example.com">
        <wsdl:service name="TestService">
        </wsdl:service>
    </wsdl:definitions>"""

    with pytest.raises(ValueError, match="WSDL service is missing a port definition"):
        extractor.extract(SourceConfig(file_content=content))


def test_extract_raises_for_missing_binding() -> None:
    """Test extraction raises ValueError when binding not found."""
    extractor = SOAPWSDLExtractor()

    content = """<?xml version="1.0"?>
    <wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                      xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
                      xmlns:tns="http://example.com"
                      targetNamespace="http://example.com">
        <wsdl:service name="TestService">
            <wsdl:port name="TestPort" binding="tns:MissingBinding">
                <soap:address location="http://example.com/soap"/>
            </wsdl:port>
        </wsdl:service>
    </wsdl:definitions>"""

    with pytest.raises(ValueError, match="WSDL binding 'MissingBinding' not found"):
        extractor.extract(SourceConfig(file_content=content))


def test_extract_raises_for_missing_port_type() -> None:
    """Test extraction raises ValueError when portType not found."""
    extractor = SOAPWSDLExtractor()

    content = """<?xml version="1.0"?>
    <wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                      xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
                      xmlns:tns="http://example.com"
                      targetNamespace="http://example.com">
        <wsdl:binding name="TestBinding" type="tns:MissingPortType">
            <soap:binding style="document" transport="http://schemas.xmlsoap.org/soap/http"/>
        </wsdl:binding>
        <wsdl:service name="TestService">
            <wsdl:port name="TestPort" binding="tns:TestBinding">
                <soap:address location="http://example.com/soap"/>
            </wsdl:port>
        </wsdl:service>
    </wsdl:definitions>"""

    with pytest.raises(ValueError, match="WSDL portType 'MissingPortType' not found"):
        extractor.extract(SourceConfig(file_content=content))


def test_extract_raises_for_unsupported_binding_style() -> None:
    """Test extraction raises ValueError for unsupported binding styles (not document or rpc)."""
    extractor = SOAPWSDLExtractor()

    content = """<?xml version="1.0"?>
    <wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                      xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
                      xmlns:tns="http://example.com"
                      targetNamespace="http://example.com">
        <wsdl:portType name="TestPortType">
        </wsdl:portType>
        <wsdl:binding name="TestBinding" type="tns:TestPortType">
            <soap:binding style="mixed" transport="http://schemas.xmlsoap.org/soap/http"/>
        </wsdl:binding>
        <wsdl:service name="TestService">
            <wsdl:port name="TestPort" binding="tns:TestBinding">
                <soap:address location="http://example.com/soap"/>
            </wsdl:port>
        </wsdl:service>
    </wsdl:definitions>"""

    with pytest.raises(ValueError, match="Unsupported SOAP binding style: 'mixed'"):
        extractor.extract(SourceConfig(file_content=content))


def test_extract_accepts_encoded_soap_bodies() -> None:
    """Test extraction accepts encoded SOAP bodies (encoding only affects wire format)."""
    extractor = SOAPWSDLExtractor()

    content = """<?xml version="1.0"?>
    <wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                      xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
                      xmlns:tns="http://example.com"
                      xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                      targetNamespace="http://example.com">
        <wsdl:types>
            <xsd:schema targetNamespace="http://example.com">
                <xsd:element name="TestRequest"><xsd:complexType/></xsd:element>
                <xsd:element name="TestResponse"><xsd:complexType/></xsd:element>
            </xsd:schema>
        </wsdl:types>
        <wsdl:message name="TestInput">
            <wsdl:part name="body" element="tns:TestRequest"/>
        </wsdl:message>
        <wsdl:message name="TestOutput">
            <wsdl:part name="body" element="tns:TestResponse"/>
        </wsdl:message>
        <wsdl:portType name="TestPortType">
            <wsdl:operation name="TestOperation">
                <wsdl:input message="tns:TestInput"/>
                <wsdl:output message="tns:TestOutput"/>
            </wsdl:operation>
        </wsdl:portType>
        <wsdl:binding name="TestBinding" type="tns:TestPortType">
            <soap:binding style="document" transport="http://schemas.xmlsoap.org/soap/http"/>
            <wsdl:operation name="TestOperation">
                <wsdl:input><soap:body use="encoded"/></wsdl:input>
                <wsdl:output><soap:body use="literal"/></wsdl:output>
            </wsdl:operation>
        </wsdl:binding>
        <wsdl:service name="TestService">
            <wsdl:port name="TestPort" binding="tns:TestBinding">
                <soap:address location="http://example.com/soap"/>
            </wsdl:port>
        </wsdl:service>
    </wsdl:definitions>"""

    ir = extractor.extract(SourceConfig(file_content=content))
    assert len(ir.operations) == 1
    op = ir.operations[0]
    assert op.soap is not None
    assert op.soap.body_use == "encoded"


def test_get_content_from_url_failure() -> None:
    """Test _get_content handles URL fetch failures gracefully."""
    import httpx
    import respx

    extractor = SOAPWSDLExtractor()

    with respx.mock:
        respx.get("https://example.com/test.wsdl").mock(
            side_effect=httpx.RequestError("Connection failed")
        )

        content = extractor._get_content(SourceConfig(url="https://example.com/test.wsdl"))
        assert content is None


def test_get_content_with_auth_header() -> None:
    """Test _get_content uses auth_header correctly."""
    import httpx
    import respx

    extractor = SOAPWSDLExtractor()

    wsdl_content = """<?xml version="1.0"?>
    <wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/">
    </wsdl:definitions>"""

    with respx.mock:
        respx.get("https://example.com/test.wsdl").mock(
            return_value=httpx.Response(
                200,
                text=wsdl_content,
                request=httpx.Request("GET", "https://example.com/test.wsdl"),
            )
        )

        content = extractor._get_content(
            SourceConfig(url="https://example.com/test.wsdl", auth_header="Bearer token123")
        )
        assert content == wsdl_content


def test_get_content_with_auth_token() -> None:
    """Test _get_content uses auth_token correctly."""
    import httpx
    import respx

    extractor = SOAPWSDLExtractor()

    wsdl_content = """<?xml version="1.0"?>
    <wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/">
    </wsdl:definitions>"""

    with respx.mock:
        respx.get("https://example.com/test.wsdl").mock(
            return_value=httpx.Response(
                200,
                text=wsdl_content,
                request=httpx.Request("GET", "https://example.com/test.wsdl"),
            )
        )

        content = extractor._get_content(
            SourceConfig(url="https://example.com/test.wsdl", auth_token="token123")
        )
        assert content == wsdl_content


def test_looks_like_wsdl_with_parse_error() -> None:
    """Test _looks_like_wsdl handles XML parse errors gracefully."""
    extractor = SOAPWSDLExtractor()

    # Invalid XML content
    invalid_xml = "not xml at all"
    result = extractor._looks_like_wsdl(invalid_xml)
    assert result is False


def test_parse_messages_skips_missing_names() -> None:
    """Test _parse_messages skips messages without names."""
    import xml.etree.ElementTree as ET

    from libs.extractors.soap import _parse_messages

    content = """<?xml version="1.0"?>
    <wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                      xmlns:tns="http://example.com">
        <wsdl:message name="ValidMessage">
            <wsdl:part name="body" element="tns:ValidElement"/>
        </wsdl:message>
        <wsdl:message>
            <wsdl:part name="body" element="tns:InvalidElement"/>
        </wsdl:message>
    </wsdl:definitions>"""

    root = ET.fromstring(content)
    messages = _parse_messages(root)
    assert "ValidMessage" in messages
    assert len(messages) == 1


def test_parse_messages_skips_missing_parts() -> None:
    """Test _parse_messages skips messages without parts."""
    import xml.etree.ElementTree as ET

    from libs.extractors.soap import _parse_messages

    content = """<?xml version="1.0"?>
    <wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                      xmlns:tns="http://example.com">
        <wsdl:message name="ValidMessage">
            <wsdl:part name="body" element="tns:ValidElement"/>
        </wsdl:message>
        <wsdl:message name="EmptyMessage">
        </wsdl:message>
    </wsdl:definitions>"""

    root = ET.fromstring(content)
    messages = _parse_messages(root)
    assert "ValidMessage" in messages
    assert "EmptyMessage" not in messages


def test_parse_messages_skips_missing_element_or_type() -> None:
    """Test _parse_messages skips parts without element or type."""
    import xml.etree.ElementTree as ET

    from libs.extractors.soap import _parse_messages

    content = """<?xml version="1.0"?>
    <wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                      xmlns:tns="http://example.com">
        <wsdl:message name="ValidMessage">
            <wsdl:part name="body" element="tns:ValidElement"/>
        </wsdl:message>
        <wsdl:message name="InvalidMessage">
            <wsdl:part name="body"/>
        </wsdl:message>
    </wsdl:definitions>"""

    root = ET.fromstring(content)
    messages = _parse_messages(root)
    assert "ValidMessage" in messages
    assert "InvalidMessage" not in messages


def test_parse_schema_types_skips_missing_complex_type_names() -> None:
    """Test _parse_schema_types skips complexTypes without names."""
    import xml.etree.ElementTree as ET

    from libs.extractors.soap import _parse_schema_types

    content = """<?xml version="1.0"?>
    <wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                      xmlns:xsd="http://www.w3.org/2001/XMLSchema">
        <wsdl:types>
            <xsd:schema>
                <xsd:complexType name="ValidType">
                    <xsd:sequence>
                        <xsd:element name="field1" type="xsd:string"/>
                    </xsd:sequence>
                </xsd:complexType>
                <xsd:complexType>
                    <xsd:sequence>
                        <xsd:element name="field2" type="xsd:string"/>
                    </xsd:sequence>
                </xsd:complexType>
            </xsd:schema>
        </wsdl:types>
    </wsdl:definitions>"""

    root = ET.fromstring(content)
    (
        elements,
        complex_types,
        child_element_forms,
        simple_types,
        simple_type_enums,
    ) = _parse_schema_types(root)
    assert "ValidType" in complex_types
    assert len(complex_types) == 1
    assert child_element_forms["ValidType"] == "unqualified"


def test_parse_schema_types_skips_missing_element_names() -> None:
    """Test _parse_schema_types skips elements without names."""
    import xml.etree.ElementTree as ET

    from libs.extractors.soap import _parse_schema_types

    content = """<?xml version="1.0"?>
    <wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                      xmlns:xsd="http://www.w3.org/2001/XMLSchema">
        <wsdl:types>
            <xsd:schema>
                <xsd:element name="ValidElement" type="xsd:string"/>
                <xsd:element type="xsd:string"/>
            </xsd:schema>
        </wsdl:types>
    </wsdl:definitions>"""

    root = ET.fromstring(content)
    (
        elements,
        complex_types,
        child_element_forms,
        simple_types,
        simple_type_enums,
    ) = _parse_schema_types(root)
    assert len(elements) == 0  # ValidElement has no complex type, so not added
    assert child_element_forms["ValidElement"] == "unqualified"


def test_parse_schema_types_handles_complex_type_references() -> None:
    """Test _parse_schema_types handles element references to complex types."""
    import xml.etree.ElementTree as ET

    from libs.extractors.soap import _parse_schema_types

    content = """<?xml version="1.0"?>
    <wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                      xmlns:xsd="http://www.w3.org/2001/XMLSchema">
        <wsdl:types>
            <xsd:schema>
                <xsd:complexType name="ValidType">
                    <xsd:sequence>
                        <xsd:element name="field1" type="xsd:string"/>
                    </xsd:sequence>
                </xsd:complexType>
                <xsd:element name="ValidElement" type="ValidType"/>
            </xsd:schema>
        </wsdl:types>
    </wsdl:definitions>"""

    root = ET.fromstring(content)
    (
        elements,
        complex_types,
        child_element_forms,
        simple_types,
        simple_type_enums,
    ) = _parse_schema_types(root)
    assert "ValidElement" in elements
    assert "ValidType" in complex_types
    assert len(elements["ValidElement"]) == 1
    assert child_element_forms["ValidElement"] == "unqualified"


def test_extract_xsd_fields_skips_missing_element_names() -> None:
    """Test _extract_xsd_fields skips elements without names."""
    import xml.etree.ElementTree as ET

    from libs.extractors.soap import _extract_xsd_fields

    content = """<?xml version="1.0"?>
    <xsd:complexType xmlns:xsd="http://www.w3.org/2001/XMLSchema">
        <xsd:sequence>
            <xsd:element name="validField" type="xsd:string"/>
            <xsd:element type="xsd:string"/>
        </xsd:sequence>
    </xsd:complexType>"""

    root = ET.fromstring(content)
    fields = _extract_xsd_fields(root)
    assert len(fields) == 1
    assert fields[0].name == "validField"


def test_parse_soap_actions_skips_missing_operation_names() -> None:
    """Test _parse_soap_actions skips operations without names."""
    import xml.etree.ElementTree as ET

    from libs.extractors.soap import _parse_soap_actions

    content = """<?xml version="1.0"?>
    <wsdl:binding xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                  xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/">
        <wsdl:operation name="ValidOperation">
            <soap:operation soapAction="http://example.com/ValidAction"/>
        </wsdl:operation>
        <wsdl:operation>
            <soap:operation soapAction="http://example.com/InvalidAction"/>
        </wsdl:operation>
    </wsdl:binding>"""

    root = ET.fromstring(content)
    actions = _parse_soap_actions(root)
    assert "ValidOperation" in actions
    assert len(actions) == 1


def test_parse_soap_actions_skips_missing_soap_operations() -> None:
    """Test _parse_soap_actions skips operations without soap:operation."""
    import xml.etree.ElementTree as ET

    from libs.extractors.soap import _parse_soap_actions

    content = """<?xml version="1.0"?>
    <wsdl:binding xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                  xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/">
        <wsdl:operation name="ValidOperation">
            <soap:operation soapAction="http://example.com/ValidAction"/>
        </wsdl:operation>
        <wsdl:operation name="NoSoapOperation">
        </wsdl:operation>
    </wsdl:binding>"""

    root = ET.fromstring(content)
    actions = _parse_soap_actions(root)
    assert "ValidOperation" in actions
    assert "NoSoapOperation" not in actions


def test_build_operation_raises_for_missing_operation_name() -> None:
    """Test _build_operation raises ValueError when operation has no name."""
    import xml.etree.ElementTree as ET

    from libs.extractors.soap import _build_operation

    content = """<?xml version="1.0"?>
    <wsdl:operation xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/">
        <wsdl:input message="tns:TestInput"/>
    </wsdl:operation>"""

    operation = ET.fromstring(content)

    with pytest.raises(ValueError, match="Encountered WSDL operation without a name"):
        _build_operation(
            operation=operation,
            messages={},
            elements={},
            complex_types={},
            simple_types={},
            simple_type_enums={},
            child_element_forms={},
            soap_actions={},
            target_namespace="",
            binding_style="document",
            body_uses={},
            endpoint_path="",
        )


def test_resolve_wsdl_fields_returns_empty_for_unknown_name() -> None:
    """Test _resolve_wsdl_fields returns empty list for unknown names."""
    from libs.extractors.soap import _resolve_wsdl_fields

    fields = _resolve_wsdl_fields("UnknownType", elements={}, complex_types={})
    assert fields == []


def test_response_schema_returns_none_for_empty_fields() -> None:
    """Test _response_schema returns None for empty fields list."""
    from libs.extractors.soap import _response_schema

    schema = _response_schema([], {})
    assert schema is None


def test_ir_type_for_xsd_returns_object_for_complex_types() -> None:
    """Test _ir_type_for_xsd returns 'object' for known complex types."""
    from libs.extractors.soap import _ir_type_for_xsd

    complex_types = {"MyComplexType": []}
    ir_type = _ir_type_for_xsd("MyComplexType", complex_types)
    assert ir_type == "object"


def test_ir_type_for_xsd_returns_object_for_unknown_types() -> None:
    """Test _ir_type_for_xsd returns 'object' for unknown types."""
    from libs.extractors.soap import _ir_type_for_xsd

    ir_type = _ir_type_for_xsd("UnknownType", {})
    assert ir_type == "object"


def test_ir_type_for_xsd_resolves_simple_types() -> None:
    """Test _ir_type_for_xsd resolves named simpleType to base XSD type."""
    from libs.extractors.soap import _ir_type_for_xsd

    simple_types = {"Priority": "string", "Quantity": "int"}
    assert _ir_type_for_xsd("Priority", {}, simple_types) == "string"
    assert _ir_type_for_xsd("Quantity", {}, simple_types) == "integer"
    # simpleType checked before complex_types — simpleType wins
    assert _ir_type_for_xsd("Priority", {"Priority": []}, simple_types) == "string"
    # Without simple_types, falls to complex_types
    assert _ir_type_for_xsd("Priority", {"Priority": []}) == "object"


def test_parse_schema_types_extracts_simple_types() -> None:
    """Test _parse_schema_types parses xsd:simpleType restrictions."""
    import xml.etree.ElementTree as ET

    from libs.extractors.soap import _parse_schema_types

    content = """<?xml version="1.0"?>
    <wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                      xmlns:xsd="http://www.w3.org/2001/XMLSchema">
        <wsdl:types>
            <xsd:schema>
                <xsd:simpleType name="Priority">
                    <xsd:restriction base="xsd:string">
                        <xsd:enumeration value="STANDARD"/>
                        <xsd:enumeration value="EXPRESS"/>
                    </xsd:restriction>
                </xsd:simpleType>
                <xsd:simpleType name="Quantity">
                    <xsd:restriction base="xsd:int"/>
                </xsd:simpleType>
            </xsd:schema>
        </wsdl:types>
    </wsdl:definitions>"""

    root = ET.fromstring(content)
    _elements, _complex_types, _forms, simple_types, simple_type_enums = _parse_schema_types(root)
    assert simple_types == {"Priority": "string", "Quantity": "int"}
    assert simple_type_enums == {"Priority": ["STANDARD", "EXPRESS"]}
    assert "Quantity" not in simple_type_enums  # no enumerations


def test_soap_extraction_repeated_fields_become_array() -> None:
    """Test that maxOccurs='unbounded' XSD elements produce type='array' IR params."""
    from libs.extractors.soap import SOAPWSDLExtractor

    wsdl = """<?xml version="1.0"?>
    <definitions xmlns="http://schemas.xmlsoap.org/wsdl/"
                 xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
                 xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                 xmlns:tns="http://example.com/test"
                 name="TestService" targetNamespace="http://example.com/test">
        <types>
            <xsd:schema targetNamespace="http://example.com/test"
                        xmlns:tns="http://example.com/test">
                <xsd:simpleType name="Priority">
                    <xsd:restriction base="xsd:string">
                        <xsd:enumeration value="LOW"/>
                        <xsd:enumeration value="HIGH"/>
                    </xsd:restriction>
                </xsd:simpleType>
                <xsd:complexType name="LineItem">
                    <xsd:sequence>
                        <xsd:element name="sku" type="xsd:string"/>
                        <xsd:element name="qty" type="xsd:int"/>
                    </xsd:sequence>
                </xsd:complexType>
                <xsd:element name="SubmitOrderRequest">
                    <xsd:complexType>
                        <xsd:sequence>
                            <xsd:element name="customerId" type="xsd:string"/>
                            <xsd:element name="priority" type="tns:Priority"/>
                            <xsd:element name="items" type="tns:LineItem"
                                         maxOccurs="unbounded"/>
                        </xsd:sequence>
                    </xsd:complexType>
                </xsd:element>
                <xsd:element name="SubmitOrderResponse">
                    <xsd:complexType>
                        <xsd:sequence>
                            <xsd:element name="orderId" type="xsd:string"/>
                        </xsd:sequence>
                    </xsd:complexType>
                </xsd:element>
            </xsd:schema>
        </types>
        <message name="SubmitOrderInput">
            <part name="parameters" element="tns:SubmitOrderRequest"/>
        </message>
        <message name="SubmitOrderOutput">
            <part name="parameters" element="tns:SubmitOrderResponse"/>
        </message>
        <portType name="OrderPortType">
            <operation name="SubmitOrder">
                <input message="tns:SubmitOrderInput"/>
                <output message="tns:SubmitOrderOutput"/>
            </operation>
        </portType>
        <binding name="OrderBinding" type="tns:OrderPortType">
            <soap:binding style="document"
                          transport="http://schemas.xmlsoap.org/soap/http"/>
            <operation name="SubmitOrder">
                <soap:operation soapAction="SubmitOrder"/>
                <input><soap:body use="literal"/></input>
                <output><soap:body use="literal"/></output>
            </operation>
        </binding>
        <service name="OrderService">
            <port name="OrderPort" binding="tns:OrderBinding">
                <soap:address location="http://localhost:8080/ws"/>
            </port>
        </service>
    </definitions>"""

    from unittest.mock import patch

    from libs.extractors.base import SourceConfig

    source = SourceConfig(url="http://localhost:8080/ws?wsdl")
    extractor = SOAPWSDLExtractor()
    with patch("libs.extractors.soap.get_content", return_value=wsdl):
        ir = extractor.extract(source)

    assert len(ir.operations) == 1
    op = ir.operations[0]
    assert op.id == "SubmitOrder"

    param_map = {p.name: p for p in op.params}
    assert param_map["customerId"].type == "string"
    assert param_map["priority"].type == "string"  # simpleType resolved
    assert param_map["items"].type == "array"  # maxOccurs=unbounded


def test_risk_for_dangerous_operations() -> None:
    """Test _risk_for_operation returns dangerous risk for operations with dangerous prefixes."""
    from libs.extractors.soap import _risk_for_operation

    risk = _risk_for_operation("DeleteUser")
    assert risk.risk_level.value == "dangerous"
    assert risk.destructive is True
    assert risk.writes_state is True


def test_json_schema_for_field_returns_none_for_scalars() -> None:
    """Scalar fields should not get a json_schema."""
    from libs.extractors.soap import XSDField, _json_schema_for_field

    field = XSDField(name="orderId", type_name="string", required=True, repeated=False)
    assert _json_schema_for_field(field, {}, {}) is None


def test_json_schema_for_complex_type_field() -> None:
    """Object params referencing a complexType get a full JSON Schema."""
    from libs.extractors.soap import XSDField, _json_schema_for_field

    complex_types = {
        "Address": [
            XSDField(name="street", type_name="string", required=True, repeated=False),
            XSDField(name="city", type_name="string", required=True, repeated=False),
            XSDField(name="state", type_name="string", required=False, repeated=False),
        ],
    }
    field = XSDField(name="shippingAddress", type_name="Address", required=True, repeated=False)
    schema = _json_schema_for_field(field, complex_types, {})
    assert schema is not None
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"street", "city", "state"}
    assert schema["required"] == ["street", "city"]


def test_json_schema_for_repeated_complex_type() -> None:
    """Array params of complex items get array schema with items sub-schema."""
    from libs.extractors.soap import XSDField, _json_schema_for_field

    complex_types = {
        "OrderLineItem": [
            XSDField(name="sku", type_name="string", required=True, repeated=False),
            XSDField(name="quantity", type_name="int", required=True, repeated=False),
        ],
    }
    field = XSDField(name="items", type_name="OrderLineItem", required=True, repeated=True)
    schema = _json_schema_for_field(field, complex_types, {})
    assert schema is not None
    assert schema["type"] == "array"
    assert schema["items"]["type"] == "object"
    assert "sku" in schema["items"]["properties"]
    assert schema["items"]["properties"]["quantity"]["type"] == "integer"


def test_complex_type_to_schema_handles_cycles() -> None:
    """Recursive complexType references don't infinite-loop."""
    from libs.extractors.soap import XSDField, _complex_type_to_schema

    complex_types = {
        "Node": [
            XSDField(name="value", type_name="string", required=True, repeated=False),
            XSDField(name="child", type_name="Node", required=False, repeated=False),
        ],
    }
    schema = _complex_type_to_schema("Node", complex_types, {})
    assert schema["type"] == "object"
    assert "value" in schema["properties"]
    # The recursive child should degrade to plain {"type": "object"}
    assert schema["properties"]["child"]["type"] == "object"


def test_json_schema_for_enum_field() -> None:
    """Test that simpleType with enumerations produces json_schema with enum."""
    from libs.extractors.soap import XSDField, _json_schema_for_field

    field = XSDField(name="priority", type_name="Priority", required=True, repeated=False)
    simple_types = {"Priority": "string"}
    simple_type_enums = {"Priority": ["STANDARD", "EXPRESS", "OVERNIGHT"]}
    schema = _json_schema_for_field(field, {}, simple_types, simple_type_enums)
    assert schema is not None
    assert schema["type"] == "string"
    assert schema["enum"] == ["STANDARD", "EXPRESS", "OVERNIGHT"]


def test_json_schema_enum_in_complex_type() -> None:
    """Test that enum fields inside complexTypes get enum constraint."""
    from libs.extractors.soap import XSDField, _complex_type_to_schema

    complex_types = {
        "Order": [
            XSDField(name="id", type_name="string", required=True, repeated=False),
            XSDField(name="priority", type_name="Priority", required=True, repeated=False),
        ],
    }
    simple_types = {"Priority": "string"}
    simple_type_enums = {"Priority": ["LOW", "HIGH"]}
    schema = _complex_type_to_schema(
        "Order", complex_types, simple_types, simple_type_enums=simple_type_enums
    )
    assert schema["properties"]["id"] == {"type": "string"}
    assert schema["properties"]["priority"] == {"type": "string", "enum": ["LOW", "HIGH"]}
