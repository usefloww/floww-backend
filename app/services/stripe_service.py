import structlog

from app.models import Subscription, User, SubscriptionTier
from app.settings import settings

logger = structlog.stdlib.get_logger(__name__)

stripe_client = None
try:
    import stripe

    if settings.STRIPE_SECRET_KEY:
        stripe.api_key = settings.STRIPE_SECRET_KEY
        stripe_client = stripe
except ImportError:
    logger.debug("Stripe SDK not installed - billing features will not be available")


async def get_or_create_customer(user: User, subscription: Subscription) -> str:
    """
    Get or create a Stripe customer for a user.
    returns:
        str: Stripe customer ID
    """
    if not stripe_client:
        raise ValueError("Stripe is not configured")

    if subscription.stripe_customer_id:
        return subscription.stripe_customer_id

    customer = stripe_client.Customer.create(
        email=user.email,
        name=f"{user.first_name or ''} {user.last_name or ''}".strip() or user.email,
        metadata={
            "user_id": str(user.id),
            "subscription_id": str(subscription.id),
        },
    )

    logger.info(
        "Created Stripe customer",
        user_id=user.id,
        customer_id=customer.id,
    )

    return customer.id


async def create_checkout_session(
    user: User,
    subscription: Subscription,
    success_url: str,
    cancel_url: str,
    session_db=None,
) -> dict:
    """
    Create a Stripe Checkout session for upgrading to Pro.
    Returns the session data including the checkout URL.

    If session_db is provided, updates the subscription with the customer_id.
    """
    if not stripe_client:
        raise ValueError("Stripe is not configured")

    if not settings.STRIPE_PRICE_ID_PRO:
        raise ValueError("STRIPE_PRICE_ID_PRO is not configured")

    customer_id = await get_or_create_customer(user, subscription)

    # Update subscription with customer_id if not already set
    if not subscription.stripe_customer_id:
        subscription.stripe_customer_id = customer_id
        if session_db:
            await session_db.flush()

    has_trial = (
        subscription.tier != SubscriptionTier.HOBBY
        and subscription.trial_ends_at is None
        and settings.TRIAL_PERIOD_DAYS > 0
    )

    session_params = {
        "customer": customer_id,
        "mode": "subscription",
        "line_items": [
            {
                "price": settings.STRIPE_PRICE_ID_PRO,
                "quantity": 1,
            }
        ],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata": {
            "user_id": str(user.id),
            "subscription_id": str(subscription.id),
        },
        "subscription_data": {
            "metadata": {
                "user_id": str(user.id),
                "subscription_id": str(subscription.id),
            },
        },
    }

    if has_trial:
        session_params["subscription_data"]["trial_period_days"] = (
            settings.TRIAL_PERIOD_DAYS
        )

    session = stripe_client.checkout.Session.create(**session_params)

    logger.info(
        "Created checkout session",
        user_id=user.id,
        session_id=session.id,
        has_trial=has_trial,
    )

    return {
        "session_id": session.id,
        "url": session.url,
    }


async def create_customer_portal_session(
    subscription: Subscription,
    return_url: str,
) -> dict:
    """
    Create a Stripe Customer Portal session for managing subscription.
    Returns the portal URL.
    """
    if not stripe_client:
        raise ValueError("Stripe is not configured")

    if not subscription.stripe_customer_id:
        raise ValueError("No Stripe customer ID found for this subscription")

    session = stripe_client.billing_portal.Session.create(
        customer=subscription.stripe_customer_id,
        return_url=return_url,
    )

    logger.info(
        "Created customer portal session",
        subscription_id=subscription.id,
        session_id=session.id,
    )

    return {
        "url": session.url,
    }


async def cancel_stripe_subscription(
    subscription: Subscription, immediate: bool = False
) -> None:
    """Cancel a Stripe subscription"""
    if not stripe_client:
        raise ValueError("Stripe is not configured")

    if not subscription.stripe_subscription_id:
        raise ValueError("No Stripe subscription ID found")

    if immediate:
        stripe_client.Subscription.delete(subscription.stripe_subscription_id)
        logger.info(
            "Immediately canceled Stripe subscription",
            subscription_id=subscription.id,
            stripe_subscription_id=subscription.stripe_subscription_id,
        )
    else:
        stripe_client.Subscription.modify(
            subscription.stripe_subscription_id,
            cancel_at_period_end=True,
        )
        logger.info(
            "Scheduled Stripe subscription for cancellation at period end",
            subscription_id=subscription.id,
            stripe_subscription_id=subscription.stripe_subscription_id,
        )


def construct_webhook_event(payload: bytes, sig_header: str):
    """
    Construct and verify a Stripe webhook event.
    Raises an exception if the signature is invalid.
    """
    if not stripe_client:
        raise ValueError("Stripe is not configured")

    if not settings.STRIPE_WEBHOOK_SECRET:
        raise ValueError("STRIPE_WEBHOOK_SECRET is not configured")

    try:
        event = stripe_client.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
        return event
    except ValueError as e:
        logger.error("Invalid payload", error=str(e))
        raise
    except stripe.SignatureVerificationError as e:
        logger.error("Invalid signature", error=str(e))
        raise
