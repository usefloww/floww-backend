from datetime import datetime, timezone
from typing import Any

from app.packages.runtimes.utils.docker import (
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
    def __init__(self, public_api_url: str, repository_name: str):
        self.public_api_url = public_api_url
        self.repository_name = repository_name

    async def create_runtime(
        self,
        runtime_config: RuntimeConfig,
    ) -> RuntimeCreationStatus:
        # Construct full image URI for Docker (needs to pull through public API proxy)
        public_api_host = self.public_api_url.replace("https://", "").replace(
            "http://", ""
        )
        image_uri = (
            f"{public_api_host}/{self.repository_name}@{runtime_config.image_digest}"
        )

        await create_container(
            runtime_config.runtime_id,
            image_uri,
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
        user_code: dict[str, str],
        payload: dict[str, Any],
    ) -> None:
        """
        Invoke Docker container with V2 payload format.
        Payload already contains trigger, data, auth_token, execution_id, and providerConfigs.
        """
        await start_container_if_stopped(runtime_config.runtime_id)
        event_payload = {
            "userCode": user_code,
            **payload,  # Includes trigger, data, auth_token, execution_id, providerConfigs
        }
        await send_webhook_to_container(
            runtime_config.runtime_id,
            event_payload,
            timeout=60,
        )
