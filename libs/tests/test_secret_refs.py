"""Tests for libs/secret_refs.py."""

from __future__ import annotations

import os

import pytest

from libs.secret_refs import MissingSecretReferenceError, resolve_secret_ref


class TestResolveSecretRef:
    """Tests for resolve_secret_ref()."""

    def test_resolves_existing_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_SECRET", "hunter2")
        result = resolve_secret_ref("MY_SECRET", purpose="test", context="unit")
        assert result == "hunter2"

    def test_rejects_whitespace_only_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WHITESPACE_SECRET", "   ")
        with pytest.raises(MissingSecretReferenceError):
            resolve_secret_ref("WHITESPACE_SECRET", purpose="test", context="unit")

    def test_rejects_empty_string_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EMPTY_SECRET", "")
        with pytest.raises(MissingSecretReferenceError):
            resolve_secret_ref("EMPTY_SECRET", purpose="test", context="unit")

    def test_rejects_missing_env_var(self) -> None:
        # Ensure env var doesn't exist
        os.environ.pop("NONEXISTENT_SECRET_XYZ", None)
        with pytest.raises(MissingSecretReferenceError):
            resolve_secret_ref("NONEXISTENT_SECRET_XYZ", purpose="test", context="unit")

    def test_accepts_value_with_surrounding_whitespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A secret with real content plus whitespace should be accepted."""
        monkeypatch.setenv("PADDED_SECRET", "  real_value  ")
        result = resolve_secret_ref("PADDED_SECRET", purpose="test", context="unit")
        assert result == "  real_value  "

    def test_rejects_crlf_in_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Secrets with CRLF are rejected to prevent header injection."""
        monkeypatch.setenv("CRLF_SECRET", "token\r\nX-Injected: bad")
        with pytest.raises(ValueError, match="control characters"):
            resolve_secret_ref("CRLF_SECRET", purpose="test", context="unit")

    def test_rejects_newline_in_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NEWLINE_SECRET", "token\nX-Injected: bad")
        with pytest.raises(ValueError, match="control characters"):
            resolve_secret_ref("NEWLINE_SECRET", purpose="test", context="unit")

    def test_rejects_null_byte_in_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Secrets with null bytes are rejected (OS rejects null in env vars,
        so we test via direct function call with patched os.environ.get)."""
        monkeypatch.setattr(
            "os.environ.get",
            lambda key, default=None: "token\x00evil" if key == "NULL_SECRET" else default,
        )
        with pytest.raises(ValueError, match="control characters"):
            resolve_secret_ref("NULL_SECRET", purpose="test", context="unit")
