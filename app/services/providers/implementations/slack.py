from pydantic import BaseModel

from app.services.providers.provider_setup import (
    ProviderSetupStep,
    ProviderSetupStepSecret,
    ProviderSetupStepValue,
)
from app.services.providers.provider_utils import (
    ProviderI,
    TriggerI,
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


class OnMessage(TriggerI[OnMessageInput, OnMessageState, SlackProvider]):
    """
    Trigger for Slack message events.

    IMPORTANT: This trigger requires manual configuration in the Slack App dashboard:
    1. Go to https://api.slack.com/apps and select your Slack app
    2. Navigate to "Event Subscriptions" in the left sidebar
    3. Toggle "Enable Events" to On
    4. Set the "Request URL" to the webhook_url provided during trigger creation
    5. Under "Subscribe to bot events", add the following events:
       - message.channels (messages in public channels)
       - message.groups (messages in private channels)
       - message.im (direct messages)
       - message.mpim (group direct messages)
    6. Click "Save Changes"
    7. Reinstall your app to the workspace if prompted

    Required OAuth Scopes (configure in "OAuth & Permissions"):
    - channels:history (view messages in public channels)
    - groups:history (view messages in private channels)
    - im:history (view messages in direct messages)
    - mpim:history (view messages in group direct messages)

    Note: Slack will send a URL verification challenge to the webhook_url when you save
    the Event Subscriptions configuration. The webhook handler must respond with the
    challenge value to complete the setup.
    """

    async def create(
        self,
        provider: SlackProviderState,
        input: OnMessageInput,
        webhook_url: str,
    ) -> OnMessageState:
        """
        Store the webhook configuration for Slack message events.

        Since Slack Event Subscriptions are configured manually in the Slack App dashboard,
        this method only stores the state. The actual webhook setup must be done manually
        by configuring the Request URL in the Slack App's Event Subscriptions settings.

        Args:
            provider: Slack provider configuration (workspace_url, bot_token)
            input: Filter configuration (channel_id, user_id)
            webhook_url: The webhook URL to configure in Slack App dashboard

        Returns:
            State containing the webhook configuration
        """
        # No API call needed - Slack webhooks are configured manually in the app dashboard
        # The webhook_url should be used to configure Event Subscriptions in Slack App settings
        return OnMessageState(
            channel_id=input.channel_id,
            user_id=input.user_id,
            webhook_url=webhook_url,
        )

    async def destroy(
        self,
        provider: SlackProviderState,
        input: OnMessageInput,
        state: OnMessageState,
    ) -> None:
        """
        Clean up the trigger state.

        Since Slack Event Subscriptions are configured manually in the Slack App dashboard,
        this method only cleans up the stored state. The Event Subscriptions configuration
        will remain active in the Slack App until manually disabled.

        Note: If multiple workflows use the same Slack app, you should NOT disable
        Event Subscriptions when destroying one trigger.
        """
        # No API call needed - Event Subscriptions remain configured in Slack App
        # The user can manually disable Event Subscriptions if needed
        pass

    async def refresh(
        self,
        provider: SlackProviderState,
        input: OnMessageInput,
        state: OnMessageState,
    ) -> OnMessageState:
        """
        Verify the trigger state is still valid.

        Since Slack Event Subscriptions are configured manually, we cannot programmatically
        verify if the webhook is still active. We simply return the existing state.

        In a production system, you might want to:
        1. Call Slack's auth.test API to verify the bot token is still valid
        2. Check if the configured channels/users still exist
        3. Verify the app still has the required scopes
        """
        # No verification needed - return existing state
        # In production, you could call Slack API to verify bot token and permissions
        return state


# Registry of Slack trigger types
SLACK_TRIGGER_TYPES = {
    "onMessage": OnMessage,
}
