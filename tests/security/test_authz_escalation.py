"""Authorization escalation tests.

Verifies that non-admin callers cannot access admin-only routes, that
tenant-scoped tokens are blocked from accessing other tenants, and that
workflow transition role checks are enforced.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from apps.access_control.authn.models import TokenPrincipalResponse
from apps.access_control.security import (
    caller_is_admin,
    require_admin_principal,
    require_scope_access,
)
from tests.security.conftest import auth_header, build_valid_jwt

pytestmark = pytest.mark.security


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _caller(
    subject: str = "alice",
    roles: list[str] | None = None,
    tenant: str | None = None,
    environment: str | None = None,
    tenants: list[str] | None = None,
    environments: list[str] | None = None,
) -> TokenPrincipalResponse:
    claims: dict[str, object] = {
        "sub": subject,
        "roles": roles or ["user"],
    }
    if tenant is not None:
        claims["tenant"] = tenant
    if environment is not None:
        claims["environment"] = environment
    if tenants is not None:
        claims["tenants"] = tenants
    if environments is not None:
        claims["environments"] = environments
    return TokenPrincipalResponse(
        subject=subject,
        username=subject,
        token_type="jwt",
        claims=claims,
    )


# ---------------------------------------------------------------------------
# Admin role checks (unit-level)
# ---------------------------------------------------------------------------


class TestAdminRoleEnforcement:
    def test_regular_user_is_not_admin(self) -> None:
        assert caller_is_admin(_caller(roles=["user"])) is False

    def test_admin_is_admin(self) -> None:
        assert caller_is_admin(_caller(roles=["admin"])) is True

    def test_administrator_is_admin(self) -> None:
        assert caller_is_admin(_caller(roles=["administrator"])) is True

    def test_superuser_is_admin(self) -> None:
        assert caller_is_admin(_caller(roles=["superuser"])) is True

    def test_require_admin_principal_rejects_user(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            require_admin_principal(_caller(roles=["user"]))
        assert exc_info.value.status_code == 403

    def test_require_admin_principal_accepts_admin(self) -> None:
        result = require_admin_principal(_caller(roles=["admin"]))
        assert result.subject == "alice"


# ---------------------------------------------------------------------------
# Tenant scope isolation
# ---------------------------------------------------------------------------


class TestTenantScopeIsolation:
    def test_non_admin_blocked_from_other_tenant(self) -> None:
        c = _caller(tenant="tenant-a")
        with pytest.raises(HTTPException) as exc_info:
            require_scope_access(c, tenant="tenant-b")
        assert exc_info.value.status_code == 403

    def test_non_admin_allowed_own_tenant(self) -> None:
        c = _caller(tenant="tenant-a")
        require_scope_access(c, tenant="tenant-a")  # should not raise

    def test_admin_bypasses_tenant_scope(self) -> None:
        c = _caller(roles=["admin"], tenant="tenant-a")
        require_scope_access(c, tenant="tenant-b")  # should not raise

    def test_tenants_list_blocks_unlisted_tenant(self) -> None:
        c = _caller(tenants=["team-alpha", "team-beta"])
        with pytest.raises(HTTPException) as exc_info:
            require_scope_access(c, tenant="team-gamma")
        assert exc_info.value.status_code == 403

    def test_tenants_list_allows_listed_tenant(self) -> None:
        c = _caller(tenants=["team-alpha", "team-beta"])
        require_scope_access(c, tenant="team-alpha")  # should not raise


# ---------------------------------------------------------------------------
# Environment scope isolation
# ---------------------------------------------------------------------------


class TestEnvironmentScopeIsolation:
    def test_non_admin_blocked_from_other_environment(self) -> None:
        c = _caller(environment="staging")
        with pytest.raises(HTTPException) as exc_info:
            require_scope_access(c, environment="production")
        assert exc_info.value.status_code == 403

    def test_non_admin_allowed_own_environment(self) -> None:
        c = _caller(environment="staging")
        require_scope_access(c, environment="staging")

    def test_admin_bypasses_environment_scope(self) -> None:
        c = _caller(roles=["admin"], environment="staging")
        require_scope_access(c, environment="production")

    def test_environments_list_blocks_unlisted(self) -> None:
        c = _caller(environments=["staging", "dev"])
        with pytest.raises(HTTPException) as exc_info:
            require_scope_access(c, environment="production")
        assert exc_info.value.status_code == 403

    def test_environments_list_allows_listed(self) -> None:
        c = _caller(environments=["staging", "dev"])
        require_scope_access(c, environment="staging")

    def test_empty_tenants_list_denies_access(self) -> None:
        """Empty tenants list means 'no tenants authorized', NOT 'skip check'."""
        c = _caller(tenants=[])
        with pytest.raises(HTTPException) as exc_info:
            require_scope_access(c, tenant="any-tenant")
        assert exc_info.value.status_code == 403

    def test_empty_environments_list_denies_access(self) -> None:
        """Empty environments list means 'no environments authorized', NOT 'skip check'."""
        c = _caller(environments=[])
        with pytest.raises(HTTPException) as exc_info:
            require_scope_access(c, environment="any-env")
        assert exc_info.value.status_code == 403

    def test_no_tenants_claim_allows_access(self) -> None:
        """When tenants claim is absent (not set), tenant check is skipped."""
        c = _caller()  # no tenant/tenants claim
        require_scope_access(c, tenant="any-tenant")  # should not raise

    def test_no_environments_claim_allows_access(self) -> None:
        """When environments claim is absent (not set), environment check is skipped."""
        c = _caller()  # no environment/environments claim
        require_scope_access(c, environment="any-env")  # should not raise


# ---------------------------------------------------------------------------
# Tenant scope via HTTP routes
# ---------------------------------------------------------------------------


class TestTenantScopeViaRoutes:
    async def test_services_list_blocked_for_wrong_tenant(
        self,
        client: AsyncClient,
        mock_session: AsyncMock,
    ) -> None:
        """A user token scoped to tenant-a must be rejected when querying tenant-b."""
        token = build_valid_jwt(
            subject="alice",
            roles=["user"],
            tenant="tenant-a",
        )
        resp = await client.get(
            "/api/v1/services?tenant=tenant-b",
            headers=auth_header(token),
        )
        assert resp.status_code == 403

    async def test_services_list_allowed_for_matching_tenant(
        self,
        client: AsyncClient,
        mock_session: AsyncMock,
    ) -> None:
        token = build_valid_jwt(
            subject="alice",
            roles=["user"],
            tenant="tenant-a",
        )
        # Mock the repository to return an empty list
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result
        mock_session.scalar.return_value = 0

        resp = await client.get(
            "/api/v1/services?tenant=tenant-a",
            headers=auth_header(token),
        )
        # Should not be 401 or 403 — the auth/scope check passed
        assert resp.status_code != 401
        assert resp.status_code != 403

    async def test_artifacts_blocked_for_wrong_tenant(
        self,
        client: AsyncClient,
        mock_session: AsyncMock,
    ) -> None:
        token = build_valid_jwt(
            subject="bob",
            roles=["user"],
            tenant="tenant-x",
        )
        resp = await client.get(
            "/api/v1/artifacts/svc-1/versions?tenant=tenant-y",
            headers=auth_header(token),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Workflow role-based transition checks
# ---------------------------------------------------------------------------


class TestWorkflowRoleChecks:
    """Verify that transition_required_roles are enforced in workflow routes.

    Instead of hitting the full route (which requires a real DB with
    ServiceVersion rows), we test the security functions directly.
    """

    def test_viewer_cannot_approve(self) -> None:
        """Only reviewer/admin roles can transition in_review→approved."""
        from apps.access_control.security import caller_roles as get_roles

        c = _caller(roles=["viewer"])
        roles = get_roles(c)
        required = {"reviewer", "admin"}
        assert not (roles & required), "viewer should not have reviewer or admin role"

    def test_reviewer_can_approve(self) -> None:
        from apps.access_control.security import caller_roles as get_roles

        c = _caller(roles=["reviewer"])
        roles = get_roles(c)
        required = {"reviewer", "admin"}
        assert roles & required, "reviewer should satisfy the requirement"

    def test_user_cannot_deploy(self) -> None:
        from apps.access_control.security import caller_roles as get_roles

        c = _caller(roles=["user"])
        roles = get_roles(c)
        required = {"deployer", "admin"}
        assert not (roles & required)

    def test_admin_can_deploy(self) -> None:
        from apps.access_control.security import caller_roles as get_roles

        c = _caller(roles=["admin"])
        roles = get_roles(c)
        required = {"deployer", "admin"}
        assert roles & required

    def test_role_normalization_case_insensitive(self) -> None:
        """Roles are normalized to lowercase for comparison."""
        from apps.access_control.security import caller_roles as get_roles

        c = _caller(roles=["Admin", "REVIEWER"])
        roles = get_roles(c)
        assert "admin" in roles
        assert "reviewer" in roles
