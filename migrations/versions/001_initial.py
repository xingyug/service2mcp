"""Initial database schema — all control plane tables.

Revision ID: 001_initial
Create Date: 2026-03-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create schemas
    op.execute("CREATE SCHEMA IF NOT EXISTS compiler")
    op.execute("CREATE SCHEMA IF NOT EXISTS registry")
    op.execute("CREATE SCHEMA IF NOT EXISTS auth")

    # ── Compiler Schema ──────────────────────────────────────────────

    op.create_table(
        "compilation_jobs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column("source_hash", sa.String(64), nullable=True),
        sa.Column("protocol", sa.String(32), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("current_stage", sa.String(32), nullable=True),
        sa.Column("error_detail", sa.Text, nullable=True),
        sa.Column("options", JSONB, nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("service_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        schema="compiler",
    )
    op.create_index("ix_compilation_jobs_status", "compilation_jobs", ["status"], schema="compiler")
    op.create_index(
        "ix_compilation_jobs_created_at",
        "compilation_jobs",
        ["created_at"],
        schema="compiler",
    )

    op.create_table(
        "compilation_events",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("compiler.compilation_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence_number", sa.Integer, nullable=False),
        sa.Column("stage", sa.String(32), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("attempt", sa.Integer, nullable=True),
        sa.Column("detail", JSONB, nullable=True),
        sa.Column("error_detail", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("job_id", "sequence_number", name="uq_compilation_event_sequence"),
        schema="compiler",
    )
    op.create_index(
        "ix_compilation_events_job_id",
        "compilation_events",
        ["job_id"],
        schema="compiler",
    )
    op.create_index(
        "ix_compilation_events_created_at",
        "compilation_events",
        ["created_at"],
        schema="compiler",
    )

    # ── Registry Schema ──────────────────────────────────────────────

    op.create_table(
        "service_versions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("service_id", sa.String(255), nullable=False),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true")),
        sa.Column("ir_json", JSONB, nullable=False),
        sa.Column("raw_ir_json", JSONB, nullable=True),
        sa.Column("compiler_version", sa.String(32), nullable=False, server_default="0.1.0"),
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column("source_hash", sa.String(64), nullable=True),
        sa.Column("protocol", sa.String(32), nullable=True),
        sa.Column("validation_report", JSONB, nullable=True),
        sa.Column("deployment_revision", sa.String(255), nullable=True),
        sa.Column("route_config", JSONB, nullable=True),
        sa.Column("tenant", sa.String(255), nullable=True),
        sa.Column("environment", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("service_id", "version_number", name="uq_service_version"),
        schema="registry",
    )
    op.create_index(
        "ix_service_versions_service_id",
        "service_versions",
        ["service_id"],
        schema="registry",
    )
    op.create_index(
        "ix_service_versions_active",
        "service_versions",
        ["service_id", "is_active"],
        schema="registry",
    )

    op.create_table(
        "artifact_records",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "service_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("registry.service_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("artifact_type", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.Text, nullable=True),
        sa.Column("metadata_json", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        schema="registry",
    )
    op.create_index(
        "ix_artifact_records_version",
        "artifact_records",
        ["service_version_id"],
        schema="registry",
    )

    # ── Auth Schema ──────────────────────────────────────────────────

    op.create_table(
        "users",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("username", sa.String(255), nullable=False, unique=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("ldap_dn", sa.String(512), nullable=True),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        schema="auth",
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True, schema="auth")

    op.create_table(
        "pats",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("auth.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        schema="auth",
    )
    op.create_index("ix_pats_user_id", "pats", ["user_id"], schema="auth")
    op.create_index("ix_pats_token_hash", "pats", ["token_hash"], unique=True, schema="auth")

    op.create_table(
        "policies",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.String(255), nullable=False),
        sa.Column("resource_id", sa.String(255), nullable=False),
        sa.Column("action_pattern", sa.String(255), nullable=False, server_default="*"),
        sa.Column("risk_threshold", sa.String(32), nullable=False, server_default="safe"),
        sa.Column("decision", sa.String(32), nullable=False, server_default="deny"),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        schema="auth",
    )
    op.create_index(
        "ix_policies_subject",
        "policies",
        ["subject_type", "subject_id"],
        schema="auth",
    )
    op.create_index("ix_policies_resource", "policies", ["resource_id"], schema="auth")

    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("action", sa.String(255), nullable=False),
        sa.Column("resource", sa.String(255), nullable=True),
        sa.Column("detail", JSONB, nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        schema="auth",
    )
    op.create_index("ix_audit_log_actor", "audit_log", ["actor"], schema="auth")
    op.create_index("ix_audit_log_timestamp", "audit_log", ["timestamp"], schema="auth")
    op.create_index("ix_audit_log_action", "audit_log", ["action"], schema="auth")


def downgrade() -> None:
    # Auth tables
    op.drop_table("audit_log", schema="auth")
    op.drop_table("policies", schema="auth")
    op.drop_table("pats", schema="auth")
    op.drop_table("users", schema="auth")

    # Registry tables
    op.drop_table("artifact_records", schema="registry")
    op.drop_table("service_versions", schema="registry")

    # Compiler tables
    op.drop_table("compilation_events", schema="compiler")
    op.drop_table("compilation_jobs", schema="compiler")

    # Drop schemas
    op.execute("DROP SCHEMA IF EXISTS auth CASCADE")
    op.execute("DROP SCHEMA IF EXISTS registry CASCADE")
    op.execute("DROP SCHEMA IF EXISTS compiler CASCADE")
