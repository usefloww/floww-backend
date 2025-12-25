import json
from datetime import datetime
from typing import Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep, TransactionSessionDep
from app.factories import runtime_factory
from app.models import (
    Provider,
    Runtime,
    Workflow,
    WorkflowDeployment,
    WorkflowDeploymentStatus,
)
from app.packages.runtimes.runtime_types import RuntimeConfig
from app.services.crud_helpers import CrudHelper
from app.services.trigger_service import TriggerService
from app.utils.encryption import decrypt_secret
from app.utils.query_helpers import UserAccessibleQuery

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/workflow_deployments", tags=["Workflow Deployments"])


class WebhookInfo(BaseModel):
    id: UUID
    url: str
    path: Optional[str] = None
    method: Optional[str] = None
    trigger_id: Optional[UUID] = None
    trigger_type: Optional[str] = None
    provider_type: Optional[str] = None
    provider_alias: Optional[str] = None


class WorkflowDeploymentRead(BaseModel):
    id: UUID
    workflow_id: UUID
    runtime_id: UUID
    deployed_by_id: Optional[UUID]
    user_code: dict
    status: WorkflowDeploymentStatus
    deployed_at: datetime
    note: Optional[str] = None
    webhooks: Optional[list[WebhookInfo]] = None


class WorkflowDeploymentUserCode(BaseModel):
    files: dict[str, str]
    entrypoint: str


class TriggerMetadata(BaseModel):
    type: str  # "webhook", "cron", "realtime"
    path: Optional[str] = None  # For webhook triggers
    method: Optional[str] = None  # For webhook triggers
    expression: Optional[str] = None  # For cron triggers
    channel: Optional[str] = None  # For realtime triggers
    # Provider-managed trigger fields
    provider_type: Optional[str] = None  # e.g., "gitlab"
    provider_alias: Optional[str] = None  # e.g., "default"
    trigger_type: Optional[str] = None  # e.g., "onMergeRequestComment"
    input: Optional[dict] = None  # Provider-specific input


class WorkflowDeploymentCreate(BaseModel):
    workflow_id: UUID
    runtime_id: UUID
    code: WorkflowDeploymentUserCode
    triggers: Optional[list[TriggerMetadata]] = None


class WorkflowDeploymentUpdate(BaseModel):
    status: Optional[WorkflowDeploymentStatus] = None
    user_code: Optional[dict] = None


