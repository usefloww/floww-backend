from typing import Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep
from app.models import Runtime, Workflow, WorkflowDeployment, WorkflowDeploymentStatus
from app.utils.query_helpers import UserAccessibleQuery
from app.utils.response_helpers import (
    create_deployments_response,
    serialize_workflow_deployment_detailed,
)

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/workflow_deployments", tags=["Workflow Deployments"])


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

    return create_deployments_response(list(deployments), current_user)


class WorkflowDeploymentUserCode(BaseModel):
    files: dict[str, str]
    entrypoint: str


class WorkflowDeploymentCreate(BaseModel):
    workflow_id: UUID
    runtime_id: UUID
    code: WorkflowDeploymentUserCode


@router.post("")
async def create_workflow_deployment(
    workflow_deployment_data: WorkflowDeploymentCreate,
    current_user: CurrentUser,
    session: SessionDep,
):
    """Create a new workflow deployment."""

    # Verify user has access to the workflow
    workflow_query = (
        UserAccessibleQuery(current_user.id)
        .workflows()
        .where(Workflow.id == workflow_deployment_data.workflow_id)
    )
    workflow_result = await session.execute(workflow_query)
    workflow = workflow_result.scalar_one_or_none()
    if not workflow:
        raise HTTPException(status_code=400, detail="Workflow not found")

    runtime_query = (
        UserAccessibleQuery(current_user.id)
        .runtimes()
        .where(Runtime.id == workflow_deployment_data.runtime_id)
    )
    runtime_result = await session.execute(runtime_query)
    runtime = runtime_result.scalar_one_or_none()
    if not runtime:
        raise HTTPException(status_code=400, detail="Runtime not found")

    # Create the workflow deployment
    workflow_deployment = WorkflowDeployment(
        workflow_id=workflow_deployment_data.workflow_id,
        runtime_id=workflow_deployment_data.runtime_id,
        deployed_by_id=current_user.id,
        user_code={
            "files": workflow_deployment_data.code.files,
            "entrypoint": workflow_deployment_data.code.entrypoint,
        },
        status=WorkflowDeploymentStatus.ACTIVE,
    )

    session.add(workflow_deployment)
    await session.commit()
    await session.refresh(workflow_deployment)

    # Manually set the relationships since we already have the objects
    workflow_deployment.workflow = workflow
    workflow_deployment.runtime = runtime

    structlog.get_logger().info(
        "Created new workflow deployment",
        deployment_id=str(workflow_deployment.id),
        workflow_id=str(workflow_deployment.workflow_id),
    )

    return serialize_workflow_deployment_detailed(workflow_deployment)
