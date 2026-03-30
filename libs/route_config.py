"""Shared validation helpers for gateway route configuration payloads."""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GatewayRouteDefinition(BaseModel):
    """Single route definition embedded in a gateway route configuration."""

    model_config = ConfigDict(extra="allow")

    route_id: str = Field(min_length=1)
    target_service: dict[str, Any]
    switch_strategy: str | None = None
    match: dict[str, Any] | None = None


class GatewayRouteConfig(BaseModel):
    """Validated route publication payload shared across services."""

    model_config = ConfigDict(extra="allow")

    service_id: str = Field(min_length=1)
    service_name: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    version_number: int | None = None
    default_route: GatewayRouteDefinition | None = None
    version_route: GatewayRouteDefinition | None = None
    tenant: str | None = None
    environment: str | None = None

    @model_validator(mode="after")
    def validate_version_route(self) -> Self:
        if self.version_route is not None and not isinstance(self.version_number, int):
            raise ValueError("Version route configuration is missing a valid version_number.")
        return self


def validate_route_config(route_config: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a route_config payload."""

    return GatewayRouteConfig.model_validate(route_config).model_dump(
        mode="python",
        exclude_none=True,
    )


__all__ = ["GatewayRouteConfig", "validate_route_config"]
