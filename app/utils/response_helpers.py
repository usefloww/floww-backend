from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from app.models import User, Workflow, WorkflowDeployment


def create_paginated_response(
    items: List[Any],
    total: Optional[int] = None,
    user_id: Optional[Union[str, UUID]] = None,
    **additional_fields,
) -> Dict[str, Any]:
    """Create a standardized paginated response."""
    response = {
        "total": total if total is not None else len(items),
        **additional_fields,
    }

    if user_id is not None:
        response["user_id"] = str(user_id)

    return response


def serialize_workflow(workflow: Workflow) -> Dict[str, Any]:
    """Serialize a workflow to a standard response format."""
    return {
        "id": str(workflow.id),
        "name": workflow.name,
        "description": workflow.description,
        "namespace_id": str(workflow.namespace_id),
        "namespace_name": workflow.namespace.name if workflow.namespace else None,
        "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
        "updated_at": workflow.updated_at.isoformat() if workflow.updated_at else None,
    }


def serialize_workflow_deployment(deployment: WorkflowDeployment) -> Dict[str, Any]:
    """Serialize a workflow deployment to a standard response format."""
    return {
        "id": str(deployment.id),
        "workflow_id": str(deployment.workflow_id),
        "workflow_name": deployment.workflow.name if deployment.workflow else None,
        "runtime_id": str(deployment.runtime_id),
        "runtime_name": deployment.runtime.name if deployment.runtime else None,
        "deployed_by_id": str(deployment.deployed_by_id)
        if deployment.deployed_by_id
        else None,
        "status": deployment.status.value,
        "deployed_at": deployment.deployed_at.isoformat()
        if deployment.deployed_at
        else None,
        "note": deployment.note,
    }


def serialize_workflow_deployment_detailed(
    deployment: WorkflowDeployment,
) -> Dict[str, Any]:
    """Serialize a workflow deployment with additional details like user_code."""
    base_data = serialize_workflow_deployment(deployment)
    base_data["user_code"] = deployment.user_code
    return base_data


def create_workflows_response(workflows: List[Workflow], user: User) -> Dict[str, Any]:
    """Create a standardized workflows list response."""
    return create_paginated_response(
        items=[serialize_workflow(workflow) for workflow in workflows],
        total=len(workflows),
        user_id=str(user.id),
        workflows=[serialize_workflow(workflow) for workflow in workflows],
    )


def create_deployments_response(
    deployments: List[WorkflowDeployment], user: User
) -> Dict[str, Any]:
    """Create a standardized deployments list response."""
    return create_paginated_response(
        items=[serialize_workflow_deployment(deployment) for deployment in deployments],
        total=len(deployments),
        user_id=str(user.id),
        deployments=[
            serialize_workflow_deployment(deployment) for deployment in deployments
        ],
    )


def create_creation_response(resource: Any, **additional_fields) -> Dict[str, Any]:
    """Create a standardized response for resource creation."""
    base_response = {
        "id": str(resource.id),
    }

    # Add common fields if they exist
    if hasattr(resource, "created_at") and resource.created_at:
        base_response["created_at"] = resource.created_at.isoformat()

    if hasattr(resource, "updated_at") and resource.updated_at:
        base_response["updated_at"] = resource.updated_at.isoformat()

    # Add any additional fields provided
    base_response.update(additional_fields)

    return base_response
