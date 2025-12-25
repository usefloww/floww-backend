from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Namespace,
    Organization,
    OrganizationMember,
    Subscription,
    SubscriptionStatus,
    SubscriptionTier,
    User,
)
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


async def _create_test_org_with_user(
    session: AsyncSession, name_prefix: str
) -> tuple[User, Organization, Namespace]:
    """Helper to create a user with an organization and namespace."""
    user = await get_or_create_user(session, f"{name_prefix}_{uuid4()}", create=False)
    await session.flush()

    # The user creation already creates an org and namespace, so fetch them
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    org_query = (
        select(Organization)
        .join(OrganizationMember)
        .where(OrganizationMember.user_id == user.id)
        .order_by(OrganizationMember.created_at)
        .limit(1)
    )
    org_result = await session.execute(org_query)
    organization = org_result.scalar_one()

    namespace_query = (
        select(Namespace)
        .options(selectinload(Namespace.organization_owner))
        .where(Namespace.organization_owner_id == organization.id)
    )
    namespace_result = await session.execute(namespace_query)
    namespace = namespace_result.scalar_one()

    return user, organization, namespace


@pytest.fixture
async def test_user(session: AsyncSession) -> User:
    """Create a test user without a subscription"""
    user, _, _ = await _create_test_org_with_user(session, "test_billing_user")
    return user


@pytest.fixture
async def test_org(session: AsyncSession) -> tuple[User, Organization, Namespace]:
    """Create a test organization with user and namespace"""
    return await _create_test_org_with_user(session, "test_org")


@pytest.fixture
async def test_org_with_free_subscription(
    session: AsyncSession,
) -> tuple[Organization, Subscription, Namespace]:
    """Create a test organization with a FREE tier subscription"""
    _, organization, namespace = await _create_test_org_with_user(
        session, "test_free_org"
    )

    subscription = Subscription(
        organization_id=organization.id,
        tier=SubscriptionTier.FREE,
        status=SubscriptionStatus.ACTIVE,
    )
    session.add(subscription)
    await session.flush()

    return organization, subscription, namespace


@pytest.fixture
async def test_org_with_pro_subscription(
    session: AsyncSession,
) -> tuple[Organization, Subscription, Namespace]:
    """Create a test organization with an active HOBBY subscription"""
    _, organization, namespace = await _create_test_org_with_user(
        session, "test_pro_org"
    )

    subscription = Subscription(
        organization_id=organization.id,
        tier=SubscriptionTier.HOBBY,
        status=SubscriptionStatus.ACTIVE,
        stripe_customer_id=f"cus_test_{uuid4().hex[:8]}",
        stripe_subscription_id=f"sub_test_{uuid4().hex[:8]}",
        current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
    )
    session.add(subscription)
    await session.flush()

    return organization, subscription, namespace


@pytest.fixture
async def test_org_in_trial(
    session: AsyncSession,
) -> tuple[Organization, Subscription, Namespace]:
    """Create a test organization in trial period"""
    _, organization, namespace = await _create_test_org_with_user(
        session, "test_trial_org"
    )

    subscription = Subscription(
        organization_id=organization.id,
        tier=SubscriptionTier.HOBBY,
        status=SubscriptionStatus.TRIALING,
        stripe_customer_id=f"cus_test_{uuid4().hex[:8]}",
        stripe_subscription_id=f"sub_test_{uuid4().hex[:8]}",
        trial_ends_at=datetime.now(timezone.utc) + timedelta(days=14),
        current_period_end=datetime.now(timezone.utc) + timedelta(days=14),
    )
    session.add(subscription)
    await session.flush()

    return organization, subscription, namespace


@pytest.fixture
async def test_org_trial_expired(
    session: AsyncSession,
) -> tuple[Organization, Subscription, Namespace]:
    """Create a test organization with expired trial"""
    _, organization, namespace = await _create_test_org_with_user(
        session, "test_expired_trial_org"
    )

    subscription = Subscription(
        organization_id=organization.id,
        tier=SubscriptionTier.HOBBY,
        status=SubscriptionStatus.TRIALING,
        stripe_customer_id=f"cus_test_{uuid4().hex[:8]}",
        stripe_subscription_id=f"sub_test_{uuid4().hex[:8]}",
        trial_ends_at=datetime.now(timezone.utc) - timedelta(days=1),
        current_period_end=datetime.now(timezone.utc) - timedelta(days=1),
    )
    session.add(subscription)
    await session.flush()

    return organization, subscription, namespace


