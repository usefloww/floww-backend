from typing import Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import selectinload

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep, TransactionSessionDep
from app.models import Namespace, Workflow
from app.utils.query_helpers import UserAccessibleQuery
from app.utils.response_helpers import (
    create_creation_response,
    create_workflows_response,
)

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/workflows", tags=["Workflows"])


@router.get("")
async def list_workflows(current_user: CurrentUser, session: SessionDep):
    query = UserAccessibleQuery(current_user.id).workflows()

    query = query.options(selectinload(Workflow.namespace))
    result = await session.execute(query)
    workflows = result.scalars().all()

    return create_workflows_response(list(workflows), current_user)


class WorkflowCreate(BaseModel):
    name: str
    namespace_id: UUID
    description: Optional[str] = None


@router.post("")
async def create_workflow(
    workflow_data: WorkflowCreate,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Create a new workflow."""

    namespace_query = (
        UserAccessibleQuery(current_user.id)
        .namespaces()
        .where(Namespace.id == workflow_data.namespace_id)
    )
    namespace_result = await session.execute(namespace_query)
    namespace = namespace_result.scalar_one_or_none()

    if not namespace:
        raise HTTPException(status_code=400, detail="Namespace not found")

    # Create the workflow
    workflow = Workflow(
        name=workflow_data.name,
        description=workflow_data.description,
        namespace_id=workflow_data.namespace_id,
        created_by_id=current_user.id,
    )

    session.add(workflow)
    await session.flush()
    await session.refresh(workflow)

    logger.info(
        "Created new workflow", workflow_id=str(workflow.id), name=workflow.name
    )

    return create_creation_response(
        workflow,
        name=workflow.name,
        description=workflow.description,
        namespace_id=str(workflow.namespace_id),
        created_by_id=str(workflow.created_by_id),
    )


@router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: UUID, current_user: CurrentUser, session: SessionDep
):
    """Get a specific workflow."""

    query = (
        UserAccessibleQuery(current_user.id)
        .workflows()
        .where(Workflow.id == workflow_id)
        .options(selectinload(Workflow.namespace))
    )
    result = await session.execute(query)
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    return create_creation_response(
        workflow,
        name=workflow.name,
        description=workflow.description,
        namespace_id=str(workflow.namespace_id),
        created_by_id=str(workflow.created_by_id),
    )
