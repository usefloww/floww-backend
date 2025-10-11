from typing import Optional
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep
from app.models import Runtime, Workflow, WorkflowDeployment, WorkflowDeploymentStatus

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/runtimes", tags=["Runtimes"])


class RuntimeCreate(BaseModel):
    image_uri: str
    hash: str
    name: str
    version: str
    config: Optional[dict] = None
    workflow_id: UUID


class RuntimeOnlyCreate(BaseModel):
    image_uri: str
    hash: str
    name: str
    version: str
    config: Optional[dict] = None


class PushTokenRequest(BaseModel):
    image_name: str
    tag: str


class PushTokenResponse(BaseModel):
    password: str
    expires_in: int


@router.post("/push_token")
async def get_push_token(
    request: PushTokenRequest, current_user: CurrentUser, session: SessionDep
) -> PushTokenResponse:
    """Get a push token from ECR proxy for pushing Docker images"""

    # ECR proxy endpoint
    ecr_proxy_url = "https://registry.flow.toondn.app/api/token"

    payload = {"image_name": request.image_name, "tag": request.tag, "action": "push"}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(ecr_proxy_url, json=payload)
            response.raise_for_status()

            token_data = response.json()
            return PushTokenResponse(
                password=token_data["password"],
                expires_in=token_data.get("expires_in", 3600),
            )
    except httpx.HTTPError as e:
        logger.error("Failed to get push token from ECR proxy", error=str(e))
        raise HTTPException(
            status_code=502, detail="Failed to get push token from registry"
        )
    except Exception as e:
        logger.error("Unexpected error getting push token", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/only")
async def create_runtime_only(
    runtime_data: RuntimeOnlyCreate, current_user: CurrentUser, session: SessionDep
):
    """Create a new runtime without deployment"""

    # Check if runtime with this hash already exists
    existing_runtime_result = await session.execute(
        select(Runtime).where(Runtime.hash == runtime_data.hash)
    )
    existing_runtime = existing_runtime_result.scalar_one_or_none()

    if existing_runtime:
        # Reuse existing runtime
        runtime = existing_runtime
        logger.info(
            "Reusing existing runtime", runtime_id=str(runtime.id), hash=runtime.hash
        )
        reused_existing = True
    else:
        # Create new runtime
        runtime = Runtime(
            name=runtime_data.name,
            version=runtime_data.version,
            hash=runtime_data.hash,
            config=runtime_data.config,
        )
        session.add(runtime)
        await session.commit()
        await session.refresh(runtime)
        logger.info(
            "Created new runtime", runtime_id=str(runtime.id), hash=runtime.hash
        )
        reused_existing = False

    return {
        "runtime_id": str(runtime.id),
        "name": runtime.name,
        "version": runtime.version,
        "hash": runtime.hash,
        "created_at": runtime.created_at.isoformat(),
        "reused_existing": reused_existing,
    }


@router.post("")
async def create_runtime(
    runtime_data: RuntimeCreate, current_user: CurrentUser, session: SessionDep
):
    """Create a new runtime and deployment (DEPRECATED - use /runtimes/only + /workflow_deployments)"""

    # Verify user has access to the workflow
    workflow_result = await session.execute(
        select(Workflow).where(Workflow.id == runtime_data.workflow_id)
    )
    workflow = workflow_result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Check if runtime with this hash already exists
    existing_runtime_result = await session.execute(
        select(Runtime).where(Runtime.hash == runtime_data.hash)
    )
    existing_runtime = existing_runtime_result.scalar_one_or_none()

    if existing_runtime:
        # Reuse existing runtime
        runtime = existing_runtime
        logger.info(
            "Reusing existing runtime", runtime_id=str(runtime.id), hash=runtime.hash
        )
    else:
        # Create new runtime
        runtime = Runtime(
            name=runtime_data.name,
            version=runtime_data.version,
            hash=runtime_data.hash,
            config=runtime_data.config,
        )
        session.add(runtime)
        await session.flush()  # Get the runtime ID
        logger.info(
            "Created new runtime", runtime_id=str(runtime.id), hash=runtime.hash
        )

    # Create new deployment (deprecated behavior)
    deployment = WorkflowDeployment(
        workflow_id=runtime_data.workflow_id,
        runtime_id=runtime.id,
        deployed_by_id=current_user.id,
        user_code={
            "files": {},
            "entrypoint": "main.ts",
        },  # Empty code for backward compatibility
        status=WorkflowDeploymentStatus.ACTIVE,
    )
    session.add(deployment)

    await session.commit()

    return {
        "runtime_id": str(runtime.id),
        "deployment_id": str(deployment.id),
        "status": deployment.status.value,
        "deployed_at": deployment.deployed_at.isoformat(),
        "reused_existing": existing_runtime is not None,
    }
