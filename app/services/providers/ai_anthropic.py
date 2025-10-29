from pydantic import BaseModel

from app.services.providers.provider_setup import (
    ProviderSetupStep,
    ProviderSetupStepSecret,
)
from app.services.providers.provider_utils import ProviderI


class AnthropicProviderState(BaseModel):
    apiKey: str


class AnthropicProvider(ProviderI):
    name: str = "anthropic"
    setup_steps: list[ProviderSetupStep] = [
        ProviderSetupStepSecret(
            title="Anthropic API Key",
            description="Your Anthropic API key",
            alias="apiKey",
            placeholder="sk-ant-...",
        ),
    ]
    model = AnthropicProviderState
