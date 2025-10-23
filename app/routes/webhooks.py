from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.deps.db import SessionDep
from app.models import IncomingWebhook, WorkflowDeployment, WorkflowDeploymentStatus
from app.utils.aws_lambda import invoke_lambda_async
import structlog

router = APIRouter()
logger = structlog.stdlib.get_logger(__name__)


@router.post("/webhooks/{webhook_id}")
async def webhook_listener(request: Request, webhook_id: str, session: SessionDep):
    # Query webhook with its workflow
    result = await session.execute(
        select(IncomingWebhook)
        .options(selectinload(IncomingWebhook.workflow))
        .where(IncomingWebhook.id == webhook_id)
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

    # Find active deployment for this workflow
    deployment_result = await session.execute(
        select(WorkflowDeployment)
        .where(WorkflowDeployment.workflow_id == webhook.workflow_id)
        .where(WorkflowDeployment.status == WorkflowDeploymentStatus.ACTIVE)
        .order_by(WorkflowDeployment.deployed_at.desc())
        .limit(1)
    )
    deployment = deployment_result.scalar_one_or_none()

    if not deployment:
        logger.warning(
            "No active deployment found for webhook",
            webhook_id=webhook_id,
            workflow_id=str(webhook.workflow_id),
        )
        return JSONResponse(
            content={"error": "No active deployment found"},
            status_code=503,
        )

    # Build Lambda event payload
    event_payload = {
        "userCode": deployment.user_code.get("files", {}),
        "triggerType": "webhook",
        "path": request.url.path.replace(f"/webhooks/{webhook_id}", "")
        or "/webhook",
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
            webhook_id=webhook_id,
            runtime_id=str(deployment.runtime_id),
            error=invoke_result.get("error"),
        )
        return JSONResponse(
            content={"error": "Failed to invoke workflow"},
            status_code=500,
        )

    logger.info(
        "Webhook invoked Lambda",
        webhook_id=webhook_id,
        workflow_id=str(webhook.workflow_id),
        runtime_id=str(deployment.runtime_id),
    )

    return JSONResponse(
        content={
            "webhook_id": webhook_id,
            "workflow_id": str(webhook.workflow_id),
            "status": "invoked",
        }
    )
