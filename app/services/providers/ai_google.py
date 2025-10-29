from pydantic import BaseModel

from app.services.providers.provider_setup import (
    ProviderSetupStep,
    ProviderSetupStepSecret,
)
from app.services.providers.provider_utils import ProviderI


class GoogleAIProviderState(BaseModel):
    apiKey: str


class GoogleAIProvider(ProviderI):
    name: str = "google"
    setup_steps: list[ProviderSetupStep] = [
        ProviderSetupStepSecret(
            title="Google AI API Key",
            description="Your Google AI API key",
            alias="apiKey",
            placeholder="AIza...",
        ),
    ]
    model = GoogleAIProviderState
