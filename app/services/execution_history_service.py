"""
Service for managing execution history records.

Provides CRUD operations for tracking workflow execution lifecycle:
- Create execution record when webhook received
- Update status as execution progresses
- Query execution history with filters
"""

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from sqlalchemy.sql import func

from app.models import ExecutionHistory, ExecutionStatus, Trigger


async def create_execution_record(
    session: AsyncSession,
    workflow_id: UUID,
    trigger_id: UUID,
) -> ExecutionHistory:
    """
    Create initial execution record when webhook received.

    Args:
        session: Database session
        workflow_id: ID of the workflow being executed
        trigger_id: ID of the trigger that initiated execution

    Returns:
        ExecutionHistory with status=RECEIVED
    """
    execution = ExecutionHistory(
        workflow_id=workflow_id,
        trigger_id=trigger_id,
        status=ExecutionStatus.RECEIVED,
    )
    session.add(execution)
    await session.flush()
    return execution


async def update_execution_started(
    session: AsyncSession,
    execution_id: UUID,
    deployment_id: UUID,
) -> ExecutionHistory:
    """
    Mark execution as started when runtime invoked.

    Args:
        session: Database session
        execution_id: ID of the execution
        deployment_id: ID of the deployment being used

    Returns:
        Updated ExecutionHistory
    """
    result = await session.execute(
        select(ExecutionHistory).where(ExecutionHistory.id == execution_id)
    )
    execution = result.scalar_one()

    execution.status = ExecutionStatus.STARTED
    execution.started_at = func.now()
    execution.deployment_id = deployment_id

    await session.flush()
    return execution


async def update_execution_completed(
    session: AsyncSession,
    execution_id: UUID,
    logs: Optional[str] = None,
) -> ExecutionHistory:
    """
    Mark execution as successfully completed.

    Args:
        session: Database session
        execution_id: ID of the execution
        logs: Optional execution logs

    Returns:
        Updated ExecutionHistory
    """
    result = await session.execute(
        select(ExecutionHistory).where(ExecutionHistory.id == execution_id)
    )
    execution = result.scalar_one()

    execution.status = ExecutionStatus.COMPLETED
    execution.completed_at = func.now()
    execution.logs = logs

    await session.flush()
    return execution


async def update_execution_failed(
    session: AsyncSession,
    execution_id: UUID,
    error_message: str,
    error_stack: Optional[str] = None,
    logs: Optional[str] = None,
) -> ExecutionHistory:
    """
    Mark execution as failed with error details.

    Args:
        session: Database session
        execution_id: ID of the execution
        error_message: Error message
        error_stack: Optional error stack trace
        logs: Optional execution logs

    Returns:
        Updated ExecutionHistory
    """
    result = await session.execute(
        select(ExecutionHistory).where(ExecutionHistory.id == execution_id)
    )
    execution = result.scalar_one()

    execution.status = ExecutionStatus.FAILED
    execution.completed_at = func.now()
    execution.error_message = error_message
    execution.error_stack = error_stack
    execution.logs = logs

    await session.flush()
    return execution


async def update_execution_no_deployment(
    session: AsyncSession,
    execution_id: UUID,
) -> ExecutionHistory:
    """
    Mark execution when no deployment found.

    Args:
        session: Database session
        execution_id: ID of the execution

    Returns:
        Updated ExecutionHistory
    """
    result = await session.execute(
        select(ExecutionHistory).where(ExecutionHistory.id == execution_id)
    )
    execution = result.scalar_one()

    execution.status = ExecutionStatus.NO_DEPLOYMENT
    execution.completed_at = func.now()

    await session.flush()
    return execution


