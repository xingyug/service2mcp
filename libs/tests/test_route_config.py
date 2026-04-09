"""Unit tests for libs.route_config validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from libs.route_config import GatewayRouteDefinition, validate_route_config


class TestGatewayRouteDefinitionTargetService:
    def test_valid_target_service_accepted(self) -> None:
        defn = GatewayRouteDefinition(
            route_id="r1",
            target_service={"name": "my-svc", "port": 8080},
        )
        assert defn.target_service.host == "my-svc"
        assert defn.target_service.port == 8080

    def test_missing_name_rejected(self) -> None:
        """target_service without 'name'/'host' must be rejected."""
        with pytest.raises(ValidationError, match="host"):
            GatewayRouteDefinition(
                route_id="r1",
                target_service={"namespace": "default", "port": 8080},
            )

    def test_missing_port_rejected(self) -> None:
        """target_service without 'port' must be rejected."""
        with pytest.raises(ValidationError, match="port"):
            GatewayRouteDefinition(
                route_id="r1",
                target_service={"name": "my-svc", "namespace": "default"},
            )

    def test_missing_name_and_port_rejected(self) -> None:
        """target_service missing both name and port must be rejected."""
        with pytest.raises(ValidationError):
            GatewayRouteDefinition(
                route_id="r1",
                target_service={"namespace": "default"},
            )


class TestValidateRouteConfig:
    def test_malformed_target_service_rejected_through_validate_route_config(self) -> None:
        """validate_route_config rejects malformed target_service."""
        with pytest.raises(ValidationError):
            validate_route_config(
                {
                    "service_id": "svc",
                    "service_name": "svc-v1",
                    "namespace": "default",
                    "default_route": {
                        "route_id": "svc-active",
                        "target_service": {"namespace": "default"},
                    },
                }
            )
