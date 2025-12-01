from typing import (
    TYPE_CHECKING,
    Any,
    TypeVar,
)
from uuid import uuid4

import structlog
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.factories import scheduler_factory
from app.models import IncomingWebhook, RecurringTask
from app.services.scheduler_service import execute_cron_job

if TYPE_CHECKING:
    from app.models import Provider, Trigger

logger = structlog.stdlib.get_logger(__name__)

I = TypeVar("I", bound=BaseModel)  # noqa: E741
S = TypeVar("S", bound=BaseModel)
P = TypeVar("P")


class TriggerState(BaseModel):
    webhooks: list[str]
    schedules: list[str]
    data: Any


class TriggerUtils:
    """Utility class for managing trigger lifecycle: webhooks and recurring tasks."""

    def __init__(
        self,
        session: AsyncSession,
        provider: "Provider",
        trigger: "Trigger",
        public_api_url: str,
    ):
        self.session = session
        self.provider = provider
        self.trigger = trigger
        self.public_api_url = public_api_url

    async def register_webhook(
        self,
        *,
        path: str | None = None,
        method: str = "POST",
        owner: str = "trigger",
        reuse_existing: bool = False,
    ) -> dict[str, Any]:
        """
        Register a webhook for this trigger.

        Args:
            path: Custom webhook path (optional)
            method: HTTP method (defaults to POST)
            owner: Either "trigger" or "provider"
            reuse_existing: If True and owner="provider", reuse existing provider webhook

        Returns:
            Dictionary with webhook info: id, url, path, method, owner
        """
        method = (method or "POST").upper()

        if owner not in {"trigger", "provider"}:
            raise ValueError("owner must be 'trigger' or 'provider'")

        # Check for existing provider webhook if reuse_existing is True
        if owner == "provider" and reuse_existing:
            result = await self.session.execute(
                select(IncomingWebhook).where(
                    IncomingWebhook.provider_id == self.provider.id
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                return {
                    "id": existing.id,
                    "url": f"{self.public_api_url}{existing.path}",
                    "path": existing.path,
                    "method": existing.method,
                    "owner": "provider",
                }

        # Generate webhook path
        webhook_path = path or f"/webhook/{uuid4()}"
        if path:
            webhook_path = path.strip()
            if not webhook_path.startswith("/"):
                webhook_path = f"/{webhook_path}"
            if not webhook_path.startswith("/webhook"):
                webhook_path = f"/webhook{webhook_path}"

        # Create webhook record
        webhook = IncomingWebhook(
            provider_id=self.provider.id if owner == "provider" else None,
            trigger_id=self.trigger.id if owner == "trigger" else None,
            path=webhook_path,
            method=method,
        )
        self.session.add(webhook)
        await self.session.flush()
        await self.session.refresh(webhook)

        return {
            "id": webhook.id,
            "url": f"{self.public_api_url}{webhook.path}",
            "path": webhook.path,
            "method": webhook.method,
            "owner": owner,
        }

    async def register_recurring_task(
        self,
        *,
        cron_expression: str | None = None,
        interval_seconds: int | None = None,
    ) -> dict[str, Any]:
        """
        Register a recurring task for this trigger.

        Creates a RecurringTask database record and immediately adds the
        corresponding job to APScheduler.

        Args:
            cron_expression: Cron expression for scheduling (e.g., "0 0 * * *")
            interval_seconds: Interval in seconds for polling

        Returns:
            Dictionary with recurring task info: id, cron_expression, interval_seconds
        """

        if not cron_expression and not interval_seconds:
            raise ValueError(
                "Either cron_expression or interval_seconds must be provided"
            )

        # Create recurring task record
        recurring_task = RecurringTask(trigger_id=self.trigger.id)
        self.session.add(recurring_task)
        await self.session.flush()
        await self.session.refresh(recurring_task)

        # Add job to APScheduler
        scheduler = scheduler_factory()
        job_id = f"recurring_task_{recurring_task.id}"

        if cron_expression:
            scheduler.add_job(
                execute_cron_job,
                trigger=CronTrigger.from_crontab(cron_expression, timezone="UTC"),
                args=[self.trigger.id],
                id=job_id,
                replace_existing=True,
            )
            logger.info(
                "Added cron job to APScheduler",
                job_id=job_id,
                trigger_id=str(self.trigger.id),
                cron_expression=cron_expression,
            )
        elif interval_seconds:
            scheduler.add_job(
                execute_cron_job,
                trigger=IntervalTrigger(seconds=interval_seconds),
                args=[self.trigger.id],
                id=job_id,
                replace_existing=True,
            )

        return {
            "id": recurring_task.id,
            "cron_expression": cron_expression,
            "interval_seconds": interval_seconds,
        }

    async def unregister_recurring_task(self) -> None:
        """
        Unregister a recurring task for this trigger.

        Removes the job from APScheduler and deletes the RecurringTask record.
        Called by trigger handlers in their destroy() method.
        """

        # Find recurring task for this trigger
        result = await self.session.execute(
            select(RecurringTask).where(RecurringTask.trigger_id == self.trigger.id)
        )
        recurring_task = result.scalar_one_or_none()

        if not recurring_task:
            logger.warning(
                "No recurring task found for trigger",
                trigger_id=str(self.trigger.id),
            )
            return

        # Remove job from APScheduler
        scheduler = scheduler_factory()
        job_id = f"recurring_task_{recurring_task.id}"

        try:
            scheduler.remove_job(job_id)
            logger.info(
                "Removed job from APScheduler",
                job_id=job_id,
                trigger_id=str(self.trigger.id),
            )
        except Exception as exc:
            logger.warning(
                "Failed to remove job from APScheduler (may not exist)",
                job_id=job_id,
                trigger_id=str(self.trigger.id),
                error=str(exc),
            )

        # Delete recurring task record
        await self.session.delete(recurring_task)
        await self.session.flush()

        logger.info(
            "Deleted recurring task record",
            recurring_task_id=str(recurring_task.id),
            trigger_id=str(self.trigger.id),
        )
