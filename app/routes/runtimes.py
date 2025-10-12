import hashlib
import json
import uuid
from datetime import datetime, timezone
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep
from app.models import (
    Runtime,
    RuntimeCreationStatus,
)
from app.settings import settings
from app.utils.aws_ecr import get_image_uri
from app.utils.aws_lambda import deploy_lambda_function, get_lambda_deploy_status

logger = structlog.stdlib.get_logger(__name__)


router = APIRouter(prefix="/runtimes", tags=["Runtimes"])


class RuntimeConfig(BaseModel):
    image_hash: str

    @property
    def hash_uuid(self) -> uuid.UUID:
        config_string = json.dumps(self.model_dump(), sort_keys=True)
        hash_bytes = hashlib.sha256(config_string.encode()).digest()[:16]

        return uuid.UUID(bytes=hash_bytes)


class RuntimeCreate(BaseModel):
    config: RuntimeConfig


class PushTokenRequest(BaseModel):
    image_hash: str


class PushTokenResponse(BaseModel):
    password: str
    expires_in: int
    registry_url: str
    image_tag: str


@router.post("/push_token")
async def get_push_token(
    request: PushTokenRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> PushTokenResponse:
    """Get a push token from ECR proxy for pushing Docker images"""

    # Check if image already exists in ECR
    image_uri = get_image_uri(settings.ECR_REGISTRY_URL, request.image_hash)
    if image_uri is not None:
        logger.info(
            "Image hash already exists in ECR, refusing push token",
            image_hash=request.image_hash,
        )
        raise HTTPException(status_code=409, detail="Image already exists in registry")

    # ECR proxy endpoint
    payload = {
        "image_name": "trigger-lambda",
        "tag": request.image_hash,
        "action": "push",
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                settings.ECR_PROXY_URL + "/api/token",
                json=payload,
            )
            response.raise_for_status()

            token_data = response.json()
            return PushTokenResponse(
                password=token_data["password"],
                expires_in=token_data.get("expires_in", 3600),
                image_tag=request.image_hash,
                registry_url=settings.ECR_PROXY_URL.replace("https://", "")
                + "/trigger-lambda",
            )
    except httpx.HTTPError as e:
        logger.error("Failed to get push token from ECR proxy", error=str(e))
        raise HTTPException(
            status_code=503, detail="Failed to get push token from registry"
        )
    except Exception as e:
        logger.error("Unexpected error getting push token", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("")
async def create_runtime(
    runtime_data: RuntimeCreate,
    current_user: CurrentUser,
    session: SessionDep,
):
    """Create a new runtime"""

    config_hash = runtime_data.config.hash_uuid

    existing_runtime_result = await session.execute(
        select(Runtime).where(Runtime.config_hash == config_hash)
    )
    existing_runtime = existing_runtime_result.scalar_one_or_none()

    if existing_runtime:
        raise HTTPException(
            409,
            {
                "message": "Runtime already exists",
                "runtime_id": str(existing_runtime.id),
            },
        )

    image_uri = get_image_uri(
        repository_name=settings.ECR_REGISTRY_URL, tag=runtime_data.config.image_hash
    )

    if image_uri is None:
        raise HTTPException(400, "Image does not exist")

    runtime = Runtime(
        config_hash=config_hash,
        config=runtime_data.config.model_dump(),
        creation_status=RuntimeCreationStatus.IN_PROGRESS,
        creation_logs=[],
    )
    session.add(runtime)
    await session.flush()
    await session.refresh(runtime)

    deploy_lambda_function(
        runtime_id=str(runtime.id),
        image_uri=image_uri,
    )

    log_entry = {
        "timestamp": str(datetime.now(timezone.utc)),
        "message": "Lambda deployment initiated",
        "level": "info",
    }

    current_logs = runtime.creation_logs or []
    runtime.creation_logs = current_logs + [log_entry]

    await session.flush()
    await session.refresh(runtime)

    return {
        "id": str(runtime.id),
        "config": runtime.config,
        "creation_status": runtime.creation_status.value,
        "creation_logs": runtime.creation_logs,
    }


async def update_runtime_status_background(runtime_id: UUID):
    """Background task to check and update runtime status"""
    from app.deps.db import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        # Get the runtime from DB
        runtime_result = await session.execute(
            select(Runtime).where(Runtime.id == runtime_id)
        )
        runtime = runtime_result.scalar_one_or_none()

        if not runtime or runtime.creation_status != RuntimeCreationStatus.IN_PROGRESS:
            return

        # Check Lambda status
        lambda_status = get_lambda_deploy_status(str(runtime_id))

        print(lambda_status)

        # Update runtime status and logs if needed
        if lambda_status["success"]:
            print(lambda_status["status"])
            new_status = RuntimeCreationStatus(lambda_status["status"].lower())

            # Only update if status changed
            if runtime.creation_status != new_status:
                runtime.creation_status = new_status

                # Add log entry
                log_entry = {
                    "timestamp": str(datetime.now(timezone.utc)),
                    "message": f"Status updated to {new_status.value}",
                    "level": "info",
                    "lambda_state": lambda_status.get("lambda_state"),
                    "last_update_status": lambda_status.get("last_update_status"),
                }

                # Add additional logs if available
                if lambda_status.get("logs"):
                    log_entry["lambda_logs"] = lambda_status["logs"]

                current_logs = runtime.creation_logs or []
                runtime.creation_logs = current_logs + [log_entry]

                await session.flush()
        else:
            # Lambda check failed, update to FAILED if not already
            if runtime.creation_status != RuntimeCreationStatus.FAILED:
                runtime.creation_status = RuntimeCreationStatus.FAILED

                log_entry = {
                    "timestamp": str(datetime.now(timezone.utc)),
                    "message": f"Lambda check failed: {lambda_status.get('logs', 'Unknown error')}",
                    "level": "error",
                }

                current_logs = runtime.creation_logs or []
                runtime.creation_logs = current_logs + [log_entry]

                await session.flush()


@router.get("/{runtime_id}")
async def get_runtime(
    runtime_id: UUID, session: SessionDep, background_tasks: BackgroundTasks
):
    """Get a runtime and check status in background if IN_PROGRESS"""

    runtime_result = await session.execute(
        select(Runtime).where(Runtime.id == runtime_id)
    )
    runtime = runtime_result.scalar_one_or_none()

    if not runtime:
        raise HTTPException(status_code=404, detail="Runtime not found")

    # If runtime is IN_PROGRESS, trigger background status check
    if runtime.creation_status == RuntimeCreationStatus.IN_PROGRESS:
        background_tasks.add_task(update_runtime_status_background, runtime_id)

    return {
        "id": str(runtime.id),
        "config": runtime.config,
        "creation_status": runtime.creation_status.value,
        "creation_logs": runtime.creation_logs,
    }