@pytest.fixture
async def test_org_in_grace_period(
    session: AsyncSession,
) -> tuple[Organization, Subscription, Namespace]:
    """Create a test organization in grace period (PAST_DUE)"""
    _, organization, namespace = await _create_test_org_with_user(
        session, "test_grace_org"
    )

    subscription = Subscription(
        organization_id=organization.id,
        tier=SubscriptionTier.HOBBY,
        status=SubscriptionStatus.PAST_DUE,
        stripe_customer_id=f"cus_test_{uuid4().hex[:8]}",
        stripe_subscription_id=f"sub_test_{uuid4().hex[:8]}",
        grace_period_ends_at=datetime.now(timezone.utc) + timedelta(days=7),
        current_period_end=datetime.now(timezone.utc) - timedelta(days=1),
    )
    session.add(subscription)
    await session.flush()

    return organization, subscription, namespace


@pytest.fixture
async def test_org_grace_expired(
    session: AsyncSession,
) -> tuple[Organization, Subscription, Namespace]:
    """Create a test organization with expired grace period"""
    _, organization, namespace = await _create_test_org_with_user(
        session, "test_grace_expired_org"
    )

    subscription = Subscription(
        organization_id=organization.id,
        tier=SubscriptionTier.HOBBY,
        status=SubscriptionStatus.PAST_DUE,
        stripe_customer_id=f"cus_test_{uuid4().hex[:8]}",
        stripe_subscription_id=f"sub_test_{uuid4().hex[:8]}",
        grace_period_ends_at=datetime.now(timezone.utc) - timedelta(days=1),
        current_period_end=datetime.now(timezone.utc) - timedelta(days=8),
    )
    session.add(subscription)
    await session.flush()

    return organization, subscription, namespace


# Legacy fixtures for backwards compatibility (return user, subscription tuple)
# These wrap the new org-based fixtures


@pytest.fixture
async def test_user_with_free_subscription(
    session: AsyncSession,
    test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
) -> tuple[Organization, Subscription]:
    """Legacy fixture that returns (organization, subscription) for tests expecting (user, subscription)"""
    organization, subscription, _ = test_org_with_free_subscription
    return organization, subscription


@pytest.fixture
async def test_user_with_pro_subscription(
    session: AsyncSession,
    test_org_with_pro_subscription: tuple[Organization, Subscription, Namespace],
) -> tuple[Organization, Subscription]:
    """Legacy fixture that returns (organization, subscription)"""
    organization, subscription, _ = test_org_with_pro_subscription
    return organization, subscription


@pytest.fixture
async def test_user_in_trial(
    session: AsyncSession,
    test_org_in_trial: tuple[Organization, Subscription, Namespace],
) -> tuple[Organization, Subscription]:
    """Legacy fixture that returns (organization, subscription)"""
    organization, subscription, _ = test_org_in_trial
    return organization, subscription


@pytest.fixture
async def test_user_trial_expired(
    session: AsyncSession,
    test_org_trial_expired: tuple[Organization, Subscription, Namespace],
) -> tuple[Organization, Subscription]:
    """Legacy fixture that returns (organization, subscription)"""
    organization, subscription, _ = test_org_trial_expired
    return organization, subscription


@pytest.fixture
async def test_user_in_grace_period(
    session: AsyncSession,
    test_org_in_grace_period: tuple[Organization, Subscription, Namespace],
) -> tuple[Organization, Subscription]:
    """Legacy fixture that returns (organization, subscription)"""
    organization, subscription, _ = test_org_in_grace_period
    return organization, subscription


@pytest.fixture
async def test_user_grace_expired(
    session: AsyncSession,
    test_org_grace_expired: tuple[Organization, Subscription, Namespace],
) -> tuple[Organization, Subscription]:
    """Legacy fixture that returns (organization, subscription)"""
    organization, subscription, _ = test_org_grace_expired
    return organization, subscription


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
