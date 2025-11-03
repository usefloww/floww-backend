import json
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IncomingWebhook, Provider, Trigger
from app.services.providers.implementations.builtin import BUILTIN_TRIGGER_TYPES
from app.services.providers.implementations.gitlab import (
    GITLAB_TRIGGER_TYPES,
)
from app.services.providers.implementations.jira import JIRA_TRIGGER_TYPES
from app.services.providers.implementations.slack import SLACK_TRIGGER_TYPES
from app.services.providers.provider_registry import PROVIDER_TYPES_MAP
from app.settings import settings
from app.utils.encryption import decrypt_secret

logger = structlog.get_logger(__name__)


class TriggerService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def ensure_builtin_provider(self, namespace_id: UUID) -> Provider:
        """Ensure builtin provider exists for namespace, create if needed."""
        result = await self.session.execute(
            select(Provider).where(
                Provider.namespace_id == namespace_id,
                Provider.type == "builtin",
                Provider.alias == "default",
            )
        )
        provider = result.scalar_one_or_none()

        if not provider:
            from app.utils.encryption import encrypt_secret

            provider = Provider(
                namespace_id=namespace_id,
                type="builtin",
                alias="default",
                encrypted_config=encrypt_secret("{}"),
            )
            self.session.add(provider)
            await self.session.flush()
            logger.info("Auto-created builtin provider", namespace_id=str(namespace_id))

        return provider

    async def sync_triggers(
        self,
        workflow_id: UUID,
        namespace_id: UUID,
        new_triggers_metadata: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Sync triggers for a workflow:
        1. Diff new triggers vs existing triggers
        2. Destroy removed triggers
        3. Create new triggers
        4. Refresh unchanged triggers

        Returns list of webhook info for created/existing webhooks.
        """
        # Ensure builtin provider exists before processing triggers
        await self.ensure_builtin_provider(namespace_id)

        # Get existing triggers for this workflow
        result = await self.session.execute(
            select(Trigger).where(Trigger.workflow_id == workflow_id)
        )
        existing_triggers = list(result.scalars().all())

        # Parse new triggers metadata (all triggers now have provider metadata from SDK)
        new_triggers = [
            t
            for t in new_triggers_metadata
            if t.get("provider_type") and t.get("provider_alias")
        ]

        # Build trigger identity keys for comparison
        def trigger_identity(trigger_meta: dict) -> tuple:
            """Create a unique identity for a trigger based on provider+type+input."""
            return (
                trigger_meta["provider_type"],
                trigger_meta["provider_alias"],
                trigger_meta["trigger_type"],
                json.dumps(trigger_meta["input"], sort_keys=True),
            )

        existing_by_identity = {}
        for trigger in existing_triggers:
            # Reconstruct identity from DB record
            provider = await self.session.get(Provider, trigger.provider_id)
            if not provider:
                continue
            identity = (
                provider.type,
                provider.alias,
                trigger.trigger_type,
                json.dumps(trigger.input, sort_keys=True),
            )
            existing_by_identity[identity] = trigger

        new_by_identity = {trigger_identity(t): t for t in new_triggers}

        # Determine changes
        to_remove = set(existing_by_identity.keys()) - set(new_by_identity.keys())
        to_add = set(new_by_identity.keys()) - set(existing_by_identity.keys())
        to_keep = set(existing_by_identity.keys()) & set(new_by_identity.keys())

        logger.info(
            "Trigger sync plan",
            workflow_id=str(workflow_id),
            to_remove=len(to_remove),
            to_add=len(to_add),
            to_keep=len(to_keep),
        )

        webhooks_info = []
        seen_webhook_ids = (
            set()
        )  # Track webhooks we've already added (for deduplication)

        # 1. Destroy removed triggers
        for identity in to_remove:
            trigger = existing_by_identity[identity]
            await self._destroy_trigger(trigger)
            await self.session.delete(trigger)
            logger.info("Destroyed trigger", trigger_id=str(trigger.id))

        # 2. Create new triggers
        for identity in to_add:
            trigger_meta = new_by_identity[identity]
            webhook_info = await self._create_trigger(
                workflow_id, namespace_id, trigger_meta
            )
            if webhook_info:
                webhooks_info.append(webhook_info)
                seen_webhook_ids.add(webhook_info["id"])

        # 3. Refresh unchanged triggers (verify they still exist)
        for identity in to_keep:
            trigger = existing_by_identity[identity]
            await self._refresh_trigger(trigger)
            logger.info("Refreshed trigger", trigger_id=str(trigger.id))

            # Add webhook info for existing triggers
            # Check both trigger-owned and provider-owned webhooks
            incoming_webhook_result = await self.session.execute(
                select(IncomingWebhook).where(
                    (IncomingWebhook.trigger_id == trigger.id)
                    | (IncomingWebhook.provider_id == trigger.provider_id)
                )
            )
            incoming_webhook = incoming_webhook_result.scalar_one_or_none()
            if incoming_webhook and incoming_webhook.id not in seen_webhook_ids:
                provider = await self.session.get(Provider, trigger.provider_id)
                webhook_url = f"{settings.PUBLIC_API_URL}{incoming_webhook.path}"
                webhooks_info.append(
                    {
                        "id": incoming_webhook.id,
                        "url": webhook_url,
                        "path": incoming_webhook.path,
                        "method": incoming_webhook.method,
                        "provider_type": provider.type if provider else None,
                        "provider_alias": provider.alias if provider else None,
                        "trigger_type": trigger.trigger_type,
                        "trigger_id": trigger.id,
                    }
                )
                seen_webhook_ids.add(incoming_webhook.id)

        await self.session.flush()
        return webhooks_info

    async def _create_trigger(
        self,
        workflow_id: UUID,
        namespace_id: UUID,
        trigger_meta: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Create a new trigger and register its webhook."""
        # Look up provider
        provider_result = await self.session.execute(
            select(Provider).where(
                Provider.namespace_id == namespace_id,
                Provider.type == trigger_meta["provider_type"],
                Provider.alias == trigger_meta["provider_alias"],
            )
        )
        provider = provider_result.scalar_one_or_none()
        if not provider:
            logger.error(
                "Provider not found",
                namespace_id=str(namespace_id),
                provider_type=trigger_meta["provider_type"],
                provider_alias=trigger_meta["provider_alias"],
            )
            raise ValueError(
                f"Provider {trigger_meta['provider_type']}:{trigger_meta['provider_alias']} not found"
            )

        # Get provider config
        provider_config_json = decrypt_secret(provider.encrypted_config)
        provider_config = json.loads(provider_config_json)

        trigger_handler = self._get_trigger_handler(
            trigger_meta["provider_type"], trigger_meta["trigger_type"]
        )

        provider_type_class = PROVIDER_TYPES_MAP[trigger_meta["provider_type"]]
        provider_state = provider_type_class.model(**provider_config)

        trigger_input_class = trigger_handler.input_schema()
        trigger_input = trigger_input_class(**trigger_meta["input"])

        # Pre-create trigger so register_webhook has an ID to associate trigger-owned webhooks
        trigger = Trigger(
            workflow_id=workflow_id,
            provider_id=provider.id,
            trigger_type=trigger_meta["trigger_type"],
            input=trigger_meta["input"],
            state={},
        )
        self.session.add(trigger)
        await self.session.flush()
        await self.session.refresh(trigger)

        registered_webhooks: list[dict[str, Any]] = []

        async def register_webhook(
            *,
            path: str | None = None,
            method: str = "POST",
            owner: str = "trigger",
            reuse_existing: bool = False,
        ) -> dict[str, Any]:
            method_upper = (method or "POST").upper()

            if owner not in {"trigger", "provider"}:
                raise ValueError("owner must be 'trigger' or 'provider'")

            # Provider-owned webhooks can optionally reuse an existing registration
            if owner == "provider" and reuse_existing:
                existing_result = await self.session.execute(
                    select(IncomingWebhook).where(
                        IncomingWebhook.provider_id == provider.id
                    )
                )
                existing_webhook = existing_result.scalar_one_or_none()
                if existing_webhook:
                    info = {
                        "id": existing_webhook.id,
                        "url": f"{settings.PUBLIC_API_URL}{existing_webhook.path}",
                        "path": existing_webhook.path,
                        "method": existing_webhook.method,
                        "owner": "provider",
                    }
                    registered_webhooks.append(info)
                    return info

            normalized_path = None
            if path:
                normalized_path = path.strip()
                if not normalized_path.startswith("/"):
                    normalized_path = f"/{normalized_path}"
                if not normalized_path.startswith("/webhook"):
                    normalized_path = f"/webhook{normalized_path}"
            else:
                normalized_path = f"/webhook/{uuid4()}"

            if owner == "provider":
                incoming_webhook = IncomingWebhook(
                    provider_id=provider.id,
                    path=normalized_path,
                    method=method_upper,
                )
            else:
                incoming_webhook = IncomingWebhook(
                    trigger_id=trigger.id,
                    path=normalized_path,
                    method=method_upper,
                )

            self.session.add(incoming_webhook)
            await self.session.flush()
            await self.session.refresh(incoming_webhook)

            info = {
                "id": incoming_webhook.id,
                "url": f"{settings.PUBLIC_API_URL}{incoming_webhook.path}",
                "path": incoming_webhook.path,
                "method": incoming_webhook.method,
                "owner": owner,
            }
            registered_webhooks.append(info)
            return info

        trigger_state = await trigger_handler().create(
            provider_state, trigger_input, register_webhook
        )

        trigger.state = trigger_state.model_dump()
        self.session.add(trigger)
        await self.session.flush()
        await self.session.refresh(trigger)

        primary_webhook = registered_webhooks[0] if registered_webhooks else None

        logger.info(
            "Created trigger",
            trigger_id=str(trigger.id),
            has_webhook=primary_webhook is not None,
            webhook_owner=primary_webhook["owner"] if primary_webhook else None,
        )

        if primary_webhook:
            return {
                "id": primary_webhook["id"],
                "url": primary_webhook["url"],
                "path": primary_webhook["path"],
                "method": primary_webhook["method"],
                "provider_type": provider.type,
                "provider_alias": provider.alias,
                "trigger_type": trigger.trigger_type,
                "trigger_id": trigger.id,
            }

        return None

    async def _destroy_trigger(self, trigger: Trigger) -> None:
        """Destroy a trigger and clean up its webhook."""
        # Get provider
        provider = await self.session.get(Provider, trigger.provider_id)
        if not provider:
            logger.warning(
                "Provider not found during trigger destruction",
                trigger_id=str(trigger.id),
            )
            return

        # Get provider config
        provider_config_json = decrypt_secret(provider.encrypted_config)
        provider_config = json.loads(provider_config_json)

        # Get trigger handler
        trigger_handler = self._get_trigger_handler(provider.type, trigger.trigger_type)

        # Call trigger's destroy method
        provider_type_class = PROVIDER_TYPES_MAP[provider.type]
        provider_state = provider_type_class.model(**provider_config)

        trigger_input_class = trigger_handler.input_schema()
        trigger_input = trigger_input_class(**trigger.input)

        trigger_state_class = trigger_handler.state_schema()
        trigger_state = trigger_state_class(**trigger.state)

        try:
            await trigger_handler().destroy(
                provider_state, trigger_input, trigger_state
            )
        except Exception as e:
            logger.error(
                "Failed to destroy trigger",
                trigger_id=str(trigger.id),
                error=str(e),
            )
            # Continue with database cleanup even if external cleanup fails

    async def _refresh_trigger(self, trigger: Trigger) -> None:
        """Refresh a trigger to verify it still exists."""
        # Get provider
        provider = await self.session.get(Provider, trigger.provider_id)
        if not provider:
            logger.warning(
                "Provider not found during trigger refresh",
                trigger_id=str(trigger.id),
            )
            return

        # Get provider config
        provider_config_json = decrypt_secret(provider.encrypted_config)
        provider_config = json.loads(provider_config_json)

        # Get trigger handler
        trigger_handler = self._get_trigger_handler(provider.type, trigger.trigger_type)

        # Call trigger's refresh method
        provider_type_class = PROVIDER_TYPES_MAP[provider.type]
        provider_state = provider_type_class.model(**provider_config)

        trigger_input_class = trigger_handler.input_schema()
        trigger_input = trigger_input_class(**trigger.input)

        trigger_state_class = trigger_handler.state_schema()
        trigger_state = trigger_state_class(**trigger.state)

        try:
            new_state = await trigger_handler().refresh(
                provider_state, trigger_input, trigger_state
            )
            trigger.state = new_state.model_dump()
        except Exception as e:
            logger.error(
                "Failed to refresh trigger",
                trigger_id=str(trigger.id),
                error=str(e),
            )

    def _get_trigger_handler(self, provider_type: str, trigger_type: str):
        """Get the trigger handler class for a given provider and trigger type."""
        if provider_type == "gitlab":
            return GITLAB_TRIGGER_TYPES[trigger_type]
        elif provider_type == "jira":
            return JIRA_TRIGGER_TYPES[trigger_type]
        elif provider_type == "slack":
            return SLACK_TRIGGER_TYPES[trigger_type]
        elif provider_type == "builtin":
            return BUILTIN_TRIGGER_TYPES[trigger_type]
        else:
            raise ValueError(f"Unknown provider type: {provider_type}")
