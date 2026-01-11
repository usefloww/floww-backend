from typing import Any

from ..runtime_types import (
    RuntimeConfig,
    RuntimeCreationStatus,
    RuntimeI,
)


class KubernetesRuntime(RuntimeI):
    def __init__(self):
        pass

    async def create_runtime(
        self,
        runtime_config: RuntimeConfig,
    ) -> RuntimeCreationStatus:
        # runtimes are created on demand so no creation is needed
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
        payload: dict[str, Any],
    ) -> None:
        pass

    async def get_definitions(
        self,
        runtime_config: RuntimeConfig,
        user_code: dict[str, str],
        provider_configs: dict[str, Any],
    ) -> dict[str, Any]:
        # Kubernetes runtime doesn't support get_definitions yet
        return {"success": True, "triggers": [], "providers": []}

    async def validate_code(
        self,
        runtime_config: RuntimeConfig,
        user_code: dict[str, str],
    ) -> dict[str, Any]:
        # Kubernetes runtime doesn't support code validation yet
        return {"success": True, "errors": []}

    async def teardown_unused_runtimes(self) -> None:
        pass
