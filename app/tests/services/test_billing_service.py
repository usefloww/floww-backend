from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BillingEvent,
    ExecutionHistory,
    ExecutionStatus,
    Namespace,
    Organization,
    Subscription,
    SubscriptionStatus,
    SubscriptionTier,
    Workflow,
)
from app.services import billing_service
from app.settings import settings


class TestSubscriptionCRUD:
    async def test_get_or_create_subscription_new_organization(
        self,
        session: AsyncSession,
        test_org: tuple,
    ):
        _, organization, _ = test_org

        subscription = await billing_service.get_or_create_subscription(
            session, organization
        )

        assert subscription is not None
        assert subscription.organization_id == organization.id
        assert subscription.tier == SubscriptionTier.FREE
        assert subscription.status == SubscriptionStatus.ACTIVE

    async def test_get_or_create_subscription_existing(
        self,
        session: AsyncSession,
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        organization, existing_subscription, _ = test_org_with_free_subscription

        subscription = await billing_service.get_or_create_subscription(
            session, organization
        )

        assert subscription.id == existing_subscription.id
        result = await session.execute(
            select(Subscription).where(Subscription.organization_id == organization.id)
        )
        all_subscriptions = result.scalars().all()
        assert len(all_subscriptions) == 1


class TestPaidSubscriptionStatus:
    async def test_has_active_paid_subscription_free_tier(
        self, test_user_with_free_subscription: tuple[Organization, Subscription]
    ):
        _, subscription = test_user_with_free_subscription

        details = billing_service.get_subscription_details(subscription)

        assert details.is_paid is False

    async def test_has_active_paid_subscription_active_pro(
        self, test_user_with_hobby_subscription: tuple[Organization, Subscription]
    ):
        _, subscription = test_user_with_hobby_subscription

        details = billing_service.get_subscription_details(subscription)

        assert details.is_paid is True

    async def test_has_active_paid_subscription_trialing(
        self, test_user_in_trial: tuple[Organization, Subscription]
    ):
        _, subscription = test_user_in_trial

        details = billing_service.get_subscription_details(subscription)

        assert details.is_paid is True

    async def test_has_active_paid_subscription_trial_expired(
        self, test_user_trial_expired: tuple[Organization, Subscription]
    ):
        _, subscription = test_user_trial_expired

        details = billing_service.get_subscription_details(subscription)

        assert details.is_paid is False

    async def test_has_active_paid_subscription_grace_period(
        self, test_user_in_grace_period: tuple[Organization, Subscription]
    ):
        _, subscription = test_user_in_grace_period

        details = billing_service.get_subscription_details(subscription)

        assert details.is_paid is True

    async def test_has_active_paid_subscription_grace_period_expired(
        self, test_user_grace_expired: tuple[Organization, Subscription]
    ):
        _, subscription = test_user_grace_expired

        details = billing_service.get_subscription_details(subscription)

        assert details.is_paid is False

    async def test_has_active_paid_subscription_canceled(
        self,
        session: AsyncSession,
        test_org: tuple,
    ):
        _, organization, _ = test_org

        subscription = Subscription(
            organization_id=organization.id,
            tier=SubscriptionTier.HOBBY,
            status=SubscriptionStatus.CANCELED,
        )
        session.add(subscription)
        await session.flush()

        details = billing_service.get_subscription_details(subscription)

        assert details.is_paid is False


class TestSubscriptionDetails:
    async def test_get_subscription_details_free_tier(
        self, test_user_with_free_subscription: tuple[Organization, Subscription]
    ):
        _, subscription = test_user_with_free_subscription

        details = billing_service.get_subscription_details(subscription)

        assert details.tier == SubscriptionTier.FREE
        assert details.plan_name == "Free"
        assert details.workflow_limit == 3
        assert details.execution_limit_per_month == 100
        assert details.is_paid is False

    async def test_get_subscription_details_hobby_tier(
        self, test_user_with_hobby_subscription: tuple[Organization, Subscription]
    ):
        _, subscription = test_user_with_hobby_subscription

        details = billing_service.get_subscription_details(subscription)

        assert details.tier == SubscriptionTier.HOBBY
        assert details.plan_name == "Hobby"
        assert details.workflow_limit == 100
        assert details.execution_limit_per_month == 10_000
        assert details.is_paid is True

    async def test_get_subscription_details_team_tier(
        self,
        session: AsyncSession,
        test_org: tuple,
    ):
        _, organization, _ = test_org

        subscription = Subscription(
            organization_id=organization.id,
            tier=SubscriptionTier.TEAM,
            status=SubscriptionStatus.ACTIVE,
        )
        session.add(subscription)
        await session.flush()

        details = billing_service.get_subscription_details(subscription)

        assert details.tier == SubscriptionTier.TEAM
        assert details.plan_name == "Team"
        assert details.workflow_limit == 100
        assert details.execution_limit_per_month == 50_000
        assert details.is_paid is True


class TestLimitChecking:
    async def test_check_workflow_limit_under_limit(
        self,
        session: AsyncSession,
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        organization, _, namespace = test_org_with_free_subscription

        workflow1 = Workflow(name="workflow1", namespace_id=namespace.id)
        workflow2 = Workflow(name="workflow2", namespace_id=namespace.id)
        session.add_all([workflow1, workflow2])
        await session.flush()

        can_create, message = await billing_service.check_workflow_limit(
            session, organization
        )

        assert can_create is True
        assert message == ""

    async def test_check_workflow_limit_at_limit(
        self,
        session: AsyncSession,
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        organization, _, namespace = test_org_with_free_subscription

        for i in range(3):
            workflow = Workflow(name=f"workflow{i}", namespace_id=namespace.id)
            session.add(workflow)
        await session.flush()

        can_create, message = await billing_service.check_workflow_limit(
            session, organization
        )

        assert can_create is False
        assert "limit" in message.lower()
        assert "3" in message

    async def test_check_workflow_limit_disabled_when_not_cloud(
        self,
        session: AsyncSession,
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        organization, _, namespace = test_org_with_free_subscription

        for i in range(10):
            workflow = Workflow(name=f"workflow{i}", namespace_id=namespace.id)
            session.add(workflow)
        await session.flush()

        with patch.object(settings, "IS_CLOUD", False):
            can_create, message = await billing_service.check_workflow_limit(
                session, organization
            )

        assert can_create is True
        assert message == ""

    async def test_check_execution_limit_under_limit(
        self,
        session: AsyncSession,
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        organization, _, namespace = test_org_with_free_subscription

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
            session, organization
        )

        assert can_execute is True
        assert message == ""

    async def test_check_execution_limit_at_limit(
        self,
        session: AsyncSession,
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        organization, _, namespace = test_org_with_free_subscription

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
            session, organization
        )

        assert can_execute is False
        assert "limit" in message.lower()
        assert "100" in message

    async def test_check_execution_limit_new_month_resets(
        self,
        session: AsyncSession,
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        organization, _, namespace = test_org_with_free_subscription

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
            session, organization
        )

        assert can_execute is True
        assert message == ""

    async def test_check_execution_limit_only_counts_valid_statuses(
        self,
        session: AsyncSession,
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        organization, _, namespace = test_org_with_free_subscription

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
            session, organization
        )

        assert can_execute is True


class TestTrialAndGracePeriod:
    async def test_start_trial(
        self,
        session: AsyncSession,
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        _, subscription, _ = test_org_with_free_subscription

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
        test_org_with_hobby_subscription: tuple[Organization, Subscription, Namespace],
    ):
        _, subscription, _ = test_org_with_hobby_subscription

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

    async def test_activate_paid_subscription(
        self,
        session: AsyncSession,
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        _, subscription, _ = test_org_with_free_subscription

        subscription.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=14)
        subscription.grace_period_ends_at = datetime.now(timezone.utc) + timedelta(
            days=7
        )
        await session.flush()

        stripe_sub_id = "sub_test_12345"
        current_period_end = datetime.now(timezone.utc) + timedelta(days=30)

        await billing_service.activate_paid_subscription(
            session, subscription, stripe_sub_id, current_period_end
        )

        assert subscription.status == SubscriptionStatus.ACTIVE
        assert subscription.stripe_subscription_id == stripe_sub_id
        assert subscription.current_period_end == current_period_end
        assert subscription.trial_ends_at is None
        assert subscription.grace_period_ends_at is None

    async def test_downgrade_to_free(
        self,
        session: AsyncSession,
        test_org_with_hobby_subscription: tuple[Organization, Subscription, Namespace],
    ):
        _, subscription, _ = test_org_with_hobby_subscription

        await billing_service.downgrade_to_free(session, subscription)

        assert subscription.tier == SubscriptionTier.FREE
        assert subscription.status == SubscriptionStatus.ACTIVE
        assert subscription.stripe_subscription_id is None
        assert subscription.current_period_end is None
        assert subscription.trial_ends_at is None
        assert subscription.grace_period_ends_at is None
        assert subscription.cancel_at_period_end is False

    async def test_cancel_subscription(
        self,
        session: AsyncSession,
        test_org_with_hobby_subscription: tuple[Organization, Subscription, Namespace],
    ):
        _, subscription, _ = test_org_with_hobby_subscription
        original_tier = subscription.tier

        await billing_service.cancel_subscription(session, subscription)

        assert subscription.cancel_at_period_end is True
        assert subscription.tier == original_tier


class TestWebhookEventHandlers:
    async def test_handle_checkout_completed(
        self,
        session: AsyncSession,
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        _, subscription, _ = test_org_with_free_subscription

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
        event_data = {"customer": "cus_test_12345"}
        stripe_event_id = "evt_test_12345"

        await billing_service.handle_checkout_completed(
            session, event_data, stripe_event_id
        )

    async def test_handle_subscription_created_trialing(
        self,
        session: AsyncSession,
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        _, subscription, _ = test_org_with_free_subscription

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
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        _, subscription, _ = test_org_with_free_subscription

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

        assert updated_subscription.status == SubscriptionStatus.ACTIVE
        assert updated_subscription.stripe_subscription_id == "sub_test_12345"
        assert updated_subscription.trial_ends_at is None

    async def test_handle_subscription_updated_to_active(
        self,
        session: AsyncSession,
        test_org_in_grace_period: tuple[Organization, Subscription, Namespace],
    ):
        _, subscription, _ = test_org_in_grace_period

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
        test_org_with_hobby_subscription: tuple[Organization, Subscription, Namespace],
    ):
        _, subscription, _ = test_org_with_hobby_subscription

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
        test_org_with_hobby_subscription: tuple[Organization, Subscription, Namespace],
    ):
        _, subscription, _ = test_org_with_hobby_subscription

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
        test_org_with_hobby_subscription: tuple[Organization, Subscription, Namespace],
    ):
        _, subscription, _ = test_org_with_hobby_subscription

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
        test_org_with_hobby_subscription: tuple[Organization, Subscription, Namespace],
    ):
        _, subscription, _ = test_org_with_hobby_subscription

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
        test_org_in_grace_period: tuple[Organization, Subscription, Namespace],
    ):
        _, subscription, _ = test_org_in_grace_period

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
        test_org_with_hobby_subscription: tuple[Organization, Subscription, Namespace],
    ):
        _, subscription, _ = test_org_with_hobby_subscription

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
