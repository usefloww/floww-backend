from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.billing import (
    check_can_create_workflow_in_namespace,
    check_can_execute_workflow_in_org,
    require_paid_subscription,
)
from app.models import (
    ExecutionHistory,
    ExecutionStatus,
    Namespace,
    Organization,
    Subscription,
    Workflow,
)
from app.settings import settings


class TestCheckCanCreateWorkflow:
    """Tests for check_can_create_workflow_in_namespace dependency"""

    async def test_check_can_create_workflow_allowed(
        self,
        session: AsyncSession,
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        """Passes when under limit"""
        _, _, namespace = test_org_with_free_subscription

        workflow = Workflow(name="workflow1", namespace_id=namespace.id)
        session.add(workflow)
        await session.flush()

        await check_can_create_workflow_in_namespace(session, namespace.id)

    async def test_check_can_create_workflow_blocked(
        self,
        session: AsyncSession,
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        """Raises HTTPException(402) when at limit"""
        _, _, namespace = test_org_with_free_subscription

        for i in range(3):
            workflow = Workflow(name=f"workflow{i}", namespace_id=namespace.id)
            session.add(workflow)
        await session.flush()

        with pytest.raises(HTTPException) as exc_info:
            await check_can_create_workflow_in_namespace(session, namespace.id)

        assert exc_info.value.status_code == 402
        assert "limit" in str(exc_info.value.detail).lower()

    async def test_check_can_create_workflow_disabled(
        self,
        session: AsyncSession,
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        """Always passes when IS_CLOUD=False"""
        _, _, namespace = test_org_with_free_subscription

        for i in range(10):
            workflow = Workflow(name=f"workflow{i}", namespace_id=namespace.id)
            session.add(workflow)
        await session.flush()

        with patch.object(settings, "IS_CLOUD", False):
            await check_can_create_workflow_in_namespace(session, namespace.id)


class TestCheckCanExecuteWorkflow:
    """Tests for check_can_execute_workflow_in_org dependency"""

    async def test_check_can_execute_workflow_allowed(
        self,
        session: AsyncSession,
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        """Passes when under limit"""
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

        await check_can_execute_workflow_in_org(session, organization)

    async def test_check_can_execute_workflow_blocked(
        self,
        session: AsyncSession,
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        """Raises HTTPException(402) when at limit"""
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

        with pytest.raises(HTTPException) as exc_info:
            await check_can_execute_workflow_in_org(session, organization)

        assert exc_info.value.status_code == 402
        assert "limit" in str(exc_info.value.detail).lower()


class TestRequirePaidSubscription:
    """Tests for require_paid_subscription dependency"""

    async def test_require_paid_subscription_allowed(
        self,
        session: AsyncSession,
        test_org_with_hobby_subscription: tuple[Organization, Subscription, Namespace],
    ):
        """Passes for paid organizations"""
        organization, _, _ = test_org_with_hobby_subscription

        await require_paid_subscription(session, organization)

    async def test_require_paid_subscription_blocked(
        self,
        session: AsyncSession,
        test_org_with_free_subscription: tuple[Organization, Subscription, Namespace],
    ):
        """Raises HTTPException(402) for free organizations"""
        organization, _, _ = test_org_with_free_subscription

        with pytest.raises(HTTPException) as exc_info:
            await require_paid_subscription(session, organization)

        assert exc_info.value.status_code == 402
        assert "subscription required" in str(exc_info.value.detail).lower()
