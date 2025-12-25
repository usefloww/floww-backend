from datetime import datetime, timedelta, timezone
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.settings import settings

logger = structlog.stdlib.get_logger(__name__)


async def get_or_create_subscription(
    session: AsyncSession, organization: Organization
) -> Subscription:
    """
    Get or create a subscription for an organization.
    New organizations start on the free tier.
    """
    result = await session.execute(
        select(Subscription).where(Subscription.organization_id == organization.id)
    )
    existing_subscription = result.scalar_one_or_none()

    if existing_subscription:
        return existing_subscription

    subscription = Subscription(
        organization_id=organization.id,
        tier=SubscriptionTier.FREE,
        status=SubscriptionStatus.ACTIVE,
    )
    session.add(subscription)
    await session.flush()
    logger.info("Created free tier subscription", organization_id=organization.id)
    return subscription


async def has_active_hobby_subscription(subscription: Subscription) -> bool:
    """
    Check if a subscription grants pro access.
    This includes:
    - Active hobby subscriptions
    - Trial period (before trial_ends_at)
    - Grace period (before grace_period_ends_at) even if payment failed
    """
    if subscription.tier != SubscriptionTier.HOBBY:
        return False

    now = datetime.now(timezone.utc)

    if subscription.status == SubscriptionStatus.TRIALING:
        if subscription.trial_ends_at and subscription.trial_ends_at > now:
            return True
        return False

    if subscription.status == SubscriptionStatus.ACTIVE:
        return True

    if subscription.status == SubscriptionStatus.PAST_DUE:
        if (
            subscription.grace_period_ends_at
            and subscription.grace_period_ends_at > now
        ):
            logger.info(
                "User in grace period",
                subscription_id=subscription.id,
                grace_period_ends_at=subscription.grace_period_ends_at,
            )
            return True
        return False

    return False


async def get_workflow_limit(subscription: Subscription) -> int:
    """Get the workflow limit based on subscription tier"""
    is_pro = await has_active_hobby_subscription(subscription)
    if is_pro:
        return settings.PRO_TIER_WORKFLOW_LIMIT
    return settings.FREE_TIER_WORKFLOW_LIMIT


async def get_execution_limit(subscription: Subscription) -> int:
    """Get the monthly execution limit based on subscription tier"""
    is_pro = await has_active_hobby_subscription(subscription)
    if is_pro:
        return settings.PRO_TIER_EXECUTION_LIMIT_PER_MONTH
    return settings.FREE_TIER_EXECUTION_LIMIT_PER_MONTH


async def get_workflow_count(session: AsyncSession, organization_id: UUID) -> int:
    """Get the number of workflows for an organization"""
    # Find the namespace owned by this organization
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
    """Get the number of workflow executions this month for an organization"""
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
    """
    Check if organization can create more workflows.
    Returns (can_create: bool, message: str)
    """
    if not settings.IS_CLOUD:
        return True, ""

    subscription = await get_or_create_subscription(session, organization)
    current_count = await get_workflow_count(session, organization.id)
    limit = await get_workflow_limit(subscription)

    if current_count >= limit:
        is_pro = await has_active_hobby_subscription(subscription)
        if is_pro:
            return False, f"You have reached your workflow limit of {limit} workflows."
        else:
            return (
                False,
                f"You have reached the free tier limit of {limit} workflows. Upgrade to Hobby to create more workflows.",
            )

    return True, ""


async def check_execution_limit(
    session: AsyncSession, organization: Organization
) -> tuple[bool, str]:
    """
    Check if organization can execute more workflows this month.
    Returns (can_execute: bool, message: str)
    """
    if not settings.IS_CLOUD:
        return True, ""

    subscription = await get_or_create_subscription(session, organization)
    current_count = await get_execution_count_this_month(session, organization.id)
    limit = await get_execution_limit(subscription)

    if current_count >= limit:
        is_pro = await has_active_hobby_subscription(subscription)
        if is_pro:
            return (
                False,
                f"You have reached your monthly execution limit of {limit:,} executions.",
            )
        else:
            return (
                False,
                f"You have reached the free tier limit of {limit:,} executions this month. Upgrade to Hobby for more executions.",
            )

    return True, ""


async def start_trial(
    session: AsyncSession,
    subscription: Subscription,
    trial_ends_at: datetime | None = None,
) -> None:
    """Start a trial period for a subscription"""
    subscription.tier = SubscriptionTier.HOBBY
    subscription.status = SubscriptionStatus.TRIALING
    if trial_ends_at:
        subscription.trial_ends_at = trial_ends_at
    else:
        subscription.trial_ends_at = datetime.now(timezone.utc) + timedelta(
            days=settings.TRIAL_PERIOD_DAYS
        )
    await session.flush()
    logger.info(
        "Started trial period",
        subscription_id=subscription.id,
        trial_ends_at=subscription.trial_ends_at,
    )


