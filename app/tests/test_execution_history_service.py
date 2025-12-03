"""Tests for execution history service CRUD operations."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ExecutionHistory,
    ExecutionLog,
    ExecutionStatus,
    LogLevel,
    Namespace,
    Provider,
    Runtime,
    Trigger,
    User,
    Workflow,
    WorkflowDeployment,
)
from app.services.execution_history_service import (
    create_execution_record,
    get_execution_by_id,
    get_executions_for_workflow,
    get_recent_executions,
    search_execution_logs,
    serialize_execution,
    update_execution_completed,
    update_execution_failed,
    update_execution_no_deployment,
    update_execution_started,
)
from app.utils.encryption import encrypt_secret


async def create_test_user(session: AsyncSession) -> User:
    """Create a test user."""
    user = User(
        workos_user_id=f"test_user_{uuid4()}",
        email="test@example.com",
    )
    session.add(user)
    await session.flush()
    return user


async def create_test_workflow(session: AsyncSession, user: User) -> Workflow:
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


async def create_test_trigger(session: AsyncSession, workflow: Workflow) -> Trigger:
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


async def create_test_deployment(
    session: AsyncSession, workflow: Workflow
) -> WorkflowDeployment:
    """Create a test deployment with runtime."""
    # Create a runtime first
    runtime = Runtime(
        id=uuid4(),
        config_hash=uuid4(),
        config={"image_hash": "sha256:test"},
    )
    session.add(runtime)
    await session.flush()

    deployment = WorkflowDeployment(
        id=uuid4(),
        workflow_id=workflow.id,
        runtime_id=runtime.id,
        user_code={
            "files": {"main.ts": "export default () => {}"},
            "entrypoint": "main.ts",
        },
    )
    session.add(deployment)
    await session.flush()
    return deployment


@pytest.mark.asyncio
async def test_create_execution_record(session):
    """Test creating a new execution record with RECEIVED status."""
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)
    trigger = await create_test_trigger(session, workflow)

    # Create execution record
    execution = await create_execution_record(
        session=session,
        workflow_id=workflow.id,
        trigger_id=trigger.id,
    )

    # Verify execution created correctly
    assert execution.id is not None
    assert execution.workflow_id == workflow.id
    assert execution.trigger_id == trigger.id
    assert execution.status == ExecutionStatus.RECEIVED
    assert execution.received_at is not None
    assert execution.started_at is None
    assert execution.completed_at is None
    assert execution.deployment_id is None
    assert execution.error_message is None

    # Verify it's in the database
    result = await session.execute(
        select(ExecutionHistory).where(ExecutionHistory.id == execution.id)
    )
    db_execution = result.scalar_one()
    assert db_execution.status == ExecutionStatus.RECEIVED


@pytest.mark.asyncio
async def test_update_execution_started(session):
    """Test updating execution to STARTED status with deployment."""
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)
    trigger = await create_test_trigger(session, workflow)
    deployment = await create_test_deployment(session, workflow)

    # Create execution
    execution = await create_execution_record(
        session=session,
        workflow_id=workflow.id,
        trigger_id=trigger.id,
    )

    # Update to started
    updated_execution = await update_execution_started(
        session=session,
        execution_id=execution.id,
        deployment_id=deployment.id,
    )

    # Verify status updated
    assert updated_execution.status == ExecutionStatus.STARTED
    assert updated_execution.deployment_id == deployment.id
    assert updated_execution.completed_at is None

    # Refetch to get server-generated timestamp
    await session.refresh(updated_execution)
    assert updated_execution.started_at is not None

    # Verify in database
    result = await session.execute(
        select(ExecutionHistory).where(ExecutionHistory.id == execution.id)
    )
    db_execution = result.scalar_one()
    assert db_execution.status == ExecutionStatus.STARTED
    assert db_execution.deployment_id == deployment.id


@pytest.mark.asyncio
async def test_update_execution_completed(session):
    """Test updating execution to COMPLETED status."""
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)
    trigger = await create_test_trigger(session, workflow)

    # Create and start execution
    execution = await create_execution_record(
        session=session,
        workflow_id=workflow.id,
        trigger_id=trigger.id,
    )

    # Update to completed
    completed_execution = await update_execution_completed(
        session=session,
        execution_id=execution.id,
    )

    # Verify status updated
    assert completed_execution.status == ExecutionStatus.COMPLETED
    assert completed_execution.error_message is None

    # Refetch to get server-generated timestamp
    await session.refresh(completed_execution)
    assert completed_execution.completed_at is not None

    # Verify in database
    result = await session.execute(
        select(ExecutionHistory).where(ExecutionHistory.id == execution.id)
    )
    db_execution = result.scalar_one()
    assert db_execution.status == ExecutionStatus.COMPLETED
    assert db_execution.completed_at is not None


@pytest.mark.asyncio
async def test_update_execution_failed(session):
    """Test updating execution to FAILED status with error details."""
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)
    trigger = await create_test_trigger(session, workflow)

    # Create execution
    execution = await create_execution_record(
        session=session,
        workflow_id=workflow.id,
        trigger_id=trigger.id,
    )

    error_message = "Test error occurred"

    # Update to failed
    failed_execution = await update_execution_failed(
        session=session,
        execution_id=execution.id,
        error_message=error_message,
    )

    # Verify status updated with error details
    assert failed_execution.status == ExecutionStatus.FAILED
    assert failed_execution.error_message == error_message

    # Refetch to get server-generated timestamp
    await session.refresh(failed_execution)
    assert failed_execution.completed_at is not None

    # Verify in database
    result = await session.execute(
        select(ExecutionHistory).where(ExecutionHistory.id == execution.id)
    )
    db_execution = result.scalar_one()
    assert db_execution.status == ExecutionStatus.FAILED
    assert db_execution.error_message == error_message


@pytest.mark.asyncio
async def test_update_execution_no_deployment(session):
    """Test updating execution to NO_DEPLOYMENT status."""
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)
    trigger = await create_test_trigger(session, workflow)

    # Create execution
    execution = await create_execution_record(
        session=session,
        workflow_id=workflow.id,
        trigger_id=trigger.id,
    )

    # Update to no deployment
    updated_execution = await update_execution_no_deployment(
        session=session,
        execution_id=execution.id,
    )

    # Verify status updated
    assert updated_execution.status == ExecutionStatus.NO_DEPLOYMENT

    # Refetch to get server-generated timestamp
    await session.refresh(updated_execution)
    assert updated_execution.completed_at is not None

    # Verify in database
    result = await session.execute(
        select(ExecutionHistory).where(ExecutionHistory.id == execution.id)
    )
    db_execution = result.scalar_one()
    assert db_execution.status == ExecutionStatus.NO_DEPLOYMENT


@pytest.mark.asyncio
async def test_get_execution_by_id(session):
    """Test retrieving execution by ID with relationships loaded."""
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)
    trigger = await create_test_trigger(session, workflow)
    deployment = await create_test_deployment(session, workflow)

    # Create and update execution
    execution = await create_execution_record(
        session=session,
        workflow_id=workflow.id,
        trigger_id=trigger.id,
    )
    await update_execution_started(
        session=session,
        execution_id=execution.id,
        deployment_id=deployment.id,
    )

    # Retrieve execution
    retrieved = await get_execution_by_id(session=session, execution_id=execution.id)

    # Verify execution retrieved with relationships
    assert retrieved is not None
    assert retrieved.id == execution.id
    assert retrieved.workflow is not None
    assert retrieved.workflow.id == workflow.id
    assert retrieved.trigger is not None
    assert retrieved.trigger.id == trigger.id
    assert retrieved.deployment is not None
    assert retrieved.deployment.id == deployment.id


@pytest.mark.asyncio
async def test_get_execution_by_id_not_found(session):
    """Test retrieving non-existent execution returns None."""
    result = await get_execution_by_id(session=session, execution_id=uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_get_executions_for_workflow(session):
    """Test listing executions for a workflow with default pagination."""
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)
    trigger = await create_test_trigger(session, workflow)

    # Create multiple executions
    executions = []
    for _ in range(3):
        execution = await create_execution_record(
            session=session,
            workflow_id=workflow.id,
            trigger_id=trigger.id,
        )
        executions.append(execution)

    # Retrieve executions
    results = await get_executions_for_workflow(
        session=session,
        workflow_id=workflow.id,
    )

    # Verify all executions returned
    assert len(results) == 3
    # Verify ordered by received_at DESC (most recent first)
    for i in range(len(results) - 1):
        assert results[i].received_at >= results[i + 1].received_at


@pytest.mark.asyncio
async def test_get_executions_for_workflow_pagination(session):
    """Test pagination with limit and offset."""
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)
    trigger = await create_test_trigger(session, workflow)

    # Create 5 executions
    for _ in range(5):
        await create_execution_record(
            session=session,
            workflow_id=workflow.id,
            trigger_id=trigger.id,
        )

    # Get first 2
    page_1 = await get_executions_for_workflow(
        session=session,
        workflow_id=workflow.id,
        limit=2,
        offset=0,
    )
    assert len(page_1) == 2

    # Get next 2
    page_2 = await get_executions_for_workflow(
        session=session,
        workflow_id=workflow.id,
        limit=2,
        offset=2,
    )
    assert len(page_2) == 2

    # Verify different results
    assert page_1[0].id != page_2[0].id


@pytest.mark.asyncio
async def test_get_executions_for_workflow_status_filter(session):
    """Test filtering executions by status."""
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)
    trigger = await create_test_trigger(session, workflow)

    # Create executions with different statuses
    execution_1 = await create_execution_record(
        session=session, workflow_id=workflow.id, trigger_id=trigger.id
    )
    execution_2 = await create_execution_record(
        session=session, workflow_id=workflow.id, trigger_id=trigger.id
    )
    execution_3 = await create_execution_record(
        session=session, workflow_id=workflow.id, trigger_id=trigger.id
    )

    # Update some to different statuses
    await update_execution_completed(session=session, execution_id=execution_1.id)
    await update_execution_failed(
        session=session, execution_id=execution_2.id, error_message="Test error"
    )
    # execution_3 stays as RECEIVED

    # Filter by COMPLETED
    completed = await get_executions_for_workflow(
        session=session,
        workflow_id=workflow.id,
        status=ExecutionStatus.COMPLETED,
    )
    assert len(completed) == 1
    assert completed[0].id == execution_1.id

    # Filter by FAILED
    failed = await get_executions_for_workflow(
        session=session,
        workflow_id=workflow.id,
        status=ExecutionStatus.FAILED,
    )
    assert len(failed) == 1
    assert failed[0].id == execution_2.id

    # Filter by RECEIVED
    received = await get_executions_for_workflow(
        session=session,
        workflow_id=workflow.id,
        status=ExecutionStatus.RECEIVED,
    )
    assert len(received) == 1
    assert received[0].id == execution_3.id


@pytest.mark.asyncio
async def test_get_executions_for_workflow_empty(session):
    """Test getting executions for workflow with no executions."""
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)

    results = await get_executions_for_workflow(
        session=session,
        workflow_id=workflow.id,
    )
    assert len(results) == 0


@pytest.mark.asyncio
async def test_get_recent_executions(session):
    """Test getting recent executions across all workflows."""
    user = await create_test_user(session)
    workflow_1 = await create_test_workflow(session, user)
    workflow_2 = await create_test_workflow(session, user)
    trigger_1 = await create_test_trigger(session, workflow_1)
    trigger_2 = await create_test_trigger(session, workflow_2)

    # Create executions in different workflows
    await create_execution_record(
        session=session, workflow_id=workflow_1.id, trigger_id=trigger_1.id
    )
    await create_execution_record(
        session=session, workflow_id=workflow_2.id, trigger_id=trigger_2.id
    )
    await create_execution_record(
        session=session, workflow_id=workflow_1.id, trigger_id=trigger_1.id
    )

    # Get recent executions
    results = await get_recent_executions(session=session, limit=100)

    # Verify all executions returned
    assert len(results) == 3
    # Verify relationships loaded
    for execution in results:
        assert execution.workflow is not None
        assert execution.trigger is not None


@pytest.mark.asyncio
async def test_get_recent_executions_limit(session):
    """Test recent executions respects limit."""
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)
    trigger = await create_test_trigger(session, workflow)

    # Create multiple executions
    for _ in range(5):
        await create_execution_record(
            session=session,
            workflow_id=workflow.id,
            trigger_id=trigger.id,
        )

    # Get with limit
    results = await get_recent_executions(session=session, limit=3)
    assert len(results) == 3


@pytest.mark.asyncio
async def test_serialize_execution_complete(session):
    """Test serializing execution with all fields populated."""
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)
    trigger = await create_test_trigger(session, workflow)
    deployment = await create_test_deployment(session, workflow)

    # Create and update execution through full lifecycle
    execution = await create_execution_record(
        session=session, workflow_id=workflow.id, trigger_id=trigger.id
    )
    execution = await update_execution_started(
        session=session, execution_id=execution.id, deployment_id=deployment.id
    )
    await session.refresh(execution)  # Get started_at

    execution = await update_execution_completed(
        session=session, execution_id=execution.id
    )
    await session.refresh(execution)  # Get completed_at

    # Retrieve with relationships
    execution = await get_execution_by_id(session=session, execution_id=execution.id)

    # Serialize
    serialized = serialize_execution(execution)

    # Verify all fields
    assert serialized["id"] == str(execution.id)
    assert serialized["workflow_id"] == str(workflow.id)
    assert serialized["trigger_id"] == str(trigger.id)
    assert serialized["deployment_id"] == str(deployment.id)
    assert serialized["status"] == "completed"
    assert serialized["received_at"] is not None
    assert serialized["started_at"] is not None
    assert serialized["completed_at"] is not None
    assert serialized["duration_ms"] is not None
    assert serialized["duration_ms"] >= 0
    assert serialized["error_message"] is None
    assert serialized["trigger_type"] == "onWebhook"


@pytest.mark.asyncio
async def test_serialize_execution_duration_calculation(session):
    """Test duration is calculated from started_at and completed_at."""
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)
    trigger = await create_test_trigger(session, workflow)
    deployment = await create_test_deployment(session, workflow)

    # Create execution and complete it
    execution = await create_execution_record(
        session=session, workflow_id=workflow.id, trigger_id=trigger.id
    )
    execution = await update_execution_started(
        session=session, execution_id=execution.id, deployment_id=deployment.id
    )
    await session.refresh(execution)  # Get started_at

    execution = await update_execution_completed(
        session=session, execution_id=execution.id
    )
    await session.refresh(execution)  # Get completed_at

    # Retrieve with relationships
    execution = await get_execution_by_id(session=session, execution_id=execution.id)

    # Serialize and check duration
    serialized = serialize_execution(execution)
    assert serialized["duration_ms"] is not None
    assert isinstance(serialized["duration_ms"], int)
    assert serialized["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_serialize_execution_partial_data(session):
    """Test serialization when optional fields are None."""
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)
    trigger = await create_test_trigger(session, workflow)

    # Create execution but don't start it
    execution = await create_execution_record(
        session=session, workflow_id=workflow.id, trigger_id=trigger.id
    )

    # Refresh to get server-generated timestamp
    await session.refresh(execution)

    # Retrieve with relationships
    execution = await get_execution_by_id(session=session, execution_id=execution.id)

    # Serialize
    serialized = serialize_execution(execution)

    # Verify None fields handled correctly
    assert serialized["started_at"] is None
    assert serialized["completed_at"] is None
    assert serialized["duration_ms"] is None
    assert serialized["deployment_id"] is None
    assert serialized["error_message"] is None


@pytest.mark.asyncio
async def test_update_execution_completed_with_structured_logs(session):
    """Test completing execution with structured log entries."""
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)
    trigger = await create_test_trigger(session, workflow)

    execution = await create_execution_record(
        session=session, workflow_id=workflow.id, trigger_id=trigger.id
    )

    structured_logs = [
        {"timestamp": "2025-01-01T10:00:00Z", "level": "info", "message": "Starting"},
        {"timestamp": "2025-01-01T10:00:01Z", "level": "debug", "message": "Debug info"},
        {"timestamp": "2025-01-01T10:00:02Z", "level": "error", "message": "Something failed"},
    ]

    await update_execution_completed(
        session=session,
        execution_id=execution.id,
        logs=structured_logs,
        duration_ms=2000,
    )
    await session.flush()

    # Verify log entries were created
    result = await session.execute(
        select(ExecutionLog).where(ExecutionLog.execution_history_id == execution.id)
    )
    log_entries = list(result.scalars().all())

    assert len(log_entries) == 3
    levels = {log.log_level for log in log_entries}
    assert LogLevel.INFO in levels
    assert LogLevel.DEBUG in levels
    assert LogLevel.ERROR in levels

    messages = {log.message for log in log_entries}
    assert "Starting" in messages
    assert "Debug info" in messages
    assert "Something failed" in messages


@pytest.mark.asyncio
async def test_update_execution_completed_with_legacy_string_log(session):
    """Test completing execution with legacy string log format."""
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)
    trigger = await create_test_trigger(session, workflow)

    execution = await create_execution_record(
        session=session, workflow_id=workflow.id, trigger_id=trigger.id
    )

    legacy_log = "Some plain text log output"

    await update_execution_completed(
        session=session,
        execution_id=execution.id,
        logs=legacy_log,
    )
    await session.flush()

    # Verify single log entry was created with LOG level
    result = await session.execute(
        select(ExecutionLog).where(ExecutionLog.execution_history_id == execution.id)
    )
    log_entries = list(result.scalars().all())

    assert len(log_entries) == 1
    assert log_entries[0].log_level == LogLevel.LOG
    assert log_entries[0].message == legacy_log


@pytest.mark.asyncio
async def test_update_execution_completed_with_duration(session):
    """Test that duration_ms is stored when provided."""
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)
    trigger = await create_test_trigger(session, workflow)

    execution = await create_execution_record(
        session=session, workflow_id=workflow.id, trigger_id=trigger.id
    )

    await update_execution_completed(
        session=session,
        execution_id=execution.id,
        duration_ms=1234,
    )
    await session.refresh(execution)

    assert execution.duration_ms == 1234

    # Verify serialization uses SDK-reported duration
    retrieved = await get_execution_by_id(session=session, execution_id=execution.id)
    serialized = serialize_execution(retrieved)
    assert serialized["duration_ms"] == 1234


@pytest.mark.asyncio
async def test_search_execution_logs_by_level(session):
    """Test filtering logs by level."""
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)
    trigger = await create_test_trigger(session, workflow)

    execution = await create_execution_record(
        session=session, workflow_id=workflow.id, trigger_id=trigger.id
    )

    logs = [
        {"timestamp": "2025-01-01T10:00:00Z", "level": "info", "message": "Info msg"},
        {"timestamp": "2025-01-01T10:00:01Z", "level": "error", "message": "Error msg"},
        {"timestamp": "2025-01-01T10:00:02Z", "level": "info", "message": "Another info"},
    ]

    await update_execution_completed(session=session, execution_id=execution.id, logs=logs)
    await session.flush()

    # Search for error logs only
    error_logs = await search_execution_logs(
        session=session,
        workflow_id=workflow.id,
        level="error",
    )
    assert len(error_logs) == 1
    assert error_logs[0].message == "Error msg"

    # Search for info logs
    info_logs = await search_execution_logs(
        session=session,
        workflow_id=workflow.id,
        level="info",
    )
    assert len(info_logs) == 2


@pytest.mark.asyncio
async def test_search_execution_logs_by_text(session):
    """Test searching logs by message content."""
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)
    trigger = await create_test_trigger(session, workflow)

    execution = await create_execution_record(
        session=session, workflow_id=workflow.id, trigger_id=trigger.id
    )

    logs = [
        {"timestamp": "2025-01-01T10:00:00Z", "level": "info", "message": "User logged in"},
        {"timestamp": "2025-01-01T10:00:01Z", "level": "info", "message": "Processing data"},
        {"timestamp": "2025-01-01T10:00:02Z", "level": "info", "message": "User logged out"},
    ]

    await update_execution_completed(session=session, execution_id=execution.id, logs=logs)
    await session.flush()

    # Search for "logged"
    logged_results = await search_execution_logs(
        session=session,
        workflow_id=workflow.id,
        search_query="logged",
    )
    assert len(logged_results) == 2

    # Search for "Processing"
    processing_results = await search_execution_logs(
        session=session,
        workflow_id=workflow.id,
        search_query="Processing",
    )
    assert len(processing_results) == 1


@pytest.mark.asyncio
async def test_serialize_execution_includes_log_entries(session):
    """Test that serialization includes log_entries array."""
    user = await create_test_user(session)
    workflow = await create_test_workflow(session, user)
    trigger = await create_test_trigger(session, workflow)

    execution = await create_execution_record(
        session=session, workflow_id=workflow.id, trigger_id=trigger.id
    )

    logs = [
        {"timestamp": "2025-01-01T10:00:00Z", "level": "info", "message": "Test log"},
    ]

    await update_execution_completed(session=session, execution_id=execution.id, logs=logs)
    await session.flush()

    # Retrieve with relationships (including log_entries)
    retrieved = await get_execution_by_id(session=session, execution_id=execution.id)
    serialized = serialize_execution(retrieved)

    assert "log_entries" in serialized
    assert len(serialized["log_entries"]) == 1
    assert serialized["log_entries"][0]["level"] == "info"
    assert serialized["log_entries"][0]["message"] == "Test log"
    assert "execution_id" in serialized["log_entries"][0]
