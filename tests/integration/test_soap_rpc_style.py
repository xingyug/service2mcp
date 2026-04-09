"""Integration tests for SOAP RPC-style binding support."""

from __future__ import annotations

from pathlib import Path

from libs.extractors.base import SourceConfig
from libs.extractors.soap import SOAPWSDLExtractor

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
WSDL_FIXTURE_PATH = FIXTURES_DIR / "wsdl" / "order_service.wsdl"

# ---------------------------------------------------------------------------
# Inline WSDL fixtures
# ---------------------------------------------------------------------------

RPC_LITERAL_WSDL = """\
<?xml version="1.0" encoding="UTF-8"?>
<wsdl:definitions
    xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
    xmlns:tns="http://example.com/products"
    xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema"
    name="ProductService"
    targetNamespace="http://example.com/products">

  <wsdl:message name="GetProductInput">
    <wsdl:part name="productId" type="xsd:int"/>
    <wsdl:part name="includeDetails" type="xsd:boolean"/>
  </wsdl:message>
  <wsdl:message name="GetProductOutput">
    <wsdl:part name="productName" type="xsd:string"/>
    <wsdl:part name="price" type="xsd:double"/>
  </wsdl:message>

  <wsdl:portType name="ProductPortType">
    <wsdl:operation name="GetProduct">
      <wsdl:input message="tns:GetProductInput"/>
      <wsdl:output message="tns:GetProductOutput"/>
    </wsdl:operation>
  </wsdl:portType>

  <wsdl:binding name="ProductBinding" type="tns:ProductPortType">
    <soap:binding transport="http://schemas.xmlsoap.org/soap/http" style="rpc"/>
    <wsdl:operation name="GetProduct">
      <soap:operation soapAction="http://example.com/products/GetProduct"/>
      <wsdl:input><soap:body use="literal" namespace="http://example.com/products"/></wsdl:input>
      <wsdl:output><soap:body use="literal" namespace="http://example.com/products"/></wsdl:output>
    </wsdl:operation>
  </wsdl:binding>

  <wsdl:service name="ProductService">
    <wsdl:port name="ProductPort" binding="tns:ProductBinding">
      <soap:address location="http://example.com/soap/products"/>
    </wsdl:port>
  </wsdl:service>
</wsdl:definitions>"""

RPC_ENCODED_WSDL = """\
<?xml version="1.0" encoding="UTF-8"?>
<wsdl:definitions
    xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
    xmlns:tns="http://example.com/legacy"
    xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema"
    name="LegacyService"
    targetNamespace="http://example.com/legacy">

  <wsdl:message name="LookupInput">
    <wsdl:part name="accountId" type="xsd:string"/>
  </wsdl:message>
  <wsdl:message name="LookupOutput">
    <wsdl:part name="balance" type="xsd:decimal"/>
  </wsdl:message>

  <wsdl:portType name="LegacyPortType">
    <wsdl:operation name="LookupBalance">
      <wsdl:input message="tns:LookupInput"/>
      <wsdl:output message="tns:LookupOutput"/>
    </wsdl:operation>
  </wsdl:portType>

  <wsdl:binding name="LegacyBinding" type="tns:LegacyPortType">
    <soap:binding transport="http://schemas.xmlsoap.org/soap/http" style="rpc"/>
    <wsdl:operation name="LookupBalance">
      <soap:operation soapAction="http://example.com/legacy/LookupBalance"/>
      <wsdl:input><soap:body use="encoded"
          encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"/></wsdl:input>
      <wsdl:output><soap:body use="encoded"
          encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"/></wsdl:output>
    </wsdl:operation>
  </wsdl:binding>

  <wsdl:service name="LegacyService">
    <wsdl:port name="LegacyPort" binding="tns:LegacyBinding">
      <soap:address location="http://example.com/soap/legacy"/>
    </wsdl:port>
  </wsdl:service>
</wsdl:definitions>"""

