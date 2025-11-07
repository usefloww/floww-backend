from app.packages.runtimes.utils.docker import get_or_create_container

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
        # containers are created on demand so no creation is needed
        return RuntimeCreationStatus(status="COMPLETED", new_logs=[])

    async def get_runtime_status(
        self,
        runtime_id: str,
    ) -> RuntimeCreationStatus:
        return RuntimeCreationStatus(status="COMPLETED", new_logs=[])

    async def invoke_trigger(
        self,
        trigger_id: str,
        runtime_config: RuntimeConfig,
        user_code: dict[str, str],
        payload: RuntimeWebhookPayload,
    ) -> None:
        await get_or_create_container(
            runtime_config.runtime_id, runtime_config.image_uri
        )
