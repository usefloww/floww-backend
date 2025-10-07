from typing import Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep
from app.models import Namespace, NamespaceMember, Workflow

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/workflows", tags=["Workflows"])


async def user_has_workflow_access(
    session: SessionDep, user_id: str, workflow_id: str
) -> bool:
    """Check if user has access to a workflow via namespace membership or ownership."""
    # Query workflow with namespace information
    result = await session.execute(
        select(Workflow)
        .options(selectinload(Workflow.namespace))
        .where(Workflow.id == workflow_id)
    )
    workflow = result.scalar_one_or_none()

    if not workflow:
        return False

    # Check if user is the creator
    if workflow.created_by_id and str(workflow.created_by_id) == user_id:
        return True

    # Check if user owns the namespace
    if (
        workflow.namespace.user_owner_id
        and str(workflow.namespace.user_owner_id) == user_id
    ):
        return True

    # Check if user is a member of the namespace
    member_result = await session.execute(
        select(NamespaceMember).where(
            NamespaceMember.namespace_id == workflow.namespace_id,
            NamespaceMember.user_id == user_id,
        )
    )
    member = member_result.scalar_one_or_none()

    return member is not None


@router.get("")
async def list_workflows(current_user: CurrentUser, session: SessionDep):
    """List workflows accessible to the authenticated user."""
    # Query workflows where user has access via namespace
    result = await session.execute(
        select(Workflow)
        .options(selectinload(Workflow.namespace))
        .join(Workflow.namespace)
        .outerjoin(
            NamespaceMember, NamespaceMember.namespace_id == Workflow.namespace_id
        )
        .where(
            or_(
                # User owns the namespace
                Workflow.namespace.has(user_owner_id=current_user.id),
                # User created the workflow
                Workflow.created_by_id == current_user.id,
                # User is a member of the namespace
                NamespaceMember.user_id == current_user.id,
            )
        )
        .distinct()
    )
    workflows = result.scalars().all()

    return {
        "workflows": [
            {
                "id": str(workflow.id),
                "name": workflow.name,
                "description": workflow.description,
                "namespace_id": str(workflow.namespace_id),
                "namespace_name": workflow.namespace.name
                if workflow.namespace
                else None,
                "created_at": workflow.created_at.isoformat()
                if workflow.created_at
                else None,
                "updated_at": workflow.updated_at.isoformat()
                if workflow.updated_at
                else None,
            }
            for workflow in workflows
        ],
        "total": len(workflows),
        "user_id": str(current_user.id),
    }


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
    has_access = (
        namespace.user_owner_id == current_user.id
        or
        # TODO: Add organization member check
        await session.execute(
            select(NamespaceMember).where(
                NamespaceMember.namespace_id == workflow_data.namespace_id,
                NamespaceMember.user_id == current_user.id,
            )
        ).scalar_one_or_none()
        is not None
    )

    if not has_access:
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

    return {
        "id": str(workflow.id),
        "name": workflow.name,
        "description": workflow.description,
        "namespace_id": str(workflow.namespace_id),
        "created_by_id": str(workflow.created_by_id),
        "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
    }
