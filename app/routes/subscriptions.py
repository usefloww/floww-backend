from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep, TransactionSessionDep
from app.models import Organization, OrganizationMember, SubscriptionTier
from app.services import billing_service, stripe_service
from app.settings import settings

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


class SubscriptionIntentRequest(BaseModel):
    plan: Literal["hobby", "team"] = "hobby"


class SubscriptionIntentResponse(BaseModel):
    subscription_id: str
    client_secret: str


class VerifyPaymentResponse(BaseModel):
    status: str
    subscription_id: str
    invoice_status: Optional[str] = None
    payment_intent_status: Optional[str] = None
    message: str
    requires_action: Optional[bool] = None


class CustomerPortalRequest(BaseModel):
    return_url: str


class CustomerPortalResponse(BaseModel):
    url: str


class PaymentMethodResponse(BaseModel):
    payment_method_id: Optional[str] = None
    brand: Optional[str] = None
    last4: Optional[str] = None
    exp_month: Optional[int] = None
    exp_year: Optional[int] = None


class InvoiceResponse(BaseModel):
    id: str
    number: Optional[str] = None
    amount_due: int
    amount_paid: int
    currency: str
    status: Optional[str] = None
    created: int
    period_start: Optional[int] = None
    period_end: Optional[int] = None
    pdf_url: Optional[str] = None
    hosted_invoice_url: Optional[str] = None


async def _get_organization_with_access(
    session: SessionDep,
    organization_id: UUID,
    user_id: UUID,
) -> Organization:
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
        raise HTTPException(status_code=404, detail="Organization not found")

    return organization


def _require_cloud():
    if not settings.IS_CLOUD:
        raise HTTPException(
            status_code=404,
            detail="Billing is not enabled in this environment",
        )


@router.get("/{organization_id}/subscription", response_model=SubscriptionResponse)
async def get_subscription(
    organization_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
):
    _require_cloud()

    organization = await _get_organization_with_access(
        session, organization_id, current_user.id
    )

    subscription = await billing_service.get_or_create_subscription(
        session, organization
    )
    details = billing_service.get_subscription_details(subscription)

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
        has_active_pro=details.is_paid,
    )


@router.get("/{organization_id}/usage", response_model=UsageResponse)
async def get_usage(
    organization_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
):
    _require_cloud()

    organization = await _get_organization_with_access(
        session, organization_id, current_user.id
    )

    subscription = await billing_service.get_or_create_subscription(
        session, organization
    )
    details = billing_service.get_subscription_details(subscription)

    workflows_count = await billing_service.get_workflow_count(session, organization.id)
    executions_count = await billing_service.get_execution_count_this_month(
        session, organization.id
    )

    return UsageResponse(
        workflows=workflows_count,
        workflows_limit=details.workflow_limit,
        executions_this_month=executions_count,
        executions_limit=details.execution_limit_per_month,
    )


@router.post(
    "/{organization_id}/subscription/intent", response_model=SubscriptionIntentResponse
)
async def create_subscription_intent(
    organization_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
    body: SubscriptionIntentRequest,
):
    _require_cloud()

    organization = await _get_organization_with_access(
        session, organization_id, current_user.id
    )

    subscription = await billing_service.get_or_create_subscription(
        session, organization
    )
    details = billing_service.get_subscription_details(subscription)

    if details.is_paid:
        raise HTTPException(
            status_code=400,
            detail="This organization already has an active subscription.",
        )

    target_tier = (
        SubscriptionTier.HOBBY if body.plan == "hobby" else SubscriptionTier.TEAM
    )

    result = await stripe_service.create_subscription_with_intent(
        organization=organization,
        subscription=subscription,
        target_tier=target_tier,
        session_db=session,
    )

    return SubscriptionIntentResponse(
        subscription_id=result["subscription_id"],
        client_secret=result["client_secret"],
    )


@router.post(
    "/{organization_id}/subscription/verify-payment",
    response_model=VerifyPaymentResponse,
)
async def verify_subscription_payment(
    organization_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
):
    _require_cloud()

    organization = await _get_organization_with_access(
        session, organization_id, current_user.id
    )

    subscription = await billing_service.get_or_create_subscription(
        session, organization
    )

    if not subscription.stripe_subscription_id:
        raise HTTPException(
            status_code=400,
            detail="No Stripe subscription found. Please create a subscription first.",
        )

    result = await stripe_service.verify_subscription_payment(
        subscription.stripe_subscription_id
    )
    return VerifyPaymentResponse(**result)


@router.post("/{organization_id}/subscription/cancel")
async def cancel_subscription(
    organization_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    _require_cloud()

    organization = await _get_organization_with_access(
        session, organization_id, current_user.id
    )

    subscription = await billing_service.get_or_create_subscription(
        session, organization
    )

    if not subscription.stripe_subscription_id:
        raise HTTPException(
            status_code=400,
            detail="No active subscription to cancel.",
        )

    await stripe_service.cancel_subscription(subscription.stripe_subscription_id)

    return {"message": "Subscription will be canceled at the end of the billing period"}


@router.post("/{organization_id}/portal", response_model=CustomerPortalResponse)
async def create_customer_portal_session(
    organization_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
    body: CustomerPortalRequest,
):
    _require_cloud()

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

    result = stripe_service.create_customer_portal_session(
        customer_id=subscription.stripe_customer_id,
        return_url=body.return_url,
    )

    return CustomerPortalResponse(url=result["url"])


@router.get("/{organization_id}/payment-method", response_model=PaymentMethodResponse)
async def get_payment_method(
    organization_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
):
    _require_cloud()

    organization = await _get_organization_with_access(
        session, organization_id, current_user.id
    )

    subscription = await billing_service.get_or_create_subscription(
        session, organization
    )

    if not subscription.stripe_customer_id:
        return PaymentMethodResponse()

    result = stripe_service.get_default_payment_method(subscription.stripe_customer_id)

    if not result:
        return PaymentMethodResponse()

    return PaymentMethodResponse(
        payment_method_id=result["payment_method_id"],
        brand=result["brand"],
        last4=result["last4"],
        exp_month=result["exp_month"],
        exp_year=result["exp_year"],
    )


@router.get("/{organization_id}/invoices", response_model=list[InvoiceResponse])
async def list_invoices(
    organization_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
):
    _require_cloud()

    organization = await _get_organization_with_access(
        session, organization_id, current_user.id
    )

    subscription = await billing_service.get_or_create_subscription(
        session, organization
    )

    if not subscription.stripe_customer_id:
        return []

    invoices = stripe_service.list_customer_invoices(subscription.stripe_customer_id)

    return [
        InvoiceResponse(
            id=inv["id"],
            number=inv["number"],
            amount_due=inv["amount_due"],
            amount_paid=inv["amount_paid"],
            currency=inv["currency"],
            status=inv["status"],
            created=inv["created"],
            period_start=inv["period_start"],
            period_end=inv["period_end"],
            pdf_url=inv["pdf_url"],
            hosted_invoice_url=inv["hosted_invoice_url"],
        )
        for inv in invoices
    ]
