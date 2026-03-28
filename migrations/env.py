"""Alembic environment configuration for async PostgreSQL migrations."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from libs.db_models import Base

# Alembic Config object
config = context.config

# Set up logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for autogenerate
target_metadata = Base.metadata


def to_migration_database_url(database_url: str) -> str:
    """Translate the app's async URL into a sync driver URL for Alembic."""
    # Only replace the driver portion — match scheme://... pattern boundary
    if "://" in database_url:
        scheme, rest = database_url.split("://", 1)
        scheme = scheme.replace("+asyncpg", "+psycopg")
        return f"{scheme}://{rest}"
    return database_url.replace("+asyncpg", "+psycopg")


def get_url() -> str:
    """Get database URL from environment or config."""
    return os.environ.get(
        "DATABASE_URL",
        config.get_main_option("sqlalchemy.url", "postgresql://localhost/toolcompiler"),
    )


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — generates SQL without connecting."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — connects to the database."""
    # Use sync driver for migrations (alembic doesn't support async natively)
    url = to_migration_database_url(get_url())
    connectable = create_engine(url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
