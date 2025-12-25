from datetime import datetime, timezone
from unittest.mock import patch
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


class TestWebhookRoute:
    """Tests for POST /api/billing/webhook"""

    async def test_webhook_valid_checkout_completed(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
        mock_stripe_webhook_event,
    ):
        """Processes checkout.session.completed event and returns 200"""
        user = await get_or_create_user(
            session, f"test_webhook_checkout_{uuid4()}", create=False
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

        mock_event = {
            "id": "evt_test_12345",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "customer": "cus_test_12345",
                    "metadata": {"subscription_id": str(subscription.id)},
                }
            },
        }
        mock_stripe_webhook_event.return_value = mock_event

        with patch.object(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test"):
            response = await client.post(
                "/api/billing/webhook",
                content=b'{"type": "checkout.session.completed"}',
                headers={"stripe-signature": "test_signature"},
            )

        assert response.status_code == 200
        assert response.json()["status"] == "success"

    async def test_webhook_valid_subscription_created(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
        mock_stripe_webhook_event,
    ):
        """Processes customer.subscription.created event and returns 200"""
        user = await get_or_create_user(
            session, f"test_webhook_sub_created_{uuid4()}", create=False
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

        mock_event = {
            "id": "evt_test_12345",
            "type": "customer.subscription.created",
            "data": {
                "object": {
                    "id": "sub_test_12345",
                    "status": "active",
                    "current_period_end": int(datetime.now(timezone.utc).timestamp()),
                    "metadata": {"subscription_id": str(subscription.id)},
                }
            },
        }
        mock_stripe_webhook_event.return_value = mock_event

        with patch.object(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test"):
            response = await client.post(
                "/api/billing/webhook",
                content=b'{"type": "customer.subscription.created"}',
                headers={"stripe-signature": "test_signature"},
            )

        assert response.status_code == 200

    async def test_webhook_valid_subscription_updated(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
        mock_stripe_webhook_event,
    ):
        """Processes customer.subscription.updated event"""
        user = await get_or_create_user(
            session, f"test_webhook_sub_updated_{uuid4()}", create=False
        )
        await session.flush()

        organization = await _get_user_organization(session, user)

        subscription = Subscription(
            organization_id=organization.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.ACTIVE,
            stripe_subscription_id="sub_test_12345",
        )
        session.add(subscription)
        await session.flush()

        mock_event = {
            "id": "evt_test_12345",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_test_12345",
                    "status": "active",
                    "current_period_end": int(datetime.now(timezone.utc).timestamp()),
                    "cancel_at_period_end": False,
                }
            },
        }
        mock_stripe_webhook_event.return_value = mock_event

        with patch.object(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test"):
            response = await client.post(
                "/api/billing/webhook",
                content=b'{"type": "customer.subscription.updated"}',
                headers={"stripe-signature": "test_signature"},
            )

        assert response.status_code == 200

    async def test_webhook_valid_subscription_deleted(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
        mock_stripe_webhook_event,
    ):
        """Processes customer.subscription.deleted event"""
        user = await get_or_create_user(
            session, f"test_webhook_sub_deleted_{uuid4()}", create=False
        )
        await session.flush()

        organization = await _get_user_organization(session, user)

        subscription = Subscription(
            organization_id=organization.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.ACTIVE,
            stripe_subscription_id="sub_test_12345",
        )
        session.add(subscription)
        await session.flush()

        mock_event = {
            "id": "evt_test_12345",
            "type": "customer.subscription.deleted",
            "data": {
                "object": {
                    "id": "sub_test_12345",
                }
            },
        }
        mock_stripe_webhook_event.return_value = mock_event

        with patch.object(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test"):
            response = await client.post(
                "/api/billing/webhook",
                content=b'{"type": "customer.subscription.deleted"}',
                headers={"stripe-signature": "test_signature"},
            )

        assert response.status_code == 200

    async def test_webhook_valid_payment_failed(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
        mock_stripe_webhook_event,
    ):
        """Processes invoice.payment_failed event"""
        user = await get_or_create_user(
            session, f"test_webhook_payment_failed_{uuid4()}", create=False
        )
        await session.flush()

        organization = await _get_user_organization(session, user)

        subscription = Subscription(
            organization_id=organization.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.ACTIVE,
            stripe_subscription_id="sub_test_12345",
        )
        session.add(subscription)
        await session.flush()

        mock_event = {
            "id": "evt_test_12345",
            "type": "invoice.payment_failed",
            "data": {
                "object": {
                    "subscription": "sub_test_12345",
                }
            },
        }
        mock_stripe_webhook_event.return_value = mock_event

        with patch.object(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test"):
            response = await client.post(
                "/api/billing/webhook",
                content=b'{"type": "invoice.payment_failed"}',
                headers={"stripe-signature": "test_signature"},
            )

        assert response.status_code == 200

    async def test_webhook_valid_payment_succeeded(
        self,
        client: AsyncClient,
        session: AsyncSession,
        dependency_overrides,
        mock_stripe_webhook_event,
    ):
        """Processes invoice.payment_succeeded event"""
        user = await get_or_create_user(
            session, f"test_webhook_payment_succeeded_{uuid4()}", create=False
        )
        await session.flush()

        organization = await _get_user_organization(session, user)

        subscription = Subscription(
            organization_id=organization.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.PAST_DUE,
            stripe_subscription_id="sub_test_12345",
        )
        session.add(subscription)
        await session.flush()

        mock_event = {
            "id": "evt_test_12345",
            "type": "invoice.payment_succeeded",
            "data": {
                "object": {
                    "subscription": "sub_test_12345",
                }
            },
        }
        mock_stripe_webhook_event.return_value = mock_event

        with patch.object(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test"):
            response = await client.post(
                "/api/billing/webhook",
                content=b'{"type": "invoice.payment_succeeded"}',
                headers={"stripe-signature": "test_signature"},
            )

        assert response.status_code == 200

    async def test_webhook_unhandled_event_type(
        self,
        client: AsyncClient,
        dependency_overrides,
        mock_stripe_webhook_event,
    ):
        """Logs info, returns 200 for unknown event types"""
        mock_event = {
            "id": "evt_test_12345",
            "type": "customer.created",
            "data": {"object": {}},
        }
        mock_stripe_webhook_event.return_value = mock_event

        with patch.object(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test"):
            response = await client.post(
                "/api/billing/webhook",
                content=b'{"type": "customer.created"}',
                headers={"stripe-signature": "test_signature"},
            )

        assert response.status_code == 200

    async def test_webhook_missing_signature(
        self,
        client: AsyncClient,
        dependency_overrides,
    ):
        """Returns 400 when stripe-signature header missing"""
        response = await client.post(
            "/api/billing/webhook",
            content=b'{"type": "checkout.session.completed"}',
        )

        assert response.status_code == 400
        assert "Missing stripe-signature header" in response.json()["detail"]

    async def test_webhook_invalid_signature(
        self,
        client: AsyncClient,
        dependency_overrides,
        mock_stripe_webhook_event,
    ):
        """Returns 400 when signature verification fails"""
        mock_stripe_webhook_event.side_effect = Exception("Invalid signature")

        with patch.object(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test"):
            response = await client.post(
                "/api/billing/webhook",
                content=b'{"type": "checkout.session.completed"}',
                headers={"stripe-signature": "invalid_signature"},
            )

        assert response.status_code == 400
        assert "Invalid signature" in response.json()["detail"]

    async def test_webhook_not_cloud(
        self,
        client: AsyncClient,
        dependency_overrides,
    ):
        """Returns 404 when IS_CLOUD=False"""
        with patch.object(settings, "IS_CLOUD", False):
            response = await client.post(
                "/api/billing/webhook",
                content=b'{"type": "checkout.session.completed"}',
                headers={"stripe-signature": "test_signature"},
            )

        assert response.status_code == 404

    async def test_webhook_processing_error(
        self,
        client: AsyncClient,
        dependency_overrides,
        mock_stripe_webhook_event,
    ):
        """Returns 500 when handler raises exception"""
        mock_event = {
            "id": "evt_test_12345",
            "type": "checkout.session.completed",
            "data": {"object": {"metadata": {}}},
        }
        mock_stripe_webhook_event.return_value = mock_event

        with patch(
            "app.services.billing_service.handle_checkout_completed"
        ) as mock_handler:
            mock_handler.side_effect = Exception("Processing error")

            with patch.object(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test"):
                response = await client.post(
                    "/api/billing/webhook",
                    content=b'{"type": "checkout.session.completed"}',
                    headers={"stripe-signature": "test_signature"},
                )

        assert response.status_code == 500
        assert "Error processing webhook" in response.json()["detail"]
