"""Trigger execution service for centralized V2 payload creation and execution."""

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.factories import registry_client_factory, runtime_factory
from app.models import Provider, Trigger, WorkflowDeployment, WorkflowDeploymentStatus
from app.packages.runtimes.runtime_types import RuntimeConfig
from app.services.execution_history_service import (
    update_execution_no_deployment,
    update_execution_started,
)
from app.services.workflow_auth_service import WorkflowAuthService
from app.settings import settings
from app.utils.encryption import decrypt_secret

logger = structlog.stdlib.get_logger(__name__)
registry_client = registry_client_factory()


def build_trigger_payload(
    trigger: Trigger,
    event_data: dict[str, Any],
    provider_configs: dict[str, dict[str, Any]],
    auth_token: str,
    execution_id: str,
) -> dict[str, Any]:
    """
    Build V2 trigger payload dict.
    Used by both production runtimes and dev mode.

    Returns standardized V2 format:
    {
        "trigger": {
            "provider": {"type": "gitlab", "alias": "default"},
            "triggerType": "onMergeRequest",
            "input": {"projectId": "123"}
        },
        "data": {...},  # Event-specific data
        "authToken": "...",
        "executionId": "...",
        "providerConfigs": {...}
    }
    """
    return {
        "trigger": {
            "provider": {
                "type": trigger.provider.type,
                "alias": trigger.provider.alias,
            },
            "triggerType": trigger.trigger_type,
            "input": trigger.input,
        },
        "data": event_data,
        "backendUrl": settings.PUBLIC_API_URL,
        "authToken": auth_token,
        "executionId": execution_id,
        "providerConfigs": provider_configs,
    }


def build_webhook_event_data(
    request: Request,
    normalized_path: str,
    webhook_data: dict,
) -> dict[str, Any]:
    """
    Build event data dict for webhook triggers.
    Consolidates HTTP request information into event payload.
    """
    return {
        "method": request.method,
        "path": normalized_path,
        "headers": dict(request.headers),
        "body": webhook_data,
        "query": dict(request.query_params),
        "params": dict(request.query_params),
    }


def build_cron_event_data(
    cron_expression: str,
    scheduled_time: datetime,
) -> dict[str, Any]:
    """
    Build event data dict for cron triggers.
    Includes schedule metadata for handler access.
    """
    return {
        "scheduledTime": scheduled_time.isoformat(),
        "expression": cron_expression,
    }


async def _get_active_deployment(
    session: AsyncSession,
    workflow_id: UUID,
) -> WorkflowDeployment | None:
    """Find active deployment for workflow."""
    result = await session.execute(
        select(WorkflowDeployment)
        .options(selectinload(WorkflowDeployment.runtime))
        .where(WorkflowDeployment.workflow_id == workflow_id)
        .where(WorkflowDeployment.status == WorkflowDeploymentStatus.ACTIVE)
        .order_by(WorkflowDeployment.deployed_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _get_provider_configs(
    session: AsyncSession,
    namespace_id: UUID,
) -> dict[str, dict[str, Any]]:
    """Fetch and decrypt all provider configs for namespace."""
    result = await session.execute(
        select(Provider).where(Provider.namespace_id == namespace_id)
    )
    providers = result.scalars().all()

    provider_configs = {}
    for provider in providers:
        config_json = decrypt_secret(provider.encrypted_config)
        config = json.loads(config_json)
        key = f"{provider.type}:{provider.alias}"
        provider_configs[key] = config

    return provider_configs


async def execute_trigger(
    session: AsyncSession,
    trigger: Trigger,
    event_data: dict[str, Any],
    execution_id: UUID,
) -> dict | None:
    """
    Execute a trigger with V2 payload format.

    Flow:
    1. Generate auth token
    2. Find active deployment
    3. Get provider configs
    4. Build V2 payload using build_trigger_payload()
    5. Invoke runtime with payload dict
    6. Return result

    Args:
        session: Database session
        trigger: The trigger to execute (with loaded provider & workflow)
        event_data: Event-specific data (webhook, cron, etc.)
        execution_id: Pre-created execution history ID

    Returns:
        Execution result dict or None if no deployment found
    """
    # Generate short-lived JWT token for this invocation
    auth_token = WorkflowAuthService.generate_invocation_token(trigger.workflow)

    # Find active deployment
    deployment = await _get_active_deployment(session, trigger.workflow_id)
    if not deployment:
        await update_execution_no_deployment(session, execution_id)
        await session.commit()
        logger.warning(
            "No active deployment found for trigger",
            trigger_id=str(trigger.id),
            workflow_id=str(trigger.workflow_id),
            execution_id=str(execution_id),
        )
        return None

    # Update execution history: started
    await update_execution_started(session, execution_id, deployment.id)

    # Commit to ensure execution record exists before runtime reports completion
    await session.commit()

    # Get image_hash from runtime config and compute image_digest
    if not deployment.runtime.config or "image_hash" not in deployment.runtime.config:
        logger.error(
            "Runtime config missing image_hash",
            runtime_id=str(deployment.runtime.id),
            deployment_id=str(deployment.id),
            execution_id=str(execution_id),
        )
        return None

    image_hash = deployment.runtime.config["image_hash"]
    image_digest = await registry_client.get_image_digest(image_hash)

    if not image_digest:
        logger.error(
            "Image not found in registry",
            runtime_id=str(deployment.runtime.id),
            image_hash=image_hash,
            execution_id=str(execution_id),
        )
        return None

    # Fetch provider configs
    provider_configs = await _get_provider_configs(
        session, trigger.workflow.namespace_id
    )

    # Build V2 payload
    payload_dict = build_trigger_payload(
        trigger=trigger,
        event_data=event_data,
        provider_configs=provider_configs,
        auth_token=auth_token,
        execution_id=str(execution_id),
    )

    # Invoke runtime
    runtime_impl = runtime_factory()
    await runtime_impl.invoke_trigger(
        trigger_id=str(trigger.id),
        runtime_config=RuntimeConfig(
            runtime_id=str(deployment.runtime.id),
            image_digest=image_digest,
        ),
        user_code=deployment.user_code,
        payload=payload_dict,
    )

    return {
        "trigger_id": str(trigger.id),
        "workflow_id": str(trigger.workflow_id),
        "execution_id": str(execution_id),
        "status": "invoked",
    }
