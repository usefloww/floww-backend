from datetime import datetime, timezone

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
    RuntimeWebhookPayload,
)


class DockerRuntime(RuntimeI):
    def __init__(self):
        pass

    async def create_runtime(
        self,
        runtime_config: RuntimeConfig,
    ) -> RuntimeCreationStatus:
        await create_container(
            runtime_config.runtime_id,
            runtime_config.image_uri,
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
        payload: RuntimeWebhookPayload,
        provider_configs: dict[str, dict[str, str]] | None = None,
    ) -> None:
        await start_container_if_stopped(runtime_config.runtime_id)
        event_payload = {
            **payload.model_dump(),
            "userCode": user_code,
            "triggerType": "webhook",
            "providerConfigs": provider_configs or {},
        }
        await send_webhook_to_container(
            runtime_config.runtime_id,
            event_payload,
            timeout=60,
        )
