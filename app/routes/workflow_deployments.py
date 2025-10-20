from datetime import datetime
from typing import Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep, TransactionSessionDep
from app.models import Runtime, Workflow, WorkflowDeployment, WorkflowDeploymentStatus
from app.services.crud_helpers import CrudHelper
from app.utils.query_helpers import UserAccessibleQuery

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/workflow_deployments", tags=["Workflow Deployments"])


class WorkflowDeploymentRead(BaseModel):
    id: UUID
    workflow_id: UUID
    runtime_id: UUID
    deployed_by_id: Optional[UUID]
    user_code: dict
    status: WorkflowDeploymentStatus
    deployed_at: datetime
    note: Optional[str] = None


class WorkflowDeploymentUserCode(BaseModel):
    files: dict[str, str]
    entrypoint: str


class WorkflowDeploymentCreate(BaseModel):
    workflow_id: UUID
    runtime_id: UUID
    code: WorkflowDeploymentUserCode


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

    logger.info(
        "Created new workflow deployment",
        deployment_id=str(workflow_deployment.id),
        workflow_id=str(workflow_deployment.workflow_id),
    )

    return WorkflowDeploymentRead.model_validate(
        workflow_deployment, from_attributes=True
    )


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
