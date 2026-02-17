"""Alembic migration environment.

Reads DATABASE_URL from schemint config (same source of truth as the app).
Uses raw SQL migrations via op.execute() — no SQLAlchemy ORM models.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _get_database_url() -> str:
    """Resolve database URL from environment or schemint config."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url

    # Fall back to schemint settings
    try:
        from schemint.config import get_settings

        settings = get_settings()
        if settings.database_url:
            return settings.database_url
    except Exception:
        pass

    raise RuntimeError(
        "DATABASE_URL must be set as an environment variable "
        "or in schemint config (.env file)."
    )


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emit SQL to stdout."""
    context.configure(
        url=_get_database_url(),
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    engine = create_engine(_get_database_url())

    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()

    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
