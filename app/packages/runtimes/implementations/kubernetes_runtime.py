from ..runtime_types import (
    RuntimeConfig,
    RuntimeCreationStatus,
    RuntimeI,
    RuntimeWebhookPayload,
)


class KubernetesRuntime(RuntimeI):
    def __init__(self):
        pass

    async def create_runtime(
        self,
        runtime_config: RuntimeConfig,
    ) -> RuntimeCreationStatus:
        # runtimes are created on demand so no creation is needed
        return RuntimeCreationStatus(status="completed", new_logs=[])

    async def get_runtime_status(
        self,
        runtime_id: str,
    ) -> RuntimeCreationStatus:
        return RuntimeCreationStatus(status="completed", new_logs=[])

    async def invoke_trigger(
        self,
        trigger_id: str,
        runtime_config: RuntimeConfig,
        user_code: dict[str, str],
        payload: RuntimeWebhookPayload,
    ) -> None:
        pass
