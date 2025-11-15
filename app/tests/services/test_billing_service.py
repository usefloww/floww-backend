from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BillingEvent,
    ExecutionHistory,
    ExecutionStatus,
    Namespace,
    Subscription,
    SubscriptionStatus,
    SubscriptionTier,
    User,
    Workflow,
)
from app.services import billing_service
from app.services.user_service import get_or_create_user
from app.settings import settings


class TestSubscriptionCRUD:
    """Tests for subscription CRUD operations"""

    async def test_get_or_create_subscription_new_user(
        self, session: AsyncSession, test_user: User
    ):
        """Creates new subscription for user without one"""
        subscription = await billing_service.get_or_create_subscription(
            session, test_user
        )

        assert subscription is not None
        assert subscription.user_id == test_user.id
        assert subscription.tier == SubscriptionTier.FREE
        assert subscription.status == SubscriptionStatus.ACTIVE

    async def test_get_or_create_subscription_existing(
        self,
        session: AsyncSession,
        test_user_with_free_subscription: tuple[User, Subscription],
    ):
        """Returns existing subscription for user who has one"""
        user, existing_subscription = test_user_with_free_subscription

        subscription = await billing_service.get_or_create_subscription(session, user)

        assert subscription.id == existing_subscription.id
        result = await session.execute(
            select(Subscription).where(Subscription.user_id == user.id)
        )
        all_subscriptions = result.scalars().all()
        assert len(all_subscriptions) == 1


class TestProSubscriptionStatus:
    """Tests for checking active hobby subscription status"""

    async def test_has_active_pro_subscription_free_tier(
        self, test_user_with_free_subscription: tuple[User, Subscription]
    ):
        """Returns False for FREE tier user"""
        _, subscription = test_user_with_free_subscription

        result = await billing_service.has_active_hobby_subscription(subscription)

        assert result is False

    async def test_has_active_pro_subscription_active_pro(
        self, test_user_with_pro_subscription: tuple[User, Subscription]
    ):
        """Returns True for ACTIVE HOBBY subscription"""
        _, subscription = test_user_with_pro_subscription

        result = await billing_service.has_active_hobby_subscription(subscription)

        assert result is True

    async def test_has_active_pro_subscription_trialing(
        self, test_user_in_trial: tuple[User, Subscription]
    ):
        """Returns True when in trial period (before trial_ends_at)"""
        _, subscription = test_user_in_trial

        result = await billing_service.has_active_hobby_subscription(subscription)

        assert result is True

    async def test_has_active_pro_subscription_trial_expired(
        self, test_user_trial_expired: tuple[User, Subscription]
    ):
        """Returns False when trial expired"""
        _, subscription = test_user_trial_expired

        result = await billing_service.has_active_hobby_subscription(subscription)

        assert result is False

    async def test_has_active_pro_subscription_grace_period(
        self, test_user_in_grace_period: tuple[User, Subscription]
    ):
        """Returns True when PAST_DUE with valid grace period"""
        _, subscription = test_user_in_grace_period

        result = await billing_service.has_active_hobby_subscription(subscription)

        assert result is True

    async def test_has_active_pro_subscription_grace_period_expired(
        self, test_user_grace_expired: tuple[User, Subscription]
    ):
        """Returns False when grace period expired"""
        _, subscription = test_user_grace_expired

        result = await billing_service.has_active_hobby_subscription(subscription)

        assert result is False

    async def test_has_active_pro_subscription_canceled(self, session: AsyncSession):
        """Returns False for CANCELED subscription"""
        user = await get_or_create_user(
            session, f"test_canceled_{uuid4()}", create=False
        )
        await session.flush()

        subscription = Subscription(
            user_id=user.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.CANCELED,
        )
        session.add(subscription)
        await session.flush()

        result = await billing_service.has_active_hobby_subscription(subscription)

        assert result is False


