"""Routes for manual trigger invocation and management."""

from uuid import UUID

import jsonschema
import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep
from app.models import ExecutionHistory, Trigger, Workflow
from app.services.execution_history_service import create_execution_record
from app.services.trigger_execution_service import execute_trigger
from app.utils.query_helpers import UserAccessibleQuery

router = APIRouter(prefix="/triggers", tags=["Triggers"])
logger = structlog.stdlib.get_logger(__name__)


async def _check_execution_limit_for_workflow(
    session: SessionDep,
    workflow_id: UUID,
) -> None:
    """
    Check if the workflow's organization has reached their execution limit.
    Raises HTTPException if limit is reached.
    """
    from app.models import Namespace
    from app.services import billing_service
    from app.settings import settings

    if not settings.IS_CLOUD:
        return

    from sqlalchemy.orm import joinedload

    result = await session.execute(
        select(Workflow)
        .options(
            joinedload(Workflow.namespace).joinedload(Namespace.organization_owner)
        )
        .where(Workflow.id == workflow_id)
    )
    workflow = result.scalar_one_or_none()

    if workflow and workflow.namespace and workflow.namespace.organization_owner:
        organization = workflow.namespace.organization_owner
        can_execute, message = await billing_service.check_execution_limit(
            session, organization
        )

        if not can_execute:
            logger.warning(
                "Execution limit reached for organization",
                organization_id=organization.id,
                workflow_id=workflow_id,
            )
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "title": "Execution limit reached",
                    "description": message,
                    "upgrade_required": True,
                },
            )


class ManualTriggerInvokeRequest(BaseModel):
    """Request body for invoking a manual trigger."""

    input_data: dict = {}


class ManualTriggerInvokeResponse(BaseModel):
    """Response for manual trigger invocation."""

    execution_id: str
    status: str


class ManualTriggerInfo(BaseModel):
    """Information about a manual trigger."""

    id: str
    name: str
    description: str | None
    input_schema: dict | None
    execution_count: int


class ManualTriggersListResponse(BaseModel):
    """Response for listing manual triggers."""

    triggers: list[ManualTriggerInfo]


@router.post("/{trigger_id}/invoke", response_model=ManualTriggerInvokeResponse)
async def invoke_manual_trigger(
    trigger_id: UUID,
    request: ManualTriggerInvokeRequest,
    current_user: CurrentUser,
    session: SessionDep,
):
    """
    Manually invoke a trigger (for manual trigger types only).
    Validates user input against trigger's parameter schema if defined.
    """
    # Load trigger with relationships
    result = await session.execute(
        select(Trigger)
        .options(
            selectinload(Trigger.workflow).selectinload(Workflow.namespace),
            selectinload(Trigger.provider),
        )
        .where(Trigger.id == trigger_id)
    )
    trigger = result.scalar_one_or_none()

    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found")

    # Verify trigger is manual type
    if trigger.trigger_type != "onManual":
        raise HTTPException(
            status_code=400,
            detail="Only manual triggers can be invoked via this endpoint",
        )

    # Verify user has access to workflow
    accessible_query = UserAccessibleQuery(current_user.id).workflows()
    workflow_result = await session.execute(
        accessible_query.where(Workflow.id == trigger.workflow_id)
    )
    workflow = workflow_result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(
            status_code=404,
            detail="Workflow not found or you don't have access to it",
        )

    # Validate input data against schema if defined
    if trigger.input and "input_schema" in trigger.input:
        input_schema = trigger.input["input_schema"]
        if input_schema:
            try:
                jsonschema.validate(
                    instance=request.input_data,
                    schema=input_schema,
                )
            except jsonschema.ValidationError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Input validation failed: {e.message}",
                )
            except jsonschema.SchemaError as e:
                logger.error(
                    "Invalid JSON schema for manual trigger",
                    trigger_id=str(trigger_id),
                    error=str(e),
                )
                raise HTTPException(
                    status_code=500,
                    detail="Invalid trigger schema configuration",
                )

    # Check execution limits
    await _check_execution_limit_for_workflow(session, trigger.workflow_id)

    # Create execution record
    execution = await create_execution_record(
        session=session,
        workflow_id=trigger.workflow_id,
        trigger_id=trigger.id,
        triggered_by_user_id=current_user.id,
    )

    # Build manual event data
    event_data = {
        "manually_triggered": True,
        "triggered_by": str(current_user.id),
        "input_data": request.input_data,
    }

    # Execute trigger
    await execute_trigger(
        session=session,
        trigger=trigger,
        event_data=event_data,
        execution_id=execution.id,
    )

    return ManualTriggerInvokeResponse(
        execution_id=str(execution.id),
        status="invoked",
    )


@router.get(
    "/workflows/{workflow_id}/manual", response_model=ManualTriggersListResponse
)
async def list_manual_triggers(
    workflow_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
):
    """
    List all manual triggers for a workflow.
    Includes trigger metadata and execution statistics.
    """
    # Verify user has access to workflow
    accessible_query = UserAccessibleQuery(current_user.id).workflows()
    workflow_result = await session.execute(
        accessible_query.where(Workflow.id == workflow_id)
    )
    workflow = workflow_result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(
            status_code=404,
            detail="Workflow not found or you don't have access to it",
        )

    # Get all manual triggers for this workflow
    triggers_result = await session.execute(
        select(Trigger).where(
            Trigger.workflow_id == workflow_id, Trigger.trigger_type == "onManual"
        )
    )
    triggers = triggers_result.scalars().all()

    # Build response with trigger info and execution counts
    trigger_infos = []
    for trigger in triggers:
        # Count executions for this trigger
        execution_count_result = await session.execute(
            select(ExecutionHistory)
            .where(ExecutionHistory.trigger_id == trigger.id)
            .where(ExecutionHistory.workflow_id == workflow_id)
        )
        execution_count = len(execution_count_result.scalars().all())

        # Extract trigger metadata from state (stored during creation)
        name = (
            trigger.state.get("name", "Unnamed Trigger")
            if trigger.state
            else "Unnamed Trigger"
        )
        description = trigger.state.get("description") if trigger.state else None
        input_schema = trigger.state.get("input_schema") if trigger.state else None

        trigger_infos.append(
            ManualTriggerInfo(
                id=str(trigger.id),
                name=name,
                description=description,
                input_schema=input_schema,
                execution_count=execution_count,
            )
        )

    return ManualTriggersListResponse(triggers=trigger_infos)
