"""Utility functions for running database migrations programmatically."""

from alembic import command
from alembic.config import Config
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.settings import settings


async def run_migrations():
    """Run the migrations programmatically by invoking alembic.

    This function passes the database connection to Alembic via config.attributes,
    which allows it to run within the existing event loop without conflicts.
    """

    # Get database URL
    url = str(settings.DATABASE_URL)

    # Create an AsyncEngine
    engine = create_async_engine(
        url,
        poolclass=pool.NullPool,
        future=True,
    )

    def run_upgrade(connection, cfg):
        """Execute the upgrade command with the provided connection."""
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")

    # Load alembic configuration
    alembic_cfg = Config("alembic.ini")

    # Run migrations with the connection in the existing event loop
    async with engine.begin() as conn:
        await conn.run_sync(run_upgrade, alembic_cfg)

    # Dispose the engine
    await engine.dispose()
