from datetime import datetime, timedelta, timezone
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
    Workflow,
)
from app.services import billing_service
from app.services.user_service import get_or_create_user


class TestFullSubscriptionFlow:
    """Integration tests for full subscription lifecycle"""

    async def test_signup_to_trial_to_paid(self, session: AsyncSession):
        """
        Full flow: New user -> Trial -> Paid subscription
        1. New user signs up (gets FREE subscription)
        2. Creates checkout session
        3. Webhook: checkout.session.completed
        4. Webhook: customer.subscription.created (trialing)
        5. User has PRO access during trial
        6. Webhook: customer.subscription.updated (active after trial)
        """
        user = await get_or_create_user(
            session, f"test_flow_trial_{uuid4()}", create=False
        )
        await session.flush()

        subscription = await billing_service.get_or_create_subscription(session, user)
        assert subscription.tier == SubscriptionTier.FREE
        assert subscription.status == SubscriptionStatus.ACTIVE

        subscription.stripe_customer_id = "cus_test_12345"
        await session.flush()

        event_data = {
            "customer": "cus_test_12345",
            "metadata": {"subscription_id": str(subscription.id)},
        }
        await billing_service.handle_checkout_completed(
            session, event_data, "evt_checkout"
        )
        await session.flush()

        trial_end_date = datetime.now(timezone.utc) + timedelta(days=14)
        event_data = {
            "id": "sub_test_12345",
            "status": "trialing",
            "trial_end": int(trial_end_date.timestamp()),
            "current_period_end": int(trial_end_date.timestamp()),
            "metadata": {"subscription_id": str(subscription.id)},
        }
        await billing_service.handle_subscription_created(
            session, event_data, "evt_sub_created"
        )
        await session.flush()

        result = await session.execute(
            select(Subscription).where(Subscription.id == subscription.id)
        )
        subscription = result.scalar_one()

        assert subscription.tier == SubscriptionTier.HOBBY
        assert subscription.status == SubscriptionStatus.TRIALING
        assert await billing_service.has_active_hobby_subscription(subscription) is True

        event_data = {
            "id": "sub_test_12345",
            "status": "active",
            "current_period_end": int(
                (datetime.now(timezone.utc) + timedelta(days=30)).timestamp()
            ),
            "cancel_at_period_end": False,
        }
        await billing_service.handle_subscription_updated(
            session, event_data, "evt_sub_updated"
        )
        await session.flush()

        result = await session.execute(
            select(Subscription).where(Subscription.id == subscription.id)
        )
        subscription = result.scalar_one()

        assert subscription.status == SubscriptionStatus.ACTIVE
        assert await billing_service.has_active_hobby_subscription(subscription) is True

    async def test_payment_failure_to_grace_to_recovery(self, session: AsyncSession):
        """
        Flow: Payment failure -> Grace period -> Recovery
        1. User has active HOBBY subscription
        2. Webhook: invoice.payment_failed
        3. User enters grace period (PAST_DUE)
        4. User still has PRO access for 7 days
        5. Webhook: invoice.payment_succeeded
        6. User reactivated
        """
        user = await get_or_create_user(
            session, f"test_flow_recovery_{uuid4()}", create=False
        )
        await session.flush()

        subscription = Subscription(
            user_id=user.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.ACTIVE,
            stripe_subscription_id="sub_test_12345",
        )
        session.add(subscription)
        await session.flush()

        event_data = {"subscription": "sub_test_12345"}
        await billing_service.handle_payment_failed_event(
            session, event_data, "evt_payment_failed"
        )
        await session.flush()

        result = await session.execute(
            select(Subscription).where(Subscription.id == subscription.id)
        )
        subscription = result.scalar_one()

        assert subscription.status == SubscriptionStatus.PAST_DUE
        assert subscription.grace_period_ends_at is not None
        assert await billing_service.has_active_hobby_subscription(subscription) is True

        event_data = {"subscription": "sub_test_12345"}
        await billing_service.handle_payment_succeeded_event(
            session, event_data, "evt_payment_succeeded"
        )
        await session.flush()

        result = await session.execute(
            select(Subscription).where(Subscription.id == subscription.id)
        )
        subscription = result.scalar_one()

        assert subscription.status == SubscriptionStatus.ACTIVE
        assert subscription.grace_period_ends_at is None

    async def test_cancel_at_period_end(self, session: AsyncSession):
        """
        Flow: Cancel at period end
        1. User cancels subscription via portal
        2. Webhook: customer.subscription.updated (cancel_at_period_end=true)
        3. User keeps PRO until current_period_end
        4. Webhook: customer.subscription.deleted
        5. User downgraded to FREE
        """
        user = await get_or_create_user(
            session, f"test_flow_cancel_{uuid4()}", create=False
        )
        await session.flush()

        subscription = Subscription(
            user_id=user.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.ACTIVE,
            stripe_subscription_id="sub_test_12345",
            current_period_end=datetime.now(timezone.utc) + timedelta(days=15),
        )
        session.add(subscription)
        await session.flush()

        event_data = {
            "id": "sub_test_12345",
            "status": "active",
            "current_period_end": int(
                (datetime.now(timezone.utc) + timedelta(days=15)).timestamp()
            ),
            "cancel_at_period_end": True,
        }
        await billing_service.handle_subscription_updated(
            session, event_data, "evt_cancel_scheduled"
        )
        await session.flush()

        result = await session.execute(
            select(Subscription).where(Subscription.id == subscription.id)
        )
        subscription = result.scalar_one()

        assert subscription.cancel_at_period_end is True
        assert subscription.tier == SubscriptionTier.HOBBY
        assert await billing_service.has_active_hobby_subscription(subscription) is True

        event_data = {"id": "sub_test_12345"}
        await billing_service.handle_subscription_deleted(
            session, event_data, "evt_sub_deleted"
        )
        await session.flush()

        result = await session.execute(
            select(Subscription).where(Subscription.id == subscription.id)
        )
        subscription = result.scalar_one()

        assert subscription.tier == SubscriptionTier.FREE
        assert subscription.status == SubscriptionStatus.ACTIVE

    async def test_immediate_cancellation(self, session: AsyncSession):
        """
        Flow: Immediate cancellation
        1. User requests immediate cancellation
        2. Webhook: customer.subscription.deleted
        3. User immediately downgraded to FREE
        """
        user = await get_or_create_user(
            session, f"test_flow_immediate_cancel_{uuid4()}", create=False
        )
        await session.flush()

        subscription = Subscription(
            user_id=user.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.ACTIVE,
            stripe_subscription_id="sub_test_12345",
        )
        session.add(subscription)
        await session.flush()

        event_data = {"id": "sub_test_12345"}
        await billing_service.handle_subscription_deleted(
            session, event_data, "evt_sub_deleted"
        )
        await session.flush()

        result = await session.execute(
            select(Subscription).where(Subscription.id == subscription.id)
        )
        subscription = result.scalar_one()

        assert subscription.tier == SubscriptionTier.FREE
        assert subscription.status == SubscriptionStatus.ACTIVE


