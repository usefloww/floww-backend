from typing import Union
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.deps.db import SessionDep
from app.models import Namespace, NamespaceMember, Workflow, WorkflowDeployment


async def user_has_workflow_access(
    session: SessionDep, user_id: Union[str, UUID], workflow_id: Union[str, UUID]
) -> bool:
    """Check if user has access to a workflow via namespace membership or ownership."""
    # Convert to string for consistent comparison
    user_id_str = str(user_id)
    workflow_id_str = str(workflow_id)

    # Query workflow with namespace information
    result = await session.execute(
        select(Workflow)
        .options(selectinload(Workflow.namespace))
        .where(Workflow.id == workflow_id_str)
    )
    workflow = result.scalar_one_or_none()

    if not workflow:
        return False

    # Check if user is the creator
    if workflow.created_by_id and str(workflow.created_by_id) == user_id_str:
        return True

    # Check if user owns the namespace
    if (
        workflow.namespace.user_owner_id
        and str(workflow.namespace.user_owner_id) == user_id_str
    ):
        return True

    # Check if user is a member of the namespace
    member_result = await session.execute(
        select(NamespaceMember).where(
            NamespaceMember.namespace_id == workflow.namespace_id,
            NamespaceMember.user_id == user_id_str,
        )
    )
    member = member_result.scalar_one_or_none()

    return member is not None


async def user_has_namespace_access(
    session: SessionDep, user_id: Union[str, UUID], namespace_id: Union[str, UUID]
) -> bool:
    """Check if user has access to a namespace via ownership or membership."""
    # Convert to string for consistent comparison
    user_id_str = str(user_id)
    namespace_id_str = str(namespace_id)

    # Query the namespace
    result = await session.execute(
        select(Namespace).where(Namespace.id == namespace_id_str)
    )
    namespace = result.scalar_one_or_none()

    if not namespace:
        return False

    # Check if user owns the namespace
    if namespace.user_owner_id and str(namespace.user_owner_id) == user_id_str:
        return True

    # If namespace is owned by an organization, check if user is a member
    if namespace.organization_owner_id:
        member_result = await session.execute(
            select(NamespaceMember).where(
                NamespaceMember.namespace_id == namespace_id_str,
                NamespaceMember.user_id == user_id_str,
            )
        )
        if member_result.scalar_one_or_none():
            return True

    return False


async def check_namespace_access(
    session: SessionDep, namespace_id: Union[str, UUID], user_id: Union[str, UUID]
) -> Namespace:
    """Check if user has access to the namespace and return it, or raise HTTPException."""
    namespace_id_str = str(namespace_id)
    user_id_str = str(user_id)

    result = await session.execute(
        select(Namespace).where(Namespace.id == namespace_id_str)
    )
    namespace = result.scalar_one_or_none()

    if not namespace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Namespace not found"
        )

    # Check if user owns the namespace or is a member
    if namespace.user_owner_id and str(namespace.user_owner_id) == user_id_str:
        return namespace

    if namespace.organization_owner_id:
        # Check if user is a member of the namespace
        member_result = await session.execute(
            select(NamespaceMember).where(
                NamespaceMember.namespace_id == namespace_id_str,
                NamespaceMember.user_id == user_id_str,
            )
        )
        if member_result.scalar_one_or_none():
            return namespace

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You don't have access to this namespace",
    )


def get_user_accessible_workflows_query(session: SessionDep, user_id: Union[str, UUID]):
    """Get a query for workflows accessible to the user."""
    user_id_str = str(user_id)

    return (
        select(Workflow)
        .options(selectinload(Workflow.namespace))
        .join(Workflow.namespace)
        .outerjoin(
            NamespaceMember, NamespaceMember.namespace_id == Workflow.namespace_id
        )
        .where(
            or_(
                # User owns the namespace
                Workflow.namespace.has(user_owner_id=user_id_str),
                # User created the workflow
                Workflow.created_by_id == user_id_str,
                # User is a member of the namespace
                NamespaceMember.user_id == user_id_str,
            )
        )
        .distinct()
    )


def get_user_accessible_deployments_query(
    session: SessionDep, user_id: Union[str, UUID]
):
    """Get a query for workflow deployments accessible to the user."""
    user_id_str = str(user_id)

    return (
        select(WorkflowDeployment)
        .options(
            selectinload(WorkflowDeployment.workflow).selectinload(Workflow.namespace),
            selectinload(WorkflowDeployment.runtime),
            selectinload(WorkflowDeployment.deployed_by),
        )
        .join(WorkflowDeployment.workflow)
        .join(Workflow.namespace)
        .outerjoin(
            NamespaceMember, NamespaceMember.namespace_id == Workflow.namespace_id
        )
        .where(
            or_(
                # User owns the namespace
                Workflow.namespace.has(user_owner_id=user_id_str),
                # User created the workflow
                Workflow.created_by_id == user_id_str,
                # User is a member of the namespace
                NamespaceMember.user_id == user_id_str,
            )
        )
        .distinct()
    )
