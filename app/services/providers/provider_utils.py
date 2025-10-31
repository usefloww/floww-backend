from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Generic, Optional, Type, TypeVar

from pydantic import BaseModel

from app.services.providers.provider_setup import ProviderSetupStep

if TYPE_CHECKING:
    from fastapi import Request
    from fastapi.responses import Response

    from app.models import Trigger

I = TypeVar("I", bound=BaseModel)  # noqa: E741
S = TypeVar("S", bound=BaseModel)
P = TypeVar("P")


class TriggerState(BaseModel):
    webhooks: list[str]
    schedules: list[str]
    data: Any


class TriggerI(ABC, Generic[I, S, P]):
    @classmethod
    def input_schema(cls):
        return cls.__orig_bases__[0].__args__[0]

    @classmethod
    def state_schema(cls):
        return cls.__orig_bases__[0].__args__[1]

    @abstractmethod
    def create(self, provider: P, input: I) -> S:
        pass

    @abstractmethod
    def refresh(self, provider: P, input: I, state: S) -> S:
        pass

    @abstractmethod
    def destroy(self, provider: P, input: I, state: S) -> None:
        pass


class ProviderI(ABC):
    name: str
    setup_steps: list[ProviderSetupStep]
    model: Type[BaseModel]

    async def validate_webhook(
        self, request: "Request", provider_state: BaseModel
    ) -> Optional["Response"]:
        """
        Validate incoming webhook request and optionally return an early response.

        This is useful for things like Slack's URL verification challenge.
        If this returns a Response, it will be returned immediately without further processing.
        If it returns None, processing continues to process_webhook().

        Args:
            request: The incoming FastAPI Request
            provider_state: The decrypted provider state

        Returns:
            Optional Response to return immediately, or None to continue processing
        """
        return None

    async def process_webhook(
        self,
        request: "Request",
        provider_state: BaseModel,
        triggers: list["Trigger"],
    ) -> list["Trigger"]:
        """
        Process incoming webhook and return list of triggers that should be executed.

        This method should:
        1. Parse the webhook payload
        2. Filter based on provider-specific logic
        3. Match against trigger inputs to determine which triggers should fire

        Args:
            request: The incoming FastAPI Request
            provider_state: The decrypted provider state
            triggers: All triggers for this provider

        Returns:
            List of triggers that should be executed
        """
        # Default implementation: execute all triggers (no filtering)
        return triggers