class TestLimitChecking:
    """Tests for limit checking functions"""

    async def test_get_workflow_limit_free_tier(
        self, test_user_with_free_subscription: tuple[User, Subscription]
    ):
        """Returns FREE_TIER_WORKFLOW_LIMIT (3)"""
        _, subscription = test_user_with_free_subscription

        limit = await billing_service.get_workflow_limit(subscription)

        assert limit == settings.FREE_TIER_WORKFLOW_LIMIT

    async def test_get_workflow_limit_pro_tier(
        self, test_user_with_pro_subscription: tuple[User, Subscription]
    ):
        """Returns PRO_TIER_WORKFLOW_LIMIT (100)"""
        _, subscription = test_user_with_pro_subscription

        limit = await billing_service.get_workflow_limit(subscription)

        assert limit == settings.PRO_TIER_WORKFLOW_LIMIT

    async def test_get_execution_limit_free_tier(
        self, test_user_with_free_subscription: tuple[User, Subscription]
    ):
        """Returns FREE_TIER_EXECUTION_LIMIT_PER_MONTH (100)"""
        _, subscription = test_user_with_free_subscription

        limit = await billing_service.get_execution_limit(subscription)

        assert limit == settings.FREE_TIER_EXECUTION_LIMIT_PER_MONTH

    async def test_get_execution_limit_pro_tier(
        self, test_user_with_pro_subscription: tuple[User, Subscription]
    ):
        """Returns PRO_TIER_EXECUTION_LIMIT_PER_MONTH (10,000)"""
        _, subscription = test_user_with_pro_subscription

        limit = await billing_service.get_execution_limit(subscription)

        assert limit == settings.PRO_TIER_EXECUTION_LIMIT_PER_MONTH

    async def test_check_workflow_limit_under_limit(
        self,
        session: AsyncSession,
        test_user_with_free_subscription: tuple[User, Subscription],
    ):
        """Returns (True, "") when user has 2 workflows (limit 3)"""
        user, _ = test_user_with_free_subscription

        result = await session.execute(
            select(Namespace).where(Namespace.user_owner_id == user.id)
        )
        namespace = result.scalar_one()

        workflow1 = Workflow(name="workflow1", namespace_id=namespace.id)
        workflow2 = Workflow(name="workflow2", namespace_id=namespace.id)
        session.add_all([workflow1, workflow2])
        await session.flush()

        can_create, message = await billing_service.check_workflow_limit(session, user)

        assert can_create is True
        assert message == ""

    async def test_check_workflow_limit_at_limit(
        self,
        session: AsyncSession,
        test_user_with_free_subscription: tuple[User, Subscription],
    ):
        """Returns (False, message) when user has 3 workflows (limit 3)"""
        user, _ = test_user_with_free_subscription

        result = await session.execute(
            select(Namespace).where(Namespace.user_owner_id == user.id)
        )
        namespace = result.scalar_one()

        for i in range(3):
            workflow = Workflow(name=f"workflow{i}", namespace_id=namespace.id)
            session.add(workflow)
        await session.flush()

        can_create, message = await billing_service.check_workflow_limit(session, user)

        assert can_create is False
        assert "limit" in message.lower()
        assert "3" in message

    async def test_check_workflow_limit_disabled_when_not_cloud(
        self,
        session: AsyncSession,
        test_user_with_free_subscription: tuple[User, Subscription],
    ):
        """Returns (True, "") when IS_CLOUD=False regardless of limit"""
        user, _ = test_user_with_free_subscription

        result = await session.execute(
            select(Namespace).where(Namespace.user_owner_id == user.id)
        )
        namespace = result.scalar_one()

        for i in range(10):
            workflow = Workflow(name=f"workflow{i}", namespace_id=namespace.id)
            session.add(workflow)
        await session.flush()

        with patch.object(settings, "IS_CLOUD", False):
            can_create, message = await billing_service.check_workflow_limit(
                session, user
            )

        assert can_create is True
        assert message == ""

    async def test_check_execution_limit_under_limit(
        self,
        session: AsyncSession,
        test_user_with_free_subscription: tuple[User, Subscription],
    ):
        """Returns (True, "") when user has 50 executions this month"""
        user, _ = test_user_with_free_subscription

        result = await session.execute(
            select(Namespace).where(Namespace.user_owner_id == user.id)
        )
        namespace = result.scalar_one()

        workflow = Workflow(name="test_workflow", namespace_id=namespace.id)
        session.add(workflow)
        await session.flush()

        now = datetime.now(timezone.utc)
        for i in range(50):
            execution = ExecutionHistory(
                workflow_id=workflow.id,
                status=ExecutionStatus.COMPLETED,
                received_at=now,
            )
            session.add(execution)
        await session.flush()

        can_execute, message = await billing_service.check_execution_limit(
            session, user
        )

        assert can_execute is True
        assert message == ""

    async def test_check_execution_limit_at_limit(
        self,
        session: AsyncSession,
        test_user_with_free_subscription: tuple[User, Subscription],
    ):
        """Returns (False, message) when user has 100 executions this month"""
        user, _ = test_user_with_free_subscription

        result = await session.execute(
            select(Namespace).where(Namespace.user_owner_id == user.id)
        )
        namespace = result.scalar_one()

        workflow = Workflow(name="test_workflow", namespace_id=namespace.id)
        session.add(workflow)
        await session.flush()

        now = datetime.now(timezone.utc)
        for i in range(100):
            execution = ExecutionHistory(
                workflow_id=workflow.id,
                status=ExecutionStatus.COMPLETED,
                received_at=now,
            )
            session.add(execution)
        await session.flush()

        can_execute, message = await billing_service.check_execution_limit(
            session, user
        )

        assert can_execute is False
        assert "limit" in message.lower()
        assert "100" in message

    async def test_check_execution_limit_new_month_resets(
        self,
        session: AsyncSession,
        test_user_with_free_subscription: tuple[User, Subscription],
    ):
        """Executions from previous month don't count"""
        user, _ = test_user_with_free_subscription

        result = await session.execute(
            select(Namespace).where(Namespace.user_owner_id == user.id)
        )
        namespace = result.scalar_one()

        workflow = Workflow(name="test_workflow", namespace_id=namespace.id)
        session.add(workflow)
        await session.flush()

        last_month = datetime.now(timezone.utc) - timedelta(days=40)
        for i in range(100):
            execution = ExecutionHistory(
                workflow_id=workflow.id,
                status=ExecutionStatus.COMPLETED,
                received_at=last_month,
            )
            session.add(execution)
        await session.flush()

        can_execute, message = await billing_service.check_execution_limit(
            session, user
        )

        assert can_execute is True
        assert message == ""

    async def test_check_execution_limit_only_counts_valid_statuses(
        self,
        session: AsyncSession,
        test_user_with_free_subscription: tuple[User, Subscription],
    ):
        """Only counts COMPLETED, STARTED, RECEIVED; not FAILED, TIMEOUT, NO_DEPLOYMENT"""
        user, _ = test_user_with_free_subscription

        result = await session.execute(
            select(Namespace).where(Namespace.user_owner_id == user.id)
        )
        namespace = result.scalar_one()

        workflow = Workflow(name="test_workflow", namespace_id=namespace.id)
        session.add(workflow)
        await session.flush()

        now = datetime.now(timezone.utc)

        for i in range(50):
            execution = ExecutionHistory(
                workflow_id=workflow.id,
                status=ExecutionStatus.COMPLETED,
                received_at=now,
            )
            session.add(execution)

        for i in range(50):
            execution = ExecutionHistory(
                workflow_id=workflow.id,
                status=ExecutionStatus.FAILED,
                received_at=now,
            )
            session.add(execution)
        await session.flush()

        can_execute, message = await billing_service.check_execution_limit(
            session, user
        )

        assert can_execute is True


