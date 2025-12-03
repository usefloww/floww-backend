"""
APScheduler service for managing scheduled tasks.

This service configures an AsyncIOScheduler with PostgreSQL job store
to ensure scheduled tasks run exactly once even with multiple Gunicorn workers.
"""

import hashlib
import logging
from datetime import datetime, timezone
from uuid import UUID

import structlog
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import create_engine, pool, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.deps.db import AsyncSessionLocal
from app.factories import runtime_factory, scheduler_factory
from app.models import RecurringTask, Trigger
from app.services.execution_history_service import create_execution_record
from app.services.trigger_execution_service import (
    build_cron_event_data,
    execute_trigger,
)
from app.settings import settings
from app.utils.locking_utils import advisory_lock

logger = logging.getLogger(__name__)
structured_logger = structlog.stdlib.get_logger(__name__)


def _generate_lock_key(trigger_id: UUID, scheduled_run_time: datetime | None) -> int:
    """
    Generates a single, deterministic 64-bit integer key from the trigger ID
    and the scheduled run time using Python's built-in hashlib (SHA-256).
    """
    # 1. Get the 128-bit integer representation of the UUID
    uuid_bytes = trigger_id.bytes

    # 2. Get the Unix timestamp as bytes (8 bytes for 64-bit representation)
    if scheduled_run_time:
        timestamp_int = int(scheduled_run_time.timestamp())
        timestamp_bytes = timestamp_int.to_bytes(8, "big")
    else:
        timestamp_bytes = b"\x00" * 8

    # 3. Concatenate the unique bytes
    data_to_hash = uuid_bytes + timestamp_bytes

    # 4. Hash the combined data using SHA-256
    # This ensures a strong, deterministic mapping.
    hasher = hashlib.sha256()
    hasher.update(data_to_hash)

    # 5. Truncate the 256-bit hash digest down to 64 bits (8 bytes)
    # This result is used as the PostgreSQL BIGINT key.
    # We take the first 8 bytes of the digest.
    truncated_digest = hasher.digest()[:8]

    # 6. Convert the 8 bytes back into an unsigned 64-bit integer
    # Then ensure it fits within PostgreSQL's signed 64-bit BIGINT range
    # (PostgreSQL BIGINT is signed: -2^63 to 2^63-1)
    unsigned_key = int.from_bytes(truncated_digest, "big")
    # Use modulo to ensure the value fits in signed 64-bit range [0, 2^63-1]
    return unsigned_key % (2**63)


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


async def execute_cron_job(
    trigger_id: UUID,
    scheduled_run_time: datetime | None = None,
) -> None:
    """
    Execute a cron job for a trigger.

    This function is called by APScheduler when a cron schedule fires.
    It loads the trigger, checks execution limits, creates an execution record,
    and invokes the workflow via the trigger execution service.

    Args:
        trigger_id: UUID of the trigger to execute
    """
    lock_key = _generate_lock_key(trigger_id, scheduled_run_time)

    async with AsyncSessionLocal() as session:
        async with advisory_lock(session, lock_key) as lock_acquired:
            if not lock_acquired:
                structured_logger.info(
                    "Skipping cron job - lock held by another worker",
                    trigger_id=str(trigger_id),
                )
                return

            try:
                # Load trigger with relationships
                result = await session.execute(
                    select(Trigger)
                    .options(
                        selectinload(Trigger.workflow),
                        selectinload(Trigger.provider),
                    )
                    .where(Trigger.id == trigger_id)
                )
                trigger = result.scalar_one_or_none()

                if not trigger:
                    structured_logger.warning(
                        "Trigger not found for cron job, removing orphaned job",
                        trigger_id=str(trigger_id),
                    )
                    # Remove orphaned job from APScheduler
                    scheduler = scheduler_factory()
                    try:
                        scheduler.remove_job(f"recurring_task_{trigger_id}")
                    except Exception as exc:
                        structured_logger.warning(
                            "Failed to remove orphaned APScheduler job",
                            job_id=f"recurring_task_{trigger_id}",
                            trigger_id=str(trigger_id),
                            error=str(exc),
                        )
                    return

                # Check execution limits (cloud only)
                if settings.IS_CLOUD:
                    from app.routes.webhooks import _check_execution_limit_for_workflow

                    try:
                        await _check_execution_limit_for_workflow(
                            session, trigger.workflow_id
                        )
                    except Exception as exc:
                        structured_logger.warning(
                            "Execution limit reached for cron job",
                            trigger_id=str(trigger_id),
                            workflow_id=str(trigger.workflow_id),
                            error=str(exc),
                        )
                        return

                # Create execution history record
                execution = await create_execution_record(
                    session=session,
                    workflow_id=trigger.workflow_id,
                    trigger_id=trigger.id,
                )

                # Build cron event data
                cron_expression = trigger.input.get("expression", "unknown")
                event_data = build_cron_event_data(
                    cron_expression=cron_expression,
                    scheduled_time=datetime.now(timezone.utc),
                )

                structured_logger.info(
                    "Executing cron job",
                    trigger_id=str(trigger_id),
                    workflow_id=str(trigger.workflow_id),
                    execution_id=str(execution.id),
                    cron_expression=cron_expression,
                )

                # Execute trigger via unified service
                await execute_trigger(
                    session=session,
                    trigger=trigger,
                    event_data=event_data,
                    execution_id=execution.id,
                )

            except Exception as exc:
                structured_logger.error(
                    "Cron job execution failed",
                    trigger_id=str(trigger_id),
                    error=str(exc),
                    exc_info=True,
                )


