"""
APScheduler service for managing scheduled tasks.

This service configures an AsyncIOScheduler with PostgreSQL job store
to ensure scheduled tasks run exactly once even with multiple Gunicorn workers.
"""

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
from app.factories import scheduler_factory
from app.models import RecurringTask, Trigger
from app.services.execution_history_service import create_execution_record
from app.services.trigger_execution_service import (
    build_cron_event_data,
    execute_trigger,
)
from app.settings import settings

logger = logging.getLogger(__name__)
structured_logger = structlog.stdlib.get_logger(__name__)


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


async def execute_cron_job(trigger_id: UUID) -> None:
    """
    Execute a cron job for a trigger.

    This function is called by APScheduler when a cron schedule fires.
    It loads the trigger, checks execution limits, creates an execution record,
    and invokes the workflow via the trigger execution service.

    Args:
        trigger_id: UUID of the trigger to execute
    """
    async with AsyncSessionLocal() as session:
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
                structured_logger.error(
                    "Trigger not found for cron job",
                    trigger_id=str(trigger_id),
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