class TestTrialAndGracePeriod:
    """Tests for trial and grace period management"""

    async def test_start_trial(
        self,
        session: AsyncSession,
        test_user_with_free_subscription: tuple[User, Subscription],
    ):
        """Sets tier to PRO, status to TRIALING, trial_ends_at to now + 14 days"""
        _, subscription = test_user_with_free_subscription

        before = datetime.now(timezone.utc)
        await billing_service.start_trial(session, subscription)
        after = datetime.now(timezone.utc)

        assert subscription.tier == SubscriptionTier.HOBBY
        assert subscription.status == SubscriptionStatus.TRIALING
        assert subscription.trial_ends_at is not None
        expected_trial_end = before + timedelta(days=settings.TRIAL_PERIOD_DAYS)
        assert subscription.trial_ends_at >= expected_trial_end
        assert subscription.trial_ends_at <= after + timedelta(
            days=settings.TRIAL_PERIOD_DAYS
        )

    async def test_start_grace_period(
        self,
        session: AsyncSession,
        test_user_with_pro_subscription: tuple[User, Subscription],
    ):
        """Sets status to PAST_DUE, grace_period_ends_at to now + 7 days"""
        _, subscription = test_user_with_pro_subscription

        before = datetime.now(timezone.utc)
        await billing_service.start_grace_period(session, subscription)
        after = datetime.now(timezone.utc)

        assert subscription.status == SubscriptionStatus.PAST_DUE
        assert subscription.grace_period_ends_at is not None
        expected_grace_end = before + timedelta(days=settings.GRACE_PERIOD_DAYS)
        assert subscription.grace_period_ends_at >= expected_grace_end
        assert subscription.grace_period_ends_at <= after + timedelta(
            days=settings.GRACE_PERIOD_DAYS
        )

    async def test_activate_pro_subscription(
        self,
        session: AsyncSession,
        test_user_with_free_subscription: tuple[User, Subscription],
    ):
        """Sets tier to PRO, status to ACTIVE, clears trial/grace, sets Stripe fields"""
        _, subscription = test_user_with_free_subscription

        subscription.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=14)
        subscription.grace_period_ends_at = datetime.now(timezone.utc) + timedelta(
            days=7
        )
        await session.flush()

        stripe_sub_id = "sub_test_12345"
        current_period_end = datetime.now(timezone.utc) + timedelta(days=30)

        await billing_service.activate_pro_subscription(
            session, subscription, stripe_sub_id, current_period_end
        )

        assert subscription.tier == SubscriptionTier.HOBBY
        assert subscription.status == SubscriptionStatus.ACTIVE
        assert subscription.stripe_subscription_id == stripe_sub_id
        assert subscription.current_period_end == current_period_end
        assert subscription.trial_ends_at is None
        assert subscription.grace_period_ends_at is None

    async def test_downgrade_to_free(
        self,
        session: AsyncSession,
        test_user_with_pro_subscription: tuple[User, Subscription],
    ):
        """Sets tier to FREE, status to ACTIVE, clears all Stripe-related fields"""
        _, subscription = test_user_with_pro_subscription

        await billing_service.downgrade_to_free(session, subscription)

        assert subscription.tier == SubscriptionTier.FREE
        assert subscription.status == SubscriptionStatus.ACTIVE
        assert subscription.stripe_subscription_id is None
        assert subscription.current_period_end is None
        assert subscription.trial_ends_at is None
        assert subscription.grace_period_ends_at is None
        assert subscription.cancel_at_period_end is False

    async def test_cancel_subscription_immediate(
        self,
        session: AsyncSession,
        test_user_with_pro_subscription: tuple[User, Subscription],
    ):
        """Immediately downgrades to FREE tier and clears Stripe fields"""
        _, subscription = test_user_with_pro_subscription

        await billing_service.cancel_subscription(session, subscription, immediate=True)

        assert subscription.tier == SubscriptionTier.FREE
        assert subscription.status == SubscriptionStatus.CANCELED
        assert subscription.stripe_subscription_id is None
        assert subscription.current_period_end is None
        assert subscription.grace_period_ends_at is None

    async def test_cancel_subscription_at_period_end(
        self,
        session: AsyncSession,
        test_user_with_pro_subscription: tuple[User, Subscription],
    ):
        """Sets cancel_at_period_end flag, does not immediately change tier"""
        _, subscription = test_user_with_pro_subscription
        original_tier = subscription.tier

        await billing_service.cancel_subscription(
            session, subscription, immediate=False
        )

        assert subscription.cancel_at_period_end is True
        assert subscription.tier == original_tier


