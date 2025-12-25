import json
import re
from typing import Optional
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import select, update

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep, TransactionSessionDep
from app.models import IncomingWebhook, Provider
from app.services.crud_helpers import CrudHelper
from app.settings import settings
from app.utils.encryption import decrypt_secret, encrypt_secret
from app.utils.query_helpers import UserAccessibleQuery

logger = structlog.get_logger()

router = APIRouter(prefix="/providers", tags=["Providers"])


class ProviderRead(BaseModel):
    id: UUID
    namespace_id: UUID
    type: str
    alias: str
    config: dict  # This will be the decrypted config

    @model_validator(mode="before")
    @classmethod
    def decrypt_config(cls, data):
        if hasattr(data, "encrypted_config"):
            # If it's a database model instance
            decrypted_config = decrypt_secret(data.encrypted_config)
            # Convert to dict for pydantic processing
            model_data = {
                "id": data.id,
                "namespace_id": data.namespace_id,
                "type": data.type,
                "alias": data.alias,
                "config": json.loads(decrypted_config),
            }
            return model_data
        return data


class ProviderCreate(BaseModel):
    namespace_id: UUID
    type: str
    alias: str
    config: dict  # This will be encrypted before saving

    @field_validator("config")
    @classmethod
    def validate_config(cls, v):
        if not isinstance(v, dict):
            raise ValueError("config must be a dictionary")
        return v


class ProviderUpdate(BaseModel):
    type: Optional[str] = None
    alias: Optional[str] = None
    config: Optional[dict] = None  # This will be encrypted before saving

    @field_validator("config")
    @classmethod
    def validate_config(cls, v):
        if v is not None and not isinstance(v, dict):
            raise ValueError("config must be a dictionary")
        return v


def helper_factory(user: CurrentUser, session: SessionDep):
    return CrudHelper(
        session=session,
        resource_name="provider",
        database_model=Provider,
        read_model=ProviderRead,
        create_model=ProviderCreate,
        update_model=ProviderUpdate,
        query_builder=lambda: UserAccessibleQuery(user.id).providers(),
    )


@router.get("")
async def list_providers(
    current_user: CurrentUser,
    session: SessionDep,
    namespace_id: Optional[UUID] = None,
):
    """List providers accessible to the authenticated user."""
    helper = helper_factory(current_user, session)
    result = await helper.list_response(namespace_id=namespace_id)
    return result


@router.post("")
async def create_provider(
    data: ProviderCreate,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Create a new provider."""
    # Create a mutable copy of the config
    config = data.config.copy()

    # For Slack providers, handle webhook URL
    if data.type == "slack":
        # Check if webhook_url was pre-generated and provided
        if "webhook_url" in config and config["webhook_url"]:
            webhook_url = config["webhook_url"]

            # Validate URL format: must start with PUBLIC_API_URL
            if not webhook_url.startswith(settings.PUBLIC_API_URL):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid webhook URL: must use the provided domain",
                )

            # Extract path from URL
            webhook_path = webhook_url.replace(settings.PUBLIC_API_URL, "")

            # Validate path format: /webhook/{uuid}
            uuid_pattern = r"^/webhook/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
            if not re.match(uuid_pattern, webhook_path, re.IGNORECASE):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid webhook path format: must be /webhook/{uuid}",
                )

            # Check uniqueness (ensure path not already used)
            result = await session.execute(
                select(IncomingWebhook).where(IncomingWebhook.path == webhook_path)
            )
            if result.scalar_one_or_none():
                raise HTTPException(
                    status_code=400,
                    detail="Webhook URL already in use. Please refresh the provider configuration to generate a new webhook URL.",
                )
        else:
            # Backward compatibility: generate webhook path if not provided
            webhook_path = f"/webhook/{uuid4()}"
            webhook_url = f"{settings.PUBLIC_API_URL}{webhook_path}"

        # Create the provider first
        provider = Provider(
            namespace_id=data.namespace_id,
            type=data.type,
            alias=data.alias,
            encrypted_config=encrypt_secret(json.dumps(config)),
        )
        session.add(provider)
        await session.flush()
        await session.refresh(provider)

        # Create provider-owned webhook
        webhook = IncomingWebhook(
            provider_id=provider.id,
            trigger_id=None,
            path=webhook_path,
            method="POST",
        )
        session.add(webhook)
        await session.flush()
        await session.refresh(webhook)

        # Ensure webhook_url is in config
        config["webhook_url"] = webhook_url
        provider.encrypted_config = encrypt_secret(json.dumps(config))
        await session.flush()
        await session.refresh(provider)

        logger.info(
            "Created Slack provider with webhook",
            provider_id=str(provider.id),
            alias=provider.alias,
            webhook_url=webhook_url,
        )
    else:
        # For non-Slack providers, create normally without webhook
        encrypted_config = encrypt_secret(json.dumps(config))
        provider = Provider(
            namespace_id=data.namespace_id,
            type=data.type,
            alias=data.alias,
            encrypted_config=encrypted_config,
        )
        session.add(provider)
        await session.flush()
        await session.refresh(provider)

        logger.info(
            "Created new provider", provider_id=str(provider.id), alias=provider.alias
        )

    return ProviderRead.model_validate(provider, from_attributes=True)


@router.get("/{provider_id}")
async def get_provider(
    provider_id: UUID, current_user: CurrentUser, session: SessionDep
):
    """Get a specific provider."""
    helper = helper_factory(current_user, session)
    result = await helper.get_response(provider_id)
    return result


@router.patch("/{provider_id}")
async def update_provider(
    provider_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
    data: ProviderUpdate,
):
    """Update a specific provider."""
    # Build the update data
    update_data = {}
    if data.type is not None:
        update_data["type"] = data.type
    if data.alias is not None:
        update_data["alias"] = data.alias
    if data.config is not None:
        update_data["encrypted_config"] = encrypt_secret(json.dumps(data.config))

    if not update_data:
        # No fields to update, just return the current provider
        helper = helper_factory(current_user, session)
        return await helper.get_response(provider_id)

    # Get the provider query for access control
    query = UserAccessibleQuery(current_user.id).providers()

    # Update the provider
    result = await session.execute(
        update(Provider)
        .where(Provider.id == provider_id)
        .where(Provider.id.in_(query.with_only_columns(Provider.id)))
        .values(**update_data)
        .returning(Provider)
    )

    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    await session.refresh(provider)
    return ProviderRead.model_validate(provider, from_attributes=True)


@router.delete("/{provider_id}")
async def delete_provider(
    provider_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Delete a provider."""
    helper = helper_factory(current_user, session)
    response = await helper.delete_response(provider_id)
    return response
