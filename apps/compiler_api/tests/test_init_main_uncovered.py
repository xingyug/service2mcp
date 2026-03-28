"""Unit tests for apps/compiler_api/__init__.py and main.py uncovered lines."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI


class TestCompilerApiInit:
    def test_getattr_app_imports_lazily(self) -> None:
        """Test lines 13-19: lazy import of app."""
        # Clear any existing cached imports
        import sys

        if "apps.compiler_api.main" in sys.modules:
            del sys.modules["apps.compiler_api.main"]

        # Import should trigger the lazy loading
        from apps.compiler_api import app

        assert app is not None
        # Verify it's a FastAPI instance
        assert isinstance(app, FastAPI)

    def test_getattr_create_app_imports_lazily(self) -> None:
        """Test lines 13-19: lazy import of create_app."""
        from apps.compiler_api import create_app

        assert create_app is not None
        assert callable(create_app)

    def test_getattr_invalid_attribute_raises_error(self) -> None:
        """Test line 12: AttributeError for invalid attributes."""
        with pytest.raises(
            AttributeError, match="module 'apps.compiler_api' has no attribute 'nonexistent'"
        ):
            import apps.compiler_api

            getattr(apps.compiler_api, "nonexistent")


class TestCompilerApiMain:
    async def test_app_lifespan_shutdown_disposes_database(self) -> None:
        """Test lines 23-24: dispose_database called on shutdown."""
        from apps.compiler_api.main import create_app

        with patch("apps.compiler_api.main.dispose_database") as mock_dispose:
            app = create_app()

            # Simulate lifespan context manager shutdown
            # The lifespan is an async context manager
            async with app.router.lifespan_context(app):
                pass  # Startup phase
            # Shutdown phase should call dispose_database

            mock_dispose.assert_called_once_with(app)

    def test_healthz_endpoint_returns_status_ok(self) -> None:
        """Test line 44: healthz endpoint returns correct response."""
        from fastapi.testclient import TestClient

        from apps.compiler_api.main import app

        client = TestClient(app)
        response = client.get("/healthz")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
