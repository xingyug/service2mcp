"""Scope review workflows by tenant/environment.

Revision ID: 003_scope_review_workflows
Revises: 002_add_review_workflows
Create Date: 2026-04-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "003_scope_review_workflows"
down_revision = "002_add_review_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "review_workflows",
        sa.Column("tenant", sa.String(length=255), nullable=True),
        schema="compiler",
    )
    op.add_column(
        "review_workflows",
        sa.Column("environment", sa.String(length=64), nullable=True),
        schema="compiler",
    )

    op.execute(
        """
        WITH scoped_matches AS (
            SELECT
                rw.id AS workflow_id,
                sv.tenant,
                sv.environment,
                ROW_NUMBER() OVER (
                    PARTITION BY rw.id
                    ORDER BY sv.tenant NULLS FIRST, sv.environment NULLS FIRST, sv.id
                ) AS match_rank
            FROM compiler.review_workflows AS rw
            JOIN registry.service_versions AS sv
              ON sv.service_id = rw.service_id
             AND sv.version_number = rw.version_number
        )
        UPDATE compiler.review_workflows AS rw
           SET tenant = scoped_matches.tenant,
               environment = scoped_matches.environment
          FROM scoped_matches
         WHERE rw.id = scoped_matches.workflow_id
           AND scoped_matches.match_rank = 1
        """
    )

    op.drop_index(
        "ix_review_workflows_service",
        table_name="review_workflows",
        schema="compiler",
    )
    op.drop_constraint(
        "uq_review_workflow",
        "review_workflows",
        schema="compiler",
        type_="unique",
    )

    op.execute(
        """
        WITH scoped_matches AS (
            SELECT
                rw.id AS workflow_id,
                sv.tenant,
                sv.environment,
                ROW_NUMBER() OVER (
                    PARTITION BY rw.id
                    ORDER BY sv.tenant NULLS FIRST, sv.environment NULLS FIRST, sv.id
                ) AS match_rank
            FROM compiler.review_workflows AS rw
            JOIN registry.service_versions AS sv
              ON sv.service_id = rw.service_id
             AND sv.version_number = rw.version_number
        )
        INSERT INTO compiler.review_workflows (
            id,
            service_id,
            version_number,
            tenant,
            environment,
            state,
            review_notes,
            history,
            created_at,
            updated_at
        )
        SELECT
            gen_random_uuid(),
            rw.service_id,
            rw.version_number,
            scoped_matches.tenant,
            scoped_matches.environment,
            rw.state,
            rw.review_notes,
            rw.history,
            rw.created_at,
            rw.updated_at
        FROM compiler.review_workflows AS rw
        JOIN scoped_matches
          ON scoped_matches.workflow_id = rw.id
        WHERE scoped_matches.match_rank > 1
        """
    )

    op.create_unique_constraint(
        "uq_review_workflow",
        "review_workflows",
        ["service_id", "version_number", "tenant", "environment"],
        schema="compiler",
    )
    op.create_index(
        "ix_review_workflows_service",
        "review_workflows",
        ["service_id", "tenant", "environment"],
        schema="compiler",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_review_workflows_service",
        table_name="review_workflows",
        schema="compiler",
    )
    op.drop_constraint(
        "uq_review_workflow",
        "review_workflows",
        schema="compiler",
        type_="unique",
    )

    op.execute(
        """
        WITH ranked_rows AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY service_id, version_number
                    ORDER BY tenant NULLS FIRST, environment NULLS FIRST, id
                ) AS row_rank
            FROM compiler.review_workflows
        )
        DELETE FROM compiler.review_workflows AS rw
         USING ranked_rows
         WHERE rw.id = ranked_rows.id
           AND ranked_rows.row_rank > 1
        """
    )

    op.drop_column("review_workflows", "environment", schema="compiler")
    op.drop_column("review_workflows", "tenant", schema="compiler")

    op.create_unique_constraint(
        "uq_review_workflow",
        "review_workflows",
        ["service_id", "version_number"],
        schema="compiler",
    )
    op.create_index(
        "ix_review_workflows_service",
        "review_workflows",
        ["service_id"],
        schema="compiler",
    )
