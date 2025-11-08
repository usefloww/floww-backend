import json
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IncomingWebhook, Provider, Trigger
from app.services.providers.implementations.builtin import BUILTIN_TRIGGER_TYPES
from app.services.providers.implementations.gitlab import GITLAB_TRIGGER_TYPES
from app.services.providers.implementations.jira import JIRA_TRIGGER_TYPES
from app.services.providers.implementations.slack import SLACK_TRIGGER_TYPES
from app.services.providers.provider_registry import PROVIDER_TYPES_MAP
from app.settings import settings
from app.utils.encryption import decrypt_secret, encrypt_secret

logger = structlog.get_logger(__name__)


async def _ensure_builtin_provider(session: AsyncSession, namespace_id: UUID) -> None:
    result = await session.execute(
        select(Provider).where(
            Provider.namespace_id == namespace_id,
            Provider.type == "builtin",
            Provider.alias == "default",
        )
    )

    if not result.scalar_one_or_none():
        session.add(
            Provider(
                namespace_id=namespace_id,
                type="builtin",
                alias="default",
                encrypted_config=encrypt_secret("{}"),
            )
        )
        await session.flush()
        logger.info("Auto-created builtin provider", namespace_id=str(namespace_id))


async def _load_existing_triggers(
    session: AsyncSession, workflow_id: UUID
) -> list[Trigger]:
    result = await session.execute(
        select(Trigger).where(Trigger.workflow_id == workflow_id)
    )
    return list(result.scalars().all())


async def _build_identity_map(
    session: AsyncSession, triggers: list[Trigger]
) -> dict[tuple, Trigger]:
    """Build map of trigger identities to trigger objects."""
    trigger_map = {}
    for trigger in triggers:
        provider = await session.get(Provider, trigger.provider_id)
        if provider:
            identity = (
                provider.type,
                provider.alias,
                trigger.trigger_type,
                json.dumps(trigger.input, sort_keys=True),
            )
            trigger_map[identity] = trigger
    return trigger_map


def _trigger_identity(trigger_meta: dict) -> tuple:
    return (
        trigger_meta["provider_type"],
        trigger_meta["provider_alias"],
        trigger_meta["trigger_type"],
        json.dumps(trigger_meta["input"], sort_keys=True),
    )


async def _create_trigger(
    session: AsyncSession,
    workflow_id: UUID,
    namespace_id: UUID,
    trigger_meta: dict[str, Any],
) -> None:
    provider = await _get_provider(session, namespace_id, trigger_meta)
    provider_state = _load_provider_state(provider, trigger_meta["provider_type"])
    handler = _get_trigger_handler(
        trigger_meta["provider_type"], trigger_meta["trigger_type"]
    )
    trigger_input = handler.input_schema()(**trigger_meta["input"])

    trigger = Trigger(
        workflow_id=workflow_id,
        provider_id=provider.id,
        trigger_type=trigger_meta["trigger_type"],
        input=trigger_meta["input"],
        state={},
    )
    session.add(trigger)
    await session.flush()
    await session.refresh(trigger)

    register_webhook = _make_webhook_registrar(session, provider, trigger)

    try:
        trigger_state = await handler().create(
            provider_state, trigger_input, register_webhook
        )
        trigger.state = trigger_state.model_dump()
        await session.flush()
        logger.info("Created trigger", trigger_id=str(trigger.id))
    except Exception:
        await session.delete(trigger)
        await session.flush()
        raise


async def _destroy_trigger(session: AsyncSession, trigger: Trigger) -> None:
    provider = await session.get(Provider, trigger.provider_id)
    if not provider:
        logger.warning(
            "Provider not found during destruction", trigger_id=str(trigger.id)
        )
        return

    try:
        provider_state = _load_provider_state(provider, provider.type)
        handler = _get_trigger_handler(provider.type, trigger.trigger_type)
        trigger_input = handler.input_schema()(**trigger.input)
        trigger_state = handler.state_schema()(**trigger.state)

        await handler().destroy(provider_state, trigger_input, trigger_state)
        logger.info("Destroyed trigger", trigger_id=str(trigger.id))
    except Exception as e:
        logger.error(
            "Failed to destroy trigger", trigger_id=str(trigger.id), error=str(e)
        )


async def _refresh_trigger(session: AsyncSession, trigger: Trigger) -> None:
    if not trigger.state:
        return

    provider = await session.get(Provider, trigger.provider_id)
    if not provider:
        logger.warning("Provider not found during refresh", trigger_id=str(trigger.id))
        return

    try:
        provider_state = _load_provider_state(provider, provider.type)
        handler = _get_trigger_handler(provider.type, trigger.trigger_type)
        trigger_input = handler.input_schema()(**trigger.input)
        trigger_state = handler.state_schema()(**trigger.state)

        new_state = await handler().refresh(
            provider_state, trigger_input, trigger_state
        )
        trigger.state = new_state.model_dump()
        logger.info("Refreshed trigger", trigger_id=str(trigger.id))
    except Exception as e:
        logger.error(
            "Failed to refresh trigger", trigger_id=str(trigger.id), error=str(e)
        )