class TestLimitEnforcementIntegration:
    """Integration tests for limit enforcement"""

    async def test_free_user_blocked_at_workflow_limit(self, session: AsyncSession):
        """
        1. Free user creates 3 workflows
        2. Attempt to create 4th is blocked
        """
        user = await get_or_create_user(
            session, f"test_limit_workflow_{uuid4()}", create=False
        )
        await session.flush()

        subscription = Subscription(
            user_id=user.id,
            tier=SubscriptionTier.FREE,
            status=SubscriptionStatus.ACTIVE,
        )
        session.add(subscription)
        await session.flush()

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

    async def test_free_user_blocked_at_execution_limit(self, session: AsyncSession):
        """
        1. Free user executes 100 workflows this month
        2. 101st execution is blocked
        """
        user = await get_or_create_user(
            session, f"test_limit_execution_{uuid4()}", create=False
        )
        await session.flush()

        subscription = Subscription(
            user_id=user.id,
            tier=SubscriptionTier.FREE,
            status=SubscriptionStatus.ACTIVE,
        )
        session.add(subscription)
        await session.flush()

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

    async def test_usage_resets_on_new_month(self, session: AsyncSession):
        """
        1. Free user executes 100 workflows in previous month
        2. Can execute more in new month
        """
        user = await get_or_create_user(
            session, f"test_monthly_reset_{uuid4()}", create=False
        )
        await session.flush()

        subscription = Subscription(
            user_id=user.id,
            tier=SubscriptionTier.FREE,
            status=SubscriptionStatus.ACTIVE,
        )
        session.add(subscription)
        await session.flush()

        result = await session.execute(
            select(Namespace).where(Namespace.user_owner_id == user.id)
        )
        namespace = result.scalar_one()

        workflow = Workflow(name="test_workflow", namespace_id=namespace.id)
        session.add(workflow)
        await session.flush()

        last_month = datetime.now(timezone.utc) - timedelta(days=35)
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


