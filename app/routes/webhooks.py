from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.deps.db import SessionDep
from app.models import IncomingWebhook

router = APIRouter()


@router.post("/webhooks/{webhook_id}")
async def webhook_listener(request: Request, webhook_id: str, session: SessionDep):
    # Query webhook with its listeners
    result = await session.execute(
        select(IncomingWebhook)
        .options(selectinload(IncomingWebhook.listeners))
        .where(IncomingWebhook.id == webhook_id)
    )
    webhook = result.scalar_one_or_none()

    if not webhook:
        return JSONResponse(content={"error": "Webhook not found"}, status_code=404)

    # Print which listeners will be called
    print(f"Webhook {webhook_id} has {len(webhook.listeners)} listeners:")
    for listener in webhook.listeners:
        print(
            f"  - Listener {listener.id}: {listener.listener_type.value} for workflow {listener.workflow_id}"
        )

    return JSONResponse(
        content={
            "webhook_id": webhook_id,
            "listeners_count": len(webhook.listeners),
            "listeners": [
                {
                    "id": str(listener.id),
                    "type": listener.listener_type.value,
                    "workflow_id": str(listener.workflow_id),
                }
                for listener in webhook.listeners
            ],
        }
    )
