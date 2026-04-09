"""Contract tests for OpenAPI schemas exposed by API services."""

from __future__ import annotations

import os
from collections.abc import Callable, Hashable, Mapping
from typing import Any, cast

import pytest
from fastapi import FastAPI
from openapi_spec_validator import validate_spec

os.environ.setdefault("ACCESS_CONTROL_JWT_SECRET", "contract-test-jwt-secret")

from apps.access_control.main import create_app as create_access_control_app
from apps.compiler_api.main import create_app as create_compiler_api_app


@pytest.mark.parametrize(
    ("app_factory", "required_paths"),
    [
        (
            create_compiler_api_app,
            {
                "/api/v1/compilations",
                "/api/v1/compilations/{job_id}",
                "/api/v1/services",
                "/api/v1/services/{service_id}",
                "/api/v1/artifacts",
            },
        ),
        (
            create_access_control_app,
            {
                "/api/v1/authn/validate",
                "/api/v1/authz/policies",
                "/api/v1/gateway-binding/reconcile",
                "/api/v1/gateway-binding/service-routes/sync",
                "/api/v1/audit/logs",
            },
        ),
    ],
)
def test_service_openapi_documents_are_valid(
    app_factory: Callable[[], FastAPI],
    required_paths: set[str],
) -> None:
    app = app_factory()
    schema = app.openapi()

    validate_spec(cast(Mapping[Hashable, Any], schema))
    assert required_paths.issubset(schema["paths"])
