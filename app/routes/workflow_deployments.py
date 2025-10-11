from typing import Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep
from app.models import WorkflowDeployment, WorkflowDeploymentStatus
from app.services.access_service import (
    get_user_accessible_deployments_query,
    user_has_workflow_access,
)
from app.utils.query_helpers import (
    apply_workflow_filter,
    get_runtime_or_404,
    get_workflow_or_404,
)
from app.utils.response_helpers import (
    create_deployments_response,
    serialize_workflow_deployment_detailed,
)

router = APIRouter(prefix="/workflow_deployments", tags=["Workflow Deployments"])


@router.get("")
async def list_workflow_deployments(
    current_user: CurrentUser, session: SessionDep, workflow_id: Optional[UUID] = None
):
    """List workflow deployments accessible to the authenticated user."""

    # Use centralized query builder
    query = get_user_accessible_deployments_query(session, current_user.id)

    # Add workflow filter if specified
    query = apply_workflow_filter(query, workflow_id)

    # Execute query
    result = await session.execute(query)
    deployments = result.scalars().all()

    return create_deployments_response(deployments, current_user)


logger = structlog.stdlib.get_logger(__name__)


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
    if not await user_has_workflow_access(
        session, current_user.id, workflow_deployment_data.workflow_id
    ):
        raise HTTPException(status_code=403, detail="Access denied to workflow")

    # Verify workflow and runtime exist (using centralized helpers)
    await get_workflow_or_404(session, workflow_deployment_data.workflow_id)
    await get_runtime_or_404(session, workflow_deployment_data.runtime_id)

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

    structlog.get_logger().info(
        "Created new workflow deployment",
        deployment_id=str(workflow_deployment.id),
        workflow_id=str(workflow_deployment.workflow_id),
    )

    return serialize_workflow_deployment_detailed(workflow_deployment)
