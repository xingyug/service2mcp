"""Add review_workflows table and tenant/environment columns to compilation_jobs.

Revision ID: 002_add_review_workflows
Revises: 001_initial
Create Date: 2026-04-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "002_add_review_workflows"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Add tenant/environment to compilation_jobs ───────────────────
    op.add_column(
        "compilation_jobs",
        sa.Column("tenant", sa.String(128), nullable=True),
        schema="compiler",
    )
    op.add_column(
        "compilation_jobs",
        sa.Column("environment", sa.String(128), nullable=True),
        schema="compiler",
    )
    op.create_index(
        "ix_compilation_jobs_tenant",
        "compilation_jobs",
        ["tenant"],
        schema="compiler",
    )
    op.create_index(
        "ix_compilation_jobs_environment",
        "compilation_jobs",
        ["environment"],
        schema="compiler",
    )

    # ── Create review_workflows table ────────────────────────────────
    op.create_table(
        "review_workflows",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("service_id", sa.String(255), nullable=False),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("review_notes", JSONB, nullable=True),
        sa.Column("history", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("service_id", "version_number", name="uq_review_workflow"),
        schema="compiler",
    )
    op.create_index(
        "ix_review_workflows_service",
        "review_workflows",
        ["service_id"],
        schema="compiler",
    )


def downgrade() -> None:
    op.drop_table("review_workflows", schema="compiler")
    op.drop_index("ix_compilation_jobs_environment", table_name="compilation_jobs", schema="compiler")
    op.drop_index("ix_compilation_jobs_tenant", table_name="compilation_jobs", schema="compiler")
    op.drop_column("compilation_jobs", "environment", schema="compiler")
    op.drop_column("compilation_jobs", "tenant", schema="compiler")
