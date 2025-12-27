from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError

from app.models import (
    BillingEvent,
    ExecutionHistory,
    ExecutionStatus,
    Namespace,
    Organization,
    Subscription,
    SubscriptionStatus,
    SubscriptionTier,
    Workflow,
)
from app.services.stripe_service import (
    find_subscription_by_organization,
    set_default_payment_method_if_none,
)
from app.settings import settings

logger = structlog.stdlib.get_logger(__name__)

PLAN_LIMITS: dict[SubscriptionTier, tuple[int, int]] = {
    SubscriptionTier.FREE: (3, 100),
    SubscriptionTier.HOBBY: (100, 10_000),
    SubscriptionTier.TEAM: (100, 50_000),
}

PLAN_NAMES: dict[SubscriptionTier, str] = {
    SubscriptionTier.FREE: "Free",
    SubscriptionTier.HOBBY: "Hobby",
    SubscriptionTier.TEAM: "Team",
}


@dataclass
class SubscriptionDetails:
    tier: SubscriptionTier
    plan_name: str
    workflow_limit: int
    execution_limit_per_month: int
    is_paid: bool


def _is_subscription_active(subscription: Subscription) -> bool:
    if subscription.tier == SubscriptionTier.FREE:
        return False

    now = datetime.now(timezone.utc)

    if subscription.status == SubscriptionStatus.TRIALING:
        return bool(subscription.trial_ends_at and subscription.trial_ends_at > now)

    if subscription.status == SubscriptionStatus.ACTIVE:
        return True

    if subscription.status == SubscriptionStatus.PAST_DUE:
        return bool(
            subscription.grace_period_ends_at
            and subscription.grace_period_ends_at > now
        )

    return False


def get_subscription_details(subscription: Subscription) -> SubscriptionDetails:
    is_paid = _is_subscription_active(subscription)

    if is_paid:
        tier = subscription.tier
        workflow_limit, execution_limit = PLAN_LIMITS[tier]
        return SubscriptionDetails(
            tier=tier,
            plan_name=PLAN_NAMES[tier],
            workflow_limit=workflow_limit,
            execution_limit_per_month=execution_limit,
            is_paid=True,
        )

    workflow_limit, execution_limit = PLAN_LIMITS[SubscriptionTier.FREE]
    return SubscriptionDetails(
        tier=SubscriptionTier.FREE,
        plan_name=PLAN_NAMES[SubscriptionTier.FREE],
        workflow_limit=workflow_limit,
        execution_limit_per_month=execution_limit,
        is_paid=False,
    )


def has_active_hobby_subscription(subscription: Subscription) -> bool:
    return get_subscription_details(subscription).is_paid


async def get_or_create_subscription(
    session: AsyncSession, organization: Organization
) -> Subscription:
    organization_id = organization.id

    result = await session.execute(
        select(Subscription).where(Subscription.organization_id == organization_id)
    )
    existing = result.scalar_one_or_none()

    if existing:
        return existing

    try:
        async with session.begin_nested():
            subscription = Subscription(
                organization_id=organization.id,
                tier=SubscriptionTier.FREE,
                status=SubscriptionStatus.ACTIVE,
            )
            session.add(subscription)
            await session.flush()
            return subscription
    except IntegrityError:
        result = await session.execute(
            select(Subscription).where(Subscription.organization_id == organization_id)
        )
        return result.scalar_one()


async def get_workflow_count(session: AsyncSession, organization_id: UUID) -> int:
    result = await session.execute(
        select(func.count(Workflow.id))
        .select_from(Workflow)
        .join(Workflow.namespace)
        .where(Namespace.organization_owner_id == organization_id)
    )
    return result.scalar_one()


async def get_execution_count_this_month(
    session: AsyncSession, organization_id: UUID
) -> int:
    now = datetime.now(timezone.utc)
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    result = await session.execute(
        select(func.count(ExecutionHistory.id))
        .select_from(ExecutionHistory)
        .join(ExecutionHistory.workflow)
        .join(Workflow.namespace)
        .where(
            Namespace.organization_owner_id == organization_id,
            ExecutionHistory.received_at >= start_of_month,
            ExecutionHistory.status.in_(
                [
                    ExecutionStatus.COMPLETED,
                    ExecutionStatus.STARTED,
                    ExecutionStatus.RECEIVED,
                ]
            ),
        )
    )
    return result.scalar_one()


