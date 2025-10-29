from typing import Any, Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.deps.auth import CurrentUser
from app.deps.db import TransactionSessionDep
from app.models import Workflow
from app.services.trigger_service import TriggerService
from app.utils.query_helpers import UserAccessibleQuery

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/dev", tags=["Dev Mode"])


class WebhookInfo(BaseModel):
    id: UUID
    url: str
    path: Optional[str] = None
    method: Optional[str] = None


class DevTriggerSyncRequest(BaseModel):
    workflow_id: UUID
    triggers: list[dict[str, Any]]


class DevTriggerSyncResponse(BaseModel):
    webhooks: list[WebhookInfo]


@router.post("/sync-triggers")
async def sync_dev_triggers(
    data: DevTriggerSyncRequest,
    current_user: CurrentUser,
    session: TransactionSessionDep,
) -> DevTriggerSyncResponse:
    """
    Sync triggers for dev mode - creates real webhooks but routes events to dev session.

    This is similar to deployment but:
    1. Doesn't create a deployment record
    2. Marks triggers as "dev mode" so webhook events are routed via websocket
    """

    # Verify user has access to the workflow
    workflow_query = (
        UserAccessibleQuery(current_user.id)
        .workflows()
        .where(Workflow.id == data.workflow_id)
    )
    workflow_result = await session.execute(workflow_query)
    workflow = workflow_result.scalar_one_or_none()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Sync triggers using TriggerService
    trigger_service = TriggerService(session)
    webhooks_info = await trigger_service.sync_triggers(
        workflow_id=data.workflow_id,
        namespace_id=workflow.namespace_id,
        new_triggers_metadata=data.triggers,
    )

    logger.info(
        "Synced triggers for dev mode",
        workflow_id=str(data.workflow_id),
        webhooks_count=len(webhooks_info),
        user_id=str(current_user.id),
    )

    return DevTriggerSyncResponse(webhooks=[WebhookInfo(**wh) for wh in webhooks_info])
