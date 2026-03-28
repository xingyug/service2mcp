"""Tests for apps/proof_runner/grpc_mock.py."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from google.protobuf import descriptor_pb2
from google.protobuf.message_factory import GetMessageClass

from apps.proof_runner.grpc_mock import (
    _PACKAGE_NAME,
    _SERVICE_NAME,
    _scalar_field,
    _service_method,
    build_inventory_descriptor_pool,
    main,
    serve,
)


class TestBuildInventoryDescriptorPool:
    def test_returns_descriptor_pool(self) -> None:
        pool = build_inventory_descriptor_pool()
        # The pool may be a C extension type, so check by method presence
        assert hasattr(pool, "FindMessageTypeByName")

    def test_message_types_exist(self) -> None:
        pool = build_inventory_descriptor_pool()
        for name in (
            "ListItemsRequest",
            "ListItemsResponse",
            "Item",
            "AdjustInventoryRequest",
            "AdjustInventoryResponse",
            "WatchInventoryRequest",
            "InventoryEvent",
            "ItemFilter",
        ):
            descriptor = pool.FindMessageTypeByName(f"{_PACKAGE_NAME}.{name}")
            assert descriptor is not None
            assert descriptor.name == name

    def test_enum_type_exists(self) -> None:
        pool = build_inventory_descriptor_pool()
        enum_desc = pool.FindEnumTypeByName(f"{_PACKAGE_NAME}.AdjustmentReason")
        assert enum_desc is not None
        names = [v.name for v in enum_desc.values]
        assert "ADJUSTMENT_REASON_UNSPECIFIED" in names
        assert "ADJUSTMENT_REASON_SALE" in names
        assert "ADJUSTMENT_REASON_RESTOCK" in names

    def test_service_descriptor(self) -> None:
        pool = build_inventory_descriptor_pool()
        svc = pool.FindServiceByName(f"{_PACKAGE_NAME}.{_SERVICE_NAME}")
        assert svc is not None
        method_names = [m.name for m in svc.methods]
        assert "ListItems" in method_names
        assert "AdjustInventory" in method_names
        assert "WatchInventory" in method_names

    def test_watch_inventory_is_server_streaming(self) -> None:
        pool = build_inventory_descriptor_pool()
        svc = pool.FindServiceByName(f"{_PACKAGE_NAME}.{_SERVICE_NAME}")
        watch = next(m for m in svc.methods if m.name == "WatchInventory")
        assert watch.server_streaming is True

    def test_list_items_not_streaming(self) -> None:
        pool = build_inventory_descriptor_pool()
        svc = pool.FindServiceByName(f"{_PACKAGE_NAME}.{_SERVICE_NAME}")
        list_items = next(m for m in svc.methods if m.name == "ListItems")
        assert list_items.server_streaming is False

    def test_item_filter_fields(self) -> None:
        pool = build_inventory_descriptor_pool()
        item_filter = pool.FindMessageTypeByName(f"{_PACKAGE_NAME}.ItemFilter")
        field_names = [f.name for f in item_filter.fields]
        assert "categories" in field_names
        assert "include_inactive" in field_names

    def test_list_items_request_has_filter_field(self) -> None:
        pool = build_inventory_descriptor_pool()
        req = pool.FindMessageTypeByName(f"{_PACKAGE_NAME}.ListItemsRequest")
        field_names = [f.name for f in req.fields]
        assert "filter" in field_names
        assert "location_id" in field_names
        assert "page_size" in field_names
        assert "page_token" in field_names

    def test_adjust_inventory_request_has_reason_enum(self) -> None:
        pool = build_inventory_descriptor_pool()
        req = pool.FindMessageTypeByName(f"{_PACKAGE_NAME}.AdjustInventoryRequest")
        field_names = [f.name for f in req.fields]
        assert "sku" in field_names
        assert "delta" in field_names
        assert "reason" in field_names

    def test_message_classes_instantiate(self) -> None:
        pool = build_inventory_descriptor_pool()
        for name in ("ListItemsRequest", "ListItemsResponse", "Item", "AdjustInventoryRequest"):
            cls = GetMessageClass(pool.FindMessageTypeByName(f"{_PACKAGE_NAME}.{name}"))
            instance = cls()
            assert instance is not None


class TestScalarField:
    def test_adds_field_to_container(self) -> None:
        container = descriptor_pb2.DescriptorProto()
        container.name = "TestMessage"
        _scalar_field(
            container,
            name="test_field",
            number=1,
            field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
        )
        assert len(container.field) == 1
        assert container.field[0].name == "test_field"
        assert container.field[0].number == 1
        assert container.field[0].label == descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
        assert container.field[0].type == descriptor_pb2.FieldDescriptorProto.TYPE_STRING


class TestServiceMethod:
    def test_adds_method_to_service(self) -> None:
        container = descriptor_pb2.ServiceDescriptorProto()
        container.name = "TestService"
        _service_method(
            container,
            name="MyMethod",
            input_type=".pkg.Request",
            output_type=".pkg.Response",
        )
        assert len(container.method) == 1
        assert container.method[0].name == "MyMethod"
        assert container.method[0].input_type == ".pkg.Request"
        assert container.method[0].output_type == ".pkg.Response"
        assert container.method[0].server_streaming is False

    def test_server_streaming(self) -> None:
        container = descriptor_pb2.ServiceDescriptorProto()
        container.name = "TestService"
        _service_method(
            container,
            name="StreamMethod",
            input_type=".pkg.Request",
            output_type=".pkg.Event",
            server_streaming=True,
        )
        assert container.method[0].server_streaming is True


class TestServe:
    def test_serve_builds_and_starts_server(self) -> None:
        mock_server = MagicMock()
        with (
            patch(
                "apps.proof_runner.grpc_mock.grpc.server", return_value=mock_server
            ) as mock_grpc_server,
            patch("apps.proof_runner.grpc_mock.reflection.enable_server_reflection"),
        ):
            mock_server.add_insecure_port = MagicMock()
            mock_server.start = MagicMock()
            mock_server.wait_for_termination = MagicMock()
            mock_server.add_generic_rpc_handlers = MagicMock()

            serve(port=50099)

            mock_grpc_server.assert_called_once()
            mock_server.add_generic_rpc_handlers.assert_called_once()
            mock_server.add_insecure_port.assert_called_once_with("[::]:50099")
            mock_server.start.assert_called_once()
            mock_server.wait_for_termination.assert_called_once()

    def test_serve_registers_reflection(self) -> None:
        mock_server = MagicMock()
        with (
            patch("apps.proof_runner.grpc_mock.grpc.server", return_value=mock_server),
            patch(
                "apps.proof_runner.grpc_mock.reflection.enable_server_reflection"
            ) as mock_reflection,
        ):
            serve(port=50099)
            mock_reflection.assert_called_once()
            args = mock_reflection.call_args
            service_names = args[0][0]
            assert f"{_PACKAGE_NAME}.{_SERVICE_NAME}" in service_names


class TestServeHandlers:
    """Test the inner handler functions defined inside serve() by extracting them."""

    def _extract_handlers(self):
        """Run serve() with mocked server to capture handler closures."""
        pool = build_inventory_descriptor_pool()
        captured_handler_map = {}

        def fake_method_handlers_generic_handler(service_name, method_handlers):
            captured_handler_map.update(method_handlers)
            return MagicMock()

        mock_server = MagicMock()
        mock_server.add_generic_rpc_handlers = MagicMock()

        with (
            patch("apps.proof_runner.grpc_mock.grpc.server", return_value=mock_server),
            patch("apps.proof_runner.grpc_mock.reflection.enable_server_reflection"),
            patch(
                "apps.proof_runner.grpc_mock.grpc.method_handlers_generic_handler",
                side_effect=fake_method_handlers_generic_handler,
            ),
        ):
            serve(port=50099)

        return pool, captured_handler_map

    def test_list_items_handler(self) -> None:
        pool, handlers = self._extract_handlers()
        list_items_handler = handlers["ListItems"]

        request_cls = GetMessageClass(pool.FindMessageTypeByName("catalog.v1.ListItemsRequest"))
        GetMessageClass(pool.FindMessageTypeByName("catalog.v1.ListItemsResponse"))

        request = request_cls()
        request.location_id = "warehouse-1"
        request.page_size = 5

        # The handler wraps our function. Call the underlying unary_unary handler.
        # We need to deserialize/serialize through the handler chain.
        serialized_request = request.SerializeToString()
        deserialized = request_cls.FromString(serialized_request)

        # Call the handler function directly via the method handler's _request_deserializer
        response = list_items_handler.unary_unary(deserialized, MagicMock())
        assert response.items[0].sku == "warehouse-1-sku"
        assert response.items[0].title == "Puzzle Box"
        assert response.next_page_token == ""

    def test_list_items_handler_default_location(self) -> None:
        pool, handlers = self._extract_handlers()
        list_items_handler = handlers["ListItems"]

        request_cls = GetMessageClass(pool.FindMessageTypeByName("catalog.v1.ListItemsRequest"))
        request = request_cls()  # no location_id set

        response = list_items_handler.unary_unary(request, MagicMock())
        assert response.items[0].sku == "warehouse-sku"

    def test_adjust_inventory_handler(self) -> None:
        pool, handlers = self._extract_handlers()
        adjust_handler = handlers["AdjustInventory"]

        request_cls = GetMessageClass(
            pool.FindMessageTypeByName("catalog.v1.AdjustInventoryRequest")
        )
        request = request_cls()
        request.sku = "sku-42"
        request.delta = 10

        response = adjust_handler.unary_unary(request, MagicMock())
        assert response.operation_id == "adj-sku-42-10"

    def test_watch_inventory_handler(self) -> None:
        pool, handlers = self._extract_handlers()
        watch_handler = handlers["WatchInventory"]

        request_cls = GetMessageClass(
            pool.FindMessageTypeByName("catalog.v1.WatchInventoryRequest")
        )
        request = request_cls()
        request.sku = "sku-test"

        events = list(watch_handler.unary_stream(request, MagicMock()))
        assert len(events) == 2
        assert all(e.sku == "sku-test" for e in events)

    def test_watch_inventory_handler_default_sku(self) -> None:
        pool, handlers = self._extract_handlers()
        watch_handler = handlers["WatchInventory"]

        request_cls = GetMessageClass(
            pool.FindMessageTypeByName("catalog.v1.WatchInventoryRequest")
        )
        request = request_cls()  # no sku set

        events = list(watch_handler.unary_stream(request, MagicMock()))
        assert len(events) == 2
        assert all(e.sku == "sku-live" for e in events)


class TestMain:
    def test_main_default_port(self) -> None:
        with patch("apps.proof_runner.grpc_mock.serve") as mock_serve:
            main()
            mock_serve.assert_called_once_with(port=50051)

    def test_main_custom_port_from_env(self) -> None:
        with (
            patch.dict(os.environ, {"GRPC_PORT": "9999"}),
            patch("apps.proof_runner.grpc_mock.serve") as mock_serve,
        ):
            main()
            mock_serve.assert_called_once_with(port=9999)

    def test_main_invalid_port_fallback(self) -> None:
        with (
            patch.dict(os.environ, {"GRPC_PORT": "not-a-number"}),
            patch("apps.proof_runner.grpc_mock.serve") as mock_serve,
        ):
            main()
            mock_serve.assert_called_once_with(port=50051)