async def cleanup_unused_runtimes() -> None:
    """
    Clean up unused runtimes across all runtime implementations.

    This function is called periodically by APScheduler to remove idle/unused
    containers and runtime resources to save costs and prevent resource exhaustion.
    """
    try:
        structured_logger.info("Starting cleanup of unused runtimes")

        runtime = runtime_factory()
        await runtime.teardown_unused_runtimes()

        structured_logger.info("Completed cleanup of unused runtimes")

    except Exception as exc:
        structured_logger.error(
            "Failed to cleanup unused runtimes",
            error=str(exc),
            exc_info=True,
        )


async def sync_all_recurring_tasks(session: AsyncSession) -> None:
    """
    Sync all recurring tasks from database to APScheduler.

    This function is meant to be called manually from a production shell
    to sync the database state with APScheduler's job store.

    It loads all RecurringTask records, adds/updates their jobs in APScheduler,
    and removes orphaned jobs that exist in APScheduler but not in the database.

    Args:
        session: Database session
    """
    scheduler = scheduler_factory()

    structured_logger.info("Starting sync of all recurring tasks")

    # Load all RecurringTask records with their triggers
    result = await session.execute(
        select(RecurringTask).options(selectinload(RecurringTask.trigger))
    )
    recurring_tasks = list(result.scalars().all())

    structured_logger.info(
        "Loaded recurring tasks from database",
        count=len(recurring_tasks),
    )

    # Track job IDs that should exist
    expected_job_ids = set()

    # Add/update jobs for each recurring task
    for recurring_task in recurring_tasks:
        job_id = f"recurring_task_{recurring_task.id}"
        expected_job_ids.add(job_id)

        # Get cron expression from trigger input
        cron_expression = recurring_task.trigger.input.get("expression")
        if not cron_expression:
            structured_logger.warning(
                "Recurring task missing cron expression in trigger input",
                recurring_task_id=str(recurring_task.id),
                trigger_id=str(recurring_task.trigger_id),
            )
            continue

        try:
            # Check if job already exists
            existing_job = scheduler.get_job(job_id)

            if existing_job:
                # Update existing job
                scheduler.reschedule_job(
                    job_id,
                    trigger=CronTrigger.from_crontab(cron_expression, timezone="UTC"),
                )
                structured_logger.info(
                    "Updated APScheduler job",
                    job_id=job_id,
                    cron_expression=cron_expression,
                )
            else:
                # Add new job
                scheduler.add_job(
                    execute_cron_job,
                    trigger=CronTrigger.from_crontab(cron_expression, timezone="UTC"),
                    args=[recurring_task.trigger_id],
                    id=job_id,
                    replace_existing=True,
                )
                structured_logger.info(
                    "Added APScheduler job",
                    job_id=job_id,
                    cron_expression=cron_expression,
                )
        except Exception as exc:
            structured_logger.error(
                "Failed to sync recurring task to APScheduler",
                recurring_task_id=str(recurring_task.id),
                job_id=job_id,
                error=str(exc),
            )

    # Remove orphaned jobs (in APScheduler but not in database)
    all_jobs = scheduler.get_jobs()
    for job in all_jobs:
        if job.id.startswith("recurring_task_") and job.id not in expected_job_ids:
            scheduler.remove_job(job.id)
            structured_logger.info(
                "Removed orphaned APScheduler job",
                job_id=job.id,
            )

    structured_logger.info(
        "Completed sync of all recurring tasks",
        synced_count=len(recurring_tasks),
        total_jobs=len(scheduler.get_jobs()),
    )


def sync_recurring_tasks():
    pass
