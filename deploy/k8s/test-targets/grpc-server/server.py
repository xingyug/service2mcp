import uuid
import time
from concurrent import futures
import grpc
from grpc_reflection.v1alpha import reflection

import inventory_pb2
import inventory_pb2_grpc

MOCK_ITEMS = [
    inventory_pb2.Item(sku="SKU-001", title="Widget A"),
    inventory_pb2.Item(sku="SKU-002", title="Gadget B"),
    inventory_pb2.Item(sku="SKU-003", title="Thingamajig C"),
    inventory_pb2.Item(sku="SKU-004", title="Doohickey D"),
    inventory_pb2.Item(sku="SKU-005", title="Gizmo E"),
]

class InventoryServicer(inventory_pb2_grpc.InventoryServiceServicer):
    def ListItems(self, request, context):
        items = MOCK_ITEMS
        if request.filter.categories:
            pass  # return all for mock
        page_size = request.page_size or 10
        return inventory_pb2.ListItemsResponse(
            items=items[:page_size],
            next_page_token=""
        )

    def AdjustInventory(self, request, context):
        return inventory_pb2.AdjustInventoryResponse(
            operation_id=str(uuid.uuid4())
        )

    def WatchInventory(self, request, context):
        for i in range(3):
            yield inventory_pb2.InventoryEvent(sku=request.sku)
            time.sleep(0.5)

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    inventory_pb2_grpc.add_InventoryServiceServicer_to_server(
        InventoryServicer(), server
    )
    # Enable reflection
    SERVICE_NAMES = (
        inventory_pb2.DESCRIPTOR.services_by_name['InventoryService'].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(SERVICE_NAMES, server)
    server.add_insecure_port('[::]:50051')
    server.start()
    print("gRPC InventoryService listening on :50051 (reflection enabled)")
    server.wait_for_termination()

if __name__ == '__main__':
    serve()
