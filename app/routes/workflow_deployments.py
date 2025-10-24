from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep, TransactionSessionDep
from app.models import (
    IncomingWebhook,
    Runtime,
    Workflow,
    WorkflowDeployment,
    WorkflowDeploymentStatus,
)
from app.services.crud_helpers import CrudHelper
from app.settings import settings
from app.utils.query_helpers import UserAccessibleQuery

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/workflow_deployments", tags=["Workflow Deployments"])


class WebhookInfo(BaseModel):
    id: UUID
    url: str
    path: Optional[str] = None
    method: Optional[str] = None


class WorkflowDeploymentRead(BaseModel):
    id: UUID
    workflow_id: UUID
    runtime_id: UUID
    deployed_by_id: Optional[UUID]
    user_code: dict
    status: WorkflowDeploymentStatus
    deployed_at: datetime
    note: Optional[str] = None
    webhooks: Optional[list[WebhookInfo]] = None


class WorkflowDeploymentUserCode(BaseModel):
    files: dict[str, str]
    entrypoint: str


class TriggerMetadata(BaseModel):
    type: str  # "webhook", "cron", "realtime"
    path: Optional[str] = None  # For webhook triggers
    method: Optional[str] = None  # For webhook triggers
    expression: Optional[str] = None  # For cron triggers
    channel: Optional[str] = None  # For realtime triggers


class WorkflowDeploymentCreate(BaseModel):
    workflow_id: UUID
    runtime_id: UUID
    code: WorkflowDeploymentUserCode
    triggers: Optional[list[TriggerMetadata]] = None


class WorkflowDeploymentUpdate(BaseModel):
    status: Optional[WorkflowDeploymentStatus] = None
    user_code: Optional[dict] = None


def helper_factory(user: CurrentUser, session: SessionDep):
    return CrudHelper(
        session=session,
        resource_name="workflow_deployment",
        database_model=WorkflowDeployment,
        read_model=WorkflowDeploymentRead,
        create_model=WorkflowDeploymentCreate,
        update_model=WorkflowDeploymentUpdate,
        query_builder=lambda: UserAccessibleQuery(user.id).deployments(),
    )


@router.get("")
async def list_workflow_deployments(
    current_user: CurrentUser, session: SessionDep, workflow_id: Optional[UUID] = None
):
    """List workflow deployments accessible to the authenticated user."""
    query = UserAccessibleQuery(current_user.id).deployments()
    if workflow_id:
        query = query.where(WorkflowDeployment.workflow_id == workflow_id)

    result = await session.execute(query)
    deployments = result.scalars().all()

    deployment_results = [
        WorkflowDeploymentRead.model_validate(d, from_attributes=True)
        for d in deployments
    ]
    return {"deployments": deployment_results}


