"""Tests for execution history API endpoints."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Namespace, Provider, Trigger, User, Workflow
from app.services.execution_history_service import (
    create_execution_record,
    get_execution_by_id,
)
from app.services.workflow_auth_service import WorkflowAuthService
from app.utils.encryption import encrypt_secret


async def create_test_user(session) -> User:
    """Create a test user."""
    user = User(
        workos_user_id=f"test_user_{uuid4()}",
        email="test@example.com",
    )
    session.add(user)
    await session.flush()
    return user


async def create_test_workflow(session, user: User) -> Workflow:
    """Create a test workflow with namespace."""
    namespace = Namespace(user_owner_id=user.id)
    session.add(namespace)
    await session.flush()

    workflow = Workflow(
        name="Test Workflow",
        namespace_id=namespace.id,
        created_by_id=user.id,
    )
    session.add(workflow)
    await session.flush()
    return workflow


async def create_test_trigger(session, workflow: Workflow) -> Trigger:
    """Create a test trigger with provider."""
    # Fetch the workflow's namespace
    namespace_result = await session.execute(
        select(Namespace).where(Namespace.id == workflow.namespace_id)
    )
    namespace = namespace_result.scalar_one()

    # Create provider for the trigger
    provider = Provider(
        id=uuid4(),
        namespace_id=namespace.id,
        type="builtin",
        alias="default",
        encrypted_config=encrypt_secret(json.dumps({})),
    )
    session.add(provider)
    await session.flush()

    trigger = Trigger(
        id=uuid4(),
        workflow_id=workflow.id,
        provider_id=provider.id,
        trigger_type="onWebhook",
        input={},
        state={},
    )
    session.add(trigger)
    await session.flush()
    return trigger


@pytest.mark.asyncio
async def test_complete_execution_success(client, session):
    """Test successfully completing an execution with valid invocation token and logs."""
    # Setup: Create user, workflow, trigger, and execution
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)
    trigger = await create_test_trigger(session, workflow)

    # Generate valid invocation token for the workflow (before commit to avoid lazy load)
    invocation_token = WorkflowAuthService.generate_invocation_token(workflow)

    # Create execution record
    execution = await create_execution_record(
        session=session,
        workflow_id=workflow.id,
        trigger_id=trigger.id,
    )
    execution_id = execution.id  # Save ID before commit
    await session.commit()

    # Make request to complete endpoint with logs
    test_logs = "[2025-01-01T00:00:00Z] [LOG] Hello from trigger"
    response = await client.post(
        f"/api/executions/{execution_id}/complete",
        headers={"Authorization": f"Bearer {invocation_token}"},
        json={"logs": test_logs},
    )

    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"

    # Verify logs were stored
    await session.commit()
    execution = await get_execution_by_id(session, execution_id)
    assert execution.logs == test_logs
