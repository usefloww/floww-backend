"""Tests for execution history API endpoints."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import ExecutionLog, LogLevel, Namespace, Provider, Trigger, User, Workflow
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

    # Make request to complete endpoint with legacy string log
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

    # Verify log entry was stored in execution_logs table
    await session.commit()
    execution = await get_execution_by_id(session, execution_id)
    assert len(execution.log_entries) == 1
    assert execution.log_entries[0].message == test_logs
    assert execution.log_entries[0].log_level == LogLevel.LOG


@pytest.mark.asyncio
async def test_complete_execution_with_structured_logs(client, session):
    """Test completing execution with structured log entries via API."""
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)
    trigger = await create_test_trigger(session, workflow)

    invocation_token = WorkflowAuthService.generate_invocation_token(workflow)

    execution = await create_execution_record(
        session=session,
        workflow_id=workflow.id,
        trigger_id=trigger.id,
    )
    execution_id = execution.id
    await session.commit()

    # Send structured logs with duration
    structured_logs = [
        {"timestamp": "2025-01-01T10:00:00Z", "level": "info", "message": "Starting"},
        {"timestamp": "2025-01-01T10:00:01Z", "level": "error", "message": "Failed"},
    ]
    response = await client.post(
        f"/api/executions/{execution_id}/complete",
        headers={"Authorization": f"Bearer {invocation_token}"},
        json={"logs": structured_logs, "duration_ms": 1000},
    )

    assert response.status_code == 200

    # Verify logs and duration stored correctly
    await session.commit()
    execution = await get_execution_by_id(session, execution_id)
    assert len(execution.log_entries) == 2
    assert execution.duration_ms == 1000

    levels = {log.log_level for log in execution.log_entries}
    assert LogLevel.INFO in levels
    assert LogLevel.ERROR in levels
