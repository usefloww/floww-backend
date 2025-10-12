import asyncio
from logging.config import fileConfig

import alembic_postgresql_enum  # noqa
from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app import models
from app.settings import settings

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
fileConfig(config.config_file_name)

# Base metadata for 'autogenerate' support
target_metadata = models.Base.metadata


def get_url():
    """
    Return the database URL from settings. Make sure it uses asyncpg:
    e.g. 'postgresql+asyncpg://user:pass@host:port/dbname'
    """
    return str(settings.DATABASE_URL)


def do_run_migrations(connection):
    """
    Configure the Alembic context for running migrations against
    the given connection.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    """
    Run migrations in 'online' mode using an AsyncEngine and asyncpg.
    """
    # Create an AsyncEngine; ensure DATABASE_URL is asyncpg
    url = get_url()
    connectable = create_async_engine(
        url,
        poolclass=pool.NullPool,
        future=True,
    )

    # Run migrations in a transactional context
    async with connectable.begin() as conn:
        await conn.run_sync(do_run_migrations)

    # Dispose the engine
    await connectable.dispose()


def run_migrations_offline():
    """
    Run migrations in 'offline' mode.
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """
    Entry point for running migrations in 'online' mode.
    """
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
