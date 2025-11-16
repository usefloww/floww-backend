"""
Proof-of-concept test job for APScheduler.

This job demonstrates that the scheduler is working correctly and only
executes once across multiple Gunicorn workers.
"""

import asyncio
import logging
import os
from datetime import UTC, datetime

from sqlalchemy import text

from app.deps.db import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def test_scheduled_job():
    """
    Simple test job that runs every minute.

    This job:
    1. Logs execution time and worker process ID
    2. Queries the database to verify connectivity
    3. Demonstrates async execution within APScheduler

    This helps verify:
    - Scheduler is running correctly
    - Only one execution occurs despite multiple workers
    - Database connectivity works from scheduled jobs
    """
    execution_time = datetime.now(UTC)
    worker_pid = os.getpid()

    logger.info(
        "Test scheduled job executed",
        extra={
            "execution_time": execution_time.isoformat(),
            "worker_pid": worker_pid,
            "job_name": "test_scheduled_job",
        },
    )

    # Test database connectivity
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("SELECT 1"))
            db_check = result.scalar()

            logger.info(
                "Database connectivity verified from scheduled job",
                extra={
                    "db_check_result": db_check,
                    "worker_pid": worker_pid,
                },
            )
    except Exception as e:
        logger.error(
            "Database connectivity failed in scheduled job",
            extra={
                "error": str(e),
                "worker_pid": worker_pid,
            },
            exc_info=True,
        )
        raise

    # Simulate some async work
    await asyncio.sleep(0.1)

    logger.info(
        "Test scheduled job completed successfully",
        extra={
            "worker_pid": worker_pid,
            "duration_ms": 100,
        },
    )
