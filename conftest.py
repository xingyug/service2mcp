"""Root conftest — loaded before any test collection across all testpaths.

Sets environment defaults required for module-level imports that happen
during collection (e.g. apps.compiler_api.main triggers JWT settings load).
"""

from __future__ import annotations

import os

# Ensure ACCESS_CONTROL_JWT_SECRET is available during test collection.
# Without this, any import of apps.compiler_api.main triggers
# load_jwt_settings() which raises JWTConfigurationError.
os.environ.setdefault("ACCESS_CONTROL_JWT_SECRET", "test-jwt-secret-for-ci")
