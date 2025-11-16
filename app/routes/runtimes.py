import hashlib
import json
import uuid
from urllib.parse import urlparse
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select

from app.deps.auth import CurrentUser, security
from app.deps.db import AsyncSessionLocal, SessionDep, TransactionSessionDep
from app.factories import registry_client_factory, runtime_factory
from app.models import (
    Runtime,
    RuntimeCreationStatus,
)
from app.packages.runtimes.runtime_types import RuntimeConfig
from app.routes.admin_auth import get_jwt_from_session_cookie
from app.settings import settings

logger = structlog.stdlib.get_logger(__name__)


router = APIRouter(prefix="/runtimes", tags=["Runtimes"])

# Get registry client based on runtime configuration
registry_client = registry_client_factory()


class RuntimeCreateConfig(BaseModel):
    image_hash: str

    @property
    def hash_uuid(self) -> uuid.UUID:
        config_string = json.dumps(self.model_dump(), sort_keys=True)
        hash_bytes = hashlib.sha256(config_string.encode()).digest()[:16]

        return uuid.UUID(bytes=hash_bytes)


class RuntimeCreate(BaseModel):
    config: RuntimeCreateConfig


class PushTokenRequest(BaseModel):
    image_hash: str


class PushTokenResponse(BaseModel):
    password: str
    expires_in: int
    registry_url: str
    image_tag: str


@router.post("/push_token")
async def get_push_token(
    push_request: PushTokenRequest,
    request: Request,
    current_user: CurrentUser,
    session: SessionDep,
) -> PushTokenResponse:
    """Get Docker credentials for pushing images.

    Returns the user's WorkOS token as the Docker password for use with
    the internal Docker registry proxy.
    """

    # Check if image already exists in registry
    image_uri = await registry_client.get_image_uri(push_request.image_hash)
    if image_uri is not None:
        logger.info(
            "Image hash already exists in registry, refusing push token",
            image_hash=push_request.image_hash,
        )
        raise HTTPException(status_code=409, detail="Image already exists in registry")

    # Extract WorkOS token from request
    # Same logic as get_current_user in app/deps/auth.py
    credentials: HTTPAuthorizationCredentials | None = await security(request)

    workos_token = None
    if credentials and credentials.credentials:
        workos_token = credentials.credentials
    else:
        # Try session cookie
        session_cookie = request.cookies.get("session")
        if session_cookie:
            workos_token = get_jwt_from_session_cookie(session_cookie)

    if not workos_token:
        logger.error("Could not extract WorkOS token from request")
        raise HTTPException(
            status_code=401,
            detail="Authentication token not found",
        )

    # Return Docker credentials using the user's WorkOS token
    # Docker will use: docker login <registry_url> -u token -p <workos_token>
    registry_host = settings.PUBLIC_API_URL.replace("https://", "").replace(
        "http://", ""
    )

    logger.info(
        "Generated Docker push credentials",
        user_id=str(current_user.id),
        image_hash=push_request.image_hash,
    )

    return PushTokenResponse(
        password=workos_token,
        expires_in=3600,  # WorkOS tokens typically valid for 1 hour
        image_tag=push_request.image_hash,
        registry_url=f"{registry_host}/{settings.REGISTRY_REPOSITORY_NAME}",
    )


@router.post("")
async def create_runtime(
    runtime_data: RuntimeCreate,
    current_user: CurrentUser,
    session: TransactionSessionDep,
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

    image_uri = await registry_client.get_image_uri(runtime_data.config.image_hash)

    if image_uri is None:
        raise HTTPException(400, "Image does not exist")

    image_uri = image_uri.replace(
        urlparse(settings.REGISTRY_URL).netloc, urlparse(settings.PUBLIC_API_URL).netloc
    )

    runtime = Runtime(
        config_hash=config_hash,
        config=runtime_data.config.model_dump(),
        creation_status=RuntimeCreationStatus.IN_PROGRESS,
        creation_logs=[],
    )
    session.add(runtime)
    await session.flush()
    await session.refresh(runtime)

    runtime_impl = runtime_factory()

    creation_status = await runtime_impl.create_runtime(
        RuntimeConfig(
            runtime_id=str(runtime.id),
            image_uri=image_uri,
        ),
    )

    runtime.creation_status = RuntimeCreationStatus(creation_status.status)
    runtime.creation_logs = creation_status.new_logs

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
    async with AsyncSessionLocal() as session:
        # Get the runtime from DB
        runtime_result = await session.execute(
            select(Runtime).where(Runtime.id == runtime_id)
        )
        runtime = runtime_result.scalar_one_or_none()

        if not runtime or runtime.creation_status != RuntimeCreationStatus.IN_PROGRESS:
            return

        # Check status
        runtime_impl = runtime_factory()
        creation_status = await runtime_impl.get_runtime_status(str(runtime_id))

        if creation_status.status != runtime.creation_status.value:
            runtime.creation_status = RuntimeCreationStatus(creation_status.status)
            current_logs = runtime.creation_logs or []
            runtime.creation_logs = current_logs + creation_status.new_logs

            await session.commit()


@router.get("/{runtime_id}")
async def get_runtime(
    runtime_id: UUID,
    session: SessionDep,
    background_tasks: BackgroundTasks,
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
        "creation_status": runtime.creation_status.value.lower(),
        "creation_logs": runtime.creation_logs,
    }
