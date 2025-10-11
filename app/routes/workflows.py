from typing import Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep
from app.models import Namespace, Workflow
from app.services.access_service import (
    get_user_accessible_workflows_query,
    user_has_namespace_access,
)
from app.utils.response_helpers import (
    create_creation_response,
    create_workflows_response,
)

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/workflows", tags=["Workflows"])


@router.get("")
async def list_workflows(current_user: CurrentUser, session: SessionDep):
    """List workflows accessible to the authenticated user."""
    # Use centralized query builder
    query = get_user_accessible_workflows_query(session, current_user.id)
    result = await session.execute(query)
    workflows = result.scalars().all()

    return create_workflows_response(workflows, current_user)


class WorkflowCreate(BaseModel):
    name: str
    namespace_id: UUID
    description: Optional[str] = None


@router.post("")
async def create_workflow(
    workflow_data: WorkflowCreate, current_user: CurrentUser, session: SessionDep
):
    """Create a new workflow."""
    # Verify user has access to the namespace
    namespace_result = await session.execute(
        select(Namespace).where(Namespace.id == workflow_data.namespace_id)
    )
    namespace = namespace_result.scalar_one_or_none()

    if not namespace:
        raise HTTPException(status_code=404, detail="Namespace not found")

    # Check if user has access to this namespace
    if not await user_has_namespace_access(
        session, current_user.id, workflow_data.namespace_id
    ):
        raise HTTPException(status_code=403, detail="Access denied to namespace")

    # Create the workflow
    workflow = Workflow(
        name=workflow_data.name,
        description=workflow_data.description,
        namespace_id=workflow_data.namespace_id,
        created_by_id=current_user.id,
    )

    session.add(workflow)
    await session.commit()
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
