from fastapi import APIRouter
from sqlalchemy import select

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep
from app.models import Workflow

router = APIRouter(prefix="/workflows", tags=["Workflows"])


@router.get("/")
async def list_workflows(current_user: CurrentUser, session: SessionDep):
    """List workflows for the authenticated user."""
    # Query workflows owned by the current user
    result = await session.execute(
        select(Workflow).where(Workflow.user_id == current_user.id)
    )
    workflows = result.scalars().all()

    return {
        "workflows": [
            {
                "id": str(workflow.id),
                "name": workflow.name,
                "description": workflow.description,
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
