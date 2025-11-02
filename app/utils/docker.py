"""Docker container management utilities for runtime execution.

This module provides async functions to manage Docker containers for user code execution.
Containers are long-running and reused across webhook invocations, with automatic cleanup
of idle containers.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiodocker
import httpx
import structlog

logger = structlog.stdlib.get_logger(__name__)

# Container naming convention
CONTAINER_NAME_PREFIX = "floww-runtime-"
# Label keys for container metadata
LABEL_RUNTIME_ID = "floww.runtime.id"
LABEL_LAST_USED = "floww.runtime.last_used"
LABEL_IMAGE_HASH = "floww.runtime.image_hash"

# Container idle timeout (in seconds) - containers idle longer than this will be cleaned up
CONTAINER_IDLE_TIMEOUT = 300  # 5 minutes


async def get_docker_client() -> aiodocker.Docker:
    """Get a Docker client instance."""
    return aiodocker.Docker()


def _get_container_name(runtime_id: str) -> str:
    """Get the container name for a runtime ID."""
    return f"{CONTAINER_NAME_PREFIX}{runtime_id}"


async def get_or_create_container(runtime_id: str, image_hash: str) -> str:
    """Get existing container or create a new one for the runtime.

    Args:
        runtime_id: Unique identifier for the runtime
        image_hash: Docker image hash/tag to use (e.g., "sha256:abc123" or "registry/image:tag")

    Returns:
        Container name

    Raises:
        Exception: If container creation or start fails
    """
    container_name = _get_container_name(runtime_id)

    async with aiodocker.Docker() as docker:
        try:
            # Check if container already exists
            container = await docker.containers.get(container_name)

            # Check container status
            container_info = await container.show()
            state = container_info["State"]

            if state["Running"]:
                logger.info(
                    "Container already running",
                    runtime_id=runtime_id,
                    container_name=container_name,
                )
                return container_name
            else:
                logger.info(
                    "Container exists but not running, starting it",
                    runtime_id=runtime_id,
                    container_name=container_name,
                )
                await container.start()

                # Wait for container to be healthy/ready
                await _wait_for_container_ready(container_name)
                return container_name

        except aiodocker.exceptions.DockerError as e:
            if e.status == 404:
                # Container doesn't exist, create it
                logger.info(
                    "Container not found, creating new one",
                    runtime_id=runtime_id,
                    container_name=container_name,
                    image_hash=image_hash,
                )

                # Pull image if needed
                await _ensure_image_exists(docker, image_hash)

                # Create container configuration
                config = {
                    "Image": image_hash,
                    "Hostname": container_name,
                    "Labels": {
                        LABEL_RUNTIME_ID: runtime_id,
                        LABEL_LAST_USED: datetime.now(timezone.utc).isoformat(),
                        LABEL_IMAGE_HASH: image_hash,
                    },
                    "HostConfig": {
                        # No port mapping needed - containers communicate via Docker network
                        "NetworkMode": "bridge",
                        # Resource limits (optional, can be adjusted)
                        "Memory": 512 * 1024 * 1024,  # 512 MB
                        "CpuQuota": 100000,  # 100% of one CPU
                    },
                }

                # Create and start the container
                container = await docker.containers.create(
                    config=config,
                    name=container_name,
                )

                await container.start()
                logger.info(
                    "Container created and started",
                    runtime_id=runtime_id,
                    container_name=container_name,
                )

                # Wait for container to be healthy/ready
                await _wait_for_container_ready(container_name)

                return container_name
            else:
                logger.error(
                    "Docker error while getting/creating container",
                    runtime_id=runtime_id,
                    error=str(e),
                )
                raise


async def _ensure_image_exists(docker: aiodocker.Docker, image_hash: str):
    """Ensure the Docker image exists locally, pull if needed.

    Args:
        docker: Docker client
        image_hash: Image hash or tag to check/pull
    """
    try:
        # Try to get image info
        await docker.images.inspect(image_hash)
        logger.info("Docker image already exists", image_hash=image_hash)
    except aiodocker.exceptions.DockerError as e:
        if e.status == 404:
            logger.info("Pulling Docker image", image_hash=image_hash)

            # Pull the image
            await docker.images.pull(image_hash)

            logger.info("Docker image pulled successfully", image_hash=image_hash)
        else:
            raise


async def _wait_for_container_ready(container_name: str, timeout: int = 30):
    """Wait for container to be ready to accept HTTP requests.

    Args:
        container_name: Name of the container
        timeout: Maximum time to wait in seconds

    Raises:
        TimeoutError: If container doesn't become ready within timeout
    """
    start_time = datetime.now(timezone.utc)

    async with httpx.AsyncClient() as client:
        while (datetime.now(timezone.utc) - start_time).total_seconds() < timeout:
            try:
                # Try to connect to the container's HTTP endpoint
                response = await client.get(
                    f"http://{container_name}:8000/health",
                    timeout=2.0,
                )
                if response.status_code == 200:
                    logger.info(
                        "Container is ready",
                        container_name=container_name,
                    )
                    return
            except (httpx.ConnectError, httpx.TimeoutException):
                # Container not ready yet, wait and retry
                await asyncio.sleep(1)

        raise TimeoutError(
            f"Container {container_name} did not become ready within {timeout} seconds"
        )


async def send_webhook_to_container(
    runtime_id: str,
    payload: dict,
    timeout: int = 60,
) -> dict:
    """Send webhook payload to container via HTTP POST.

    Args:
        runtime_id: Runtime ID (used to determine container name)
        payload: Webhook payload to send
        timeout: Request timeout in seconds

    Returns:
        Response from the container

    Raises:
        httpx.HTTPError: If request fails
    """
    container_name = _get_container_name(runtime_id)
    url = f"http://{container_name}:8000/execute"

    logger.info(
        "Sending webhook to container",
        runtime_id=runtime_id,
        container_name=container_name,
    )

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()

            logger.info(
                "Container executed webhook successfully",
                runtime_id=runtime_id,
                status_code=response.status_code,
            )

            return response.json()

        except httpx.HTTPError as e:
            logger.error(
                "Failed to send webhook to container",
                runtime_id=runtime_id,
                error=str(e),
            )
            raise


async def _get_last_activity_time(container, details: dict) -> datetime:
    """Get the last activity time for a container by parsing logs.

    Looks for the last log line that does NOT contain '/health' (health checks
    don't count as actual activity). Falls back to container StartedAt time if
    no logs are found.

    Args:
        container: Docker container object
        details: Container details dict from container.show()

    Returns:
        Datetime of last activity
    """
    try:
        # Get all container logs with timestamps
        # Note: aiodocker returns logs as async generator
        log_lines = []
        async for log_chunk in container.log(
            stdout=True,
            stderr=True,
            timestamps=True,
        ):
            log_lines.append(log_chunk)

        # Search backwards for the last non-health log line
        for log_line in reversed(log_lines):
            # Log format from Docker: "2024-01-01T12:00:00.000000000Z message"
            if "/health" not in log_line:
                # Extract timestamp from the log line
                # Docker timestamp format: YYYY-MM-DDTHH:MM:SS.nnnnnnnnnZ
                try:
                    timestamp_str = log_line.split(" ")[0]
                    # Remove nanoseconds (keep only microseconds for Python)
                    if "." in timestamp_str:
                        date_part, nano_part = timestamp_str.rsplit(".", 1)
                        # Convert nanoseconds to microseconds
                        nano_part = nano_part.rstrip("Z")
                        micro_part = nano_part[:6].ljust(6, "0")
                        timestamp_str = f"{date_part}.{micro_part}Z"

                    # Parse the timestamp
                    last_activity = datetime.fromisoformat(
                        timestamp_str.replace("Z", "+00:00")
                    )

                    logger.debug(
                        "Found last activity from logs",
                        last_activity=last_activity.isoformat(),
                    )
                    return last_activity
                except (ValueError, IndexError) as e:
                    logger.warning(
                        "Failed to parse log timestamp",
                        log_line=log_line[:100],
                        error=str(e),
                    )
                    continue

    except Exception as e:
        logger.warning(
            "Failed to get container logs",
            error=str(e),
        )

    # Fallback to StartedAt time if no logs found or parsing failed
    started_at = details["State"].get("StartedAt", "")
    if started_at:
        try:
            return datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        except ValueError:
            pass

    # Final fallback: use current time (will prevent cleanup this round)
    logger.warning("Could not determine last activity time, using current time")
    return datetime.now(timezone.utc)


async def cleanup_idle_containers(idle_timeout: Optional[int] = None):
    """Remove containers that have been idle for longer than the timeout.

    This function should be called periodically (e.g., via a background task).

    Args:
        idle_timeout: Timeout in seconds (defaults to CONTAINER_IDLE_TIMEOUT)
    """
    if idle_timeout is None:
        idle_timeout = CONTAINER_IDLE_TIMEOUT

    cutoff_time = datetime.now(timezone.utc) - timedelta(seconds=idle_timeout)

    async with aiodocker.Docker() as docker:
        try:
            # List all containers with our label
            containers = await docker.containers.list(
                all=True,
                filters={
                    "label": [LABEL_RUNTIME_ID],
                },
            )

            for container_info in containers:
                try:
                    container_name = container_info["Names"][0].lstrip("/")
                    runtime_id = container_info["Labels"].get(LABEL_RUNTIME_ID)

                    # Get container details
                    container = await docker.containers.get(container_info["Id"])
                    details = await container.show()

                    # Determine last activity time by checking logs
                    last_activity_time = await _get_last_activity_time(
                        container, details
                    )

                    # Check if container should be removed
                    if details["State"]["Running"]:
                        # Check if idle for too long
                        if last_activity_time < cutoff_time:
                            logger.info(
                                "Removing idle container",
                                runtime_id=runtime_id,
                                container_name=container_name,
                                last_activity=last_activity_time.isoformat(),
                                idle_seconds=(
                                    datetime.now(timezone.utc) - last_activity_time
                                ).total_seconds(),
                            )
                            await container.delete(force=True)
                        else:
                            logger.debug(
                                "Container still active",
                                runtime_id=runtime_id,
                                container_name=container_name,
                                last_activity=last_activity_time.isoformat(),
                            )
                    else:
                        # Remove stopped containers immediately
                        logger.info(
                            "Removing stopped container",
                            runtime_id=runtime_id,
                            container_name=container_name,
                        )
                        await container.delete(force=True)

                except Exception as e:
                    logger.error(
                        "Error processing container for cleanup",
                        error=str(e),
                    )

        except Exception as e:
            logger.error(
                "Error during container cleanup",
                error=str(e),
            )


async def remove_container(runtime_id: str) -> bool:
    """Explicitly remove a container for a runtime.

    Args:
        runtime_id: Runtime ID

    Returns:
        True if container was removed, False if it didn't exist
    """
    container_name = _get_container_name(runtime_id)

    async with aiodocker.Docker() as docker:
        try:
            container = await docker.containers.get(container_name)
            await container.delete(force=True)

            logger.info(
                "Container removed",
                runtime_id=runtime_id,
                container_name=container_name,
            )
            return True

        except aiodocker.exceptions.DockerError as e:
            if e.status == 404:
                logger.info(
                    "Container not found, nothing to remove",
                    runtime_id=runtime_id,
                    container_name=container_name,
                )
                return False
            else:
                logger.error(
                    "Error removing container",
                    runtime_id=runtime_id,
                    error=str(e),
                )
                raise
