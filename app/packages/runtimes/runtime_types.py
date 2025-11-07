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


class RuntimeConfig(BaseModel):
    runtime_id: str
    image_uri: str


class RuntimeCreationStatus(BaseModel):
    status: Literal["COMPLETED", "FAILED", "IN_PROGRESS"]
    new_logs: list[dict]


class RuntimeI(ABC):
    @abstractmethod
    async def create_runtime(
        self,
        runtime_config: RuntimeConfig,
    ) -> RuntimeCreationStatus: ...

    @abstractmethod
    async def get_runtime_status(
        self,
        runtime_id: str,
    ) -> RuntimeCreationStatus: ...

    @abstractmethod
    async def invoke_trigger(
        self,
        trigger_id: str,
        runtime_config: RuntimeConfig,
        user_code: dict[str, str],
        payload: RuntimeWebhookPayload,
    ) -> None: ...