@router.post("")
async def create_workflow_deployment(
    data: WorkflowDeploymentCreate,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Create a new workflow deployment."""

    # Verify user has access to the workflow
    workflow_query = (
        UserAccessibleQuery(current_user.id)
        .workflows()
        .where(Workflow.id == data.workflow_id)
    )
    workflow_result = await session.execute(workflow_query)
    workflow = workflow_result.scalar_one_or_none()
    if not workflow:
        raise HTTPException(status_code=400, detail="Workflow not found")

    runtime_query = (
        UserAccessibleQuery(current_user.id)
        .runtimes()
        .where(Runtime.id == data.runtime_id)
    )
    runtime_result = await session.execute(runtime_query)
    runtime = runtime_result.scalar_one_or_none()
    if not runtime:
        raise HTTPException(status_code=400, detail="Runtime not found")

    # Create the workflow deployment manually with additional fields
    workflow_deployment = WorkflowDeployment(
        workflow_id=data.workflow_id,
        runtime_id=data.runtime_id,
        deployed_by_id=current_user.id,
        user_code={
            "files": data.code.files,
            "entrypoint": data.code.entrypoint,
        },
        status=WorkflowDeploymentStatus.ACTIVE,
    )

    session.add(workflow_deployment)
    await session.flush()
    await session.refresh(workflow_deployment)

    # Update workflow with triggers metadata
    if data.triggers:
        workflow.triggers_metadata = [
            {
                "type": trigger.type,
                **({"path": trigger.path} if trigger.path else {}),
                **({"method": trigger.method} if trigger.method else {}),
                **({"expression": trigger.expression} if trigger.expression else {}),
                **({"channel": trigger.channel} if trigger.channel else {}),
            }
            for trigger in data.triggers
        ]
        session.add(workflow)

    # Create IncomingWebhook records for webhook triggers
    webhooks_info = []
    if data.triggers:
        # Get base URL from settings
        base_url = settings.PUBLIC_API_URL

        for trigger in data.triggers:
            if trigger.type == "webhook":
                # Generate path if not provided (for provider webhooks)
                webhook_path = trigger.path if trigger.path else f"/webhook/{uuid4()}"
                webhook_method = trigger.method if trigger.method else "POST"

                # Check if webhook already exists
                existing_webhook_result = await session.execute(
                    select(IncomingWebhook)
                    .where(IncomingWebhook.path == webhook_path)
                    .where(IncomingWebhook.method == webhook_method)
                )
                existing_webhook = existing_webhook_result.scalar_one_or_none()

                if existing_webhook:
                    # Webhook exists - check if it belongs to this workflow
                    if existing_webhook.workflow_id != data.workflow_id:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Webhook path {webhook_path} with method {webhook_method} is already in use by another workflow",
                        )
                    # Reuse existing webhook
                    incoming_webhook = existing_webhook
                    logger.info(
                        "Reusing existing webhook",
                        webhook_id=str(incoming_webhook.id),
                        workflow_id=str(data.workflow_id),
                        path=webhook_path,
                        method=webhook_method,
                    )
                else:
                    # Create new webhook
                    incoming_webhook = IncomingWebhook(
                        workflow_id=data.workflow_id,
                        path=webhook_path,
                        method=webhook_method,
                    )
                    session.add(incoming_webhook)
                    await session.flush()
                    await session.refresh(incoming_webhook)
                    logger.info(
                        "Created new webhook",
                        webhook_id=str(incoming_webhook.id),
                        workflow_id=str(data.workflow_id),
                        path=webhook_path,
                        method=webhook_method,
                    )

                # Build webhook URL
                webhook_url = f"{base_url}{webhook_path}"

                webhooks_info.append(
                    WebhookInfo(
                        id=incoming_webhook.id,
                        url=webhook_url,
                        path=webhook_path,
                        method=webhook_method,
                    )
                )

    logger.info(
        "Created new workflow deployment",
        deployment_id=str(workflow_deployment.id),
        workflow_id=str(workflow_deployment.workflow_id),
        webhooks_count=len(webhooks_info),
    )

    # Build response
    deployment_dict = {
        "id": workflow_deployment.id,
        "workflow_id": workflow_deployment.workflow_id,
        "runtime_id": workflow_deployment.runtime_id,
        "deployed_by_id": workflow_deployment.deployed_by_id,
        "user_code": workflow_deployment.user_code,
        "status": workflow_deployment.status,
        "deployed_at": workflow_deployment.deployed_at,
        "note": workflow_deployment.note,
        "webhooks": webhooks_info,
    }

    return WorkflowDeploymentRead.model_validate(deployment_dict)


@router.get("/{deployment_id}")
async def get_workflow_deployment(
    deployment_id: UUID, current_user: CurrentUser, session: SessionDep
):
    """Get a specific workflow deployment."""
    helper = helper_factory(current_user, session)
    result = await helper.get_response(deployment_id)
    return result


@router.patch("/{deployment_id}")
async def update_workflow_deployment(
    deployment_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
    data: WorkflowDeploymentUpdate,
):
    """Update a specific workflow deployment."""
    helper = helper_factory(current_user, session)
    result = await helper.update_response(deployment_id, data)
    return result


@router.delete("/{deployment_id}")
async def delete_workflow_deployment(
    deployment_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Delete a workflow deployment."""
    helper = helper_factory(current_user, session)
    response = await helper.delete_response(deployment_id)
    return response