async def _collect_webhooks(
    session: AsyncSession,
    workflow_id: UUID,
    existing_map: dict[tuple, Trigger],
    new_map: dict[tuple, dict],
    added: set[tuple],
    kept: set[tuple],
) -> list[dict[str, Any]]:
    """Collect webhook info from all active triggers, avoiding duplicates."""
    webhooks_info = []
    seen_ids = set()

    all_current_triggers = await _load_existing_triggers(session, workflow_id)
    current_trigger_map = await _build_identity_map(session, all_current_triggers)

    # Collect webhooks from both kept and newly added triggers
    for identity in kept | added:
        trigger = current_trigger_map.get(identity)
        if not trigger:
            continue

        result = await session.execute(
            select(IncomingWebhook).where(
                (IncomingWebhook.trigger_id == trigger.id)
                | (IncomingWebhook.provider_id == trigger.provider_id)
            )
        )
        webhook = result.scalar_one_or_none()

        if webhook and webhook.id not in seen_ids:
            provider = await session.get(Provider, trigger.provider_id)
            if provider:
                webhooks_info.append(
                    {
                        "id": webhook.id,
                        "url": f"{settings.PUBLIC_API_URL}{webhook.path}",
                        "path": webhook.path,
                        "method": webhook.method,
                        "provider_type": provider.type,
                        "provider_alias": provider.alias,
                        "trigger_type": trigger.trigger_type,
                        "trigger_id": trigger.id,
                    }
                )
                seen_ids.add(webhook.id)

    return webhooks_info


async def _get_provider(
    session: AsyncSession, namespace_id: UUID, trigger_meta: dict
) -> Provider:
    result = await session.execute(
        select(Provider).where(
            Provider.namespace_id == namespace_id,
            Provider.type == trigger_meta["provider_type"],
            Provider.alias == trigger_meta["provider_alias"],
        )
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise ValueError(
            f"Provider {trigger_meta['provider_type']}:{trigger_meta['provider_alias']} not found"
        )
    return provider


def _load_provider_state(provider: Provider, provider_type: str):
    config = json.loads(decrypt_secret(provider.encrypted_config))
    return PROVIDER_TYPES_MAP[provider_type].model(**config)


def _get_trigger_handler(provider_type: str, trigger_type: str):
    handlers = {
        "gitlab": GITLAB_TRIGGER_TYPES,
        "jira": JIRA_TRIGGER_TYPES,
        "slack": SLACK_TRIGGER_TYPES,
        "builtin": BUILTIN_TRIGGER_TYPES,
    }
    if provider_type not in handlers:
        raise ValueError(f"Unknown provider type: {provider_type}")
    return handlers[provider_type][trigger_type]


def _make_webhook_registrar(
    session: AsyncSession, provider: Provider, trigger: Trigger
):
    """Create webhook registration function for trigger handlers."""

    async def register_webhook(
        *,
        path: str | None = None,
        method: str = "POST",
        owner: str = "trigger",
        reuse_existing: bool = False,
    ) -> dict[str, Any]:
        method = (method or "POST").upper()

        if owner not in {"trigger", "provider"}:
            raise ValueError("owner must be 'trigger' or 'provider'")

        if owner == "provider" and reuse_existing:
            result = await session.execute(
                select(IncomingWebhook).where(
                    IncomingWebhook.provider_id == provider.id
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                return {
                    "id": existing.id,
                    "url": f"{settings.PUBLIC_API_URL}{existing.path}",
                    "path": existing.path,
                    "method": existing.method,
                    "owner": "provider",
                }

        webhook_path = path or f"/webhook/{uuid4()}"
        if path:
            webhook_path = path.strip()
            if not webhook_path.startswith("/"):
                webhook_path = f"/{webhook_path}"
            if not webhook_path.startswith("/webhook"):
                webhook_path = f"/webhook{webhook_path}"

        webhook = IncomingWebhook(
            provider_id=provider.id if owner == "provider" else None,
            trigger_id=trigger.id if owner == "trigger" else None,
            path=webhook_path,
            method=method,
        )
        session.add(webhook)
        await session.flush()
        await session.refresh(webhook)

        return {
            "id": webhook.id,
            "url": f"{settings.PUBLIC_API_URL}{webhook.path}",
            "path": webhook.path,
            "method": webhook.method,
            "owner": owner,
        }

    return register_webhook


class TriggerService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def sync_triggers(
        self,
        workflow_id: UUID,
        namespace_id: UUID,
        new_triggers_metadata: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Sync triggers for a workflow.
        Returns list of webhook info for created/existing webhooks.
        """
        await _ensure_builtin_provider(self.session, namespace_id)

        existing_triggers = await _load_existing_triggers(self.session, workflow_id)
        new_triggers = [
            t
            for t in new_triggers_metadata
            if t.get("provider_type") and t.get("provider_alias")
        ]

        existing_map = await _build_identity_map(self.session, existing_triggers)
        new_map = {_trigger_identity(t): t for t in new_triggers}

        to_remove = set(existing_map.keys()) - set(new_map.keys())
        to_add = set(new_map.keys()) - set(existing_map.keys())
        to_keep = set(existing_map.keys()) & set(new_map.keys())

        logger.info(
            "Trigger sync plan",
            workflow_id=str(workflow_id),
            to_remove=len(to_remove),
            to_add=len(to_add),
            to_keep=len(to_keep),
        )

        for identity in to_remove:
            await _destroy_trigger(self.session, existing_map[identity])
            await self.session.delete(existing_map[identity])

        errors = []
        for identity in to_add:
            try:
                await _create_trigger(
                    self.session, workflow_id, namespace_id, new_map[identity]
                )
            except Exception as e:
                meta = new_map[identity]
                errors.append(
                    {
                        "provider_type": meta["provider_type"],
                        "trigger_type": meta["trigger_type"],
                        "error": str(e),
                    }
                )
                logger.error(
                    "Failed to create trigger",
                    provider_type=meta["provider_type"],
                    trigger_type=meta["trigger_type"],
                    error=str(e),
                )

        for identity in to_keep:
            await _refresh_trigger(self.session, existing_map[identity])

        webhooks_info = await _collect_webhooks(
            self.session, workflow_id, existing_map, new_map, to_add, to_keep
        )

        if errors:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=400,
                detail={
                    "message": "Failed to create one or more triggers",
                    "failed_triggers": errors,
                },
            )

        await self.session.flush()
        return webhooks_info