RPC_MULTIPLE_OPS_WSDL = """\
<?xml version="1.0" encoding="UTF-8"?>
<wsdl:definitions
    xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
    xmlns:tns="http://example.com/inventory"
    xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema"
    name="InventoryService"
    targetNamespace="http://example.com/inventory">

  <wsdl:message name="GetStockInput">
    <wsdl:part name="sku" type="xsd:string"/>
  </wsdl:message>
  <wsdl:message name="GetStockOutput">
    <wsdl:part name="quantity" type="xsd:int"/>
  </wsdl:message>
  <wsdl:message name="UpdateStockInput">
    <wsdl:part name="sku" type="xsd:string"/>
    <wsdl:part name="delta" type="xsd:int"/>
  </wsdl:message>
  <wsdl:message name="UpdateStockOutput">
    <wsdl:part name="newQuantity" type="xsd:int"/>
  </wsdl:message>
  <wsdl:message name="DeleteItemInput">
    <wsdl:part name="sku" type="xsd:string"/>
  </wsdl:message>
  <wsdl:message name="DeleteItemOutput">
    <wsdl:part name="success" type="xsd:boolean"/>
  </wsdl:message>

  <wsdl:portType name="InventoryPortType">
    <wsdl:operation name="GetStock">
      <wsdl:input message="tns:GetStockInput"/>
      <wsdl:output message="tns:GetStockOutput"/>
    </wsdl:operation>
    <wsdl:operation name="UpdateStock">
      <wsdl:input message="tns:UpdateStockInput"/>
      <wsdl:output message="tns:UpdateStockOutput"/>
    </wsdl:operation>
    <wsdl:operation name="DeleteItem">
      <wsdl:input message="tns:DeleteItemInput"/>
      <wsdl:output message="tns:DeleteItemOutput"/>
    </wsdl:operation>
  </wsdl:portType>

  <wsdl:binding name="InventoryBinding" type="tns:InventoryPortType">
    <soap:binding transport="http://schemas.xmlsoap.org/soap/http" style="rpc"/>
    <wsdl:operation name="GetStock">
      <soap:operation soapAction="http://example.com/inventory/GetStock"/>
      <wsdl:input><soap:body use="literal"/></wsdl:input>
      <wsdl:output><soap:body use="literal"/></wsdl:output>
    </wsdl:operation>
    <wsdl:operation name="UpdateStock">
      <soap:operation soapAction="http://example.com/inventory/UpdateStock"/>
      <wsdl:input><soap:body use="literal"/></wsdl:input>
      <wsdl:output><soap:body use="literal"/></wsdl:output>
    </wsdl:operation>
    <wsdl:operation name="DeleteItem">
      <soap:operation soapAction="http://example.com/inventory/DeleteItem"/>
      <wsdl:input><soap:body use="literal"/></wsdl:input>
      <wsdl:output><soap:body use="literal"/></wsdl:output>
    </wsdl:operation>
  </wsdl:binding>

  <wsdl:service name="InventoryService">
    <wsdl:port name="InventoryPort" binding="tns:InventoryBinding">
      <soap:address location="http://example.com/soap/inventory"/>
    </wsdl:port>
  </wsdl:service>
</wsdl:definitions>"""

