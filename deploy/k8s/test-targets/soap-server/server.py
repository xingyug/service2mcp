import uuid
from datetime import datetime, timedelta
from spyne import Application, Service, rpc, Unicode, Boolean, Integer, DateTime, ComplexModel
from spyne.protocol.soap import Soap11
from spyne.server.wsgi import WsgiApplication
from wsgiref.simple_server import make_server


class OrderPayload(ComplexModel):
    sku = Unicode
    quantity = Integer


class OrderService(Service):
    @rpc(Unicode, Boolean, _returns=(Unicode, DateTime))
    def GetOrderStatus(ctx, orderId, includeHistory):
        return ('shipped', datetime.utcnow() + timedelta(days=3))

    @rpc(Unicode, Unicode, OrderPayload, _returns=Unicode)
    def SubmitOrder(ctx, customerId, priority, order):
        return str(uuid.uuid4())


def healthz_app(environ, start_response):
    if environ['PATH_INFO'] == '/healthz':
        start_response('200 OK', [('Content-Type', 'text/plain')])
        return [b'ok']
    return soap_wsgi(environ, start_response)


app = Application(
    [OrderService],
    tns='urn:example:order-service',
    in_protocol=Soap11(validator='lxml'),
    out_protocol=Soap11(),
)
soap_wsgi = WsgiApplication(app)

if __name__ == '__main__':
    print("SOAP OrderService listening on :8000 (WSDL at /?wsdl)")
    server = make_server('0.0.0.0', 8000, healthz_app)
    server.serve_forever()