class TestWebhookEventHandlers:
    """Tests for webhook event handlers"""

    async def test_handle_checkout_completed(
        self,
        session: AsyncSession,
        test_user_with_free_subscription: tuple[User, Subscription],
    ):
        """Updates subscription with stripe_customer_id and creates BillingEvent"""
        _, subscription = test_user_with_free_subscription

        event_data = {
            "customer": "cus_test_12345",
            "metadata": {"subscription_id": str(subscription.id)},
        }
        stripe_event_id = "evt_test_12345"

        await billing_service.handle_checkout_completed(
            session, event_data, stripe_event_id
        )
        await session.flush()

        result = await session.execute(
            select(Subscription).where(Subscription.id == subscription.id)
        )
        updated_subscription = result.scalar_one()

        assert updated_subscription.stripe_customer_id == "cus_test_12345"

        billing_events = await session.execute(
            select(BillingEvent).where(BillingEvent.subscription_id == subscription.id)
        )
        events = billing_events.scalars().all()
        assert len(events) == 1
        assert events[0].event_type == "checkout.session.completed"
        assert events[0].stripe_event_id == stripe_event_id

    async def test_handle_checkout_completed_missing_metadata(
        self, session: AsyncSession
    ):
        """Logs warning, does not crash"""
        event_data = {"customer": "cus_test_12345"}
        stripe_event_id = "evt_test_12345"

        await billing_service.handle_checkout_completed(
            session, event_data, stripe_event_id
        )

    async def test_handle_subscription_created_trialing(
        self,
        session: AsyncSession,
        test_user_with_free_subscription: tuple[User, Subscription],
    ):
        """Starts trial period and sets stripe_subscription_id"""
        _, subscription = test_user_with_free_subscription

        event_data = {
            "id": "sub_test_12345",
            "status": "trialing",
            "current_period_end": int(
                (datetime.now(timezone.utc) + timedelta(days=14)).timestamp()
            ),
            "metadata": {"subscription_id": str(subscription.id)},
        }
        stripe_event_id = "evt_test_12345"

        await billing_service.handle_subscription_created(
            session, event_data, stripe_event_id
        )
        await session.flush()

        result = await session.execute(
            select(Subscription).where(Subscription.id == subscription.id)
        )
        updated_subscription = result.scalar_one()

        assert updated_subscription.tier == SubscriptionTier.HOBBY
        assert updated_subscription.status == SubscriptionStatus.TRIALING
        assert updated_subscription.stripe_subscription_id == "sub_test_12345"
        assert updated_subscription.trial_ends_at is not None

    async def test_handle_subscription_created_active(
        self,
        session: AsyncSession,
        test_user_with_free_subscription: tuple[User, Subscription],
    ):
        """Activates hobby subscription without trial"""
        _, subscription = test_user_with_free_subscription

        event_data = {
            "id": "sub_test_12345",
            "status": "active",
            "current_period_end": int(
                (datetime.now(timezone.utc) + timedelta(days=30)).timestamp()
            ),
            "metadata": {"subscription_id": str(subscription.id)},
        }
        stripe_event_id = "evt_test_12345"

        await billing_service.handle_subscription_created(
            session, event_data, stripe_event_id
        )
        await session.flush()

        result = await session.execute(
            select(Subscription).where(Subscription.id == subscription.id)
        )
        updated_subscription = result.scalar_one()

        assert updated_subscription.tier == SubscriptionTier.HOBBY
        assert updated_subscription.status == SubscriptionStatus.ACTIVE
        assert updated_subscription.stripe_subscription_id == "sub_test_12345"
        assert updated_subscription.trial_ends_at is None

    async def test_handle_subscription_updated_to_active(
        self,
        session: AsyncSession,
        test_user_in_grace_period: tuple[User, Subscription],
    ):
        """Reactivates from PAST_DUE and clears grace_period_ends_at"""
        _, subscription = test_user_in_grace_period

        event_data = {
            "id": subscription.stripe_subscription_id,
            "status": "active",
            "current_period_end": int(
                (datetime.now(timezone.utc) + timedelta(days=30)).timestamp()
            ),
            "cancel_at_period_end": False,
        }
        stripe_event_id = "evt_test_12345"

        await billing_service.handle_subscription_updated(
            session, event_data, stripe_event_id
        )
        await session.flush()

        result = await session.execute(
            select(Subscription).where(Subscription.id == subscription.id)
        )
        updated_subscription = result.scalar_one()

        assert updated_subscription.status == SubscriptionStatus.ACTIVE
        assert updated_subscription.grace_period_ends_at is None

    async def test_handle_subscription_updated_to_canceled(
        self,
        session: AsyncSession,
        test_user_with_pro_subscription: tuple[User, Subscription],
    ):
        """Downgrades to FREE tier"""
        _, subscription = test_user_with_pro_subscription

        event_data = {
            "id": subscription.stripe_subscription_id,
            "status": "canceled",
            "current_period_end": int(datetime.now(timezone.utc).timestamp()),
            "cancel_at_period_end": False,
        }
        stripe_event_id = "evt_test_12345"

        await billing_service.handle_subscription_updated(
            session, event_data, stripe_event_id
        )
        await session.flush()

        result = await session.execute(
            select(Subscription).where(Subscription.id == subscription.id)
        )
        updated_subscription = result.scalar_one()

        assert updated_subscription.tier == SubscriptionTier.FREE
        assert updated_subscription.status == SubscriptionStatus.ACTIVE

    async def test_handle_subscription_updated_cancel_at_period_end(
        self,
        session: AsyncSession,
        test_user_with_pro_subscription: tuple[User, Subscription],
    ):
        """Sets cancel_at_period_end flag"""
        _, subscription = test_user_with_pro_subscription

        event_data = {
            "id": subscription.stripe_subscription_id,
            "status": "active",
            "current_period_end": int(
                (datetime.now(timezone.utc) + timedelta(days=30)).timestamp()
            ),
            "cancel_at_period_end": True,
        }
        stripe_event_id = "evt_test_12345"

        await billing_service.handle_subscription_updated(
            session, event_data, stripe_event_id
        )
        await session.flush()

        result = await session.execute(
            select(Subscription).where(Subscription.id == subscription.id)
        )
        updated_subscription = result.scalar_one()

        assert updated_subscription.cancel_at_period_end is True

    async def test_handle_subscription_deleted(
        self,
        session: AsyncSession,
        test_user_with_pro_subscription: tuple[User, Subscription],
    ):
        """Downgrades to FREE tier and creates BillingEvent"""
        _, subscription = test_user_with_pro_subscription

        event_data = {"id": subscription.stripe_subscription_id}
        stripe_event_id = "evt_test_12345"

        await billing_service.handle_subscription_deleted(
            session, event_data, stripe_event_id
        )
        await session.flush()

        result = await session.execute(
            select(Subscription).where(Subscription.id == subscription.id)
        )
        updated_subscription = result.scalar_one()

        assert updated_subscription.tier == SubscriptionTier.FREE
        assert updated_subscription.status == SubscriptionStatus.ACTIVE

        billing_events = await session.execute(
            select(BillingEvent).where(BillingEvent.subscription_id == subscription.id)
        )
        events = billing_events.scalars().all()
        assert len(events) == 1
        assert events[0].event_type == "customer.subscription.deleted"

    async def test_handle_payment_failed_event(
        self,
        session: AsyncSession,
        test_user_with_pro_subscription: tuple[User, Subscription],
    ):
        """Starts grace period and sets status to PAST_DUE"""
        _, subscription = test_user_with_pro_subscription

        event_data = {"subscription": subscription.stripe_subscription_id}
        stripe_event_id = "evt_test_12345"

        await billing_service.handle_payment_failed_event(
            session, event_data, stripe_event_id
        )
        await session.flush()

        result = await session.execute(
            select(Subscription).where(Subscription.id == subscription.id)
        )
        updated_subscription = result.scalar_one()

        assert updated_subscription.status == SubscriptionStatus.PAST_DUE
        assert updated_subscription.grace_period_ends_at is not None

    async def test_handle_payment_succeeded_event_from_past_due(
        self,
        session: AsyncSession,
        test_user_in_grace_period: tuple[User, Subscription],
    ):
        """Reactivates subscription and clears grace period"""
        _, subscription = test_user_in_grace_period

        event_data = {"subscription": subscription.stripe_subscription_id}
        stripe_event_id = "evt_test_12345"

        await billing_service.handle_payment_succeeded_event(
            session, event_data, stripe_event_id
        )
        await session.flush()

        result = await session.execute(
            select(Subscription).where(Subscription.id == subscription.id)
        )
        updated_subscription = result.scalar_one()

        assert updated_subscription.status == SubscriptionStatus.ACTIVE
        assert updated_subscription.grace_period_ends_at is None

    async def test_handle_payment_succeeded_event_already_active(
        self,
        session: AsyncSession,
        test_user_with_pro_subscription: tuple[User, Subscription],
    ):
        """No status change needed"""
        _, subscription = test_user_with_pro_subscription

        event_data = {"subscription": subscription.stripe_subscription_id}
        stripe_event_id = "evt_test_12345"

        original_status = subscription.status

        await billing_service.handle_payment_succeeded_event(
            session, event_data, stripe_event_id
        )
        await session.flush()

        result = await session.execute(
            select(Subscription).where(Subscription.id == subscription.id)
        )
        updated_subscription = result.scalar_one()

        assert updated_subscription.status == original_status
