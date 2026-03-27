"""Tests for the SOAP/WSDL extractor foundation."""

from __future__ import annotations

from pathlib import Path

import pytest

from libs.extractors.base import SourceConfig, TypeDetector
from libs.extractors.soap import SOAPWSDLExtractor
from libs.ir.models import RiskLevel

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "tests" / "fixtures"
WSDL_FIXTURE_PATH = FIXTURES_DIR / "wsdl" / "order_service.wsdl"


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
