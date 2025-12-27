from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Organization,
    OrganizationMember,
    Subscription,
    SubscriptionStatus,
    SubscriptionTier,
)
from app.services.user_service import get_or_create_user
from app.settings import settings


async def _get_user_organization(session: AsyncSession, user) -> Organization:
    """Get the user's first organization."""
    org_query = (
        select(Organization)
        .join(OrganizationMember)
        .where(OrganizationMember.user_id == user.id)
        .order_by(OrganizationMember.created_at)
        .limit(1)
    )
    org_result = await session.execute(org_query)
    return org_result.scalar_one()


class TestGetOrganizationSubscription:
    """Tests for GET /api/organizations/{organization_id}/subscription"""

    async def test_get_subscription_free_tier(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
    ):
        """Returns subscription with tier=free, has_active_pro=false"""
        user = await get_or_create_user(session, f"test_free_{uuid4()}", create=False)
        await session.flush()

        organization = await _get_user_organization(session, user)

        subscription = Subscription(
            organization_id=organization.id,
            tier=SubscriptionTier.FREE,
            status=SubscriptionStatus.ACTIVE,
        )
        session.add(subscription)
        await session.flush()

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        response = await client.get(
            f"/api/organizations/{organization.id}/subscription"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["tier"] == "free"
        assert data["status"] == "active"
        assert data["has_active_pro"] is False

    async def test_get_subscription_hobby_tier(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
    ):
        """Returns subscription with tier=hobby, has_active_pro=true"""
        user = await get_or_create_user(session, f"test_hobby_{uuid4()}", create=False)
        await session.flush()

        organization = await _get_user_organization(session, user)

        subscription = Subscription(
            organization_id=organization.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.ACTIVE,
            stripe_customer_id="cus_test",
            stripe_subscription_id="sub_test",
        )
        session.add(subscription)
        await session.flush()

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        response = await client.get(
            f"/api/organizations/{organization.id}/subscription"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["tier"] == "hobby"
        assert data["status"] == "active"
        assert data["has_active_pro"] is True

    async def test_get_subscription_trialing(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
    ):
        """Returns subscription with status=trialing, trial_ends_at"""
        user = await get_or_create_user(session, f"test_trial_{uuid4()}", create=False)
        await session.flush()

        organization = await _get_user_organization(session, user)

        trial_ends_at = datetime.now(timezone.utc) + timedelta(days=14)

        subscription = Subscription(
            organization_id=organization.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.TRIALING,
            trial_ends_at=trial_ends_at,
        )
        session.add(subscription)
        await session.flush()

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        response = await client.get(
            f"/api/organizations/{organization.id}/subscription"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "trialing"
        assert data["trial_ends_at"] is not None

    async def test_get_subscription_grace_period(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
    ):
        """Returns subscription with grace_period_ends_at"""
        user = await get_or_create_user(session, f"test_grace_{uuid4()}", create=False)
        await session.flush()

        organization = await _get_user_organization(session, user)

        grace_period_ends_at = datetime.now(timezone.utc) + timedelta(days=7)

        subscription = Subscription(
            organization_id=organization.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.PAST_DUE,
            grace_period_ends_at=grace_period_ends_at,
        )
        session.add(subscription)
        await session.flush()

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        response = await client.get(
            f"/api/organizations/{organization.id}/subscription"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "past_due"
        assert data["grace_period_ends_at"] is not None

    async def test_get_subscription_not_cloud(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
    ):
        """Returns 404 when IS_CLOUD=False"""
        user = await get_or_create_user(
            session, f"test_not_cloud_{uuid4()}", create=False
        )
        await session.flush()

        organization = await _get_user_organization(session, user)

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        with patch.object(settings, "IS_CLOUD", False):
            response = await client.get(
                f"/api/organizations/{organization.id}/subscription"
            )

        assert response.status_code == 404

    async def test_get_subscription_unauthorized(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
    ):
        """Returns 404 when user doesn't have access to org"""
        user1 = await get_or_create_user(session, f"test_user1_{uuid4()}", create=False)
        user2 = await get_or_create_user(session, f"test_user2_{uuid4()}", create=False)
        await session.flush()

        # Get user1's organization
        organization = await _get_user_organization(session, user1)

        # Try to access with user2's auth
        client.headers["Authorization"] = f"Bearer {user2.workos_user_id}"

        response = await client.get(
            f"/api/organizations/{organization.id}/subscription"
        )

        assert response.status_code == 404


class TestGetOrganizationUsage:
    """Tests for GET /api/organizations/{organization_id}/usage"""

    async def test_get_usage_free_tier(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
    ):
        """Returns correct workflows/executions counts and limits for free tier"""
        user = await get_or_create_user(
            session, f"test_usage_free_{uuid4()}", create=False
        )
        await session.flush()

        organization = await _get_user_organization(session, user)

        subscription = Subscription(
            organization_id=organization.id,
            tier=SubscriptionTier.FREE,
            status=SubscriptionStatus.ACTIVE,
        )
        session.add(subscription)
        await session.flush()

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        response = await client.get(f"/api/organizations/{organization.id}/usage")

        assert response.status_code == 200
        data = response.json()
        assert data["workflows_limit"] == 3
        assert data["executions_limit"] == 100

    async def test_get_usage_hobby_tier(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
    ):
        """Returns correct limits for hobby tier"""
        user = await get_or_create_user(
            session, f"test_usage_hobby_{uuid4()}", create=False
        )
        await session.flush()

        organization = await _get_user_organization(session, user)

        subscription = Subscription(
            organization_id=organization.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.ACTIVE,
        )
        session.add(subscription)
        await session.flush()

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        response = await client.get(f"/api/organizations/{organization.id}/usage")

        assert response.status_code == 200
        data = response.json()
        assert data["workflows_limit"] == 100
        assert data["executions_limit"] == 10_000

    async def test_get_usage_not_cloud(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
    ):
        """Returns 404 when IS_CLOUD=False"""
        user = await get_or_create_user(
            session, f"test_not_cloud_{uuid4()}", create=False
        )
        await session.flush()

        organization = await _get_user_organization(session, user)

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        with patch.object(settings, "IS_CLOUD", False):
            response = await client.get(f"/api/organizations/{organization.id}/usage")

        assert response.status_code == 404


class TestCreateCheckoutSession:
    """Tests for POST /api/organizations/{organization_id}/checkout"""

    async def test_create_checkout_session_success(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
        mock_stripe_customer,
        mock_stripe_checkout,
    ):
        """Returns session_id and URL"""
        user = await get_or_create_user(
            session, f"test_checkout_{uuid4()}", create=False
        )
        await session.flush()

        organization = await _get_user_organization(session, user)

        subscription = Subscription(
            organization_id=organization.id,
            tier=SubscriptionTier.FREE,
            status=SubscriptionStatus.ACTIVE,
        )
        session.add(subscription)
        await session.flush()

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        mock_stripe_customer.return_value = MagicMock(id="cus_test_12345")
        mock_stripe_checkout.return_value = MagicMock(
            id="cs_test_12345",
            url="https://checkout.stripe.com/test",
        )

        with patch.object(settings, "STRIPE_PRICE_ID_HOBBY", "price_test_pro"):
            response = await client.post(
                f"/api/organizations/{organization.id}/checkout",
                json={
                    "success_url": "https://example.com/success",
                    "cancel_url": "https://example.com/cancel",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "cs_test_12345"
        assert data["url"] == "https://checkout.stripe.com/test"

    async def test_create_checkout_session_already_subscribed(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
    ):
        """Returns 400 when organization already has active subscription"""
        user = await get_or_create_user(
            session, f"test_already_subscribed_{uuid4()}", create=False
        )
        await session.flush()

        organization = await _get_user_organization(session, user)

        subscription = Subscription(
            organization_id=organization.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.ACTIVE,
        )
        session.add(subscription)
        await session.flush()

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        response = await client.post(
            f"/api/organizations/{organization.id}/checkout",
            json={
                "success_url": "https://example.com/success",
                "cancel_url": "https://example.com/cancel",
            },
        )

        assert response.status_code == 400
        assert "already has an active subscription" in response.json()["detail"]

    async def test_create_checkout_session_not_cloud(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
    ):
        """Returns 404 when IS_CLOUD=False"""
        user = await get_or_create_user(
            session, f"test_not_cloud_{uuid4()}", create=False
        )
        await session.flush()

        organization = await _get_user_organization(session, user)

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        with patch.object(settings, "IS_CLOUD", False):
            response = await client.post(
                f"/api/organizations/{organization.id}/checkout",
                json={
                    "success_url": "https://example.com/success",
                    "cancel_url": "https://example.com/cancel",
                },
            )

        assert response.status_code == 404

    async def test_create_checkout_session_stripe_error(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
        mock_stripe_customer,
    ):
        """Returns 500 when Stripe API fails"""
        user = await get_or_create_user(
            session, f"test_stripe_error_{uuid4()}", create=False
        )
        await session.flush()

        organization = await _get_user_organization(session, user)

        subscription = Subscription(
            organization_id=organization.id,
            tier=SubscriptionTier.FREE,
            status=SubscriptionStatus.ACTIVE,
        )
        session.add(subscription)
        await session.flush()

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        mock_stripe_customer.side_effect = Exception("Stripe API error")

        with patch.object(settings, "STRIPE_PRICE_ID_HOBBY", "price_test_pro"):
            response = await client.post(
                f"/api/organizations/{organization.id}/checkout",
                json={
                    "success_url": "https://example.com/success",
                    "cancel_url": "https://example.com/cancel",
                },
            )

        assert response.status_code == 500


class TestCreatePortalSession:
    """Tests for POST /api/organizations/{organization_id}/portal"""

    async def test_create_portal_session_success(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
        mock_stripe_portal,
    ):
        """Returns portal URL"""
        user = await get_or_create_user(session, f"test_portal_{uuid4()}", create=False)
        await session.flush()

        organization = await _get_user_organization(session, user)

        subscription = Subscription(
            organization_id=organization.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.ACTIVE,
            stripe_customer_id="cus_test_12345",
        )
        session.add(subscription)
        await session.flush()

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        mock_stripe_portal.return_value = MagicMock(
            url="https://billing.stripe.com/test",
        )

        response = await client.post(
            f"/api/organizations/{organization.id}/portal",
            json={"return_url": "https://example.com/return"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["url"] == "https://billing.stripe.com/test"

    async def test_create_portal_session_no_customer(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
    ):
        """Returns 400 when no stripe_customer_id"""
        user = await get_or_create_user(
            session, f"test_no_customer_{uuid4()}", create=False
        )
        await session.flush()

        organization = await _get_user_organization(session, user)

        subscription = Subscription(
            organization_id=organization.id,
            tier=SubscriptionTier.FREE,
            status=SubscriptionStatus.ACTIVE,
        )
        session.add(subscription)
        await session.flush()

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        response = await client.post(
            f"/api/organizations/{organization.id}/portal",
            json={"return_url": "https://example.com/return"},
        )

        assert response.status_code == 400
        assert "No Stripe customer found" in response.json()["detail"]

    async def test_create_portal_session_not_cloud(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
    ):
        """Returns 404 when IS_CLOUD=False"""
        user = await get_or_create_user(
            session, f"test_not_cloud_{uuid4()}", create=False
        )
        await session.flush()

        organization = await _get_user_organization(session, user)

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        with patch.object(settings, "IS_CLOUD", False):
            response = await client.post(
                f"/api/organizations/{organization.id}/portal",
                json={"return_url": "https://example.com/return"},
            )

        assert response.status_code == 404
