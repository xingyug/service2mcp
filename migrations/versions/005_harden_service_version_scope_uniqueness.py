"""Harden service_versions uniqueness semantics for nullable scopes.

Revision ID: 005_harden_service_version_scope_uniqueness
Revises: 004_add_auth_user_roles
Create Date: 2026-04-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "005_harden_service_version_scope_uniqueness"
down_revision = "004_add_auth_user_roles"
branch_labels = None
depends_on = None


def _raise_if_duplicate_scope_rows_exist() -> None:
    bind = op.get_bind()

    duplicate_version = bind.execute(
        sa.text(
            """
            SELECT
                service_id,
                version_number,
                tenant,
                environment,
                COUNT(*) AS row_count
            FROM registry.service_versions
            GROUP BY service_id, version_number, tenant, environment
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).mappings().first()
    if duplicate_version is not None:
        raise RuntimeError(
            "Cannot upgrade registry.service_versions because duplicate scoped version rows "
            f"already exist for {duplicate_version['service_id']}:"
            f"{duplicate_version['version_number']} "
            f"(tenant={duplicate_version['tenant']!r}, "
            f"environment={duplicate_version['environment']!r}, "
            f"count={duplicate_version['row_count']})."
        )

    duplicate_active = bind.execute(
        sa.text(
            """
            SELECT
                service_id,
                tenant,
                environment,
                COUNT(*) AS row_count
            FROM registry.service_versions
            WHERE is_active = true
            GROUP BY service_id, tenant, environment
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).mappings().first()
    if duplicate_active is not None:
        raise RuntimeError(
            "Cannot upgrade registry.service_versions because multiple active rows already "
            f"exist for {duplicate_active['service_id']} "
            f"(tenant={duplicate_active['tenant']!r}, "
            f"environment={duplicate_active['environment']!r}, "
            f"count={duplicate_active['row_count']})."
        )


def upgrade() -> None:
    _raise_if_duplicate_scope_rows_exist()

    op.execute("ALTER TABLE registry.service_versions DROP CONSTRAINT IF EXISTS uq_service_version")
    op.execute("DROP INDEX IF EXISTS registry.uq_service_version")
    op.execute("DROP INDEX IF EXISTS registry.uq_service_versions_one_active")

    op.execute(
        """
        CREATE UNIQUE INDEX uq_service_version
            ON registry.service_versions (
                service_id,
                version_number,
                COALESCE(tenant, ''),
                COALESCE(environment, '')
            )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_service_versions_one_active
            ON registry.service_versions (
                service_id,
                COALESCE(tenant, ''),
                COALESCE(environment, '')
            )
            WHERE is_active = true
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS registry.uq_service_versions_one_active")
    op.execute("DROP INDEX IF EXISTS registry.uq_service_version")

    op.create_unique_constraint(
        "uq_service_version",
        "service_versions",
        ["service_id", "version_number"],
        schema="registry",
    )
