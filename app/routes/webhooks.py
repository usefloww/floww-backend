from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from app.deps.db import SessionDep
from app.models import (
    IncomingWebhook,
    Namespace,
    Trigger,
    Workflow,
)
from app.services import billing_service
from app.services.centrifugo_service import centrifugo_service
from app.services.execution_history_service import create_execution_record
from app.services.providers.provider_registry import PROVIDER_TYPES_MAP
from app.services.workflow_auth_service import WorkflowAuthService
from app.settings import settings
from app.utils.encryption import decrypt_secret

router = APIRouter()
logger = structlog.stdlib.get_logger(__name__)


async def _check_execution_limit_for_workflow(
    session: SessionDep,
    workflow_id: UUID,
) -> None:
    """
    Check if the workflow owner has reached their execution limit.
    Raises HTTPException if limit is reached.
    """
    if not settings.IS_CLOUD:
        return

    result = await session.execute(
        select(Workflow)
        .options(joinedload(Workflow.namespace).joinedload(Namespace.user_owner))
        .where(Workflow.id == workflow_id)
    )
    workflow = result.scalar_one_or_none()

    if workflow and workflow.namespace and workflow.namespace.user_owner:
        user = workflow.namespace.user_owner
        can_execute, message = await billing_service.check_execution_limit(
            session, user
        )

        if not can_execute:
            logger.warning(
                "Execution limit reached for user",
                user_id=user.id,
                workflow_id=workflow_id,
            )
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "title": "Execution limit reached",
                    "description": message,
                    "upgrade_required": True,
                },
            )


async def _execute_trigger(
    session: SessionDep,
    request: Request,
    trigger: Trigger,
    normalized_path: str,
    webhook_data: dict,
    execution_id: UUID,
) -> dict | None:
    """Execute a single trigger via webhook invocation."""
    from app.services.trigger_execution_service import (
        build_webhook_event_data,
        execute_trigger,
    )

    # Publish to dev channel (fire-and-forget for local development)
    # TODO: Update to use V2 format via build_trigger_payload
    trigger_metadata = {
        "provider_type": trigger.provider.type,
        "provider_alias": trigger.provider.alias,
        "trigger_type": trigger.trigger_type,
        "input": trigger.input,
    }

    auth_token = WorkflowAuthService.generate_invocation_token(trigger.workflow)
    await centrifugo_service.publish_dev_webhook_event(
        workflow_id=trigger.workflow_id,
        trigger_metadata=trigger_metadata,
        webhook_data={
            "auth_token": auth_token,
            "path": normalized_path,
            "method": request.method,
            "headers": dict(request.headers),
            "body": webhook_data,
            "query": dict(request.query_params),
        },
    )

    # Build webhook event data
    event_data = build_webhook_event_data(request, normalized_path, webhook_data)

    # Execute via unified service with V2 format
    return await execute_trigger(
        session=session,
        trigger=trigger,
        event_data=event_data,
        execution_id=execution_id,
    )


@router.post("/webhook/{path:path}")
@router.get("/webhook/{path:path}")
@router.put("/webhook/{path:path}")
@router.delete("/webhook/{path:path}")
async def webhook_listener(request: Request, path: str, session: SessionDep):
    # Normalize path to always have leading slash and include /webhook/ prefix
    normalized_path = (
        f"/webhook/{path}" if not path.startswith("/") else f"/webhook{path}"
    )

    logger.info(
        "Webhook lookup",
        path=path,
        normalized_path=normalized_path,
        method=request.method,
    )

    # Query webhook by path and method (with both trigger and provider relationships)
    result = await session.execute(
        select(IncomingWebhook)
        .options(
            selectinload(IncomingWebhook.trigger).options(
                selectinload(Trigger.provider),
                selectinload(Trigger.workflow),
            ),
            selectinload(IncomingWebhook.provider),
        )
        .where(IncomingWebhook.path == normalized_path)
        .where(IncomingWebhook.method == request.method)
    )
    webhook = result.scalar_one_or_none()

    logger.info(
        "Webhook lookup result",
        webhook_found=webhook is not None,
        webhook_id=str(webhook.id) if webhook else None,
        is_provider_owned=webhook.provider_id is not None if webhook else None,
    )

    if not webhook:
        return JSONResponse(content={"error": "Webhook not found"}, status_code=404)

    # Get webhook payload
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            webhook_data = await request.json()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning(
                "Failed to parse JSON body for webhook",
                error=str(exc),
                content_type=content_type,
            )
            webhook_data = {}
    else:
        webhook_data = {}

    # Branch based on webhook ownership
    if webhook.provider_id:
        # Provider-owned webhook: route to all matching triggers for this provider
        return await _handle_provider_webhook(
            session, request, webhook, normalized_path, webhook_data
        )
    elif webhook.trigger_id:
        # Trigger-owned webhook: execute single trigger
        return await _handle_trigger_webhook(
            session, request, webhook, normalized_path, webhook_data
        )
    else:
        # This should never happen due to database constraint
        logger.error(
            "Webhook has neither provider_id nor trigger_id",
            webhook_id=str(webhook.id),
        )
        return JSONResponse(
            content={"error": "Invalid webhook configuration"},
            status_code=500,
        )


