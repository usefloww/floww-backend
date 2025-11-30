import json
from typing import Any, Type
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    IncomingWebhook,
    Provider,
    Trigger,
    WorkflowDeployment,
    WorkflowDeploymentStatus,
)
from app.services.providers.implementations.builtin import BUILTIN_TRIGGER_TYPES
from app.services.providers.implementations.discord import DISCORD_TRIGGER_TYPES
from app.services.providers.implementations.github import GITHUB_TRIGGER_TYPES
from app.services.providers.implementations.gitlab import GITLAB_TRIGGER_TYPES
from app.services.providers.implementations.jira import JIRA_TRIGGER_TYPES
from app.services.providers.implementations.slack import SLACK_TRIGGER_TYPES
from app.services.providers.provider_registry import PROVIDER_TYPES_MAP
from app.services.providers.provider_utils import TriggerI, TriggerUtils
from app.settings import settings
from app.utils.encryption import decrypt_secret, encrypt_secret

logger = structlog.get_logger(__name__)


async def _ensure_provider_exists(
    session: AsyncSession, namespace_id: UUID, provider_type: str, provider_alias: str
) -> None:
    """
    Ensure a provider exists, auto-creating it if:
    1. It doesn't exist
    2. The provider type has no setup steps (like builtin, kvstore)
    """
    # Check if provider already exists
    result = await session.execute(
        select(Provider).where(
            Provider.namespace_id == namespace_id,
            Provider.type == provider_type,
            Provider.alias == provider_alias,
        )
    )

    if result.scalar_one_or_none():
        return  # Provider already exists

    # Check if this provider type requires setup
    provider_class = PROVIDER_TYPES_MAP.get(provider_type)
    if not provider_class:
        logger.warning(f"Unknown provider type: {provider_type}")
        return

    # Only auto-create providers with no setup steps
    if len(provider_class.setup_steps) > 0:
        logger.warning(
            f"Provider {provider_type}:{provider_alias} requires setup and cannot be auto-created"
        )
        return

    # Auto-create the provider
    session.add(
        Provider(
            namespace_id=namespace_id,
            type=provider_type,
            alias=provider_alias,
            encrypted_config=encrypt_secret("{}"),
        )
    )
    await session.flush()
    logger.info(
        f"Auto-created {provider_type} provider",
        namespace_id=str(namespace_id),
        alias=provider_alias,
    )


async def _ensure_builtin_provider(session: AsyncSession, namespace_id: UUID) -> None:
    """Legacy function - redirects to _ensure_provider_exists"""
    await _ensure_provider_exists(session, namespace_id, "builtin", "default")


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

    utils = _make_trigger_utils(session, provider, trigger)

    try:
        trigger_state = await handler().create(provider_state, trigger_input, utils)
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

        # Create TriggerUtils for handler
        utils = _make_trigger_utils(session, provider, trigger)

        await handler().destroy(provider_state, trigger_input, trigger_state, utils)
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


def _get_trigger_handler(provider_type: str, trigger_type: str) -> Type[TriggerI]:
    handlers = {
        "builtin": BUILTIN_TRIGGER_TYPES,
        "discord": DISCORD_TRIGGER_TYPES,
        "github": GITHUB_TRIGGER_TYPES,
        "gitlab": GITLAB_TRIGGER_TYPES,
        "jira": JIRA_TRIGGER_TYPES,
        "slack": SLACK_TRIGGER_TYPES,
    }
    if provider_type not in handlers:
        raise ValueError(f"Unknown provider type: {provider_type}")
    return handlers[provider_type][trigger_type]


def _make_trigger_utils(
    session: AsyncSession, provider: Provider, trigger: Trigger
) -> TriggerUtils:
    """Create TriggerUtils instance for trigger handlers."""
    return TriggerUtils(
        session=session,
        provider=provider,
        trigger=trigger,
        public_api_url=settings.PUBLIC_API_URL,
    )


async def _get_deployed_trigger_identities(
    session: AsyncSession, workflow_id: UUID
) -> set[tuple]:
    """
    Get trigger identities from the active deployment's trigger_definitions.
    These triggers should be preserved and not removed during dev sync.

    Returns:
        Set of trigger identities (provider_type, provider_alias, trigger_type, input_json)
    """
    # Find active deployment for the workflow
    result = await session.execute(
        select(WorkflowDeployment)
        .where(WorkflowDeployment.workflow_id == workflow_id)
        .where(WorkflowDeployment.status == WorkflowDeploymentStatus.ACTIVE)
        .order_by(WorkflowDeployment.deployed_at.desc())
        .limit(1)
    )
    deployment = result.scalar_one_or_none()

    if not deployment or not deployment.trigger_definitions:
        return set()

    # Convert deployment trigger_definitions to trigger identities
    deployed_identities = set()
    for trigger_def in deployment.trigger_definitions:
        identity = (
            trigger_def["provider"]["type"],
            trigger_def["provider"]["alias"],
            trigger_def["triggerType"],
            json.dumps(trigger_def.get("input", {}), sort_keys=True),
        )
        deployed_identities.add(identity)

    logger.info(
        "Found deployed trigger identities to preserve",
        workflow_id=str(workflow_id),
        count=len(deployed_identities),
    )

    return deployed_identities


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
        # Ensure builtin provider exists (legacy compatibility)
        await _ensure_builtin_provider(self.session, namespace_id)

        existing_triggers = await _load_existing_triggers(self.session, workflow_id)
        new_triggers = [
            t
            for t in new_triggers_metadata
            if t.get("provider_type") and t.get("provider_alias")
        ]

        # Auto-create any providers with no setup steps that don't exist yet
        unique_providers = {
            (t["provider_type"], t["provider_alias"]) for t in new_triggers
        }
        for provider_type, provider_alias in unique_providers:
            await _ensure_provider_exists(
                self.session, namespace_id, provider_type, provider_alias
            )

        existing_map = await _build_identity_map(self.session, existing_triggers)
        new_map = {_trigger_identity(t): t for t in new_triggers}

        # Get deployed trigger identities to preserve
        deployed_identities = await _get_deployed_trigger_identities(
            self.session, workflow_id
        )

        # Exclude deployed triggers from removal
        # Logic: to_remove = (existing - new) - deployed
        to_remove = (
            set(existing_map.keys()) - set(new_map.keys())
        ) - deployed_identities
        to_add = set(new_map.keys()) - set(existing_map.keys())
        to_keep = set(existing_map.keys()) & set(new_map.keys())

        logger.info(
            "Trigger sync plan",
            workflow_id=str(workflow_id),
            to_remove=len(to_remove),
            to_add=len(to_add),
            to_keep=len(to_keep),
            deployed_protected=len(deployed_identities),
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