async def check_workflow_limit(
    session: AsyncSession, organization: Organization
) -> tuple[bool, str]:
    if not settings.IS_CLOUD:
        return True, ""

    subscription = await get_or_create_subscription(session, organization)
    details = get_subscription_details(subscription)
    current_count = await get_workflow_count(session, organization.id)

    if current_count >= details.workflow_limit:
        if details.is_paid:
            return (
                False,
                f"You have reached your workflow limit of {details.workflow_limit} workflows.",
            )
        return (
            False,
            f"You have reached the free tier limit of {details.workflow_limit} workflows. Upgrade to Hobby to create more workflows.",
        )

    return True, ""


async def check_execution_limit(
    session: AsyncSession, organization: Organization
) -> tuple[bool, str]:
    if not settings.IS_CLOUD:
        return True, ""

    subscription = await get_or_create_subscription(session, organization)
    details = get_subscription_details(subscription)
    current_count = await get_execution_count_this_month(session, organization.id)

    if current_count >= details.execution_limit_per_month:
        if details.is_paid:
            return (
                False,
                f"You have reached your monthly execution limit of {details.execution_limit_per_month:,} executions.",
            )
        return (
            False,
            f"You have reached the free tier limit of {details.execution_limit_per_month:,} executions this month. Upgrade to Hobby for more executions.",
        )

    return True, ""


async def start_trial(
    session: AsyncSession,
    subscription: Subscription,
    trial_ends_at: datetime | None = None,
) -> None:
    subscription.tier = SubscriptionTier.HOBBY
    subscription.status = SubscriptionStatus.TRIALING
    subscription.trial_ends_at = trial_ends_at or (
        datetime.now(timezone.utc) + timedelta(days=settings.TRIAL_PERIOD_DAYS)
    )
    await session.flush()


async def activate_paid_subscription(
    session: AsyncSession,
    subscription: Subscription,
    stripe_subscription_id: str,
    current_period_end: datetime,
) -> None:
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.stripe_subscription_id = stripe_subscription_id
    subscription.current_period_end = current_period_end
    subscription.trial_ends_at = None
    subscription.grace_period_ends_at = None
    await session.flush()


async def cancel_subscription(
    session: AsyncSession, subscription: Subscription
) -> None:
    subscription.cancel_at_period_end = True
    await session.flush()


async def start_grace_period(session: AsyncSession, subscription: Subscription) -> None:
    subscription.status = SubscriptionStatus.PAST_DUE
    subscription.grace_period_ends_at = datetime.now(timezone.utc) + timedelta(
        days=settings.GRACE_PERIOD_DAYS
    )
    await session.flush()


async def downgrade_to_free(session: AsyncSession, subscription: Subscription) -> None:
    subscription.tier = SubscriptionTier.FREE
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.stripe_subscription_id = None
    subscription.current_period_end = None
    subscription.trial_ends_at = None
    subscription.grace_period_ends_at = None
    subscription.cancel_at_period_end = False
    await session.flush()


# Webhook event handlers


async def _is_duplicate_event(session: AsyncSession, stripe_event_id: str) -> bool:
    result = await session.execute(
        select(BillingEvent).where(BillingEvent.stripe_event_id == stripe_event_id)
    )
    return result.scalar_one_or_none() is not None


async def _record_event(
    session: AsyncSession,
    subscription_id: UUID,
    event_type: str,
    stripe_event_id: str,
    payload: dict,
) -> None:
    event = BillingEvent(
        subscription_id=subscription_id,
        event_type=event_type,
        stripe_event_id=stripe_event_id,
        payload=payload,
    )
    session.add(event)
    await session.flush()