async def activate_pro_subscription(
    session: AsyncSession,
    subscription: Subscription,
    stripe_subscription_id: str,
    current_period_end: datetime,
) -> None:
    """Activate a hobby subscription after successful payment"""
    subscription.tier = SubscriptionTier.HOBBY
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.stripe_subscription_id = stripe_subscription_id
    subscription.current_period_end = current_period_end
    subscription.trial_ends_at = None
    subscription.grace_period_ends_at = None
    await session.flush()
    logger.info(
        "Activated hobby subscription",
        subscription_id=subscription.id,
        stripe_subscription_id=stripe_subscription_id,
    )


async def cancel_subscription(
    session: AsyncSession, subscription: Subscription, immediate: bool = False
) -> None:
    """Cancel a subscription"""
    if immediate:
        subscription.tier = SubscriptionTier.FREE
        subscription.status = SubscriptionStatus.CANCELED
        subscription.stripe_subscription_id = None
        subscription.current_period_end = None
        subscription.grace_period_ends_at = None
    else:
        subscription.cancel_at_period_end = True

    await session.flush()
    logger.info(
        "Canceled subscription",
        subscription_id=subscription.id,
        immediate=immediate,
    )


async def start_grace_period(session: AsyncSession, subscription: Subscription) -> None:
    """Start grace period after failed payment"""
    subscription.status = SubscriptionStatus.PAST_DUE
    subscription.grace_period_ends_at = datetime.now(timezone.utc) + timedelta(
        days=settings.GRACE_PERIOD_DAYS
    )
    await session.flush()
    logger.warning(
        "Payment failed, started grace period",
        subscription_id=subscription.id,
        grace_period_ends_at=subscription.grace_period_ends_at,
    )


async def downgrade_to_free(session: AsyncSession, subscription: Subscription) -> None:
    """Downgrade a subscription to free tier"""
    subscription.tier = SubscriptionTier.FREE
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.stripe_subscription_id = None
    subscription.current_period_end = None
    subscription.trial_ends_at = None
    subscription.grace_period_ends_at = None
    subscription.cancel_at_period_end = False
    await session.flush()
    logger.info("Downgraded subscription to free tier", subscription_id=subscription.id)


# Webhook event handlers


async def _is_duplicate_event(session: AsyncSession, stripe_event_id: str) -> bool:
    """Check if we've already processed this Stripe event"""
    result = await session.execute(
        select(BillingEvent).where(BillingEvent.stripe_event_id == stripe_event_id)
    )
    return result.scalar_one_or_none() is not None


