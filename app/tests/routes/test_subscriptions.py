from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Subscription, SubscriptionStatus, SubscriptionTier
from app.services.user_service import get_or_create_user
from app.settings import settings


class TestGetMySubscription:
    """Tests for GET /api/subscriptions/me"""

    async def test_get_my_subscription_free_tier(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
    ):
        """Returns subscription with tier=free, has_active_pro=false"""
        user = await get_or_create_user(session, f"test_free_{uuid4()}", create=False)
        await session.flush()

        subscription = Subscription(
            user_id=user.id,
            tier=SubscriptionTier.FREE,
            status=SubscriptionStatus.ACTIVE,
        )
        session.add(subscription)
        await session.flush()

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        response = await client.get("/api/subscriptions/me")

        assert response.status_code == 200
        data = response.json()
        assert data["tier"] == "free"
        assert data["status"] == "active"
        assert data["has_active_pro"] is False

    async def test_get_my_subscription_pro_tier(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
    ):
        """Returns subscription with tier=pro, has_active_pro=true"""
        user = await get_or_create_user(session, f"test_pro_{uuid4()}", create=False)
        await session.flush()

        subscription = Subscription(
            user_id=user.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.ACTIVE,
            stripe_customer_id="cus_test",
            stripe_subscription_id="sub_test",
        )
        session.add(subscription)
        await session.flush()

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        response = await client.get("/api/subscriptions/me")

        assert response.status_code == 200
        data = response.json()
        assert data["tier"] == "hobby"
        assert data["status"] == "active"
        assert data["has_active_pro"] is True

    async def test_get_my_subscription_trialing(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
    ):
        """Returns subscription with status=trialing, trial_ends_at"""
        user = await get_or_create_user(session, f"test_trial_{uuid4()}", create=False)
        await session.flush()

        trial_ends_at = datetime.now(timezone.utc) + timedelta(days=14)

        subscription = Subscription(
            user_id=user.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.TRIALING,
            trial_ends_at=trial_ends_at,
        )
        session.add(subscription)
        await session.flush()

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        response = await client.get("/api/subscriptions/me")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "trialing"
        assert data["trial_ends_at"] is not None

    async def test_get_my_subscription_grace_period(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
    ):
        """Returns subscription with grace_period_ends_at"""
        user = await get_or_create_user(session, f"test_grace_{uuid4()}", create=False)
        await session.flush()

        grace_period_ends_at = datetime.now(timezone.utc) + timedelta(days=7)

        subscription = Subscription(
            user_id=user.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.PAST_DUE,
            grace_period_ends_at=grace_period_ends_at,
        )
        session.add(subscription)
        await session.flush()

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        response = await client.get("/api/subscriptions/me")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "past_due"
        assert data["grace_period_ends_at"] is not None

    async def test_get_my_subscription_not_cloud(
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

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        with patch.object(settings, "IS_CLOUD", False):
            response = await client.get("/api/subscriptions/me")

        assert response.status_code == 404

    async def test_get_my_subscription_unauthenticated(
        self,
        client: AsyncClient,
        dependency_overrides,
    ):
        """Returns 401 without auth"""
        response = await client.get("/api/subscriptions/me")

        assert response.status_code == 401


class TestGetMyUsage:
    """Tests for GET /api/subscriptions/usage"""

    async def test_get_my_usage_free_tier(
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

        subscription = Subscription(
            user_id=user.id,
            tier=SubscriptionTier.FREE,
            status=SubscriptionStatus.ACTIVE,
        )
        session.add(subscription)
        await session.flush()

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        response = await client.get("/api/subscriptions/usage")

        assert response.status_code == 200
        data = response.json()
        assert data["workflows_limit"] == settings.FREE_TIER_WORKFLOW_LIMIT
        assert data["executions_limit"] == settings.FREE_TIER_EXECUTION_LIMIT_PER_MONTH

    async def test_get_my_usage_pro_tier(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
    ):
        """Returns correct limits for hobby tier"""
        user = await get_or_create_user(
            session, f"test_usage_pro_{uuid4()}", create=False
        )
        await session.flush()

        subscription = Subscription(
            user_id=user.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.ACTIVE,
        )
        session.add(subscription)
        await session.flush()

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        response = await client.get("/api/subscriptions/usage")

        assert response.status_code == 200
        data = response.json()
        assert data["workflows_limit"] == settings.PRO_TIER_WORKFLOW_LIMIT
        assert data["executions_limit"] == settings.PRO_TIER_EXECUTION_LIMIT_PER_MONTH

    async def test_get_my_usage_not_cloud(
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

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        with patch.object(settings, "IS_CLOUD", False):
            response = await client.get("/api/subscriptions/usage")

        assert response.status_code == 404


class TestCreateCheckoutSession:
    """Tests for POST /api/subscriptions/checkout"""

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

        subscription = Subscription(
            user_id=user.id,
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

        with patch.object(settings, "STRIPE_PRICE_ID_PRO", "price_test_pro"):
            response = await client.post(
                "/api/subscriptions/checkout",
                json={
                    "success_url": "https://example.com/success",
                    "cancel_url": "https://example.com/cancel",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "cs_test_12345"
        assert data["url"] == "https://checkout.stripe.com/test"

    async def test_create_checkout_session_already_pro(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
    ):
        """Returns 400 when user already has active pro"""
        user = await get_or_create_user(
            session, f"test_already_pro_{uuid4()}", create=False
        )
        await session.flush()

        subscription = Subscription(
            user_id=user.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.ACTIVE,
        )
        session.add(subscription)
        await session.flush()

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        response = await client.post(
            "/api/subscriptions/checkout",
            json={
                "success_url": "https://example.com/success",
                "cancel_url": "https://example.com/cancel",
            },
        )

        assert response.status_code == 400
        assert "already have an active Hobby subscription" in response.json()["detail"]

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

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        with patch.object(settings, "IS_CLOUD", False):
            response = await client.post(
                "/api/subscriptions/checkout",
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

        subscription = Subscription(
            user_id=user.id,
            tier=SubscriptionTier.FREE,
            status=SubscriptionStatus.ACTIVE,
        )
        session.add(subscription)
        await session.flush()

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        mock_stripe_customer.side_effect = Exception("Stripe API error")

        with patch.object(settings, "STRIPE_PRICE_ID_PRO", "price_test_pro"):
            response = await client.post(
                "/api/subscriptions/checkout",
                json={
                    "success_url": "https://example.com/success",
                    "cancel_url": "https://example.com/cancel",
                },
            )

        assert response.status_code == 500


class TestCreatePortalSession:
    """Tests for POST /api/subscriptions/portal"""

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

        subscription = Subscription(
            user_id=user.id,
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
            "/api/subscriptions/portal",
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

        subscription = Subscription(
            user_id=user.id,
            tier=SubscriptionTier.FREE,
            status=SubscriptionStatus.ACTIVE,
        )
        session.add(subscription)
        await session.flush()

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        response = await client.post(
            "/api/subscriptions/portal",
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

        client.headers["Authorization"] = f"Bearer {user.workos_user_id}"

        with patch.object(settings, "IS_CLOUD", False):
            response = await client.post(
                "/api/subscriptions/portal",
                json={"return_url": "https://example.com/return"},
            )

        assert response.status_code == 404
