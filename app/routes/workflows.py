from datetime import datetime
from typing import Optional, cast
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.deps.auth import CurrentUser
from app.deps.billing import check_can_create_workflow
from app.deps.db import SessionDep, TransactionSessionDep
from app.models import Namespace, Workflow, WorkflowDeployment
from app.services.crud_helpers import CrudHelper, ListResult
from app.utils.query_helpers import UserAccessibleQuery

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/workflows", tags=["Workflows"])


class CreatedByUser(BaseModel):
    id: UUID
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class LastDeployment(BaseModel):
    deployed_at: datetime
    provider_definitions: Optional[list[dict]] = None


class WorkflowRead(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    namespace_id: UUID
    created_by_id: Optional[UUID] = None
    created_by: Optional[CreatedByUser] = None
    created_at: datetime
    updated_at: datetime
    active: Optional[bool] = None
    last_deployment: Optional[LastDeployment] = None


class WorkflowCreate(BaseModel):
    name: str
    namespace_id: UUID
    description: Optional[str] = None


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    namespace_id: Optional[UUID] = None
    active: Optional[bool] = None


class N8nImportRequest(BaseModel):
    namespace_id: UUID
    n8n_json: dict  # The raw n8n workflow JSON


class N8nImportResponse(BaseModel):
    workflow: WorkflowRead
    generated_code: str
    message: str


def helper_factory(user: CurrentUser, session: SessionDep):
    return CrudHelper(
        session=session,
        resource_name="workflow",
        database_model=Workflow,
        read_model=WorkflowRead,
        create_model=WorkflowCreate,
        update_model=WorkflowUpdate,
        query_builder=lambda: UserAccessibleQuery(user.id).workflows(),
    )


@router.get("")
async def list_workflows(
    current_user: CurrentUser,
    session: SessionDep,
    namespace_id: Optional[UUID] = None,
):
    """List workflows accessible to the authenticated user."""
    # Build query with eager loading of created_by relationship
    query = (
        UserAccessibleQuery(current_user.id)
        .workflows()
        .options(selectinload(Workflow.created_by))
        .order_by(Workflow.id.desc())
    )

    if namespace_id:
        query = query.where(Workflow.namespace_id == namespace_id)

    result = await session.execute(query)
    workflows = result.scalars().all()

    # Get workflow IDs
    workflow_ids = [w.id for w in workflows]

    deployment_map = {}
    if workflow_ids:
        # Get latest deployment for each workflow
        # Fetch all deployments for these workflows, ordered by deployed_at desc
        all_deployments_query = (
            select(WorkflowDeployment)
            .where(WorkflowDeployment.workflow_id.in_(workflow_ids))
            .order_by(
                WorkflowDeployment.workflow_id, WorkflowDeployment.deployed_at.desc()
            )
        )

        deployment_result = await session.execute(all_deployments_query)
        deployments = deployment_result.scalars().all()

        # Group by workflow_id, keeping only the first (latest) deployment per workflow
        seen_workflow_ids = set()
        for deployment in deployments:
            if deployment.workflow_id not in seen_workflow_ids:
                seen_workflow_ids.add(deployment.workflow_id)
                # Cast provider_definitions to correct type (model says list[str] but it's actually list[dict])
                provider_defs = None
                if deployment.provider_definitions:
                    if isinstance(deployment.provider_definitions, list) and all(
                        isinstance(item, dict)
                        for item in deployment.provider_definitions
                    ):
                        provider_defs = cast(
                            list[dict], deployment.provider_definitions
                        )
                deployment_map[deployment.workflow_id] = LastDeployment(
                    deployed_at=deployment.deployed_at,
                    provider_definitions=provider_defs,
                )

    # Convert to WorkflowRead with last_deployment
    workflow_reads = [
        WorkflowRead.model_validate(workflow, from_attributes=True).model_copy(
            update={"last_deployment": deployment_map.get(workflow.id)}
        )
        for workflow in workflows
    ]

    return ListResult(results=workflow_reads)


@router.post("")
async def create_workflow(
    data: WorkflowCreate,
    current_user: CurrentUser,
    session: TransactionSessionDep,
    _: None = Depends(check_can_create_workflow),
):
    """Create a new workflow."""
    # Verify user has access to the namespace
    namespace_query = (
        UserAccessibleQuery(current_user.id)
        .namespaces()
        .where(Namespace.id == data.namespace_id)
    )
    namespace_result = await session.execute(namespace_query)
    namespace = namespace_result.scalar_one_or_none()

    if not namespace:
        raise HTTPException(status_code=400, detail="Namespace not found")

    # Create a workflow manually with created_by_id
    workflow = Workflow(
        name=data.name,
        description=data.description,
        namespace_id=data.namespace_id,
        created_by_id=current_user.id,
    )

    session.add(workflow)
    await session.flush()

    logger.info(
        "Created new workflow", workflow_id=str(workflow.id), name=workflow.name
    )

    return WorkflowRead(
        id=workflow.id,
        name=workflow.name,
        description=workflow.description,
        namespace_id=workflow.namespace_id,
        created_by_id=workflow.created_by_id,
        created_by=CreatedByUser(
            id=current_user.id,
            email=current_user.email,
            first_name=current_user.first_name,
            last_name=current_user.last_name,
        ),
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        active=workflow.active,
        last_deployment=None,
    )


@router.post("/import/n8n")
async def import_n8n_workflow(
    data: N8nImportRequest,
    current_user: CurrentUser,
    session: TransactionSessionDep,
    _: None = Depends(check_can_create_workflow),
) -> N8nImportResponse:
    """
    Import a workflow from n8n JSON format.

    This endpoint accepts an n8n workflow export and converts it to a Floww workflow.
    Currently uses a mock implementation for code generation.
    """
    # Verify user has access to the namespace
    namespace_query = (
        UserAccessibleQuery(current_user.id)
        .namespaces()
        .where(Namespace.id == data.namespace_id)
    )
    namespace_result = await session.execute(namespace_query)
    namespace = namespace_result.scalar_one_or_none()

    if not namespace:
        raise HTTPException(status_code=400, detail="Namespace not found")

    # Extract workflow info from n8n JSON
    n8n_workflow = data.n8n_json
    workflow_name = n8n_workflow.get("name", "Imported Workflow")
    nodes = n8n_workflow.get("nodes", [])
    connections = n8n_workflow.get("connections", {})

    # Build description from n8n workflow metadata
    description_parts = []
    if n8n_workflow.get("meta", {}).get("instanceId"):
        description_parts.append("Imported from n8n")
    description_parts.append(f"{len(nodes)} nodes")
    description = (
        " | ".join(description_parts) if description_parts else "Imported from n8n"
    )

    # Create the workflow
    workflow = Workflow(
        name=workflow_name,
        description=description,
        namespace_id=data.namespace_id,
        created_by_id=current_user.id,
    )

    session.add(workflow)
    await session.flush()

    # Generate mock code from n8n workflow
    generated_code = _generate_mock_code_from_n8n(n8n_workflow)

    logger.info(
        "Imported n8n workflow",
        workflow_id=str(workflow.id),
        name=workflow.name,
        node_count=len(nodes),
        connection_count=len(connections),
    )

    workflow_read = WorkflowRead(
        id=workflow.id,
        name=workflow.name,
        description=workflow.description,
        namespace_id=workflow.namespace_id,
        created_by_id=workflow.created_by_id,
        created_by=CreatedByUser(
            id=current_user.id,
            email=current_user.email,
            first_name=current_user.first_name,
            last_name=current_user.last_name,
        ),
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        active=workflow.active,
        last_deployment=None,
    )

    return N8nImportResponse(
        workflow=workflow_read,
        generated_code=generated_code,
        message=f"Successfully imported workflow '{workflow_name}' with {len(nodes)} nodes",
    )


def _generate_mock_code_from_n8n(n8n_workflow: dict) -> str:
    """
    Generate mock Python code from an n8n workflow.

    This is a placeholder implementation. In the future, this will use
    AI/LLM to generate proper Floww workflow code from n8n structure.
    """
    nodes = n8n_workflow.get("nodes", [])
    workflow_name = n8n_workflow.get("name", "imported_workflow")

    # Build a simple mock code representation
    code_lines = [
        '"""',
        f"Workflow: {workflow_name}",
        "Imported from n8n",
        f"Nodes: {len(nodes)}",
        '"""',
        "",
        "from floww import workflow, trigger, action",
        "",
        "",
        f'@workflow(name="{workflow_name}")',
        "async def main():",
    ]

    if not nodes:
        code_lines.append("    # No nodes in this workflow")
        code_lines.append("    pass")
    else:
        code_lines.append("    # TODO: Implement workflow logic")
        code_lines.append("    # The following nodes were found in the n8n workflow:")
        code_lines.append("    #")

        for node in nodes:
            node_name = node.get("name", "Unknown")
            node_type = node.get("type", "unknown")
            code_lines.append(f"    # - {node_name} ({node_type})")

        code_lines.append("    #")
        code_lines.append("    # Implement your workflow logic here")
        code_lines.append("    pass")

    return "\n".join(code_lines)


@router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: UUID, current_user: CurrentUser, session: SessionDep
):
    """Get a specific workflow."""
    # Build query with eager loading of created_by relationship
    query = (
        UserAccessibleQuery(current_user.id)
        .workflows()
        .options(selectinload(Workflow.created_by))
        .where(Workflow.id == workflow_id)
    )

    result = await session.execute(query)
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Get latest deployment for this workflow
    latest_deployment_query = (
        select(WorkflowDeployment)
        .where(WorkflowDeployment.workflow_id == workflow_id)
        .order_by(WorkflowDeployment.deployed_at.desc())
        .limit(1)
    )

    deployment_result = await session.execute(latest_deployment_query)
    latest_deployment = deployment_result.scalar_one_or_none()

    last_deployment = None
    if latest_deployment:
        # Cast provider_definitions to correct type (model says list[str] but it's actually list[dict])
        provider_defs = None
        if latest_deployment.provider_definitions:
            if isinstance(latest_deployment.provider_definitions, list) and all(
                isinstance(item, dict)
                for item in latest_deployment.provider_definitions
            ):
                provider_defs = cast(list[dict], latest_deployment.provider_definitions)
        last_deployment = LastDeployment(
            deployed_at=latest_deployment.deployed_at,
            provider_definitions=provider_defs,
        )

    workflow_read = WorkflowRead.model_validate(workflow, from_attributes=True)
    workflow_read.last_deployment = last_deployment

    return workflow_read