async def handle_checkout_completed(
    session: AsyncSession,
    event_data: dict,
    stripe_event_id: str,
) -> None:
    """Handle successful checkout session completion"""
    if await _is_duplicate_event(session, stripe_event_id):
        logger.info("Duplicate event, skipping", stripe_event_id=stripe_event_id)
        return

    subscription_id_str = event_data.get("metadata", {}).get("subscription_id")

    if not subscription_id_str:
        logger.warning("No subscription_id in checkout session metadata")
        return

    try:
        subscription_id = UUID(subscription_id_str)
    except (ValueError, TypeError) as e:
        logger.error(
            "Invalid subscription_id format",
            subscription_id=subscription_id_str,
            error=str(e),
        )
        return

    result = await session.execute(
        select(Subscription).where(Subscription.id == subscription_id)
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        logger.error("Subscription not found", subscription_id=subscription_id)
        return

    stripe_customer_id = event_data.get("customer")
    if stripe_customer_id and not subscription.stripe_customer_id:
        subscription.stripe_customer_id = stripe_customer_id

    billing_event = BillingEvent(
        subscription_id=subscription.id,
        event_type="checkout.session.completed",
        stripe_event_id=stripe_event_id,
        payload=event_data,
    )
    session.add(billing_event)
    await session.flush()

    logger.info(
        "Checkout session completed",
        subscription_id=subscription.id,
        customer_id=stripe_customer_id,
    )


async def handle_subscription_created(
    session: AsyncSession,
    event_data: dict,
    stripe_event_id: str,
) -> None:
    """Handle subscription creation"""
    if await _is_duplicate_event(session, stripe_event_id):
        logger.info("Duplicate event, skipping", stripe_event_id=stripe_event_id)
        return

    subscription_id_str = event_data.get("metadata", {}).get("subscription_id")

    if not subscription_id_str:
        logger.warning("No subscription_id in subscription metadata")
        return

    try:
        subscription_id = UUID(subscription_id_str)
    except (ValueError, TypeError) as e:
        logger.error(
            "Invalid subscription_id format",
            subscription_id=subscription_id_str,
            error=str(e),
        )
        return

    result = await session.execute(
        select(Subscription).where(Subscription.id == subscription_id)
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        logger.error("Subscription not found", subscription_id=subscription_id)
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
    elif stripe_status in ["active", "past_due"]:
        await activate_pro_subscription(
            session, subscription, stripe_subscription_id, current_period_end
        )

    billing_event = BillingEvent(
        subscription_id=subscription.id,
        event_type="customer.subscription.created",
        stripe_event_id=stripe_event_id,
        payload=event_data,
    )
    session.add(billing_event)
    await session.flush()

    logger.info(
        "Subscription created",
        subscription_id=subscription.id,
        stripe_subscription_id=stripe_subscription_id,
        status=stripe_status,
    )


async def handle_subscription_updated(
    session: AsyncSession,
    event_data: dict,
    stripe_event_id: str,
) -> None:
    """Handle subscription updates"""
    if await _is_duplicate_event(session, stripe_event_id):
        logger.info("Duplicate event, skipping", stripe_event_id=stripe_event_id)
        return

    stripe_subscription_id = event_data.get("id")

    result = await session.execute(
        select(Subscription).where(
            Subscription.stripe_subscription_id == stripe_subscription_id
        )
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        logger.warning(
            "Subscription not found for update",
            stripe_subscription_id=stripe_subscription_id,
        )
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

    billing_event = BillingEvent(
        subscription_id=subscription.id,
        event_type="customer.subscription.updated",
        stripe_event_id=stripe_event_id,
        payload=event_data,
    )
    session.add(billing_event)
    await session.flush()

    logger.info(
        "Subscription updated",
        subscription_id=subscription.id,
        status=stripe_status,
        cancel_at_period_end=cancel_at_period_end,
    )


async def handle_subscription_deleted(
    session: AsyncSession,
    event_data: dict,
    stripe_event_id: str,
) -> None:
    """Handle subscription deletion/cancellation"""
    if await _is_duplicate_event(session, stripe_event_id):
        logger.info("Duplicate event, skipping", stripe_event_id=stripe_event_id)
        return

    stripe_subscription_id = event_data.get("id")

    result = await session.execute(
        select(Subscription).where(
            Subscription.stripe_subscription_id == stripe_subscription_id
        )
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        logger.warning(
            "Subscription not found for deletion",
            stripe_subscription_id=stripe_subscription_id,
        )
        return

    await downgrade_to_free(session, subscription)

    billing_event = BillingEvent(
        subscription_id=subscription.id,
        event_type="customer.subscription.deleted",
        stripe_event_id=stripe_event_id,
        payload=event_data,
    )
    session.add(billing_event)
    await session.flush()

    logger.info(
        "Subscription deleted",
        subscription_id=subscription.id,
    )


async def handle_payment_failed_event(
    session: AsyncSession,
    event_data: dict,
    stripe_event_id: str,
) -> None:
    """Handle failed payment webhook event"""
    if await _is_duplicate_event(session, stripe_event_id):
        logger.info("Duplicate event, skipping", stripe_event_id=stripe_event_id)
        return

    stripe_subscription_id = event_data.get("subscription")

    if not stripe_subscription_id:
        logger.info("Payment failed event without subscription")
        return

    result = await session.execute(
        select(Subscription).where(
            Subscription.stripe_subscription_id == stripe_subscription_id
        )
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        logger.warning(
            "Subscription not found for payment failure",
            stripe_subscription_id=stripe_subscription_id,
        )
        return

    await start_grace_period(session, subscription)

    billing_event = BillingEvent(
        subscription_id=subscription.id,
        event_type="invoice.payment_failed",
        stripe_event_id=stripe_event_id,
        payload=event_data,
    )
    session.add(billing_event)
    await session.flush()

    logger.warning(
        "Payment failed",
        subscription_id=subscription.id,
    )


async def handle_payment_succeeded_event(
    session: AsyncSession,
    event_data: dict,
    stripe_event_id: str,
) -> None:
    """Handle successful payment webhook event"""
    if await _is_duplicate_event(session, stripe_event_id):
        logger.info("Duplicate event, skipping", stripe_event_id=stripe_event_id)
        return

    stripe_subscription_id = event_data.get("subscription")

    if not stripe_subscription_id:
        logger.info("Payment succeeded event without subscription")
        return

    result = await session.execute(
        select(Subscription).where(
            Subscription.stripe_subscription_id == stripe_subscription_id
        )
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        logger.warning(
            "Subscription not found for payment success",
            stripe_subscription_id=stripe_subscription_id,
        )
        return

    if subscription.status == SubscriptionStatus.PAST_DUE:
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.grace_period_ends_at = None
        logger.info(
            "Subscription reactivated after payment",
            subscription_id=subscription.id,
        )

    billing_event = BillingEvent(
        subscription_id=subscription.id,
        event_type="invoice.payment_succeeded",
        stripe_event_id=stripe_event_id,
        payload=event_data,
    )
    session.add(billing_event)
    await session.flush()

    logger.info(
        "Payment succeeded",
        subscription_id=subscription.id,
    )
