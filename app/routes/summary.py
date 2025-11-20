from datetime import datetime, timedelta, timezone
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep
from app.models import ExecutionHistory, ExecutionStatus, Namespace, Workflow
from app.utils.query_helpers import UserAccessibleQuery

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/summary", tags=["Summary"])


class ExecutionDaySummary(BaseModel):
    date: str
    total: int
    completed: int
    failed: int
    started: int
    received: int
    timeout: int
    no_deployment: int


class SummaryResponse(BaseModel):
    executions_by_day: list[ExecutionDaySummary]
    total_executions: int
    total_completed: int
    total_failed: int
    period_days: int


@router.get("")
async def get_summary(
    namespace_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
    days: int = 7,
):
    """Get execution summary for a namespace over the specified period."""
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="Days must be between 1 and 365")

    # Verify user has access to the namespace
    namespace_query = (
        UserAccessibleQuery(current_user.id)
        .namespaces()
        .where(Namespace.id == namespace_id)
    )
    namespace_result = await session.execute(namespace_query)
    namespace = namespace_result.scalar_one_or_none()

    if not namespace:
        raise HTTPException(status_code=404, detail="Namespace not found")

    # Calculate date range
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)

    # Get all workflows in this namespace
    workflows_query = (
        UserAccessibleQuery(current_user.id)
        .workflows()
        .where(Workflow.namespace_id == namespace_id)
    )
    workflows_result = await session.execute(workflows_query)
    workflows = workflows_result.scalars().all()
    workflow_ids = [w.id for w in workflows]

    if not workflow_ids:
        # Return empty summary if no workflows
        return SummaryResponse(
            executions_by_day=[],
            total_executions=0,
            total_completed=0,
            total_failed=0,
            period_days=days,
        )

    # Query executions grouped by date and status
    executions_query = (
        select(
            func.date(ExecutionHistory.received_at).label("date"),
            ExecutionHistory.status,
            func.count(ExecutionHistory.id).label("count"),
        )
        .where(
            ExecutionHistory.workflow_id.in_(workflow_ids),
            ExecutionHistory.received_at >= start_date,
            ExecutionHistory.received_at <= end_date,
        )
        .group_by(func.date(ExecutionHistory.received_at), ExecutionHistory.status)
        .order_by(func.date(ExecutionHistory.received_at))
    )

    result = await session.execute(executions_query)
    rows = result.all()

    # Build date map with all status counts
    date_map: dict[str, dict[str, int]] = {}
    total_executions = 0
    total_completed = 0
    total_failed = 0

    for row in rows:
        date_str = row.date.isoformat()
        if date_str not in date_map:
            date_map[date_str] = {
                "total": 0,
                "completed": 0,
                "failed": 0,
                "started": 0,
                "received": 0,
                "timeout": 0,
                "no_deployment": 0,
            }

        # Access count from the row - SQLAlchemy returns it as an integer
        count = getattr(row, "count", 0)
        if not isinstance(count, int):
            count = int(count) if count else 0
        status = row.status.value if hasattr(row.status, "value") else str(row.status)

        date_map[date_str]["total"] += count
        total_executions += count

        if status == ExecutionStatus.COMPLETED.value:
            date_map[date_str]["completed"] += count
            total_completed += count
        elif status == ExecutionStatus.FAILED.value:
            date_map[date_str]["failed"] += count
            total_failed += count
        elif status == ExecutionStatus.STARTED.value:
            date_map[date_str]["started"] += count
        elif status == ExecutionStatus.RECEIVED.value:
            date_map[date_str]["received"] += count
        elif status == ExecutionStatus.TIMEOUT.value:
            date_map[date_str]["timeout"] += count
        elif status == ExecutionStatus.NO_DEPLOYMENT.value:
            date_map[date_str]["no_deployment"] += count

    # Fill in missing dates with zeros
    executions_by_day = []
    current_date = start_date.date()
    end_date_only = end_date.date()

    while current_date <= end_date_only:
        date_str = current_date.isoformat()
        day_data = date_map.get(
            date_str,
            {
                "total": 0,
                "completed": 0,
                "failed": 0,
                "started": 0,
                "received": 0,
                "timeout": 0,
                "no_deployment": 0,
            },
        )

        executions_by_day.append(
            ExecutionDaySummary(
                date=date_str,
                total=day_data["total"],
                completed=day_data["completed"],
                failed=day_data["failed"],
                started=day_data["started"],
                received=day_data["received"],
                timeout=day_data["timeout"],
                no_deployment=day_data["no_deployment"],
            )
        )

        current_date += timedelta(days=1)

    return SummaryResponse(
        executions_by_day=executions_by_day,
        total_executions=total_executions,
        total_completed=total_completed,
        total_failed=total_failed,
        period_days=days,
    )
