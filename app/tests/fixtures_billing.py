from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Subscription, SubscriptionStatus, SubscriptionTier, User
from app.services.user_service import get_or_create_user
from app.settings import settings


@pytest.fixture(autouse=True)
def enable_cloud_mode():
    """Enable cloud mode and Stripe configuration for all billing tests"""
    with (
        patch.object(settings, "IS_CLOUD", True),
        patch.object(settings, "STRIPE_SECRET_KEY", "sk_test_fake"),
        patch.object(settings, "STRIPE_WEBHOOK_SECRET", "whsec_fake"),
    ):
        yield


@pytest.fixture
async def test_user(session: AsyncSession) -> User:
    """Create a test user without a subscription"""
    user = await get_or_create_user(
        session, f"test_billing_user_{uuid4()}", create=False
    )
    await session.flush()
    return user


@pytest.fixture
async def test_user_with_free_subscription(
    session: AsyncSession,
) -> tuple[User, Subscription]:
    """Create a test user with a FREE tier subscription"""
    user = await get_or_create_user(session, f"test_free_user_{uuid4()}", create=False)
    await session.flush()

    subscription = Subscription(
        user_id=user.id,
        tier=SubscriptionTier.FREE,
        status=SubscriptionStatus.ACTIVE,
    )
    session.add(subscription)
    await session.flush()

    return user, subscription


@pytest.fixture
async def test_user_with_pro_subscription(
    session: AsyncSession,
) -> tuple[User, Subscription]:
    """Create a test user with an active HOBBY subscription"""
    user = await get_or_create_user(session, f"test_pro_user_{uuid4()}", create=False)
    await session.flush()

    subscription = Subscription(
        user_id=user.id,
        tier=SubscriptionTier.HOBBY,
        status=SubscriptionStatus.ACTIVE,
        stripe_customer_id=f"cus_test_{uuid4().hex[:8]}",
        stripe_subscription_id=f"sub_test_{uuid4().hex[:8]}",
        current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
    )
    session.add(subscription)
    await session.flush()

    return user, subscription


@pytest.fixture
async def test_user_in_trial(session: AsyncSession) -> tuple[User, Subscription]:
    """Create a test user in trial period"""
    user = await get_or_create_user(session, f"test_trial_user_{uuid4()}", create=False)
    await session.flush()

    subscription = Subscription(
        user_id=user.id,
        tier=SubscriptionTier.HOBBY,
        status=SubscriptionStatus.TRIALING,
        stripe_customer_id=f"cus_test_{uuid4().hex[:8]}",
        stripe_subscription_id=f"sub_test_{uuid4().hex[:8]}",
        trial_ends_at=datetime.now(timezone.utc) + timedelta(days=14),
        current_period_end=datetime.now(timezone.utc) + timedelta(days=14),
    )
    session.add(subscription)
    await session.flush()

    return user, subscription


@pytest.fixture
async def test_user_trial_expired(session: AsyncSession) -> tuple[User, Subscription]:
    """Create a test user with expired trial"""
    user = await get_or_create_user(
        session, f"test_expired_trial_user_{uuid4()}", create=False
    )
    await session.flush()

    subscription = Subscription(
        user_id=user.id,
        tier=SubscriptionTier.HOBBY,
        status=SubscriptionStatus.TRIALING,
        stripe_customer_id=f"cus_test_{uuid4().hex[:8]}",
        stripe_subscription_id=f"sub_test_{uuid4().hex[:8]}",
        trial_ends_at=datetime.now(timezone.utc) - timedelta(days=1),
        current_period_end=datetime.now(timezone.utc) - timedelta(days=1),
    )
    session.add(subscription)
    await session.flush()

    return user, subscription


@pytest.fixture
async def test_user_in_grace_period(session: AsyncSession) -> tuple[User, Subscription]:
    """Create a test user in grace period (PAST_DUE)"""
    user = await get_or_create_user(session, f"test_grace_user_{uuid4()}", create=False)
    await session.flush()

    subscription = Subscription(
        user_id=user.id,
        tier=SubscriptionTier.HOBBY,
        status=SubscriptionStatus.PAST_DUE,
        stripe_customer_id=f"cus_test_{uuid4().hex[:8]}",
        stripe_subscription_id=f"sub_test_{uuid4().hex[:8]}",
        grace_period_ends_at=datetime.now(timezone.utc) + timedelta(days=7),
        current_period_end=datetime.now(timezone.utc) - timedelta(days=1),
    )
    session.add(subscription)
    await session.flush()

    return user, subscription


@pytest.fixture
async def test_user_grace_expired(session: AsyncSession) -> tuple[User, Subscription]:
    """Create a test user with expired grace period"""
    user = await get_or_create_user(
        session, f"test_grace_expired_user_{uuid4()}", create=False
    )
    await session.flush()

    subscription = Subscription(
        user_id=user.id,
        tier=SubscriptionTier.HOBBY,
        status=SubscriptionStatus.PAST_DUE,
        stripe_customer_id=f"cus_test_{uuid4().hex[:8]}",
        stripe_subscription_id=f"sub_test_{uuid4().hex[:8]}",
        grace_period_ends_at=datetime.now(timezone.utc) - timedelta(days=1),
        current_period_end=datetime.now(timezone.utc) - timedelta(days=8),
    )
    session.add(subscription)
    await session.flush()

    return user, subscription


@pytest.fixture
def mock_stripe_customer():
    """Mock Stripe customer creation"""
    import stripe

    with (
        patch("app.services.stripe_service.stripe_client", stripe),
        patch("stripe.Customer.create") as mock,
    ):
        mock.return_value = MagicMock(id="cus_test_12345")
        yield mock


@pytest.fixture
def mock_stripe_checkout():
    """Mock Stripe checkout session creation"""
    import stripe

    with (
        patch("app.services.stripe_service.stripe_client", stripe),
        patch("stripe.checkout.Session.create") as mock,
    ):
        mock.return_value = MagicMock(
            id="cs_test_12345",
            url="https://checkout.stripe.com/test",
        )
        yield mock


@pytest.fixture
def mock_stripe_portal():
    """Mock Stripe customer portal session creation"""
    import stripe

    with (
        patch("app.services.stripe_service.stripe_client", stripe),
        patch("stripe.billing_portal.Session.create") as mock,
    ):
        mock.return_value = MagicMock(
            url="https://billing.stripe.com/test",
        )
        yield mock


@pytest.fixture
def mock_stripe_webhook_event():
    """Mock Stripe webhook event construction"""
    import stripe

    with (
        patch("app.services.stripe_service.stripe_client", stripe),
        patch("stripe.Webhook.construct_event") as mock,
    ):
        yield mock


@pytest.fixture
def mock_stripe_subscription():
    """Mock Stripe subscription object"""
    return MagicMock(
        id="sub_test_12345",
        customer="cus_test_12345",
        status="active",
        current_period_end=1234567890,
        metadata={},
        cancel_at_period_end=False,
    )
