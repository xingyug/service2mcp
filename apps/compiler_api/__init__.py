"""Compiler API package with lazy exports."""

from __future__ import annotations

from typing import Any

__all__ = ["app", "create_app"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from apps.compiler_api.main import app, create_app

    exports: dict[str, Any] = {
        "app": app,
        "create_app": create_app,
    }
    return exports[name]
