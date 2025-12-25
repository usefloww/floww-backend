from typing import TYPE_CHECKING, Optional

import structlog
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.services.providers.provider_setup import (
    ProviderSetupStep,
    ProviderSetupStepSecret,
    ProviderSetupStepValue,
    ProviderSetupStepWebhook,
)
from app.services.providers.provider_utils import (
    ProviderI,
    TriggerI,
    TriggerUtils,
)

if TYPE_CHECKING:
    from fastapi import Request

    from app.models import Trigger

logger = structlog.stdlib.get_logger(__name__)


#### Provider ####
class SlackProviderState(BaseModel):
    workspace_url: str
    bot_token: str
    webhook_url: str


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
        ProviderSetupStepWebhook(
            title="Webhook URL",
            description="The webhook URL for Slack",
            alias="webhook_url",
            required=False,
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
        - Event type (only event_callback with message or reaction_added events)
        - Bot messages (filtered out to prevent loops)
        - Message subtypes (only new messages and thread_broadcast)
        - Channel ID (if specified in trigger input)
        - User ID (if specified in trigger input)
        - Reaction name (if specified in trigger input for reaction events)
        """
        webhook_data = (
            await request.json()
            if request.headers.get("content-type") == "application/json"
            else {}
        )

        # Only process event callbacks
        if webhook_data.get("type") != "event_callback":
            logger.debug(
                "Ignoring non-event_callback Slack webhook",
                event_type=webhook_data.get("type"),
            )
            return []

        event = webhook_data.get("event", {})
        event_type = event.get("type")

        # Handle message events
        if event_type == "message":
            return await self._process_message_event(event, triggers)

        # Handle reaction_added events
        if event_type == "reaction_added":
            return await self._process_reaction_event(event, triggers)

        # Ignore other event types
        logger.debug(
            "Ignoring unsupported Slack event type",
            event_type=event_type,
        )
        return []

    async def _process_message_event(
        self, event: dict, triggers: list["Trigger"]
    ) -> list["Trigger"]:
        """Process message events and return matching triggers."""
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
                "Slack message event matched trigger",
                trigger_id=str(trigger.id),
                channel=event.get("channel"),
                user=event.get("user"),
            )

        return matching_triggers

    async def _process_reaction_event(
        self, event: dict, triggers: list["Trigger"]
    ) -> list["Trigger"]:
        """Process reaction_added events and return matching triggers."""
        # Extract channel from item (reactions are on messages)
        item = event.get("item", {})
        channel = item.get("channel")

        # Filter triggers based on their input configuration
        matching_triggers = []
        for trigger in triggers:
            # Only process onReaction triggers
            if trigger.trigger_type != "onReaction":
                continue

            trigger_input = trigger.input or {}

            # Apply channel filter if specified
            if trigger_input.get("channel_id") and channel != trigger_input.get(
                "channel_id"
            ):
                logger.debug(
                    "Reaction trigger channel filter mismatch",
                    trigger_id=str(trigger.id),
                    expected_channel=trigger_input.get("channel_id"),
                    actual_channel=channel,
                )
                continue

            # Apply user filter if specified (user who added the reaction)
            if trigger_input.get("user_id") and event.get("user") != trigger_input.get(
                "user_id"
            ):
                logger.debug(
                    "Reaction trigger user filter mismatch",
                    trigger_id=str(trigger.id),
                    expected_user=trigger_input.get("user_id"),
                    actual_user=event.get("user"),
                )
                continue

            # Apply reaction name filter if specified
            if trigger_input.get("reaction") and event.get(
                "reaction"
            ) != trigger_input.get("reaction"):
                logger.debug(
                    "Reaction trigger reaction name filter mismatch",
                    trigger_id=str(trigger.id),
                    expected_reaction=trigger_input.get("reaction"),
                    actual_reaction=event.get("reaction"),
                )
                continue

            # This trigger matches!
            matching_triggers.append(trigger)
            logger.info(
                "Slack reaction event matched trigger",
                trigger_id=str(trigger.id),
                channel=channel,
                user=event.get("user"),
                reaction=event.get("reaction"),
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


class OnMessage(TriggerI[OnMessageInput, OnMessageState, SlackProviderState]):
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
        utils: TriggerUtils,
    ) -> OnMessageState:
        """
        Store the trigger configuration for Slack message events.

        The webhook URL is configured at the provider level during provider setup,
        so this method only stores the trigger-specific filter configuration.

        Args:
            provider: Slack provider configuration (workspace_url, bot_token, webhook_url)
            input: Filter configuration (channel_id, user_id)
            utils: TriggerUtils instance for managing webhooks and recurring tasks

        Returns:
            State containing the trigger configuration
        """
        # No webhook registration needed - the provider already has a webhook_url
        # No API call needed - Slack webhooks are configured manually in the app dashboard
        return OnMessageState(
            channel_id=input.channel_id,
            user_id=input.user_id,
            include_thread_messages=input.include_thread_messages,
        )

    async def destroy(
        self,
        provider: SlackProviderState,
        input: OnMessageInput,
        state: OnMessageState,
        utils: TriggerUtils,
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


#### OnReaction Trigger ####
class OnReactionInput(BaseModel):
    channel_id: str | None = None
    user_id: str | None = None
    reaction: str | None = None


class OnReactionState(BaseModel):
    channel_id: str | None = None
    user_id: str | None = None
    reaction: str | None = None


class OnReaction(TriggerI[OnReactionInput, OnReactionState, SlackProviderState]):
    """
    Trigger for Slack reaction_added events.

    IMPORTANT: This trigger requires manual configuration in the Slack App dashboard:
    1. Go to https://api.slack.com/apps and select your Slack app
    2. Navigate to "Event Subscriptions" in the left sidebar
    3. Toggle "Enable Events" to On
    4. Set the "Request URL" to the webhook_url provided during trigger creation
    5. Under "Subscribe to bot events", add the following event:
       - reaction_added (a reaction was added to a message)
    6. Click "Save Changes"
    7. Reinstall your app to the workspace if prompted

    Required OAuth Scopes (configure in "OAuth & Permissions"):
    - reactions:read (view emoji reactions and their associated content)

    Note: Slack will send a URL verification challenge to the webhook_url when you save
    the Event Subscriptions configuration. The webhook handler must respond with the
    challenge value to complete the setup.
    """

    async def create(
        self,
        provider: SlackProviderState,
        input: OnReactionInput,
        utils: TriggerUtils,
    ) -> OnReactionState:
        """
        Store the trigger configuration for Slack reaction events.

        The webhook URL is configured at the provider level during provider setup,
        so this method only stores the trigger-specific filter configuration.

        Args:
            provider: Slack provider configuration (workspace_url, bot_token, webhook_url)
            input: Filter configuration (channel_id, user_id, reaction)
            utils: TriggerUtils instance for managing webhooks and recurring tasks

        Returns:
            State containing the trigger configuration
        """
        # No webhook registration needed - the provider already has a webhook_url
        # No API call needed - Slack webhooks are configured manually in the app dashboard
        return OnReactionState(
            channel_id=input.channel_id,
            user_id=input.user_id,
            reaction=input.reaction,
        )

    async def destroy(
        self,
        provider: SlackProviderState,
        input: OnReactionInput,
        state: OnReactionState,
        utils: TriggerUtils,
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
        input: OnReactionInput,
        state: OnReactionState,
    ) -> OnReactionState:
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
    "onReaction": OnReaction,
}
