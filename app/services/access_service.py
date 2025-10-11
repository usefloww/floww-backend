from uuid import UUID

from app.deps.db import SessionDep
from app.models import Namespace, Workflow
from app.utils.query_helpers import UserAccessibleQuery


async def has_workflow_access(
    session: SessionDep, user_id: UUID, workflow_id: UUID
) -> bool:
    query = UserAccessibleQuery(user_id).workflows().where(Workflow.id == workflow_id)
    result = await session.execute(query)
    workflow = result.scalar_one_or_none()
    return workflow is not None


async def has_namespace_access(
    session: SessionDep, user_id: UUID, namespace_id: UUID
) -> bool:
    """Check if user has access to a namespace via ownership or membership."""
    query = (
        UserAccessibleQuery(user_id).namespaces().where(Namespace.id == namespace_id)
    )
    result = await session.execute(query)
    namespace = result.scalar_one_or_none()
    return namespace is not None
