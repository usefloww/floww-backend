from datetime import datetime, timezone
from typing import Any

from app.packages.runtimes.utils.docker import (
    cleanup_idle_containers,
    create_container,
    get_container_status,
    send_webhook_to_container,
    start_container_if_stopped,
)

from ..runtime_types import (
    RuntimeConfig,
    RuntimeCreationStatus,
    RuntimeI,
)


class DockerRuntime(RuntimeI):
    def __init__(self, repository_name: str, registry_url: str):
        self.repository_name = repository_name
        self.registry_url = registry_url

    async def create_runtime(
        self,
        runtime_config: RuntimeConfig,
    ) -> RuntimeCreationStatus:
        # Construct full image URI using registry URL (Docker needs direct registry access)
        # Remove protocol from registry URL for Docker image reference
        registry_host = self.registry_url.replace("https://", "").replace("http://", "")
        image_uri = (
            f"{registry_host}/{self.repository_name}@{runtime_config.image_digest}"
        )

        await create_container(
            runtime_id=runtime_config.runtime_id,
            image_uri=image_uri,
        )
        return RuntimeCreationStatus(
            status="IN_PROGRESS",
            new_logs=[
                {
                    "timestamp": str(datetime.now(timezone.utc)),
                    "message": "Container creation initiated",
                    "level": "info",
                }
            ],
        )

    async def get_runtime_status(
        self,
        runtime_id: str,
    ) -> RuntimeCreationStatus:
        status_result = await get_container_status(runtime_id)
        return RuntimeCreationStatus(
            status=status_result["status"],
            new_logs=[
                {
                    "timestamp": str(datetime.now(timezone.utc)),
                    "message": status_result["logs"],
                }
            ],
        )

    async def invoke_trigger(
        self,
        trigger_id: str,
        runtime_config: RuntimeConfig,
        user_code: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        """
        Invoke Docker container with V2 payload format.
        Payload already contains trigger, data, auth_token, execution_id, and providerConfigs.
        """
        await start_container_if_stopped(runtime_config.runtime_id)
        event_payload = {
            "type": "invoke_trigger",
            "userCode": user_code,
            **payload,  # Includes trigger, data, auth_token, execution_id, providerConfigs
        }
        await send_webhook_to_container(
            runtime_config.runtime_id,
            event_payload,
            timeout=60,
        )

    async def get_definitions(
        self,
        runtime_config: RuntimeConfig,
        user_code: dict[str, str],
        provider_configs: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Get trigger and provider definitions from user code via Docker container.
        """
        await start_container_if_stopped(runtime_config.runtime_id)
        event_payload = {
            "type": "get_definitions",
            "userCode": user_code,
            "providerConfigs": provider_configs,
        }
        result = await send_webhook_to_container(
            runtime_config.runtime_id,
            event_payload,
            timeout=30,  # Shorter timeout for definitions
        )
        return result

    async def teardown_unused_runtimes(self) -> None:
        await cleanup_idle_containers(300)
