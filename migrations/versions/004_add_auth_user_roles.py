"""Persist local auth user roles for PAT authorization.

Revision ID: 004_add_auth_user_roles
Revises: 003_scope_review_workflows
Create Date: 2026-04-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "004_add_auth_user_roles"
down_revision = "003_scope_review_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "roles",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema="auth",
    )


def downgrade() -> None:
    op.drop_column("users", "roles", schema="auth")