def validate_definitions(
    runtime_definitions: dict,
) -> tuple[bool, Optional[str]]:
    """
    Validate that runtime successfully extracted definitions from user code.

    Args:
        runtime_definitions: Definitions extracted from runtime

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not runtime_definitions.get("success"):
        error = runtime_definitions.get("error", {})
        error_msg = error.get("message", "Unknown error")
        error_stack = error.get("stack", "")
        return (
            False,
            f"Runtime failed to extract definitions: {error_msg}\n{error_stack}",
        )

    return True, None


async def _get_provider_configs(
    session: SessionDep,
    namespace_id: UUID,
) -> dict[str, dict]:
    """Fetch and decrypt all provider configs for namespace."""
    result = await session.execute(
        select(Provider).where(Provider.namespace_id == namespace_id)
    )
    providers = result.scalars().all()

    provider_configs = {}
    for provider in providers:
        config_json = decrypt_secret(provider.encrypted_config)
        config = json.loads(config_json)
        key = f"{provider.type}:{provider.alias}"
        provider_configs[key] = config

    return provider_configs


def helper_factory(user: CurrentUser, session: SessionDep):
    return CrudHelper(
        session=session,
        resource_name="workflow_deployment",
        database_model=WorkflowDeployment,
        read_model=WorkflowDeploymentRead,
        create_model=WorkflowDeploymentCreate,
        update_model=WorkflowDeploymentUpdate,
        query_builder=lambda: UserAccessibleQuery(user.id).deployments(),
    )


@router.get("")
async def list_workflow_deployments(
    current_user: CurrentUser, session: SessionDep, workflow_id: Optional[UUID] = None
):
    """List workflow deployments accessible to the authenticated user."""
    query = UserAccessibleQuery(current_user.id).deployments()
    if workflow_id:
        query = query.where(WorkflowDeployment.workflow_id == workflow_id)

    result = await session.execute(query)
    deployments = result.scalars().all()

    deployment_results = [
        WorkflowDeploymentRead.model_validate(d, from_attributes=True)
        for d in deployments
    ]
    return {"deployments": deployment_results}


@router.post("")
async def create_workflow_deployment(
    data: WorkflowDeploymentCreate,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Create a new workflow deployment."""

    # Verify user has access to the workflow
    workflow_query = (
        UserAccessibleQuery(current_user.id)
        .workflows()
        .where(Workflow.id == data.workflow_id)
    )
    workflow_result = await session.execute(workflow_query)
    workflow = workflow_result.scalar_one_or_none()
    if not workflow:
        raise HTTPException(status_code=400, detail="Workflow not found")

    runtime_query = (
        UserAccessibleQuery(current_user.id)
        .runtimes()
        .where(Runtime.id == data.runtime_id)
    )
    runtime_result = await session.execute(runtime_query)
    runtime = runtime_result.scalar_one_or_none()
    if not runtime:
        raise HTTPException(status_code=400, detail="Runtime not found")

    # Create the workflow deployment manually with additional fields
    workflow_deployment = WorkflowDeployment(
        workflow_id=data.workflow_id,
        runtime_id=data.runtime_id,
        deployed_by_id=current_user.id,
        user_code={
            "files": data.code.files,
            "entrypoint": data.code.entrypoint,
        },
        status=WorkflowDeploymentStatus.ACTIVE,
    )

    session.add(workflow_deployment)
    await session.flush()
    await session.refresh(workflow_deployment)

    # Set workflow.active to True if it's currently None (first deployment)
    if workflow.active is None:
        workflow.active = True
        session.add(workflow)

    # Always validate definitions by calling get_definitions on the runtime
    logger.info(
        "Validating workflow deployment definitions",
        deployment_id=str(workflow_deployment.id),
    )

    # Fetch and decrypt provider configs
    provider_configs = await _get_provider_configs(session, workflow.namespace_id)

    # Call runtime.get_definitions()
    runtime_impl = runtime_factory()
    runtime_definitions = await runtime_impl.get_definitions(
        runtime_config=RuntimeConfig(
            runtime_id=str(runtime.id),
            image_digest=(runtime.config or {}).get("image_hash", ""),
        ),
        user_code=workflow_deployment.user_code,
        provider_configs=provider_configs,
    )

    # Validate definitions were successfully extracted
    is_valid, error_message = validate_definitions(runtime_definitions)
    if not is_valid:
        logger.error(
            "Definition validation failed",
            deployment_id=str(workflow_deployment.id),
            error=error_message,
        )
        raise HTTPException(
            status_code=400,
            detail=f"Deployment validation failed: {error_message}",
        )

    # Populate provider_definitions and trigger_definitions fields
    workflow_deployment.provider_definitions = runtime_definitions.get("providers", [])
    workflow_deployment.trigger_definitions = runtime_definitions.get("triggers", [])
    session.add(workflow_deployment)

    logger.info(
        "Deployment definitions validated successfully",
        deployment_id=str(workflow_deployment.id),
        providers_count=len(runtime_definitions.get("providers", [])),
        triggers_count=len(runtime_definitions.get("triggers", [])),
    )

    # Convert runtime trigger definitions to format expected by TriggerService
    runtime_triggers = runtime_definitions.get("triggers", [])
    triggers_metadata = []
    for trigger_def in runtime_triggers:
        trigger_meta = {
            "provider_type": trigger_def["provider"]["type"],
            "provider_alias": trigger_def["provider"]["alias"],
            "trigger_type": trigger_def["triggerType"],
            "input": trigger_def.get("input"),
        }
        triggers_metadata.append(trigger_meta)

    # Sync triggers using TriggerService (handles provider-managed triggers)
    webhooks_info = []
    if triggers_metadata:
        trigger_service = TriggerService(session)

        # Sync provider-managed triggers (raises HTTPException if any fail)
        provider_webhooks = await trigger_service.sync_triggers(
            workflow_id=data.workflow_id,
            namespace_id=workflow.namespace_id,
            new_triggers_metadata=triggers_metadata,
        )
        webhooks_info.extend([WebhookInfo(**wh) for wh in provider_webhooks])

    logger.info(
        "Created new workflow deployment",
        deployment_id=str(workflow_deployment.id),
        workflow_id=str(workflow_deployment.workflow_id),
        webhooks_count=len(webhooks_info),
    )

    # Build response
    deployment_dict = {
        "id": workflow_deployment.id,
        "workflow_id": workflow_deployment.workflow_id,
        "runtime_id": workflow_deployment.runtime_id,
        "deployed_by_id": workflow_deployment.deployed_by_id,
        "user_code": workflow_deployment.user_code,
        "status": workflow_deployment.status,
        "deployed_at": workflow_deployment.deployed_at,
        "note": workflow_deployment.note,
        "webhooks": webhooks_info,
    }

    return WorkflowDeploymentRead.model_validate(deployment_dict)


@router.get("/{deployment_id}")
async def get_workflow_deployment(
    deployment_id: UUID, current_user: CurrentUser, session: SessionDep
):
    """Get a specific workflow deployment."""
    helper = helper_factory(current_user, session)
    result = await helper.get_response(deployment_id)
    return result


@router.patch("/{deployment_id}")
async def update_workflow_deployment(
    deployment_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
    data: WorkflowDeploymentUpdate,
):
    """Update a specific workflow deployment."""
    helper = helper_factory(current_user, session)
    result = await helper.update_response(deployment_id, data)
    return result


@router.delete("/{deployment_id}")
async def delete_workflow_deployment(
    deployment_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Delete a workflow deployment."""
    helper = helper_factory(current_user, session)
    response = await helper.delete_response(deployment_id)
    return response
