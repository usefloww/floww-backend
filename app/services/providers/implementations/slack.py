from typing import TYPE_CHECKING, Optional

import structlog
from fastapi.responses import JSONResponse
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

if TYPE_CHECKING:
    from fastapi import Request

    from app.models import Trigger

logger = structlog.stdlib.get_logger(__name__)


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

    async def validate_webhook(
        self, request: "Request", provider_state: BaseModel
    ) -> Optional[JSONResponse]:
        """
        Handle Slack URL verification challenge.

        When configuring Event Subscriptions in Slack, Slack sends a challenge
        that must be echoed back to verify the webhook URL.
        """
        webhook_data = (
            await request.json()
            if request.headers.get("content-type") == "application/json"
            else {}
        )

        if webhook_data.get("type") == "url_verification":
            challenge = webhook_data.get("challenge")
            if challenge:
                logger.info("Responding to Slack URL verification challenge")
                return JSONResponse(content={"challenge": challenge})

        return None

    async def process_webhook(
        self,
        request: "Request",
        provider_state: BaseModel,
        triggers: list["Trigger"],
    ) -> list["Trigger"]:
        """
        Process Slack webhook and return list of triggers that should be executed.

        Filters events based on:
        - Event type (only event_callback with message events)
        - Bot messages (filtered out to prevent loops)
        - Message subtypes (only new messages and thread_broadcast)
        - Channel ID (if specified in trigger input)
        - User ID (if specified in trigger input)
        """
        webhook_data = (
            await request.json()
            if request.headers.get("content-type") == "application/json"
            else {}
        )

        # Only process message events
        if webhook_data.get("type") != "event_callback":
            logger.debug(
                "Ignoring non-event_callback Slack webhook",
                event_type=webhook_data.get("type"),
            )
            return []

        event = webhook_data.get("event", {})
        if event.get("type") != "message":
            logger.debug(
                "Ignoring non-message Slack event",
                event_type=event.get("type"),
            )
            return []

        # Filter bot messages to avoid loops
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            logger.debug("Ignoring bot message to prevent loops")
            return []

        # Filter message change/delete events - only process new messages
        # This prevents duplicate processing when messages are edited or have metadata added
        subtype = event.get("subtype")
        if subtype and subtype not in ["thread_broadcast"]:
            logger.debug(
                "Ignoring message with subtype",
                subtype=subtype,
            )
            return []

        # Filter triggers based on their input configuration
        matching_triggers = []
        for trigger in triggers:
            # Only process onMessage triggers
            if trigger.trigger_type != "onMessage":
                continue

            trigger_input = trigger.input or {}

            # Apply channel filter if specified
            if trigger_input.get("channel_id") and event.get(
                "channel"
            ) != trigger_input.get("channel_id"):
                logger.debug(
                    "Trigger channel filter mismatch",
                    trigger_id=str(trigger.id),
                    expected_channel=trigger_input.get("channel_id"),
                    actual_channel=event.get("channel"),
                )
                continue

            # Apply user filter if specified
            if trigger_input.get("user_id") and event.get("user") != trigger_input.get(
                "user_id"
            ):
                logger.debug(
                    "Trigger user filter mismatch",
                    trigger_id=str(trigger.id),
                    expected_user=trigger_input.get("user_id"),
                    actual_user=event.get("user"),
                )
                continue

            # Filter thread messages if not included
            # A message is a thread reply if it has thread_ts and it's different from ts
            if not trigger_input.get("include_thread_messages", False):
                thread_ts = event.get("thread_ts")
                message_ts = event.get("ts")
                if thread_ts and thread_ts != message_ts:
                    logger.debug(
                        "Ignoring thread message (include_thread_messages is False)",
                        trigger_id=str(trigger.id),
                        thread_ts=thread_ts,
                        message_ts=message_ts,
                    )
                    continue

            # This trigger matches!
            matching_triggers.append(trigger)
            logger.info(
                "Slack event matched trigger",
                trigger_id=str(trigger.id),
                channel=event.get("channel"),
                user=event.get("user"),
            )

        return matching_triggers


#### Triggers ####
class OnMessageInput(BaseModel):
    channel_id: str | None = None
    user_id: str | None = None
    include_thread_messages: bool = False


class OnMessageState(BaseModel):
    channel_id: str | None = None
    user_id: str | None = None
    include_thread_messages: bool = False
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
        register_webhook,
    ) -> OnMessageState:
        """
        Store the webhook configuration for Slack message events.

        Since Slack Event Subscriptions are configured manually in the Slack App dashboard,
        this method only stores the state. The actual webhook setup must be done manually
        by configuring the Request URL in the Slack App's Event Subscriptions settings.

        Args:
            provider: Slack provider configuration (workspace_url, bot_token)
            input: Filter configuration (channel_id, user_id)
            register_webhook: Callback to register or reuse a webhook URL

        Returns:
            State containing the webhook configuration
        """
        webhook_registration = await register_webhook(
            owner="provider", reuse_existing=True
        )
        webhook_url = webhook_registration["url"]

        # No API call needed - Slack webhooks are configured manually in the app dashboard
        # The webhook_url should be used to configure Event Subscriptions in Slack App settings
        return OnMessageState(
            channel_id=input.channel_id,
            user_id=input.user_id,
            include_thread_messages=input.include_thread_messages,
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
