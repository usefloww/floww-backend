"""
Billing dependencies for enforcing subscription limits.

Note: These dependencies require an organization context. They look up the organization
via the namespace that the workflow/resource belongs to.
"""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep
from app.models import Namespace, Organization, OrganizationMember
from app.services import billing_service
from app.settings import settings


async def get_organization_for_namespace(
    session: SessionDep,
    namespace_id: UUID,
) -> Organization:
    """Get the organization that owns a namespace."""
    result = await session.execute(
        select(Namespace)
        .options(selectinload(Namespace.organization_owner))
        .where(Namespace.id == namespace_id)
    )
    namespace = result.scalar_one_or_none()

    if not namespace or not namespace.organization_owner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Namespace or organization not found",
        )

    return namespace.organization_owner


async def check_can_create_workflow_in_namespace(
    session: SessionDep,
    namespace_id: UUID,
) -> None:
    """
    Check if more workflows can be created in the namespace.
    Raises HTTPException if limit is reached.
    """
    if not settings.IS_CLOUD:
        return

    organization = await get_organization_for_namespace(session, namespace_id)
    can_create, message = await billing_service.check_workflow_limit(
        session, organization
    )

    if not can_create:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "title": "Workflow limit reached",
                "description": message,
                "upgrade_required": True,
            },
        )


async def check_can_execute_workflow_in_org(
    session: SessionDep,
    organization: Organization,
) -> None:
    """
    Check if more workflows can be executed in the organization this month.
    Raises HTTPException if limit is reached.
    """
    if not settings.IS_CLOUD:
        return

    can_execute, message = await billing_service.check_execution_limit(
        session, organization
    )

    if not can_execute:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "title": "Execution limit reached",
                "description": message,
                "upgrade_required": True,
            },
        )


async def require_pro_tier_for_org(
    session: SessionDep,
    organization: Organization,
) -> None:
    """
    Require an active Hobby subscription for the organization.
    Raises HTTPException if organization doesn't have Hobby.
    """
    if not settings.IS_CLOUD:
        return

    subscription = await billing_service.get_or_create_subscription(
        session, organization
    )
    has_pro = await billing_service.has_active_hobby_subscription(subscription)

    if not has_pro:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "title": "Hobby subscription required",
                "description": "This feature requires an active Hobby subscription.",
                "upgrade_required": True,
            },
        )


async def get_user_first_organization(
    session: SessionDep,
    current_user: CurrentUser,
) -> Organization:
    """Get the first organization the user is a member of (for backwards compatibility)."""
    result = await session.execute(
        select(Organization)
        .join(OrganizationMember)
        .where(OrganizationMember.user_id == current_user.id)
        .order_by(OrganizationMember.created_at)
        .limit(1)
    )
    organization = result.scalar_one_or_none()

    if not organization:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User has no organization membership",
        )

    return organization