async def get_execution_by_id(
    session: AsyncSession,
    execution_id: UUID,
) -> Optional[ExecutionHistory]:
    """
    Retrieve single execution with relationships loaded.

    Args:
        session: Database session
        execution_id: ID of the execution

    Returns:
        ExecutionHistory or None if not found
    """
    result = await session.execute(
        select(ExecutionHistory)
        .options(
            joinedload(ExecutionHistory.workflow),
            joinedload(ExecutionHistory.trigger).joinedload(Trigger.incoming_webhooks),
            joinedload(ExecutionHistory.deployment),
        )
        .where(ExecutionHistory.id == execution_id)
    )
    return result.unique().scalar_one_or_none()


async def get_executions_for_workflow(
    session: AsyncSession,
    workflow_id: UUID,
    limit: int = 50,
    offset: int = 0,
    status: Optional[ExecutionStatus] = None,
) -> list[ExecutionHistory]:
    """
    List executions for a workflow with pagination and filtering.

    Args:
        session: Database session
        workflow_id: ID of the workflow
        limit: Maximum number of results (default 50)
        offset: Number of results to skip (default 0)
        status: Optional status filter

    Returns:
        List of ExecutionHistory ordered by received_at DESC
    """
    query = (
        select(ExecutionHistory)
        .options(
            joinedload(ExecutionHistory.trigger).joinedload(Trigger.incoming_webhooks),
            joinedload(ExecutionHistory.deployment),
        )
        .where(ExecutionHistory.workflow_id == workflow_id)
    )

    if status:
        query = query.where(ExecutionHistory.status == status)

    query = (
        query.order_by(ExecutionHistory.received_at.desc()).limit(limit).offset(offset)
    )

    result = await session.execute(query)
    return list(result.unique().scalars().all())


async def get_recent_executions(
    session: AsyncSession,
    limit: int = 100,
) -> list[ExecutionHistory]:
    """
    Admin view of recent executions across all workflows.

    Args:
        session: Database session
        limit: Maximum number of results (default 100)

    Returns:
        List of ExecutionHistory ordered by received_at DESC
    """
    result = await session.execute(
        select(ExecutionHistory)
        .options(
            joinedload(ExecutionHistory.workflow),
            joinedload(ExecutionHistory.trigger).joinedload(Trigger.incoming_webhooks),
            joinedload(ExecutionHistory.deployment),
        )
        .order_by(ExecutionHistory.received_at.desc())
        .limit(limit)
    )
    return list(result.unique().scalars().all())


def serialize_execution(execution: ExecutionHistory) -> dict:
    """
    Serialize execution to dictionary with derived fields.

    Calculates duration on the fly and retrieves contextual data
    from relationships to avoid duplication in the database.

    Args:
        execution: ExecutionHistory instance

    Returns:
        Dictionary representation of the execution
    """
    # Calculate duration on the fly
    duration_ms = None
    if execution.started_at and execution.completed_at:
        duration = execution.completed_at - execution.started_at
        duration_ms = int(duration.total_seconds() * 1000)

    # Get trigger details if available
    trigger_type = None
    webhook_path = None
    webhook_method = None
    if execution.trigger:
        trigger_type = execution.trigger.trigger_type
        if execution.trigger.incoming_webhooks:
            webhook_path = execution.trigger.incoming_webhooks[0].path
            webhook_method = execution.trigger.incoming_webhooks[0].method

    return {
        "id": str(execution.id),
        "workflow_id": str(execution.workflow_id),
        "trigger_id": str(execution.trigger_id) if execution.trigger_id else None,
        "deployment_id": (
            str(execution.deployment_id) if execution.deployment_id else None
        ),
        "status": execution.status.value,
        "received_at": execution.received_at.isoformat(),
        "started_at": execution.started_at.isoformat()
        if execution.started_at
        else None,
        "completed_at": (
            execution.completed_at.isoformat() if execution.completed_at else None
        ),
        "duration_ms": duration_ms,
        "error_message": execution.error_message,
        "logs": execution.logs,
        # Derived from relationships:
        "trigger_type": trigger_type,
        "webhook_path": webhook_path,
        "webhook_method": webhook_method,
    }
