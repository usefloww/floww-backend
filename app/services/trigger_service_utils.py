from typing import (
    TYPE_CHECKING,
    Any,
    TypeVar,
)
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.models import Provider, Trigger

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
        from app.models import IncomingWebhook

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

        Args:
            cron_expression: Cron expression for scheduling (e.g., "0 0 * * *")
            interval_seconds: Interval in seconds for polling

        Returns:
            Dictionary with recurring task info: id, cron_expression, interval_seconds
        """
        from app.models import RecurringTask

        if not cron_expression and not interval_seconds:
            raise ValueError(
                "Either cron_expression or interval_seconds must be provided"
            )

        # Create recurring task record
        recurring_task = RecurringTask(trigger_id=self.trigger.id)
        self.session.add(recurring_task)
        await self.session.flush()
        await self.session.refresh(recurring_task)

        return {
            "id": recurring_task.id,
            "cron_expression": cron_expression,
            "interval_seconds": interval_seconds,
        }
