from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Subscription, User
from app.services import stripe_service


class TestCustomerManagement:
    """Tests for Stripe customer management"""

    async def test_get_or_create_customer_new(
        self,
        session: AsyncSession,
        test_user_with_free_subscription: tuple[User, Subscription],
        mock_stripe_customer,
    ):
        """Creates new Stripe customer and returns customer ID"""
        user, subscription = test_user_with_free_subscription

        mock_stripe_customer.return_value = MagicMock(id="cus_new_12345")

        customer_id = await stripe_service.get_or_create_customer(user, subscription)

        assert customer_id == "cus_new_12345"
        mock_stripe_customer.assert_called_once()
        call_kwargs = mock_stripe_customer.call_args[1]
        assert call_kwargs["email"] == user.email
        assert call_kwargs["metadata"]["user_id"] == str(user.id)
        assert call_kwargs["metadata"]["subscription_id"] == str(subscription.id)

    async def test_get_or_create_customer_existing(
        self,
        test_user_with_pro_subscription: tuple[User, Subscription],
        mock_stripe_customer,
    ):
        """Returns existing customer_id from subscription, does not create duplicate"""
        user, subscription = test_user_with_pro_subscription

        existing_customer_id = subscription.stripe_customer_id

        customer_id = await stripe_service.get_or_create_customer(user, subscription)

        assert customer_id == existing_customer_id
        mock_stripe_customer.assert_not_called()

    async def test_get_or_create_customer_without_stripe(
        self, test_user_with_free_subscription: tuple[User, Subscription]
    ):
        """Raises ValueError when Stripe not configured"""
        user, subscription = test_user_with_free_subscription

        with patch.object(stripe_service, "stripe_client", None):
            with pytest.raises(ValueError, match="Stripe is not configured"):
                await stripe_service.get_or_create_customer(user, subscription)


class TestCheckoutSession:
    """Tests for Stripe checkout session creation"""

    async def test_create_checkout_session_with_trial(
        self,
        session: AsyncSession,
        test_user_with_free_subscription: tuple[User, Subscription],
        mock_stripe_customer,
        mock_stripe_checkout,
    ):
        """Creates session with trial_period_days and correct metadata"""
        user, subscription = test_user_with_free_subscription

        mock_stripe_customer.return_value = MagicMock(id="cus_test_12345")
        mock_stripe_checkout.return_value = MagicMock(
            id="cs_test_12345",
            url="https://checkout.stripe.com/test",
        )

        from app.settings import settings

        with patch.object(settings, "STRIPE_PRICE_ID_PRO", "price_test_pro"), patch.object(
            settings, "TRIAL_PERIOD_DAYS", 14
        ):
            result = await stripe_service.create_checkout_session(
                user=user,
                subscription=subscription,
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
                session_db=session,
            )

        assert result["session_id"] == "cs_test_12345"
        assert result["url"] == "https://checkout.stripe.com/test"

        call_kwargs = mock_stripe_checkout.call_args[1]
        assert "subscription_data" in call_kwargs
        assert "trial_period_days" in call_kwargs["subscription_data"]
        assert call_kwargs["metadata"]["user_id"] == str(user.id)
        assert call_kwargs["metadata"]["subscription_id"] == str(subscription.id)

        assert subscription.stripe_customer_id == "cus_test_12345"

    async def test_create_checkout_session_without_trial(
        self,
        session: AsyncSession,
        test_user_with_pro_subscription: tuple[User, Subscription],
        mock_stripe_checkout,
    ):
        """Creates session without trial (existing PRO user)"""
        user, subscription = test_user_with_pro_subscription

        mock_stripe_checkout.return_value = MagicMock(
            id="cs_test_12345",
            url="https://checkout.stripe.com/test",
        )

        from app.settings import settings

        with patch.object(settings, "STRIPE_PRICE_ID_PRO", "price_test_pro"):
            await stripe_service.create_checkout_session(
                user=user,
                subscription=subscription,
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
                session_db=session,
            )

        call_kwargs = mock_stripe_checkout.call_args[1]
        assert "subscription_data" in call_kwargs
        assert "trial_period_days" not in call_kwargs["subscription_data"]

    async def test_create_checkout_session_without_price_id(
        self,
        test_user_with_free_subscription: tuple[User, Subscription],
        mock_stripe_customer,
    ):
        """Raises ValueError when STRIPE_PRICE_ID_PRO not set"""
        user, subscription = test_user_with_free_subscription

        mock_stripe_customer.return_value = MagicMock(id="cus_test_12345")

        from app.settings import settings

        with patch.object(settings, "STRIPE_PRICE_ID_PRO", None):
            with pytest.raises(
                ValueError, match="STRIPE_PRICE_ID_PRO is not configured"
            ):
                await stripe_service.create_checkout_session(
                    user=user,
                    subscription=subscription,
                    success_url="https://example.com/success",
                    cancel_url="https://example.com/cancel",
                )


