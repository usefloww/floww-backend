from pydantic import BaseModel

from app.services.providers.provider_setup import (
    ProviderSetupStep,
    ProviderSetupStepSecret,
    ProviderSetupStepValue,
)
from app.services.providers.provider_utils import (
    ProviderI,
)


#### Provider ####
class SlackProviderState(BaseModel):
    workspace_url: str
    bot_token: str


class SlackProvider(ProviderI):
    name: str = "slack"
    setup_steps: list[ProviderSetupStep] = [
        ProviderSetupStepValue(
            title="Workspace URL",
            description="Your Slack workspace URL",
            alias="workspace_url",
            placeholder="https://yourworkspace.slack.com",
        ),
        ProviderSetupStepSecret(
            title="Bot Token",
            description="Slack Bot User OAuth Token",
            alias="bot_token",
            placeholder="xoxb-xxxxxxxxxxxx-xxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxx",
        ),
    ]
    model = SlackProviderState


#### Triggers ####
class OnMessageInput(BaseModel):
    channel_id: str | None = None
    user_id: str | None = None


class OnMessageState(BaseModel):
    channel_id: str | None = None
    user_id: str | None = None
    webhook_url: str


# Registry of Slack trigger types
SLACK_TRIGGER_TYPES = {}
