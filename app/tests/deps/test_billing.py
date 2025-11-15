from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.billing import (
    check_can_create_workflow,
    check_can_execute_workflow,
    require_pro_tier,
)
from app.models import (
    ExecutionHistory,
    ExecutionStatus,
    Namespace,
    Subscription,
    User,
    Workflow,
)
from app.settings import settings


class TestCheckCanCreateWorkflow:
    """Tests for check_can_create_workflow dependency"""

    async def test_check_can_create_workflow_allowed(
        self,
        session: AsyncSession,
        test_user_with_free_subscription: tuple[User, Subscription],
    ):
        """Passes when under limit"""
        user, _ = test_user_with_free_subscription

        result = await session.execute(
            select(Namespace).where(Namespace.user_owner_id == user.id)
        )
        namespace = result.scalar_one()

        workflow = Workflow(name="workflow1", namespace_id=namespace.id)
        session.add(workflow)
        await session.flush()

        await check_can_create_workflow(user, session)

    async def test_check_can_create_workflow_blocked(
        self,
        session: AsyncSession,
        test_user_with_free_subscription: tuple[User, Subscription],
    ):
        """Raises HTTPException(402) when at limit"""
        user, _ = test_user_with_free_subscription

        result = await session.execute(
            select(Namespace).where(Namespace.user_owner_id == user.id)
        )
        namespace = result.scalar_one()

        for i in range(3):
            workflow = Workflow(name=f"workflow{i}", namespace_id=namespace.id)
            session.add(workflow)
        await session.flush()

        with pytest.raises(HTTPException) as exc_info:
            await check_can_create_workflow(user, session)

        assert exc_info.value.status_code == 402
        assert "limit" in str(exc_info.value.detail).lower()

    async def test_check_can_create_workflow_disabled(
        self,
        session: AsyncSession,
        test_user_with_free_subscription: tuple[User, Subscription],
    ):
        """Always passes when IS_CLOUD=False"""
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
            await check_can_create_workflow(user, session)


class TestCheckCanExecuteWorkflow:
    """Tests for check_can_execute_workflow dependency"""

    async def test_check_can_execute_workflow_allowed(
        self,
        session: AsyncSession,
        test_user_with_free_subscription: tuple[User, Subscription],
    ):
        """Passes when under limit"""
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

        await check_can_execute_workflow(user, session)

    async def test_check_can_execute_workflow_blocked(
        self,
        session: AsyncSession,
        test_user_with_free_subscription: tuple[User, Subscription],
    ):
        """Raises HTTPException(402) when at limit"""
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

        with pytest.raises(HTTPException) as exc_info:
            await check_can_execute_workflow(user, session)

        assert exc_info.value.status_code == 402
        assert "limit" in str(exc_info.value.detail).lower()


class TestRequireProTier:
    """Tests for require_pro_tier dependency"""

    async def test_require_pro_tier_allowed(
        self,
        session: AsyncSession,
        test_user_with_pro_subscription: tuple[User, Subscription],
    ):
        """Passes for pro users"""
        user, _ = test_user_with_pro_subscription

        await require_pro_tier(user, session)

    async def test_require_pro_tier_blocked(
        self,
        session: AsyncSession,
        test_user_with_free_subscription: tuple[User, Subscription],
    ):
        """Raises HTTPException(402) for free users"""
        user, _ = test_user_with_free_subscription

        with pytest.raises(HTTPException) as exc_info:
            await require_pro_tier(user, session)

        assert exc_info.value.status_code == 402
        assert "Hobby subscription required" in str(exc_info.value.detail)
