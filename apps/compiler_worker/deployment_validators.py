"""Deployment metadata validators for persistence boundaries.

Validates route_config, deployment_revision, and storage_path shapes
before they are persisted or used in deployment activities.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator


class DeploymentRevisionValidator(BaseModel):
    """Validates that a deployment revision string is well-formed."""

    revision: str = Field(min_length=1, max_length=255)

    @field_validator("revision")
    @classmethod
    def must_be_printable(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("deployment_revision must not be blank")
        return v


class StoragePathValidator(BaseModel):
    """Validates that a manifest storage path is safe and well-formed."""

    path: str = Field(min_length=1, max_length=1024)

    @field_validator("path")
    @classmethod
    def must_not_contain_traversal(cls, v: str) -> str:
        if ".." in v:
            raise ValueError("storage_path must not contain '..' traversal")
        if not v.strip():
            raise ValueError("storage_path must not be blank")
        return v


_ROUTE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-./]+$")


class RouteConfigValidator(BaseModel):
    """Validates that a route_config dict has the required structure."""

    default_route: dict[str, Any] = Field(default_factory=dict)
    service_name: str | None = None

    @field_validator("default_route")
    @classmethod
    def default_route_must_have_route_id(cls, v: dict[str, Any]) -> dict[str, Any]:
        if not v:
            return v
        route_id = v.get("route_id")
        if route_id is None:
            raise ValueError("default_route must contain a 'route_id'")
        if not isinstance(route_id, str) or not route_id.strip():
            raise ValueError("route_id must be a non-empty string")
        if not _ROUTE_ID_PATTERN.match(route_id):
            raise ValueError(
                f"route_id '{route_id}' contains invalid characters "
                "(only alphanumerics, hyphens, underscores, dots, slashes allowed)"
            )
        return v


def validate_route_config(route_config: dict[str, Any]) -> list[str]:
    """Validate a route_config dict. Returns a list of error messages."""
    try:
        RouteConfigValidator(**route_config)
        return []
    except Exception as exc:
        return [str(exc)]


def validate_deployment_revision(revision: str) -> list[str]:
    """Validate a deployment revision string. Returns a list of error messages."""
    try:
        DeploymentRevisionValidator(revision=revision)
        return []
    except Exception as exc:
        return [str(exc)]


def validate_storage_path(path: str) -> list[str]:
    """Validate a storage path. Returns a list of error messages."""
    try:
        StoragePathValidator(path=path)
        return []
    except Exception as exc:
        return [str(exc)]
