from uuid import UUID

from sqlalchemy import select

from app.deps.db import SessionDep
from app.models import Namespace, NamespaceMember, User, Workflow


async def get_or_create_user(session: SessionDep, workos_user_id: str) -> User:
    result = await session.execute(
        select(User).where(User.workos_user_id == workos_user_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        user = User(workos_user_id=workos_user_id)
        session.add(user)
        await session.flush()

        workspace = Namespace(
            user_owner_id=user.id, name=str(user.id), display_name=str(user.id)
        )
        session.add(workspace)
        await session.commit()
    return user


async def user_has_workflow_access(
    session: SessionDep, user_id: UUID, workflow_id: UUID
) -> bool:
    """Check if user has access to a workflow via namespace membership."""
    result = await session.execute(
        select(Workflow).where(
            Workflow.id == workflow_id
            and Workflow.namespace.has(NamespaceMember.user_id == user_id)
        )
    )
    workflow = result.scalar_one_or_none()
    return workflow is not None
