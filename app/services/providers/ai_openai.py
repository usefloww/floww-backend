from pydantic import BaseModel

from app.services.providers.provider_setup import (
    ProviderSetupStep,
    ProviderSetupStepSecret,
)
from app.services.providers.provider_utils import ProviderI


class OpenAIProviderState(BaseModel):
    apiKey: str


class OpenAIProvider(ProviderI):
    name: str = "openai"
    setup_steps: list[ProviderSetupStep] = [
        ProviderSetupStepSecret(
            title="OpenAI API Key",
            description="Your OpenAI API key",
            alias="apiKey",
            placeholder="sk-...",
        ),
    ]
    model = OpenAIProviderState
