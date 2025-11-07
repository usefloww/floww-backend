"""
Placeholder KV-store routes demonstrating workflow authentication.

These endpoints show how to use WorkflowContext for authenticated
workflow-to-backend communication. Actual KV-store implementation
should replace these placeholders.
"""

from fastapi import APIRouter

from app.deps.workflow_auth import WorkflowContextDep

router = APIRouter(tags=["kv-store"])


@router.get("/kv/{key}")
async def get_kv_value(key: str, ctx: WorkflowContextDep) -> dict:
    """
    Get a value from the KV store (placeholder).

    The WorkflowContext (ctx) provides:
    - namespace_id: Scope for the key-value pair
    - workflow_id: Which workflow is making the request
    - deployment_id: Which deployment is making the request
    - invocation_id: Unique identifier for this invocation

    Example usage from a workflow:
        const response = await fetch('http://backend/kv/my-key', {
            headers: { 'Authorization': `Bearer ${event.auth_token}` }
        });
    """
    return {
        "message": "KV-store not yet implemented",
        "key": key,
        "context": {
            "namespace_id": str(ctx.namespace_id),
            "workflow_id": str(ctx.workflow_id),
            "deployment_id": str(ctx.deployment_id),
            "invocation_id": ctx.invocation_id,
        },
    }


@router.put("/kv/{key}")
async def set_kv_value(key: str, value: dict, ctx: WorkflowContextDep) -> dict:
    """
    Set a value in the KV store (placeholder).

    Example usage from a workflow:
        await fetch('http://backend/kv/my-key', {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${event.auth_token}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ value: 'my-value' })
        });
    """
    return {
        "message": "KV-store not yet implemented",
        "key": key,
        "value": value,
        "context": {
            "namespace_id": str(ctx.namespace_id),
            "workflow_id": str(ctx.workflow_id),
            "deployment_id": str(ctx.deployment_id),
            "invocation_id": ctx.invocation_id,
        },
    }


@router.delete("/kv/{key}")
async def delete_kv_value(key: str, ctx: WorkflowContextDep) -> dict:
    """
    Delete a value from the KV store (placeholder).

    Example usage from a workflow:
        await fetch('http://backend/kv/my-key', {
            method: 'DELETE',
            headers: { 'Authorization': `Bearer ${event.auth_token}` }
        });
    """
    return {
        "message": "KV-store not yet implemented",
        "key": key,
        "context": {
            "namespace_id": str(ctx.namespace_id),
            "workflow_id": str(ctx.workflow_id),
            "deployment_id": str(ctx.deployment_id),
            "invocation_id": ctx.invocation_id,
        },
    }


@router.get("/kv")
async def list_kv_keys(ctx: WorkflowContextDep) -> dict:
    """
    List all keys in the KV store for this namespace (placeholder).

    Example usage from a workflow:
        const response = await fetch('http://backend/kv', {
            headers: { 'Authorization': `Bearer ${event.auth_token}` }
        });
    """
    return {
        "message": "KV-store not yet implemented",
        "keys": [],
        "context": {
            "namespace_id": str(ctx.namespace_id),
            "workflow_id": str(ctx.workflow_id),
            "deployment_id": str(ctx.deployment_id),
            "invocation_id": ctx.invocation_id,
        },
    }
