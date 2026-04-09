"""Tests for SQLAlchemy ORM models and migration structure."""

from __future__ import annotations

from libs.db_models import (
    ArtifactRecord,
    AuditLog,
    Base,
    CompilationEvent,
    CompilationJob,
    PersonalAccessToken,
    Policy,
    ReviewWorkflow,
    ServiceVersion,
    User,
)


class TestModelDefinitions:
    """Verify that all ORM models are properly defined and importable."""

    def test_base_has_all_tables(self):
        table_names = {t.name for t in Base.metadata.sorted_tables}
        expected = {
            "compilation_jobs",
            "compilation_events",
            "service_versions",
            "artifact_records",
            "users",
            "pats",
            "policies",
            "audit_log",
            "review_workflows",
        }
        assert expected == table_names

    def test_compilation_job_columns(self):
        cols = {c.name for c in CompilationJob.__table__.columns}
        assert "id" in cols
        assert "source_url" in cols
        assert "status" in cols
        assert "current_stage" in cols
        assert "error_detail" in cols
        assert "created_at" in cols
        assert "updated_at" in cols
        assert "options" in cols

    def test_service_version_columns(self):
        cols = {c.name for c in ServiceVersion.__table__.columns}
        assert "id" in cols
        assert "service_id" in cols
        assert "version_number" in cols
        assert "is_active" in cols
        assert "ir_json" in cols
        assert "raw_ir_json" in cols
        assert "validation_report" in cols
        assert "tenant" in cols

    def test_compilation_event_columns(self):
        cols = {c.name for c in CompilationEvent.__table__.columns}
        assert "job_id" in cols
        assert "sequence_number" in cols
        assert "stage" in cols
        assert "event_type" in cols
        assert "attempt" in cols
        assert "detail" in cols
        assert "error_detail" in cols

    def test_artifact_record_columns(self):
        cols = {c.name for c in ArtifactRecord.__table__.columns}
        assert "service_version_id" in cols
        assert "artifact_type" in cols
        assert "content_hash" in cols
        assert "storage_path" in cols

    def test_user_columns(self):
        cols = {c.name for c in User.__table__.columns}
        assert "username" in cols
        assert "email" in cols
        assert "ldap_dn" in cols
        assert "roles" in cols
        assert "is_active" in cols

    def test_pat_columns(self):
        cols = {c.name for c in PersonalAccessToken.__table__.columns}
        assert "user_id" in cols
        assert "token_hash" in cols
        assert "name" in cols
        assert "revoked_at" in cols

    def test_policy_columns(self):
        cols = {c.name for c in Policy.__table__.columns}
        assert "subject_type" in cols
        assert "subject_id" in cols
        assert "resource_id" in cols
        assert "action_pattern" in cols
        assert "risk_threshold" in cols
        assert "decision" in cols

    def test_audit_log_columns(self):
        cols = {c.name for c in AuditLog.__table__.columns}
        assert "actor" in cols
        assert "action" in cols
        assert "resource" in cols
        assert "detail" in cols
        assert "timestamp" in cols

    def test_review_workflow_columns(self):
        cols = {c.name for c in ReviewWorkflow.__table__.columns}
        assert "service_id" in cols
        assert "version_number" in cols
        assert "tenant" in cols
        assert "environment" in cols
        assert "state" in cols
        assert "review_notes" in cols
        assert "history" in cols


class TestSchemaAssignment:
    """Verify tables are assigned to the correct schemas."""

    def test_compilation_in_compiler_schema(self):
        assert CompilationJob.__table__.schema == "compiler"

    def test_compilation_events_in_compiler_schema(self):
        assert CompilationEvent.__table__.schema == "compiler"

    def test_service_versions_in_registry_schema(self):
        assert ServiceVersion.__table__.schema == "registry"

    def test_artifact_records_in_registry_schema(self):
        assert ArtifactRecord.__table__.schema == "registry"

    def test_users_in_auth_schema(self):
        assert User.__table__.schema == "auth"

    def test_pats_in_auth_schema(self):
        assert PersonalAccessToken.__table__.schema == "auth"

    def test_policies_in_auth_schema(self):
        assert Policy.__table__.schema == "auth"

    def test_audit_log_in_auth_schema(self):
        assert AuditLog.__table__.schema == "auth"


class TestForeignKeys:
    """Verify foreign key relationships are defined."""

    def test_artifact_record_fk_to_service_version(self):
        fks = {fk.target_fullname for fk in ArtifactRecord.__table__.foreign_keys}
        assert "registry.service_versions.id" in fks

    def test_pat_fk_to_user(self):
        fks = {fk.target_fullname for fk in PersonalAccessToken.__table__.foreign_keys}
        assert "auth.users.id" in fks

    def test_compilation_event_fk_to_job(self):
        fks = {fk.target_fullname for fk in CompilationEvent.__table__.foreign_keys}
        assert "compiler.compilation_jobs.id" in fks


class TestIndices:
    """Verify important indices exist."""

    def test_compilation_jobs_has_status_index(self):
        index_names = {idx.name for idx in CompilationJob.__table__.indexes}
        assert "ix_compilation_jobs_status" in index_names

    def test_compilation_events_has_job_index(self):
        index_names = {idx.name for idx in CompilationEvent.__table__.indexes}
        assert "ix_compilation_events_job_id" in index_names

    def test_service_versions_has_service_id_index(self):
        index_names = {idx.name for idx in ServiceVersion.__table__.indexes}
        assert "ix_service_versions_service_id" in index_names

    def test_service_versions_has_scope_unique_indexes(self):
        index_names = {idx.name for idx in ServiceVersion.__table__.indexes}
        assert "uq_service_version" in index_names
        assert "uq_service_versions_one_active" in index_names

    def test_users_has_username_index(self):
        index_names = {idx.name for idx in User.__table__.indexes}
        assert "ix_users_username" in index_names

    def test_audit_log_has_timestamp_index(self):
        index_names = {idx.name for idx in AuditLog.__table__.indexes}
        assert "ix_audit_log_timestamp" in index_names


class TestMigrationScript:
    """Verify the migration script is importable and has required functions."""

    def test_migration_importable(self):
        import importlib

        m = importlib.import_module("migrations.versions.001_initial")
        assert hasattr(m, "upgrade")
        assert hasattr(m, "downgrade")
        assert m.revision == "001_initial"
        assert m.down_revision is None
