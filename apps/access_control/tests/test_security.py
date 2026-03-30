"""Unit tests for shared access-control security helpers."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from apps.access_control.authn.models import TokenPrincipalResponse
from apps.access_control.security import (
    caller_is_admin,
    caller_roles,
    require_admin_principal,
    require_self_or_admin,
)


def _caller(
    subject: str = "alice",
    *,
    username: str | None = None,
    roles: list[str] | None = None,
) -> TokenPrincipalResponse:
    claims: dict[str, object] = {"sub": subject}
    if roles is not None:
        claims["roles"] = roles
    return TokenPrincipalResponse(
        subject=subject,
        username=username,
        token_type="jwt",
        claims=claims,
    )


def test_caller_roles_normalizes_strings_and_case() -> None:
    caller = _caller(roles=[" Admin ", "viewer"])
    assert caller_roles(caller) == {"admin", "viewer"}


def test_caller_is_admin_accepts_known_admin_roles() -> None:
    assert caller_is_admin(_caller(roles=["administrator"])) is True


def test_require_self_or_admin_allows_same_subject() -> None:
    caller = _caller(subject="alice")
    assert require_self_or_admin(caller, username="alice") == caller


def test_require_self_or_admin_allows_matching_username_when_subject_differs() -> None:
    caller = _caller(subject="alice@example.com", username="alice")
    assert require_self_or_admin(caller, username="alice") == caller


def test_require_self_or_admin_allows_admin() -> None:
    caller = _caller(subject="ops", roles=["admin"])
    assert require_self_or_admin(caller, username="alice") == caller


def test_require_self_or_admin_rejects_other_non_admin() -> None:
    with pytest.raises(HTTPException) as exc_info:
        require_self_or_admin(_caller(subject="bob"), username="alice")

    assert exc_info.value.status_code == 403
    assert "another user's PATs" in exc_info.value.detail


def test_require_admin_principal_rejects_non_admin() -> None:
    with pytest.raises(HTTPException) as exc_info:
        require_admin_principal(_caller(subject="alice"))

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Admin role required."