RPC_COMPLEX_TYPE_PARTS_WSDL = """\
<?xml version="1.0" encoding="UTF-8"?>
<wsdl:definitions
    xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
    xmlns:tns="http://example.com/crm"
    xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema"
    name="CRMService"
    targetNamespace="http://example.com/crm">

  <wsdl:types>
    <xsd:schema targetNamespace="http://example.com/crm">
      <xsd:complexType name="Address">
        <xsd:sequence>
          <xsd:element name="street" type="xsd:string"/>
          <xsd:element name="city" type="xsd:string"/>
          <xsd:element name="zip" type="xsd:string"/>
        </xsd:sequence>
      </xsd:complexType>
    </xsd:schema>
  </wsdl:types>

  <wsdl:message name="CreateCustomerInput">
    <wsdl:part name="name" type="xsd:string"/>
    <wsdl:part name="address" type="tns:Address"/>
  </wsdl:message>
  <wsdl:message name="CreateCustomerOutput">
    <wsdl:part name="customerId" type="xsd:string"/>
  </wsdl:message>

  <wsdl:portType name="CRMPortType">
    <wsdl:operation name="CreateCustomer">
      <wsdl:input message="tns:CreateCustomerInput"/>
      <wsdl:output message="tns:CreateCustomerOutput"/>
    </wsdl:operation>
  </wsdl:portType>

  <wsdl:binding name="CRMBinding" type="tns:CRMPortType">
    <soap:binding transport="http://schemas.xmlsoap.org/soap/http" style="rpc"/>
    <wsdl:operation name="CreateCustomer">
      <soap:operation soapAction="http://example.com/crm/CreateCustomer"/>
      <wsdl:input><soap:body use="literal"/></wsdl:input>
      <wsdl:output><soap:body use="literal"/></wsdl:output>
    </wsdl:operation>
  </wsdl:binding>

  <wsdl:service name="CRMService">
    <wsdl:port name="CRMPort" binding="tns:CRMBinding">
      <soap:address location="http://example.com/soap/crm"/>
    </wsdl:port>
  </wsdl:service>
</wsdl:definitions>"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_rpc_literal_extraction() -> None:
    """RPC/literal WSDL with typed parts -> params extracted with correct types."""
    extractor = SOAPWSDLExtractor()

    ir = extractor.extract(SourceConfig(file_content=RPC_LITERAL_WSDL))

    assert ir.protocol == "soap"
    assert ir.service_name == "product-service"
    assert len(ir.operations) == 1

    op = ir.operations[0]
    assert op.id == "GetProduct"
    assert len(op.params) == 2

    param_map = {p.name: p for p in op.params}
    assert param_map["productId"].type == "integer"
    assert param_map["productId"].required is True
    assert param_map["includeDetails"].type == "boolean"
    assert param_map["includeDetails"].required is True

    assert op.soap is not None
    assert op.soap.binding_style == "rpc"
    assert op.soap.body_use == "literal"

    # Response schema should reflect output parts
    assert op.response_schema is not None
    assert "productName" in op.response_schema["properties"]
    assert "price" in op.response_schema["properties"]
    assert op.response_schema["properties"]["productName"]["type"] == "string"
    assert op.response_schema["properties"]["price"]["type"] == "number"


def test_rpc_encoded_extraction() -> None:
    """RPC/encoded WSDL -> params extracted (encoding affects wire format only)."""
    extractor = SOAPWSDLExtractor()

    ir = extractor.extract(SourceConfig(file_content=RPC_ENCODED_WSDL))

    assert ir.protocol == "soap"
    assert ir.service_name == "legacy-service"
    assert len(ir.operations) == 1

    op = ir.operations[0]
    assert op.id == "LookupBalance"
    assert len(op.params) == 1
    assert op.params[0].name == "accountId"
    assert op.params[0].type == "string"

    assert op.soap is not None
    assert op.soap.binding_style == "rpc"
    assert op.soap.body_use == "encoded"

    assert op.response_schema is not None
    assert op.response_schema["properties"]["balance"]["type"] == "number"


def test_rpc_multiple_operations() -> None:
    """Multiple operations in RPC-style service -> all extracted."""
    extractor = SOAPWSDLExtractor()

    ir = extractor.extract(SourceConfig(file_content=RPC_MULTIPLE_OPS_WSDL))

    assert len(ir.operations) == 3
    op_ids = {op.id for op in ir.operations}
    assert op_ids == {"GetStock", "UpdateStock", "DeleteItem"}

    get_stock = next(op for op in ir.operations if op.id == "GetStock")
    assert len(get_stock.params) == 1
    assert get_stock.params[0].name == "sku"
    assert get_stock.params[0].type == "string"

    update_stock = next(op for op in ir.operations if op.id == "UpdateStock")
    assert len(update_stock.params) == 2
    param_map = {p.name: p for p in update_stock.params}
    assert param_map["sku"].type == "string"
    assert param_map["delta"].type == "integer"

    delete_item = next(op for op in ir.operations if op.id == "DeleteItem")
    assert len(delete_item.params) == 1
    assert delete_item.params[0].name == "sku"

    # Verify all have rpc binding style
    for op in ir.operations:
        assert op.soap is not None
        assert op.soap.binding_style == "rpc"


def test_rpc_complex_type_parts() -> None:
    """Parts referencing complexTypes -> object-type params."""
    extractor = SOAPWSDLExtractor()

    ir = extractor.extract(SourceConfig(file_content=RPC_COMPLEX_TYPE_PARTS_WSDL))

    assert len(ir.operations) == 1
    op = ir.operations[0]
    assert op.id == "CreateCustomer"
    assert len(op.params) == 2

    param_map = {p.name: p for p in op.params}
    assert param_map["name"].type == "string"
    assert param_map["address"].type == "object"

    assert op.soap is not None
    assert op.soap.binding_style == "rpc"


def test_document_style_still_works() -> None:
    """Existing document-style WSDL still works after refactor."""
    extractor = SOAPWSDLExtractor()

    ir = extractor.extract(SourceConfig(file_path=str(WSDL_FIXTURE_PATH)))

    assert ir.protocol == "soap"
    assert ir.service_name == "order-service"
    assert len(ir.operations) == 2

    get_order = next(op for op in ir.operations if op.id == "GetOrderStatus")
    assert len(get_order.params) == 2
    param_map = {p.name: p for p in get_order.params}
    assert param_map["orderId"].type == "string"
    assert param_map["orderId"].required is True

    assert get_order.soap is not None
    assert get_order.soap.binding_style == "document"
    assert get_order.soap.body_use == "literal"

    submit_order = next(op for op in ir.operations if op.id == "SubmitOrder")
    assert len(submit_order.params) == 3
