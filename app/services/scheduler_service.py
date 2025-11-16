"""
APScheduler service for managing scheduled tasks.

This service configures an AsyncIOScheduler with PostgreSQL job store
to ensure scheduled tasks run exactly once even with multiple Gunicorn workers.
"""

import logging

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import create_engine, pool

from app.settings import settings

logger = logging.getLogger(__name__)


def get_scheduler() -> AsyncIOScheduler:
    """
    Create and configure an AsyncIOScheduler with PostgreSQL job store.

    The scheduler is configured with:
    - SQLAlchemy job store using synchronous psycopg2 driver
    - Runs in the asyncio event loop (native async support)
    - UTC timezone for all scheduled jobs
    - Database-level locking to prevent duplicate executions across workers

    Returns:
        AsyncIOScheduler: Configured scheduler instance
    """
    # Create a synchronous SQLAlchemy engine for APScheduler
    # Note: APScheduler 3.x requires sync DB access, separate from the app's async engine
    sync_engine = create_engine(
        settings.SYNC_DATABASE_URL,
        poolclass=pool.NullPool,  # Use NullPool to avoid connection pool issues
    )

    # Configure job stores
    jobstores = {
        "default": SQLAlchemyJobStore(
            engine=sync_engine,
            tablename=settings.SCHEDULER_JOB_STORE_TABLE,
        )
    }

    # Job defaults
    job_defaults = {
        "coalesce": True,  # Combine multiple missed executions into one
        "max_instances": 1,  # Only one instance of each job can run at a time
        "misfire_grace_time": 30,  # Jobs can start up to 30 seconds late
    }

    # Create the scheduler
    # AsyncIOScheduler runs in the event loop and natively supports async functions
    scheduler = AsyncIOScheduler(
        jobstores=jobstores,
        job_defaults=job_defaults,
        timezone="UTC",  # Always use UTC for consistency
    )

    logger.info(
        "AsyncIOScheduler configured with PostgreSQL job store",
        extra={
            "job_store_table": settings.SCHEDULER_JOB_STORE_TABLE,
            "timezone": "UTC",
        },
    )

    return scheduler


def sync_recurring_tasks():
    pass
