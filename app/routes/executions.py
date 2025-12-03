"""
Execution history API endpoints.

Provides endpoints for:
- Completing execution records (called by SDK)
- Querying execution history for workflows
- Viewing execution details
"""

from typing import Any, Optional, Union
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep, TransactionSessionDep
from app.models import ExecutionStatus, Workflow
from app.services.execution_history_service import (
    get_execution_by_id,
    get_executions_for_workflow,
    search_execution_logs,
    serialize_execution,
    serialize_log,
    update_execution_completed,
    update_execution_failed,
)
from app.services.workflow_auth_service import WorkflowAuthService
from app.utils.query_helpers import UserAccessibleQuery

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/executions", tags=["Executions"])


class ExecutionErrorRequest(BaseModel):
    message: str


class StructuredLogEntry(BaseModel):
    timestamp: str
    level: str
    message: str


class ExecutionCompleteRequest(BaseModel):
    error: Optional[ExecutionErrorRequest] = None
    logs: Optional[Union[str, list[StructuredLogEntry]]] = None
    duration_ms: Optional[int] = None


@router.post("/{execution_id}/complete")
async def complete_execution(
    execution_id: UUID,
    request: Request,
    body: ExecutionCompleteRequest,
    session: TransactionSessionDep,
):
    """
    Mark an execution as completed or failed.

    Called by the SDK runtime handler after workflow execution.
    Accepts workflow invocation token for authentication.
    """
    # Extract and validate Bearer token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401, detail="Missing or invalid Authorization header"
        )

    token = auth_header.replace("Bearer ", "")

    # Validate the invocation token and get workflow_id
    try:
        workflow_id = WorkflowAuthService.verify_invocation_token(token)
    except Exception as e:
        logger.warning("Invalid invocation token", error=str(e))
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # Get execution record
    execution = await get_execution_by_id(session, execution_id)
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    # Verify token matches the execution's workflow
    if execution.workflow_id != workflow_id:
        raise HTTPException(
            status_code=403, detail="Token does not match execution workflow"
        )

    # Convert structured logs to dict format for service layer
    logs_data: Optional[Union[str, list[dict[str, Any]]]] = None
    if body.logs is not None:
        if isinstance(body.logs, str):
            logs_data = body.logs
        else:
            logs_data = [entry.model_dump() for entry in body.logs]

    # Update execution based on success/failure
    if body.error:
        await update_execution_failed(
            session,
            execution_id,
            error_message=body.error.message,
            logs=logs_data,
            duration_ms=body.duration_ms,
        )
        logger.info(
            "Execution marked as failed",
            execution_id=str(execution_id),
            error_message=body.error.message,
        )
    else:
        await update_execution_completed(
            session,
            execution_id,
            logs=logs_data,
            duration_ms=body.duration_ms,
        )
        logger.info("Execution marked as completed", execution_id=str(execution_id))

    return {"status": "ok"}


@router.get("/workflows/{workflow_id}")
async def get_workflow_executions(
    workflow_id: UUID,
    session: SessionDep,
    current_user: CurrentUser,
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
):
    """
    List executions for a workflow with pagination and filtering.

    Query parameters:
    - limit: Maximum number of results (default 50, max 100)
    - offset: Number of results to skip (default 0)
    - status: Optional status filter (received, started, completed, failed, timeout, no_deployment)
    """
    # Verify user has access to workflow
    query = UserAccessibleQuery(current_user.id).workflows()
    result = await session.execute(query.where(Workflow.id == workflow_id))
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(
            status_code=404, detail="Workflow not found or access denied"
        )

    # Validate and parse status filter
    parsed_status = None
    if status:
        try:
            parsed_status = ExecutionStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Must be one of: {', '.join([s.value for s in ExecutionStatus])}",
            )

    # Limit max results
    if limit > 100:
        limit = 100

    # Get executions
    executions = await get_executions_for_workflow(
        session, workflow_id, limit=limit, offset=offset, status=parsed_status
    )

    return {
        "workflow_id": str(workflow_id),
        "limit": limit,
        "offset": offset,
        "count": len(executions),
        "executions": [serialize_execution(e) for e in executions],
    }


@router.get("/{execution_id}")
async def get_execution_detail(
    execution_id: UUID,
    session: SessionDep,
    current_user: CurrentUser,
):
    """Get detailed information about a specific execution."""
    # Get execution with relationships
    execution = await get_execution_by_id(session, execution_id)
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    # Verify user has access to the workflow
    query = UserAccessibleQuery(current_user.id).workflows()
    result = await session.execute(
        query.where(Workflow.id == execution.workflow_id)
    )
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(
            status_code=404, detail="Execution not found or access denied"
        )

    return serialize_execution(execution)


@router.get("/workflows/{workflow_id}/logs")
async def get_workflow_logs(
    workflow_id: UUID,
    session: SessionDep,
    current_user: CurrentUser,
    q: Optional[str] = None,
    level: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """
    Search logs across all executions for a workflow.

    Query parameters:
    - q: Optional text search query
    - level: Optional log level filter (debug, info, warn, error, log)
    - limit: Maximum number of results (default 100, max 500)
    - offset: Number of results to skip (default 0)
    """
    # Verify user has access to workflow
    query = UserAccessibleQuery(current_user.id).workflows()
    result = await session.execute(query.where(Workflow.id == workflow_id))
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(
            status_code=404, detail="Workflow not found or access denied"
        )

    # Limit max results
    if limit > 500:
        limit = 500

    logs = await search_execution_logs(
        session,
        workflow_id=workflow_id,
        search_query=q,
        level=level,
        limit=limit,
        offset=offset,
    )

    return {
        "workflow_id": str(workflow_id),
        "logs": [serialize_log(log) for log in logs],
        "limit": limit,
        "offset": offset,
    }