async def _handle_provider_webhook(
    session: SessionDep,
    request: Request,
    webhook: IncomingWebhook,
    normalized_path: str,
    webhook_data: dict,
) -> JSONResponse:
    """Handle webhook owned by a provider (routes to multiple triggers)."""
    provider = webhook.provider
    logger.info(
        "Processing provider-owned webhook",
        webhook_id=str(webhook.id),
        provider_id=str(provider.id),
        provider_type=provider.type,
    )

    # Get provider class from registry
    provider_class = PROVIDER_TYPES_MAP.get(provider.type)
    if not provider_class:
        logger.error(
            "Unknown provider type",
            provider_type=provider.type,
        )
        return JSONResponse(
            content={"error": f"Unknown provider type: {provider.type}"},
            status_code=500,
        )

    # Instantiate provider
    provider_instance = provider_class()

    # Decrypt provider config
    provider_config_json = decrypt_secret(provider.encrypted_config)
    provider_state = provider_class.model.model_validate_json(provider_config_json)

    # Call validate_webhook for early response (e.g., Slack URL verification)
    validation_response = await provider_instance.validate_webhook(
        request, provider_state
    )
    if validation_response:
        return validation_response

    # Load all triggers for this provider
    triggers_result = await session.execute(
        select(Trigger)
        .options(
            selectinload(Trigger.provider),
            selectinload(Trigger.workflow),
        )
        .where(Trigger.provider_id == provider.id)
    )
    triggers = list(triggers_result.scalars().all())

    logger.info(
        "Loaded triggers for provider",
        provider_id=str(provider.id),
        trigger_count=len(triggers),
    )

    # Call process_webhook to filter triggers
    matching_triggers = await provider_instance.process_webhook(
        request, provider_state, triggers
    )

    logger.info(
        "Provider filtered triggers",
        provider_id=str(provider.id),
        matching_trigger_count=len(matching_triggers),
    )

    if not matching_triggers:
        return JSONResponse(
            content={"message": "No matching triggers for this event"},
            status_code=200,
        )

    # Execute all matching triggers
    results = []
    for trigger in matching_triggers:
        # Check execution limit before creating record
        await _check_execution_limit_for_workflow(session, trigger.workflow_id)

        # Create execution record for this trigger (minimal)
        execution = await create_execution_record(
            session=session,
            workflow_id=trigger.workflow_id,
            trigger_id=trigger.id,
        )

        result = await _execute_trigger(
            session, request, trigger, normalized_path, webhook_data, execution.id
        )
        if result:
            results.append(result)

    return JSONResponse(
        content={
            "webhook_id": str(webhook.id),
            "provider_id": str(provider.id),
            "triggers_executed": len(results),
            "results": results,
        }
    )


async def _handle_trigger_webhook(
    session: SessionDep,
    request: Request,
    webhook: IncomingWebhook,
    normalized_path: str,
    webhook_data: dict,
) -> JSONResponse:
    """Handle webhook owned by a single trigger (legacy behavior for GitLab, etc.)."""
    trigger = webhook.trigger
    logger.info(
        "Processing trigger-owned webhook",
        webhook_id=str(webhook.id),
        trigger_id=str(trigger.id),
    )

    # Check execution limit before creating record
    await _check_execution_limit_for_workflow(session, trigger.workflow_id)

    # Create execution history record (minimal - just IDs and status)
    execution = await create_execution_record(
        session=session,
        workflow_id=trigger.workflow_id,
        trigger_id=trigger.id,
    )

    # Execute the trigger with execution tracking
    result = await _execute_trigger(
        session, request, trigger, normalized_path, webhook_data, execution.id
    )

    if not result:
        return JSONResponse(
            content={"message": "No active deployment found, only sent to dev mode."},
            status_code=200,
        )

    return JSONResponse(
        content={
            "webhook_id": str(webhook.id),
            **result,
        }
    )
