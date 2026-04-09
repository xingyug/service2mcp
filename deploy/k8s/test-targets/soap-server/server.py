import uuid
from datetime import datetime, timedelta
from wsgiref.simple_server import make_server

from spyne import Application, Boolean, ComplexModel, DateTime, Integer, Service, Unicode, rpc
from spyne.protocol.soap import Soap11
from spyne.server.wsgi import WsgiApplication


class OrderPayload(ComplexModel):
    sku = Unicode
    quantity = Integer


class OrderService(Service):
    @rpc(Unicode, Boolean, _returns=(Unicode, DateTime))
    def GetOrderStatus(self, orderId, includeHistory):  # noqa: N802, N803
        return ("shipped", datetime.utcnow() + timedelta(days=3))

    @rpc(Unicode, Unicode, OrderPayload, _returns=Unicode)
    def SubmitOrder(self, customerId, priority, order):  # noqa: N802, N803
        return str(uuid.uuid4())


def healthz_app(environ, start_response):
    if environ["PATH_INFO"] == "/healthz":
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"ok"]
    return soap_wsgi(environ, start_response)


app = Application(
    [OrderService],
    tns="urn:example:order-service",
    in_protocol=Soap11(validator="lxml"),
    out_protocol=Soap11(),
)
soap_wsgi = WsgiApplication(app)

if __name__ == "__main__":
    print("SOAP OrderService listening on :8000 (WSDL at /?wsdl)")
    server = make_server("0.0.0.0", 8000, healthz_app)
    server.serve_forever()
