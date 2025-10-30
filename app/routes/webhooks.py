import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.deps.db import SessionDep
from app.models import (
    IncomingWebhook,
    Trigger,
    WorkflowDeployment,
    WorkflowDeploymentStatus,
)
from app.services.centrifugo_service import centrifugo_service
from app.utils.aws_lambda import invoke_lambda_async

router = APIRouter()
logger = structlog.stdlib.get_logger(__name__)


@router.post("/webhook/{path:path}")
@router.get("/webhook/{path:path}")
@router.put("/webhook/{path:path}")
@router.delete("/webhook/{path:path}")
async def webhook_listener(request: Request, path: str, session: SessionDep):
    # Normalize path to always have leading slash
    normalized_path = f"/{path}" if not path.startswith("/") else path

    # Query webhook by path and method (with trigger relationship for metadata)
    result = await session.execute(
        select(IncomingWebhook)
        .options(
            selectinload(IncomingWebhook.trigger).selectinload(Trigger.provider),
            selectinload(IncomingWebhook.trigger).selectinload(Trigger.workflow),
        )
        .where(IncomingWebhook.path == normalized_path)
        .where(IncomingWebhook.method == request.method)
    )
    webhook = result.scalar_one_or_none()

    if not webhook:
        return JSONResponse(content={"error": "Webhook not found"}, status_code=404)

    # Get webhook payload
    webhook_data = (
        await request.json()
        if request.headers.get("content-type") == "application/json"
        else {}
    )

    # Handle Slack URL verification challenge
    # When configuring Event Subscriptions in Slack, Slack sends a challenge
    # that must be echoed back to verify the webhook URL
    if (
        webhook.trigger
        and webhook.trigger.provider.type == "slack"
        and webhook_data.get("type") == "url_verification"
    ):
        challenge = webhook_data.get("challenge")
        if challenge:
            logger.info(
                "Responding to Slack URL verification challenge",
                webhook_id=str(webhook.id),
            )
            return JSONResponse(content={"challenge": challenge})

    # Filter Slack message events based on trigger configuration
    if (
        webhook.trigger
        and webhook.trigger.provider.type == "slack"
        and webhook.trigger.trigger_type == "onMessage"
    ):
        # Only process message events
        if webhook_data.get("type") != "event_callback":
            logger.debug(
                "Ignoring non-event_callback Slack webhook",
                webhook_id=str(webhook.id),
                event_type=webhook_data.get("type"),
            )
            return JSONResponse(content={"status": "ignored"})

        event = webhook_data.get("event", {})
        if event.get("type") != "message":
            logger.debug(
                "Ignoring non-message Slack event",
                webhook_id=str(webhook.id),
                event_type=event.get("type"),
            )
            return JSONResponse(content={"status": "ignored"})

        # Filter bot messages to avoid loops
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            logger.debug(
                "Ignoring bot message to prevent loops",
                webhook_id=str(webhook.id),
            )
            return JSONResponse(content={"status": "ignored"})

        # Apply channel filter if specified
        trigger_input = webhook.trigger.input or {}
        if trigger_input.get("channel_id") and event.get("channel") != trigger_input.get("channel_id"):
            logger.debug(
                "Ignoring message from different channel",
                webhook_id=str(webhook.id),
                expected_channel=trigger_input.get("channel_id"),
                actual_channel=event.get("channel"),
            )
            return JSONResponse(content={"status": "ignored"})

        # Apply user filter if specified
        if trigger_input.get("user_id") and event.get("user") != trigger_input.get("user_id"):
            logger.debug(
                "Ignoring message from different user",
                webhook_id=str(webhook.id),
                expected_user=trigger_input.get("user_id"),
                actual_user=event.get("user"),
            )
            return JSONResponse(content={"status": "ignored"})

    # Publish to dev channel (fire-and-forget for local development)
    # If no dev session is active, Centrifugo drops the message automatically
    if webhook.trigger:
        trigger_metadata = {
            "provider_type": webhook.trigger.provider.type,
            "provider_alias": webhook.trigger.provider.alias,
            "trigger_type": webhook.trigger.trigger_type,
            "input": webhook.trigger.input,
        }

        await centrifugo_service.publish_dev_webhook_event(
            workflow_id=webhook.trigger.workflow_id,
            trigger_metadata=trigger_metadata,
            webhook_data={
                "path": normalized_path,
                "method": request.method,
                "headers": dict(request.headers),
                "body": webhook_data,
                "query": dict(request.query_params),
            },
        )

    # Find active deployment for this workflow
    deployment_result = await session.execute(
        select(WorkflowDeployment)
        .where(WorkflowDeployment.workflow_id == webhook.trigger.workflow_id)
        .where(WorkflowDeployment.status == WorkflowDeploymentStatus.ACTIVE)
        .order_by(WorkflowDeployment.deployed_at.desc())
        .limit(1)
    )
    deployment = deployment_result.scalar_one_or_none()

    if not deployment:
        logger.warning(
            "No active deployment found for webhook",
            path=normalized_path,
            method=request.method,
            workflow_id=str(webhook.trigger.workflow_id),
        )
        return JSONResponse(
            content={"error": "No active deployment found"},
            status_code=503,
        )

    # Build Lambda event payload
    event_payload = {
        "userCode": deployment.user_code.get("files", {}),
        "triggerType": "webhook",
        "path": normalized_path,
        "method": request.method,
        "headers": dict(request.headers),
        "body": webhook_data,
        "query": dict(request.query_params),
    }

    # Invoke Lambda asynchronously
    invoke_result = invoke_lambda_async(
        runtime_id=str(deployment.runtime_id),
        event_payload=event_payload,
    )

    if not invoke_result["success"]:
        logger.error(
            "Failed to invoke Lambda",
            webhook_id=str(webhook.id),
            runtime_id=str(deployment.runtime_id),
            error=invoke_result.get("error"),
        )
        return JSONResponse(
            content={"error": "Failed to invoke workflow"},
            status_code=500,
        )

    logger.info(
        "Webhook invoked Lambda",
        webhook_id=str(webhook.id),
        workflow_id=str(webhook.trigger.workflow_id),
        runtime_id=str(deployment.runtime_id),
    )

    return JSONResponse(
        content={
            "webhook_id": str(webhook.id),
            "workflow_id": str(webhook.trigger.workflow_id),
            "status": "invoked",
        }
    )
