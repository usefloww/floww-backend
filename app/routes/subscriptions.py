"""
Subscription and billing API endpoints.

Provides endpoints for:
- Getting organization subscription status
- Getting usage statistics
- Creating checkout sessions
- Creating customer portal sessions
"""

from typing import Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep, TransactionSessionDep
from app.models import Organization, OrganizationMember
from app.services import billing_service, stripe_service
from app.settings import settings

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/organizations", tags=["Subscriptions"])


class SubscriptionResponse(BaseModel):
    tier: str
    status: str
    trial_ends_at: Optional[str] = None
    current_period_end: Optional[str] = None
    grace_period_ends_at: Optional[str] = None
    cancel_at_period_end: bool
    has_active_pro: bool


class UsageResponse(BaseModel):
    workflows: int
    workflows_limit: int
    executions_this_month: int
    executions_limit: int


class CheckoutSessionRequest(BaseModel):
    success_url: str
    cancel_url: str


class CheckoutSessionResponse(BaseModel):
    session_id: str
    url: str


class CustomerPortalRequest(BaseModel):
    return_url: str


class CustomerPortalResponse(BaseModel):
    url: str


async def _get_organization_with_access(
    session: SessionDep,
    organization_id: UUID,
    user_id: UUID,
) -> Organization:
    """Get organization if user has access."""
    result = await session.execute(
        select(Organization)
        .join(OrganizationMember)
        .where(
            Organization.id == organization_id,
            OrganizationMember.user_id == user_id,
        )
    )
    organization = result.scalar_one_or_none()

    if not organization:
        raise HTTPException(
            status_code=404,
            detail="Organization not found",
        )

    return organization


@router.get("/{organization_id}/subscription", response_model=SubscriptionResponse)
async def get_organization_subscription(
    organization_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
):
    """Get the organization's subscription information"""
    if not settings.IS_CLOUD:
        raise HTTPException(
            status_code=404,
            detail="Billing is not enabled in this environment",
        )

    organization = await _get_organization_with_access(
        session, organization_id, current_user.id
    )

    subscription = await billing_service.get_or_create_subscription(
        session, organization
    )
    has_active_pro = await billing_service.has_active_hobby_subscription(subscription)

    return SubscriptionResponse(
        tier=subscription.tier.value,
        status=subscription.status.value,
        trial_ends_at=subscription.trial_ends_at.isoformat()
        if subscription.trial_ends_at
        else None,
        current_period_end=subscription.current_period_end.isoformat()
        if subscription.current_period_end
        else None,
        grace_period_ends_at=subscription.grace_period_ends_at.isoformat()
        if subscription.grace_period_ends_at
        else None,
        cancel_at_period_end=subscription.cancel_at_period_end,
        has_active_pro=has_active_pro,
    )


@router.get("/{organization_id}/usage", response_model=UsageResponse)
async def get_organization_usage(
    organization_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
):
    """Get the organization's usage statistics"""
    if not settings.IS_CLOUD:
        raise HTTPException(
            status_code=404,
            detail="Billing is not enabled in this environment",
        )

    organization = await _get_organization_with_access(
        session, organization_id, current_user.id
    )

    subscription = await billing_service.get_or_create_subscription(
        session, organization
    )

    workflows_count = await billing_service.get_workflow_count(session, organization.id)
    workflows_limit = await billing_service.get_workflow_limit(subscription)

    executions_count = await billing_service.get_execution_count_this_month(
        session, organization.id
    )
    executions_limit = await billing_service.get_execution_limit(subscription)

    return UsageResponse(
        workflows=workflows_count,
        workflows_limit=workflows_limit,
        executions_this_month=executions_count,
        executions_limit=executions_limit,
    )


@router.post("/{organization_id}/checkout", response_model=CheckoutSessionResponse)
async def create_checkout_session(
    organization_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
    body: CheckoutSessionRequest,
):
    """Create a Stripe checkout session for upgrading to Hobby"""
    if not settings.IS_CLOUD:
        raise HTTPException(
            status_code=404,
            detail="Billing is not enabled in this environment",
        )

    organization = await _get_organization_with_access(
        session, organization_id, current_user.id
    )

    subscription = await billing_service.get_or_create_subscription(
        session, organization
    )

    has_active_pro = await billing_service.has_active_hobby_subscription(subscription)
    if has_active_pro:
        raise HTTPException(
            status_code=400,
            detail="This organization already has an active Hobby subscription",
        )

    try:
        checkout_session = await stripe_service.create_checkout_session(
            organization=organization,
            subscription=subscription,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
            session_db=session,
        )

        return CheckoutSessionResponse(
            session_id=checkout_session["session_id"],
            url=checkout_session["url"],
        )
    except Exception as e:
        logger.error(
            "Failed to create checkout session",
            error=str(e),
            organization_id=organization_id,
        )
        raise HTTPException(status_code=500, detail="Failed to create checkout session")


@router.post("/{organization_id}/portal", response_model=CustomerPortalResponse)
async def create_customer_portal_session(
    organization_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
    body: CustomerPortalRequest,
):
    """Create a Stripe customer portal session for managing subscription"""
    if not settings.IS_CLOUD:
        raise HTTPException(
            status_code=404,
            detail="Billing is not enabled in this environment",
        )

    organization = await _get_organization_with_access(
        session, organization_id, current_user.id
    )

    subscription = await billing_service.get_or_create_subscription(
        session, organization
    )

    if not subscription.stripe_customer_id:
        raise HTTPException(
            status_code=400,
            detail="No Stripe customer found. Please create a subscription first.",
        )

    try:
        portal_session = await stripe_service.create_customer_portal_session(
            subscription=subscription,
            return_url=body.return_url,
        )

        return CustomerPortalResponse(url=portal_session["url"])
    except Exception as e:
        logger.error(
            "Failed to create portal session",
            error=str(e),
            organization_id=organization_id,
        )
        raise HTTPException(status_code=500, detail="Failed to create portal session")
