from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel


class RuntimeWebhookPayload(BaseModel):
    method: str
    path: str
    headers: dict
    body: Any
    query: dict
    params: dict
    auth_token: str | None = None  # Short-lived JWT for workflow-to-backend auth
    execution_id: str | None = None  # Execution history record ID


class RuntimeConfig(BaseModel):
    runtime_id: str
    image_digest: str


class RuntimeCreationStatus(BaseModel):
    status: Literal["COMPLETED", "FAILED", "IN_PROGRESS"]
    new_logs: list[dict]


class RuntimeI(ABC):
    @abstractmethod
    async def invoke_trigger(
        self,
        trigger_id: str,
        runtime_config: RuntimeConfig,
        user_code: dict[str, str],
        payload: dict[str, Any],
    ) -> None:
        """
        Invoke trigger with V2 payload dict.

        Payload structure:
        {
            "trigger": {
                "provider": {"type": "...", "alias": "..."},
                "trigger_type": "...",
                "input": {...}
            },
            "data": {...},         # Event-specific data
            "auth_token": "...",
            "execution_id": "...",
            "providerConfigs": {...}
        }
        """
        ...

    @abstractmethod
    async def teardown_unused_runtimes(self) -> None: ...