async def handle_checkout_completed(
    session: AsyncSession,
    event_data: dict,
    stripe_event_id: str,
) -> None:
    if await _is_duplicate_event(session, stripe_event_id):
        return

    metadata = event_data.get("metadata", {})
    subscription_id_str = metadata.get("subscription_id")

    if not subscription_id_str:
        return

    try:
        subscription_id = UUID(subscription_id_str)
    except (ValueError, TypeError):
        return

    result = await session.execute(
        select(Subscription).where(Subscription.id == subscription_id)
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        return

    # Update customer ID if not set
    stripe_customer_id = event_data.get("customer")
    if stripe_customer_id and not subscription.stripe_customer_id:
        subscription.stripe_customer_id = stripe_customer_id

    await _record_event(
        session,
        subscription.id,
        "checkout.session.completed",
        stripe_event_id,
        event_data,
    )


async def handle_subscription_created(
    session: AsyncSession,
    event_data: dict,
    stripe_event_id: str,
) -> None:
    if await _is_duplicate_event(session, stripe_event_id):
        return

    subscription_id_str = event_data.get("metadata", {}).get("subscription_id")
    if not subscription_id_str:
        return

    try:
        subscription_id = UUID(subscription_id_str)
    except (ValueError, TypeError):
        return

    result = await session.execute(
        select(Subscription).where(Subscription.id == subscription_id)
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        return

    stripe_subscription_id = event_data.get("id")
    current_period_end_ts = event_data.get("current_period_end")
    current_period_end = (
        datetime.fromtimestamp(current_period_end_ts, tz=timezone.utc)
        if current_period_end_ts
        else None
    )

    trial_end_ts = event_data.get("trial_end")
    trial_end = (
        datetime.fromtimestamp(trial_end_ts, tz=timezone.utc) if trial_end_ts else None
    )

    stripe_status = event_data.get("status")

    if stripe_status == "trialing":
        await start_trial(session, subscription, trial_ends_at=trial_end)
        subscription.stripe_subscription_id = stripe_subscription_id
        subscription.current_period_end = current_period_end
    elif (
        stripe_status in ["active", "past_due"]
        and stripe_subscription_id
        and current_period_end
    ):
        await activate_paid_subscription(
            session, subscription, stripe_subscription_id, current_period_end
        )

    # Sync tier from subscription items
    items = event_data.get("items", {}).get("data", [])
    for item in items:
        price_id = item.get("price", {}).get("id")
        if price_id == settings.STRIPE_PRICE_ID_HOBBY:
            subscription.tier = SubscriptionTier.HOBBY
        elif price_id == settings.STRIPE_PRICE_ID_TEAM:
            subscription.tier = SubscriptionTier.TEAM

    await _record_event(
        session,
        subscription.id,
        "customer.subscription.created",
        stripe_event_id,
        event_data,
    )


async def handle_subscription_updated(
    session: AsyncSession,
    event_data: dict,
    stripe_event_id: str,
) -> None:
    if await _is_duplicate_event(session, stripe_event_id):
        return

    stripe_subscription_id = event_data.get("id")

    result = await session.execute(
        select(Subscription).where(
            Subscription.stripe_subscription_id == stripe_subscription_id
        )
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        return

    stripe_status = event_data.get("status")
    current_period_end_ts = event_data.get("current_period_end")
    current_period_end = (
        datetime.fromtimestamp(current_period_end_ts, tz=timezone.utc)
        if current_period_end_ts
        else None
    )
    cancel_at_period_end = event_data.get("cancel_at_period_end", False)

    if stripe_status == "active":
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.grace_period_ends_at = None
    elif stripe_status == "past_due":
        subscription.status = SubscriptionStatus.PAST_DUE
    elif stripe_status == "canceled":
        await downgrade_to_free(session, subscription)

    subscription.current_period_end = current_period_end
    subscription.cancel_at_period_end = cancel_at_period_end

    # Sync tier from subscription items
    if stripe_status != "canceled":
        items = event_data.get("items", {}).get("data", [])
        for item in items:
            price_id = item.get("price", {}).get("id")
            if price_id == settings.STRIPE_PRICE_ID_HOBBY:
                subscription.tier = SubscriptionTier.HOBBY
            elif price_id == settings.STRIPE_PRICE_ID_TEAM:
                subscription.tier = SubscriptionTier.TEAM

    await _record_event(
        session,
        subscription.id,
        "customer.subscription.updated",
        stripe_event_id,
        event_data,
    )


async def handle_subscription_deleted(
    session: AsyncSession,
    event_data: dict,
    stripe_event_id: str,
) -> None:
    if await _is_duplicate_event(session, stripe_event_id):
        return

    stripe_subscription_id = event_data.get("id")

    result = await session.execute(
        select(Subscription).where(
            Subscription.stripe_subscription_id == stripe_subscription_id
        )
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        return

    await downgrade_to_free(session, subscription)
    await _record_event(
        session,
        subscription.id,
        "customer.subscription.deleted",
        stripe_event_id,
        event_data,
    )


async def handle_payment_failed_event(
    session: AsyncSession,
    event_data: dict,
    stripe_event_id: str,
) -> None:
    if await _is_duplicate_event(session, stripe_event_id):
        return

    stripe_subscription_id = event_data.get("subscription")
    if not stripe_subscription_id:
        return

    result = await session.execute(
        select(Subscription).where(
            Subscription.stripe_subscription_id == stripe_subscription_id
        )
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        return

    await start_grace_period(session, subscription)
    await _record_event(
        session, subscription.id, "invoice.payment_failed", stripe_event_id, event_data
    )


async def handle_payment_succeeded_event(
    session: AsyncSession,
    event_data: dict,
    stripe_event_id: str,
) -> None:
    if await _is_duplicate_event(session, stripe_event_id):
        return

    stripe_subscription_id = event_data.get("subscription")

    if not stripe_subscription_id:
        return

    result = await session.execute(
        select(Subscription).where(
            Subscription.stripe_subscription_id == stripe_subscription_id
        )
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        return

    if subscription.status == SubscriptionStatus.PAST_DUE:
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.grace_period_ends_at = None

    customer_id = event_data.get("customer")
    payment_method_id = event_data.get("default_payment_method")

    if customer_id and payment_method_id:
        set_default_payment_method_if_none(customer_id, payment_method_id)

    await _record_event(
        session,
        subscription.id,
        "invoice.payment_succeeded",
        stripe_event_id,
        event_data,
    )


async def sync_subscription_from_stripe(
    session: AsyncSession,
    organization_id: UUID,
) -> tuple[bool, str]:
    result = await session.execute(
        select(Organization)
        .options(selectinload(Organization.subscription))
        .where(Organization.id == organization_id)
    )
    organization = result.scalar_one_or_none()

    if not organization:
        return False, "Organization not found"

    subscription = organization.subscription
    if not subscription:
        subscription = Subscription(
            organization_id=organization_id,
            tier=SubscriptionTier.FREE,
            status=SubscriptionStatus.ACTIVE,
        )
        session.add(subscription)
        await session.flush()

    stripe_sub = find_subscription_by_organization(str(organization_id))
    if not stripe_sub:
        return False, "No Stripe subscription found for this organization"

    stripe_subscription_id = stripe_sub.get("id")
    stripe_customer_id = stripe_sub.get("customer")
    if (
        stripe_subscription_id
        and subscription.stripe_subscription_id != stripe_subscription_id
    ):
        subscription.stripe_subscription_id = stripe_subscription_id
    if stripe_customer_id and subscription.stripe_customer_id != stripe_customer_id:
        subscription.stripe_customer_id = stripe_customer_id

    stripe_status = stripe_sub.get("status")
    current_period_end_ts = stripe_sub.get("current_period_end")
    current_period_end = (
        datetime.fromtimestamp(current_period_end_ts, tz=timezone.utc)
        if current_period_end_ts
        else None
    )
    cancel_at_period_end = stripe_sub.get("cancel_at_period_end", False)
    trial_end_ts = stripe_sub.get("trial_end")
    trial_end = (
        datetime.fromtimestamp(trial_end_ts, tz=timezone.utc) if trial_end_ts else None
    )

    if stripe_status == "active":
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.grace_period_ends_at = None
        subscription.trial_ends_at = None
    elif stripe_status == "trialing":
        subscription.status = SubscriptionStatus.TRIALING
        subscription.trial_ends_at = trial_end
    elif stripe_status == "past_due":
        subscription.status = SubscriptionStatus.PAST_DUE
        if not subscription.grace_period_ends_at:
            subscription.grace_period_ends_at = datetime.now(timezone.utc) + timedelta(
                days=settings.GRACE_PERIOD_DAYS
            )
    elif stripe_status == "canceled":
        subscription.tier = SubscriptionTier.FREE
        subscription.status = SubscriptionStatus.CANCELED
        subscription.stripe_subscription_id = None
        subscription.current_period_end = None
        subscription.trial_ends_at = None
        subscription.grace_period_ends_at = None
        subscription.cancel_at_period_end = False
        await session.flush()
        return (
            True,
            "Subscription synced - downgraded to free tier (canceled in Stripe)",
        )
    elif stripe_status == "incomplete":
        subscription.status = SubscriptionStatus.INCOMPLETE
    elif stripe_status == "incomplete_expired":
        subscription.tier = SubscriptionTier.FREE
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.stripe_subscription_id = None
        await session.flush()
        return True, "Subscription synced - downgraded to free tier (expired)"

    subscription.current_period_end = current_period_end
    subscription.cancel_at_period_end = cancel_at_period_end

    items = stripe_sub.get("items", {}).get("data", [])
    for item in items:
        price_id = item.get("price", {}).get("id")
        if price_id == settings.STRIPE_PRICE_ID_HOBBY:
            subscription.tier = SubscriptionTier.HOBBY
        elif price_id == settings.STRIPE_PRICE_ID_TEAM:
            subscription.tier = SubscriptionTier.TEAM

    await session.flush()

    return (
        True,
        f"Subscription synced successfully (status: {stripe_status}, tier: {subscription.tier.value})",
    )