class TestCustomerPortal:
    """Tests for Stripe customer portal session creation"""

    async def test_create_customer_portal_session(
        self,
        test_user_with_pro_subscription: tuple[User, Subscription],
        mock_stripe_portal,
    ):
        """Creates portal session with correct customer_id and returns portal URL"""
        _, subscription = test_user_with_pro_subscription

        mock_stripe_portal.return_value = MagicMock(
            url="https://billing.stripe.com/test",
        )

        result = await stripe_service.create_customer_portal_session(
            subscription=subscription,
            return_url="https://example.com/return",
        )

        assert result["url"] == "https://billing.stripe.com/test"

        call_kwargs = mock_stripe_portal.call_args[1]
        assert call_kwargs["customer"] == subscription.stripe_customer_id
        assert call_kwargs["return_url"] == "https://example.com/return"

    async def test_create_customer_portal_session_no_customer(
        self,
        test_user_with_free_subscription: tuple[User, Subscription],
    ):
        """Raises ValueError when no stripe_customer_id"""
        _, subscription = test_user_with_free_subscription

        with pytest.raises(ValueError, match="No Stripe customer ID found"):
            await stripe_service.create_customer_portal_session(
                subscription=subscription,
                return_url="https://example.com/return",
            )


class TestWebhookVerification:
    """Tests for webhook signature verification"""

    async def test_construct_webhook_event_valid_signature(
        self, mock_stripe_webhook_event
    ):
        """Successfully constructs event from valid payload + signature"""
        payload = b'{"type": "customer.subscription.updated"}'
        sig_header = "test_signature"

        mock_event = MagicMock(type="customer.subscription.updated")
        mock_stripe_webhook_event.return_value = mock_event

        from app.settings import settings

        with patch.object(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test"):
            event = stripe_service.construct_webhook_event(payload, sig_header)

        assert event == mock_event
        mock_stripe_webhook_event.assert_called_once_with(
            payload, sig_header, "whsec_test"
        )

    async def test_construct_webhook_event_invalid_signature(self):
        """Raises SignatureVerificationError"""
        payload = b'{"type": "customer.subscription.updated"}'
        sig_header = "invalid_signature"

        import stripe

        with patch("stripe.Webhook.construct_event") as mock_construct:
            mock_construct.side_effect = stripe.SignatureVerificationError(
                "Invalid signature", sig_header
            )

            from app.settings import settings

            with patch.object(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test"):
                with pytest.raises(stripe.SignatureVerificationError):
                    stripe_service.construct_webhook_event(payload, sig_header)

    async def test_construct_webhook_event_invalid_payload(self):
        """Raises ValueError"""
        payload = b"invalid json"
        sig_header = "test_signature"

        with patch("stripe.Webhook.construct_event") as mock_construct:
            mock_construct.side_effect = ValueError("Invalid payload")

            from app.settings import settings

            with patch.object(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test"):
                with pytest.raises(ValueError, match="Invalid payload"):
                    stripe_service.construct_webhook_event(payload, sig_header)
