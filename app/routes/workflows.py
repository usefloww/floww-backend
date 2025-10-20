from datetime import datetime
from typing import Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep, TransactionSessionDep
from app.models import Namespace, Workflow
from app.services.crud_helpers import CrudHelper
from app.utils.query_helpers import UserAccessibleQuery

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/workflows", tags=["Workflows"])


class WorkflowRead(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    namespace_id: UUID
    created_by_id: UUID
    created_at: datetime
    updated_at: datetime


class WorkflowCreate(BaseModel):
    name: str
    namespace_id: UUID
    description: Optional[str] = None


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    namespace_id: Optional[UUID] = None


def helper_factory(user: CurrentUser, session: SessionDep):
    return CrudHelper(
        session=session,
        resource_name="workflow",
        database_model=Workflow,
        read_model=WorkflowRead,
        create_model=WorkflowCreate,
        update_model=WorkflowUpdate,
        query_builder=lambda: UserAccessibleQuery(user.id).workflows(),
    )


@router.get("")
async def list_workflows(current_user: CurrentUser, session: SessionDep):
    """List workflows accessible to the authenticated user."""
    helper = helper_factory(current_user, session)
    result = await helper.list_response()
    return {"workflows": result.results}


@router.post("")
async def create_workflow(
    data: WorkflowCreate,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Create a new workflow."""
    # Verify user has access to the namespace
    namespace_query = (
        UserAccessibleQuery(current_user.id)
        .namespaces()
        .where(Namespace.id == data.namespace_id)
    )
    namespace_result = await session.execute(namespace_query)
    namespace = namespace_result.scalar_one_or_none()

    if not namespace:
        raise HTTPException(status_code=400, detail="Namespace not found")

    # Create a workflow manually with created_by_id
    workflow = Workflow(
        name=data.name,
        description=data.description,
        namespace_id=data.namespace_id,
        created_by_id=current_user.id,
    )

    session.add(workflow)
    await session.flush()

    logger.info(
        "Created new workflow", workflow_id=str(workflow.id), name=workflow.name
    )

    return WorkflowRead.model_validate(workflow, from_attributes=True)


@router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: UUID, current_user: CurrentUser, session: SessionDep
):
    """Get a specific workflow."""
    helper = helper_factory(current_user, session)
    result = await helper.get_response(workflow_id)
    return result


@router.patch("/{workflow_id}")
async def update_workflow(
    workflow_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
    data: WorkflowUpdate,
):
    """Update a specific workflow."""
    helper = helper_factory(current_user, session)
    result = await helper.update_response(workflow_id, data)
    return result


@router.delete("/{workflow_id}")
async def delete_workflow(
    workflow_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Delete a workflow."""
    helper = helper_factory(current_user, session)
    response = await helper.delete_response(workflow_id)
    return response