@router.patch("/{workflow_id}")
async def update_workflow(
    workflow_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
    data: WorkflowUpdate,
):
    """Update a specific workflow."""
    # Verify access first
    access_query = (
        UserAccessibleQuery(current_user.id)
        .workflows()
        .where(Workflow.id == workflow_id)
    )
    access_result = await session.execute(access_query)
    if not access_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Update the workflow
    await session.execute(
        update(Workflow)
        .where(Workflow.id == workflow_id)
        .values(**data.model_dump(exclude_unset=True))
    )

    # Reload with eager loading of created_by relationship
    query = (
        UserAccessibleQuery(current_user.id)
        .workflows()
        .options(selectinload(Workflow.created_by))
        .where(Workflow.id == workflow_id)
    )

    result = await session.execute(query)
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Get latest deployment for this workflow
    latest_deployment_query = (
        select(WorkflowDeployment)
        .where(WorkflowDeployment.workflow_id == workflow_id)
        .order_by(WorkflowDeployment.deployed_at.desc())
        .limit(1)
    )

    deployment_result = await session.execute(latest_deployment_query)
    latest_deployment = deployment_result.scalar_one_or_none()

    last_deployment = None
    if latest_deployment:
        # Cast provider_definitions to correct type (model says list[str] but it's actually list[dict])
        provider_defs = None
        if latest_deployment.provider_definitions:
            if isinstance(latest_deployment.provider_definitions, list) and all(
                isinstance(item, dict)
                for item in latest_deployment.provider_definitions
            ):
                provider_defs = cast(list[dict], latest_deployment.provider_definitions)
        last_deployment = LastDeployment(
            deployed_at=latest_deployment.deployed_at,
            provider_definitions=provider_defs,
        )

    workflow_read = WorkflowRead.model_validate(workflow, from_attributes=True)
    workflow_read.last_deployment = last_deployment

    return workflow_read


@router.delete("/{workflow_id}")
async def delete_workflow(
    workflow_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Delete a workflow."""
    helper = helper_factory(current_user, session)
    response = await helper.delete_response(workflow_id)
    return response
