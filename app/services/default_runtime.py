import hashlib
import uuid
from uuid import UUID

import structlog
from sqlalchemy import select

from app.deps.db import AsyncSessionLocal
from app.factories import runtime_factory
from app.models import Configuration, Runtime, RuntimeCreationStatus
from app.packages.runtimes.runtime_types import RuntimeConfig
from app.settings import settings

logger = structlog.stdlib.get_logger(__name__)

DEFAULT_RUNTIME_CONFIG_KEY = "default_runtime_id"


def _generate_config_hash(image_uri: str) -> uuid.UUID:
    """Generate a deterministic config_hash from the image URI."""
    hash_bytes = hashlib.sha256(image_uri.encode()).digest()[:16]
    return uuid.UUID(bytes=hash_bytes)


async def prepare_default_runtime() -> None:
    """
    Prepare the default runtime on startup.

    Currently only supports Lambda runtime type. For Lambda:
    1. Generates a deterministic config_hash from the image URI
    2. Upserts a Runtime record with config={"image_uri": "..."}
    3. Calls runtime_factory().create_runtime() to deploy the Lambda function
    4. Stores the runtime ID in the Configuration table

    For Docker, this function skips - Docker support will be added later.
    """
    if not settings.DEFAULT_RUNTIME_IMAGE:
        logger.info(
            "No DEFAULT_RUNTIME_IMAGE configured, skipping default runtime setup"
        )
        return

    # Only Lambda is supported for now
    if settings.RUNTIME_TYPE != "lambda":
        logger.info(
            "Default runtime only supported for Lambda, skipping",
            runtime_type=settings.RUNTIME_TYPE,
        )
        return

    image_uri = settings.DEFAULT_RUNTIME_IMAGE
    config_hash = _generate_config_hash(image_uri)

    logger.info(
        "Preparing default runtime",
        image_uri=image_uri,
        config_hash=str(config_hash),
        runtime_type=settings.RUNTIME_TYPE,
    )

    async with AsyncSessionLocal() as session:
        # Check if runtime already exists with this config_hash
        result = await session.execute(
            select(Runtime).where(Runtime.config_hash == config_hash)
        )
        runtime = result.scalar_one_or_none()

        if runtime:
            logger.info(
                "Default runtime already exists",
                runtime_id=str(runtime.id),
                creation_status=runtime.creation_status.value,
            )
            # Update the default runtime ID in configuration
            await set_default_runtime_id(runtime.id)

            # If the runtime is already completed, we're done
            if runtime.creation_status == RuntimeCreationStatus.COMPLETED:
                return

            # If it's in progress or failed, we might want to retry
            if runtime.creation_status == RuntimeCreationStatus.FAILED:
                logger.info("Retrying failed default runtime creation")
                runtime.creation_status = RuntimeCreationStatus.IN_PROGRESS
                runtime.creation_logs = []
        else:
            # Create new runtime record
            runtime = Runtime(
                config_hash=config_hash,
                config={"image_uri": image_uri},
                creation_status=RuntimeCreationStatus.IN_PROGRESS,
                creation_logs=[],
            )
            session.add(runtime)
            await session.flush()
            await session.refresh(runtime)

            logger.info(
                "Created default runtime record",
                runtime_id=str(runtime.id),
            )

        # Call runtime_factory().create_runtime() to set up the runtime
        # Pass the full image URI as image_digest
        runtime_impl = runtime_factory()
        creation_status = await runtime_impl.create_runtime(
            RuntimeConfig(
                runtime_id=str(runtime.id),
                image_digest=image_uri,
            ),
        )

        runtime.creation_status = RuntimeCreationStatus(creation_status.status)
        runtime.creation_logs = creation_status.new_logs

        await session.commit()

        # Store the runtime ID as the default
        await set_default_runtime_id(runtime.id)

        logger.info(
            "Default runtime preparation completed",
            runtime_id=str(runtime.id),
            status=runtime.creation_status.value,
        )


async def set_default_runtime_id(runtime_id: UUID) -> None:
    """Store the default runtime ID in the Configuration table."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Configuration).where(Configuration.key == DEFAULT_RUNTIME_CONFIG_KEY)
        )
        config = result.scalar_one_or_none()

        if config:
            config.value = {"runtime_id": str(runtime_id)}
        else:
            config = Configuration(
                key=DEFAULT_RUNTIME_CONFIG_KEY,
                value={"runtime_id": str(runtime_id)},
            )
            session.add(config)

        await session.commit()

        logger.info(
            "Default runtime ID stored in configuration",
            runtime_id=str(runtime_id),
        )


async def get_default_runtime_id() -> UUID | None:
    """Get the default runtime ID from the Configuration table."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Configuration).where(Configuration.key == DEFAULT_RUNTIME_CONFIG_KEY)
        )
        config = result.scalar_one_or_none()

        if config and config.value:
            value = config.value
            # Handle both dict format {"runtime_id": "..."} and legacy string format
            if isinstance(value, dict):
                runtime_id_str = value.get("runtime_id")
            elif isinstance(value, str):
                runtime_id_str = value
            else:
                runtime_id_str = None

            if runtime_id_str:
                return UUID(runtime_id_str)

    return None
