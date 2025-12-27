from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Namespace, Organization, Subscription, SubscriptionTier
from app.services import stripe_service
from app.settings import settings


class TestCustomerManagement:
    async def test_get_or_create_customer_new(
        self,
        session: AsyncSession,
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        organization, subscription, _ = test_org_with_free_subscription

        with patch("stripe.Customer.create") as mock_create:
            mock_create.return_value = MagicMock(id="cus_new_12345")

            customer_id = await stripe_service.get_or_create_customer(
                organization, subscription
            )

            assert customer_id == "cus_new_12345"
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["name"] == organization.display_name
            assert call_kwargs["metadata"]["organization_id"] == str(organization.id)
            assert call_kwargs["metadata"]["subscription_id"] == str(subscription.id)

    async def test_get_or_create_customer_existing(
        self,
        test_org_with_hobby_subscription: tuple[Organization, Subscription, Namespace],
    ):
        organization, subscription, _ = test_org_with_hobby_subscription
        existing_customer_id = subscription.stripe_customer_id

        with patch("stripe.Customer.create") as mock_create:
            customer_id = await stripe_service.get_or_create_customer(
                organization, subscription
            )

            assert customer_id == existing_customer_id
            mock_create.assert_not_called()


class TestSubscriptionWithIntent:
    async def test_create_subscription_with_intent(
        self,
        session: AsyncSession,
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        organization, subscription, _ = test_org_with_free_subscription

        mock_sub = MagicMock()
        mock_sub.id = "sub_test_12345"
        mock_sub.latest_invoice = MagicMock()
        mock_sub.latest_invoice.payment_intent = MagicMock()
        mock_sub.latest_invoice.payment_intent.client_secret = "pi_secret_test"

        with (
            patch("stripe.Customer.create") as mock_customer,
            patch("stripe.Subscription.create") as mock_sub_create,
            patch("stripe.Subscription.list") as mock_sub_list,
            patch.object(settings, "STRIPE_PRICE_ID_HOBBY", "price_test_hobby"),
        ):
            mock_customer.return_value = MagicMock(id="cus_test_12345")
            mock_sub_list.return_value = MagicMock(data=[])
            mock_sub_create.return_value = mock_sub

            result = await stripe_service.create_subscription_with_intent(
                organization=organization,
                subscription=subscription,
                target_tier=SubscriptionTier.HOBBY,
                session_db=session,
            )

            assert result["subscription_id"] == "sub_test_12345"
            assert result["client_secret"] == "pi_secret_test"
            assert subscription.stripe_customer_id == "cus_test_12345"
            assert subscription.stripe_subscription_id == "sub_test_12345"

    async def test_create_subscription_with_intent_missing_price(
        self,
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        organization, subscription, _ = test_org_with_free_subscription

        with patch.object(settings, "STRIPE_PRICE_ID_HOBBY", ""):
            with pytest.raises(
                ValueError, match="STRIPE_PRICE_ID_HOBBY is not configured"
            ):
                await stripe_service.create_subscription_with_intent(
                    organization=organization,
                    subscription=subscription,
                    target_tier=SubscriptionTier.HOBBY,
                )


class TestCustomerPortal:
    async def test_create_customer_portal_session(
        self,
        test_org_with_hobby_subscription: tuple[Organization, Subscription, Namespace],
    ):
        _, subscription, _ = test_org_with_hobby_subscription

        with patch("stripe.billing_portal.Session.create") as mock_portal:
            mock_portal.return_value = MagicMock(url="https://billing.stripe.com/test")

            result = stripe_service.create_customer_portal_session(
                customer_id=subscription.stripe_customer_id,
                return_url="https://example.com/return",
            )

            assert result["url"] == "https://billing.stripe.com/test"
            call_kwargs = mock_portal.call_args[1]
            assert call_kwargs["customer"] == subscription.stripe_customer_id
            assert call_kwargs["return_url"] == "https://example.com/return"


class TestWebhookVerification:
    async def test_construct_webhook_event_valid_signature(self):
        payload = b'{"type": "customer.subscription.updated"}'
        sig_header = "test_signature"

        mock_event = {"type": "customer.subscription.updated"}

        with (
            patch("stripe.Webhook.construct_event") as mock_construct,
            patch.object(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test"),
        ):
            mock_construct.return_value = mock_event

            event = stripe_service.construct_webhook_event(payload, sig_header)

            assert event == mock_event
            mock_construct.assert_called_once_with(payload, sig_header, "whsec_test")

    async def test_construct_webhook_event_invalid_signature(self):
        import stripe

        payload = b'{"type": "customer.subscription.updated"}'
        sig_header = "invalid_signature"

        with (
            patch("stripe.Webhook.construct_event") as mock_construct,
            patch.object(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test"),
        ):
            mock_construct.side_effect = stripe.SignatureVerificationError(
                "Invalid signature", sig_header
            )

            with pytest.raises(stripe.SignatureVerificationError):
                stripe_service.construct_webhook_event(payload, sig_header)

    async def test_construct_webhook_event_missing_secret(self):
        payload = b'{"type": "customer.subscription.updated"}'
        sig_header = "test_signature"

        with patch.object(settings, "STRIPE_WEBHOOK_SECRET", ""):
            with pytest.raises(
                ValueError, match="STRIPE_WEBHOOK_SECRET is not configured"
            ):
                stripe_service.construct_webhook_event(payload, sig_header)
