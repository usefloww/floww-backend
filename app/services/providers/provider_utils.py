from abc import ABC, abstractmethod
from typing import Any, Generic, Type, TypeVar

from pydantic import BaseModel

from app.services.providers.provider_setup import ProviderSetupStep

I = TypeVar("I", bound=BaseModel)
S = TypeVar("S", bound=BaseModel)
P = TypeVar("P")


class ResourceState(BaseModel):
    webhooks: list[str]
    schedules: list[str]
    data: Any


class ResourceI(ABC, Generic[I, S, P]):
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