class TestEdgeCases:
    """Edge cases and error handling tests"""

    async def test_webhook_duplicate_event_id(self, session: AsyncSession):
        """Same stripe_event_id received twice - second one is ignored (unique constraint)"""
        user = await get_or_create_user(
            session, f"test_duplicate_event_{uuid4()}", create=False
        )
        await session.flush()

        subscription = Subscription(
            user_id=user.id,
            tier=SubscriptionTier.FREE,
            status=SubscriptionStatus.ACTIVE,
        )
        session.add(subscription)
        await session.flush()

        event_data = {
            "customer": "cus_test_12345",
            "metadata": {"subscription_id": str(subscription.id)},
        }
        stripe_event_id = "evt_duplicate_test"

        await billing_service.handle_checkout_completed(
            session, event_data, stripe_event_id
        )
        await session.flush()

        result = await session.execute(
            select(BillingEvent).where(BillingEvent.stripe_event_id == stripe_event_id)
        )
        events = result.scalars().all()
        assert len(events) == 1

        await billing_service.handle_checkout_completed(
            session, event_data, stripe_event_id
        )

        try:
            await session.flush()
            assert False, "Should have raised IntegrityError"
        except Exception:
            await session.rollback()

    async def test_webhook_missing_subscription_id(self, session: AsyncSession):
        """Handles gracefully when metadata is missing subscription_id"""
        event_data = {"customer": "cus_test_12345"}
        stripe_event_id = "evt_missing_metadata"

        await billing_service.handle_checkout_completed(
            session, event_data, stripe_event_id
        )

    async def test_trial_expiration_timezone_aware(self, session: AsyncSession):
        """All datetime comparisons use UTC"""
        user = await get_or_create_user(
            session, f"test_timezone_{uuid4()}", create=False
        )
        await session.flush()

        trial_ends_at = datetime.now(timezone.utc) + timedelta(hours=1)

        subscription = Subscription(
            user_id=user.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.TRIALING,
            trial_ends_at=trial_ends_at,
        )
        session.add(subscription)
        await session.flush()

        has_pro = await billing_service.has_active_hobby_subscription(subscription)
        assert has_pro is True

        subscription.trial_ends_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await session.flush()

        has_pro = await billing_service.has_active_hobby_subscription(subscription)
        assert has_pro is False

    async def test_execution_count_only_counts_billable_statuses(
        self, session: AsyncSession
    ):
        """Failed executions don't count toward limit"""
        user = await get_or_create_user(
            session, f"test_billable_status_{uuid4()}", create=False
        )
        await session.flush()

        subscription = Subscription(
            user_id=user.id,
            tier=SubscriptionTier.FREE,
            status=SubscriptionStatus.ACTIVE,
        )
        session.add(subscription)
        await session.flush()

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

        for i in range(100):
            execution = ExecutionHistory(
                workflow_id=workflow.id,
                status=ExecutionStatus.FAILED,
                received_at=now,
            )
            session.add(execution)
        await session.flush()

        count = await billing_service.get_execution_count_this_month(session, user.id)
        assert count == 50

    async def test_grace_period_expiration(self, session: AsyncSession):
        """User loses PRO access when grace period expires"""
        user = await get_or_create_user(
            session, f"test_grace_expired_{uuid4()}", create=False
        )
        await session.flush()

        subscription = Subscription(
            user_id=user.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.PAST_DUE,
            grace_period_ends_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        session.add(subscription)
        await session.flush()

        has_pro = await billing_service.has_active_hobby_subscription(subscription)
        assert has_pro is True

        subscription.grace_period_ends_at = datetime.now(timezone.utc) - timedelta(
            hours=1
        )
        await session.flush()

        has_pro = await billing_service.has_active_hobby_subscription(subscription)
        assert has_pro is False
