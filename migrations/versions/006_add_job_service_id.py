"""Add service_id column to compilation_jobs table.

Revision ID: 006_add_job_service_id
Revises: 005_harden_version_scope
Create Date: 2026-07-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "006_add_job_service_id"
down_revision = "005_harden_version_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "compilation_jobs",
        sa.Column("service_id", sa.String(255), nullable=True),
        schema="compiler",
    )
    op.create_index(
        "ix_compilation_jobs_service_id",
        "compilation_jobs",
        ["service_id"],
        schema="compiler",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_compilation_jobs_service_id",
        "compilation_jobs",
        schema="compiler",
    )
    op.drop_column("compilation_jobs", "service_id", schema="compiler")
