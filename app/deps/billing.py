"""
Billing dependencies for enforcing subscription limits.
"""

from fastapi import HTTPException, status

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep
from app.services import billing_service
from app.settings import settings


async def check_can_create_workflow(
    current_user: CurrentUser,
    session: SessionDep,
) -> None:
    """
    Dependency to check if user can create more workflows.
    Raises HTTPException if limit is reached.
    """
    if not settings.IS_CLOUD:
        return

    can_create, message = await billing_service.check_workflow_limit(
        session, current_user
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


async def check_can_execute_workflow(
    current_user: CurrentUser,
    session: SessionDep,
) -> None:
    """
    Dependency to check if user can execute more workflows this month.
    Raises HTTPException if limit is reached.
    """
    if not settings.IS_CLOUD:
        return

    can_execute, message = await billing_service.check_execution_limit(
        session, current_user
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


async def require_pro_tier(
    current_user: CurrentUser,
    session: SessionDep,
) -> None:
    """
    Dependency to require an active Hobby subscription.
    Raises HTTPException if user doesn't have Hobby.
    """
    if not settings.IS_CLOUD:
        return

    subscription = await billing_service.get_or_create_subscription(
        session, current_user
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
