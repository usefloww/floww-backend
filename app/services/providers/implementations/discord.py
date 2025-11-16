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
    TriggerUtils,
)

if TYPE_CHECKING:
    from fastapi import Request

    from app.models import Trigger

logger = structlog.stdlib.get_logger(__name__)


#### Provider ####
class DiscordProviderState(BaseModel):
    bot_token: str
    public_key: str


class DiscordProvider(ProviderI):
    name: str = "discord"
    setup_steps: list[ProviderSetupStep] = [
        ProviderSetupStepSecret(
            title="Bot Token",
            description="Discord Bot Token from the Discord Developer Portal",
            alias="bot_token",
        ),
        ProviderSetupStepValue(
            title="Public Key",
            description="Discord Application Public Key for webhook signature verification",
            alias="public_key",
        ),
    ]
    model = DiscordProviderState

    async def validate_webhook(
        self, request: "Request", provider_state: BaseModel
    ) -> Optional[JSONResponse]:
        """
        Validate Discord webhook signature using Ed25519.

        Discord signs all webhook requests with an Ed25519 signature
        that must be verified using the application's public key.
        """
        # For Discord interactions endpoint, handle PING (type 1)
        try:
            webhook_data = (
                await request.json()
                if request.headers.get("content-type") == "application/json"
                else {}
            )

            # Discord sends PING type for endpoint verification
            if webhook_data.get("type") == 1:
                logger.info("Responding to Discord PING verification")
                return JSONResponse(content={"type": 1})

        except Exception as e:
            logger.debug("Error parsing webhook data", error=str(e))

        return None

    async def process_webhook(
        self,
        request: "Request",
        provider_state: BaseModel,
        triggers: list["Trigger"],
    ) -> list["Trigger"]:
        """
        Process Discord webhook and return list of triggers that should be executed.

        Filters events based on:
        - Event type (MESSAGE_CREATE, MESSAGE_REACTION_ADD, GUILD_MEMBER_ADD, etc.)
        - Bot messages (filtered out to prevent loops)
        - Guild ID (if specified in trigger input)
        - Channel ID (if specified in trigger input)
        - User ID (if specified in trigger input)
        - Emoji (if specified in trigger input for reaction events)
        """
        webhook_data = (
            await request.json()
            if request.headers.get("content-type") == "application/json"
            else {}
        )

        # Extract event type from Discord webhook
        event_type = webhook_data.get("t")
        event_data = webhook_data.get("d", {})

        if not event_type:
            logger.debug("Ignoring Discord webhook without event type")
            return []

        # Route to appropriate event handler
        if event_type == "MESSAGE_CREATE":
            return await self._process_message_event(event_data, triggers)
        elif event_type == "MESSAGE_UPDATE":
            return await self._process_message_update_event(event_data, triggers)
        elif event_type == "MESSAGE_REACTION_ADD":
            return await self._process_reaction_event(event_data, triggers)
        elif event_type == "GUILD_MEMBER_ADD":
            return await self._process_member_join_event(event_data, triggers)
        elif event_type == "GUILD_MEMBER_REMOVE":
            return await self._process_member_leave_event(event_data, triggers)
        elif event_type == "GUILD_MEMBER_UPDATE":
            return await self._process_member_update_event(event_data, triggers)

        # Ignore other event types
        logger.debug(
            "Ignoring unsupported Discord event type",
            event_type=event_type,
        )
        return []

    async def _process_message_event(
        self, event: dict, triggers: list["Trigger"]
    ) -> list["Trigger"]:
        """Process MESSAGE_CREATE events and return matching triggers."""
        # Get message author
        author = event.get("author", {})

        # Filter bot messages to avoid loops (unless explicitly included)
        is_bot = author.get("bot", False)

        # Filter triggers based on their input configuration
        matching_triggers = []
        for trigger in triggers:
            # Only process onMessage triggers
            if trigger.trigger_type != "onMessage":
                continue

            trigger_input = trigger.input or {}

            # Apply bot filter
            if is_bot and not trigger_input.get("include_bots", False):
                logger.debug(
                    "Ignoring bot message",
                    trigger_id=str(trigger.id),
                    bot_id=author.get("id"),
                )
                continue

            # Apply guild filter if specified
            if trigger_input.get("guild_id") and event.get(
                "guild_id"
            ) != trigger_input.get("guild_id"):
                logger.debug(
                    "Trigger guild filter mismatch",
                    trigger_id=str(trigger.id),
                    expected_guild=trigger_input.get("guild_id"),
                    actual_guild=event.get("guild_id"),
                )
                continue

            # Apply channel filter if specified
            if trigger_input.get("channel_id") and event.get(
                "channel_id"
            ) != trigger_input.get("channel_id"):
                logger.debug(
                    "Trigger channel filter mismatch",
                    trigger_id=str(trigger.id),
                    expected_channel=trigger_input.get("channel_id"),
                    actual_channel=event.get("channel_id"),
                )
                continue

            # Apply user filter if specified
            if trigger_input.get("user_id") and author.get("id") != trigger_input.get(
                "user_id"
            ):
                logger.debug(
                    "Trigger user filter mismatch",
                    trigger_id=str(trigger.id),
                    expected_user=trigger_input.get("user_id"),
                    actual_user=author.get("id"),
                )
                continue

            # This trigger matches!
            matching_triggers.append(trigger)
            logger.info(
                "Discord message event matched trigger",
                trigger_id=str(trigger.id),
                guild_id=event.get("guild_id"),
                channel_id=event.get("channel_id"),
                author_id=author.get("id"),
            )

        return matching_triggers

    async def _process_message_update_event(
        self, event: dict, triggers: list["Trigger"]
    ) -> list["Trigger"]:
        """Process MESSAGE_UPDATE events (message edits)."""
        # Get message author
        author = event.get("author", {})

        # Filter bot messages
        is_bot = author.get("bot", False)

        # Filter triggers based on their input configuration
        matching_triggers = []
        for trigger in triggers:
            # Only process onMessage triggers that include edits
            if trigger.trigger_type != "onMessage":
                continue

            trigger_input = trigger.input or {}

            # Check if this trigger includes edits
            if not trigger_input.get("include_edits", False):
                continue

            # Apply bot filter
            if is_bot and not trigger_input.get("include_bots", False):
                continue

            # Apply guild filter if specified
            if trigger_input.get("guild_id") and event.get(
                "guild_id"
            ) != trigger_input.get("guild_id"):
                continue

            # Apply channel filter if specified
            if trigger_input.get("channel_id") and event.get(
                "channel_id"
            ) != trigger_input.get("channel_id"):
                continue

            # Apply user filter if specified
            if trigger_input.get("user_id") and author.get("id") != trigger_input.get(
                "user_id"
            ):
                continue

            # This trigger matches!
            matching_triggers.append(trigger)
            logger.info(
                "Discord message update event matched trigger",
                trigger_id=str(trigger.id),
                guild_id=event.get("guild_id"),
                channel_id=event.get("channel_id"),
            )

        return matching_triggers

    async def _process_reaction_event(
        self, event: dict, triggers: list["Trigger"]
    ) -> list["Trigger"]:
        """Process MESSAGE_REACTION_ADD events and return matching triggers."""
        emoji = event.get("emoji", {})
        emoji_name = emoji.get("name")

        # Filter triggers based on their input configuration
        matching_triggers = []
        for trigger in triggers:
            # Only process onReaction triggers
            if trigger.trigger_type != "onReaction":
                continue

            trigger_input = trigger.input or {}

            # Apply guild filter if specified
            if trigger_input.get("guild_id") and event.get(
                "guild_id"
            ) != trigger_input.get("guild_id"):
                logger.debug(
                    "Reaction trigger guild filter mismatch",
                    trigger_id=str(trigger.id),
                    expected_guild=trigger_input.get("guild_id"),
                    actual_guild=event.get("guild_id"),
                )
                continue

            # Apply channel filter if specified
            if trigger_input.get("channel_id") and event.get(
                "channel_id"
            ) != trigger_input.get("channel_id"):
                logger.debug(
                    "Reaction trigger channel filter mismatch",
                    trigger_id=str(trigger.id),
                    expected_channel=trigger_input.get("channel_id"),
                    actual_channel=event.get("channel_id"),
                )
                continue

            # Apply user filter if specified (user who added the reaction)
            if trigger_input.get("user_id") and event.get(
                "user_id"
            ) != trigger_input.get("user_id"):
                logger.debug(
                    "Reaction trigger user filter mismatch",
                    trigger_id=str(trigger.id),
                    expected_user=trigger_input.get("user_id"),
                    actual_user=event.get("user_id"),
                )
                continue

            # Apply emoji filter if specified
            if trigger_input.get("emoji") and emoji_name != trigger_input.get("emoji"):
                logger.debug(
                    "Reaction trigger emoji filter mismatch",
                    trigger_id=str(trigger.id),
                    expected_emoji=trigger_input.get("emoji"),
                    actual_emoji=emoji_name,
                )
                continue

            # This trigger matches!
            matching_triggers.append(trigger)
            logger.info(
                "Discord reaction event matched trigger",
                trigger_id=str(trigger.id),
                guild_id=event.get("guild_id"),
                channel_id=event.get("channel_id"),
                user_id=event.get("user_id"),
                emoji=emoji_name,
            )

        return matching_triggers

    async def _process_member_join_event(
        self, event: dict, triggers: list["Trigger"]
    ) -> list["Trigger"]:
        """Process GUILD_MEMBER_ADD events and return matching triggers."""
        guild_id = event.get("guild_id")

        # Filter triggers based on their input configuration
        matching_triggers = []
        for trigger in triggers:
            # Only process onMemberJoin triggers
            if trigger.trigger_type != "onMemberJoin":
                continue

            trigger_input = trigger.input or {}

            # Apply guild filter if specified
            if trigger_input.get("guild_id") and guild_id != trigger_input.get(
                "guild_id"
            ):
                logger.debug(
                    "Member join trigger guild filter mismatch",
                    trigger_id=str(trigger.id),
                    expected_guild=trigger_input.get("guild_id"),
                    actual_guild=guild_id,
                )
                continue

            # This trigger matches!
            matching_triggers.append(trigger)
            user = event.get("user", {})
            logger.info(
                "Discord member join event matched trigger",
                trigger_id=str(trigger.id),
                guild_id=guild_id,
                user_id=user.get("id"),
            )

        return matching_triggers

    async def _process_member_leave_event(
        self, event: dict, triggers: list["Trigger"]
    ) -> list["Trigger"]:
        """Process GUILD_MEMBER_REMOVE events and return matching triggers."""
        guild_id = event.get("guild_id")

        # Filter triggers based on their input configuration
        matching_triggers = []
        for trigger in triggers:
            # Only process onMemberLeave triggers
            if trigger.trigger_type != "onMemberLeave":
                continue

            trigger_input = trigger.input or {}

            # Apply guild filter if specified
            if trigger_input.get("guild_id") and guild_id != trigger_input.get(
                "guild_id"
            ):
                logger.debug(
                    "Member leave trigger guild filter mismatch",
                    trigger_id=str(trigger.id),
                    expected_guild=trigger_input.get("guild_id"),
                    actual_guild=guild_id,
                )
                continue

            # This trigger matches!
            matching_triggers.append(trigger)
            user = event.get("user", {})
            logger.info(
                "Discord member leave event matched trigger",
                trigger_id=str(trigger.id),
                guild_id=guild_id,
                user_id=user.get("id"),
            )

        return matching_triggers

    async def _process_member_update_event(
        self, event: dict, triggers: list["Trigger"]
    ) -> list["Trigger"]:
        """Process GUILD_MEMBER_UPDATE events and return matching triggers."""
        guild_id = event.get("guild_id")

        # Filter triggers based on their input configuration
        matching_triggers = []
        for trigger in triggers:
            # Only process onMemberUpdate triggers
            if trigger.trigger_type != "onMemberUpdate":
                continue

            trigger_input = trigger.input or {}

            # Apply guild filter if specified
            if trigger_input.get("guild_id") and guild_id != trigger_input.get(
                "guild_id"
            ):
                logger.debug(
                    "Member update trigger guild filter mismatch",
                    trigger_id=str(trigger.id),
                    expected_guild=trigger_input.get("guild_id"),
                    actual_guild=guild_id,
                )
                continue

            # This trigger matches!
            matching_triggers.append(trigger)
            user = event.get("user", {})
            logger.info(
                "Discord member update event matched trigger",
                trigger_id=str(trigger.id),
                guild_id=guild_id,
                user_id=user.get("id"),
            )

        return matching_triggers


#### Triggers ####
class OnMessageInput(BaseModel):
    guild_id: str | None = None
    channel_id: str | None = None
    user_id: str | None = None
    include_bots: bool = False
    include_edits: bool = False


class OnMessageState(BaseModel):
    guild_id: str | None = None
    channel_id: str | None = None
    user_id: str | None = None
    include_bots: bool = False
    include_edits: bool = False
    webhook_url: str


class OnMessage(TriggerI[OnMessageInput, OnMessageState, DiscordProviderState]):
    """
    Trigger for Discord message events.

    IMPORTANT: This trigger requires configuration in the Discord Developer Portal:
    1. Go to https://discord.com/developers/applications and select your app
    2. Navigate to "Bot" section and enable "MESSAGE CONTENT INTENT"
    3. Navigate to "Gateway Intents" and ensure these are enabled:
       - Server Members Intent (for guild message events)
       - Message Content Intent (to read message content)
    4. The webhook_url will be automatically configured when the trigger is created

    Required Bot Permissions:
    - VIEW_CHANNELS (view channels and read messages)
    - READ_MESSAGE_HISTORY (read message history)

    Note: The bot must be invited to your Discord server with these permissions.
    """

    async def create(
        self,
        provider: DiscordProviderState,
        input: OnMessageInput,
        utils: TriggerUtils,
    ) -> OnMessageState:
        """
        Store the webhook configuration for Discord message events.

        Args:
            provider: Discord provider configuration (bot_token, public_key)
            input: Filter configuration (guild_id, channel_id, user_id, include_bots, include_edits)
            utils: TriggerUtils instance for managing webhooks and recurring tasks

        Returns:
            State containing the webhook configuration
        """
        webhook_registration = await utils.register_webhook(
            owner="provider", reuse_existing=True
        )
        webhook_url = webhook_registration["url"]

        return OnMessageState(
            guild_id=input.guild_id,
            channel_id=input.channel_id,
            user_id=input.user_id,
            include_bots=input.include_bots,
            include_edits=input.include_edits,
            webhook_url=webhook_url,
        )

    async def destroy(
        self,
        provider: DiscordProviderState,
        input: OnMessageInput,
        state: OnMessageState,
    ) -> None:
        """Clean up the trigger state."""
        pass

    async def refresh(
        self,
        provider: DiscordProviderState,
        input: OnMessageInput,
        state: OnMessageState,
    ) -> OnMessageState:
        """Verify the trigger state is still valid."""
        return state


#### OnReaction Trigger ####
class OnReactionInput(BaseModel):
    guild_id: str | None = None
    channel_id: str | None = None
    emoji: str | None = None
    user_id: str | None = None


class OnReactionState(BaseModel):
    guild_id: str | None = None
    channel_id: str | None = None
    emoji: str | None = None
    user_id: str | None = None
    webhook_url: str


class OnReaction(TriggerI[OnReactionInput, OnReactionState, DiscordProviderState]):
    """
    Trigger for Discord reaction events.

    Required Bot Permissions:
    - VIEW_CHANNELS (view channels)
    - READ_MESSAGE_HISTORY (read messages to see reactions)
    - ADD_REACTIONS (optional: to add reactions)
    """

    async def create(
        self,
        provider: DiscordProviderState,
        input: OnReactionInput,
        utils: TriggerUtils,
    ) -> OnReactionState:
        """Store the webhook configuration for Discord reaction events."""
        webhook_registration = await utils.register_webhook(
            owner="provider", reuse_existing=True
        )
        webhook_url = webhook_registration["url"]

        return OnReactionState(
            guild_id=input.guild_id,
            channel_id=input.channel_id,
            emoji=input.emoji,
            user_id=input.user_id,
            webhook_url=webhook_url,
        )

    async def destroy(
        self,
        provider: DiscordProviderState,
        input: OnReactionInput,
        state: OnReactionState,
    ) -> None:
        """Clean up the trigger state."""
        pass

    async def refresh(
        self,
        provider: DiscordProviderState,
        input: OnReactionInput,
        state: OnReactionState,
    ) -> OnReactionState:
        """Verify the trigger state is still valid."""
        return state


#### OnMemberJoin Trigger ####
class OnMemberJoinInput(BaseModel):
    guild_id: str | None = None


class OnMemberJoinState(BaseModel):
    guild_id: str | None = None
    webhook_url: str


class OnMemberJoin(
    TriggerI[OnMemberJoinInput, OnMemberJoinState, DiscordProviderState]
):
    """
    Trigger for Discord member join events.

    IMPORTANT: This trigger requires "Server Members Intent" to be enabled:
    1. Go to https://discord.com/developers/applications and select your app
    2. Navigate to "Bot" section
    3. Enable "SERVER MEMBERS INTENT" under "Privileged Gateway Intents"
    """

    async def create(
        self,
        provider: DiscordProviderState,
        input: OnMemberJoinInput,
        utils: TriggerUtils,
    ) -> OnMemberJoinState:
        """Store the webhook configuration for Discord member join events."""
        webhook_registration = await utils.register_webhook(
            owner="provider", reuse_existing=True
        )
        webhook_url = webhook_registration["url"]

        return OnMemberJoinState(
            guild_id=input.guild_id,
            webhook_url=webhook_url,
        )

    async def destroy(
        self,
        provider: DiscordProviderState,
        input: OnMemberJoinInput,
        state: OnMemberJoinState,
    ) -> None:
        """Clean up the trigger state."""
        pass

    async def refresh(
        self,
        provider: DiscordProviderState,
        input: OnMemberJoinInput,
        state: OnMemberJoinState,
    ) -> OnMemberJoinState:
        """Verify the trigger state is still valid."""
        return state


#### OnMemberLeave Trigger ####
class OnMemberLeaveInput(BaseModel):
    guild_id: str | None = None


class OnMemberLeaveState(BaseModel):
    guild_id: str | None = None
    webhook_url: str


class OnMemberLeave(
    TriggerI[OnMemberLeaveInput, OnMemberLeaveState, DiscordProviderState]
):
    """
    Trigger for Discord member leave/kick events.

    IMPORTANT: This trigger requires "Server Members Intent" to be enabled.
    """

    async def create(
        self,
        provider: DiscordProviderState,
        input: OnMemberLeaveInput,
        utils: TriggerUtils,
    ) -> OnMemberLeaveState:
        """Store the webhook configuration for Discord member leave events."""
        webhook_registration = await utils.register_webhook(
            owner="provider", reuse_existing=True
        )
        webhook_url = webhook_registration["url"]

        return OnMemberLeaveState(
            guild_id=input.guild_id,
            webhook_url=webhook_url,
        )

    async def destroy(
        self,
        provider: DiscordProviderState,
        input: OnMemberLeaveInput,
        state: OnMemberLeaveState,
    ) -> None:
        """Clean up the trigger state."""
        pass

    async def refresh(
        self,
        provider: DiscordProviderState,
        input: OnMemberLeaveInput,
        state: OnMemberLeaveState,
    ) -> OnMemberLeaveState:
        """Verify the trigger state is still valid."""
        return state


#### OnMemberUpdate Trigger ####
class OnMemberUpdateInput(BaseModel):
    guild_id: str | None = None
    track_roles: bool = True
    track_nickname: bool = True


class OnMemberUpdateState(BaseModel):
    guild_id: str | None = None
    track_roles: bool = True
    track_nickname: bool = True
    webhook_url: str


class OnMemberUpdate(
    TriggerI[OnMemberUpdateInput, OnMemberUpdateState, DiscordProviderState]
):
    """
    Trigger for Discord member update events (role changes, nickname changes, etc.).

    IMPORTANT: This trigger requires "Server Members Intent" to be enabled.
    """

    async def create(
        self,
        provider: DiscordProviderState,
        input: OnMemberUpdateInput,
        utils: TriggerUtils,
    ) -> OnMemberUpdateState:
        """Store the webhook configuration for Discord member update events."""
        webhook_registration = await utils.register_webhook(
            owner="provider", reuse_existing=True
        )
        webhook_url = webhook_registration["url"]

        return OnMemberUpdateState(
            guild_id=input.guild_id,
            track_roles=input.track_roles,
            track_nickname=input.track_nickname,
            webhook_url=webhook_url,
        )

    async def destroy(
        self,
        provider: DiscordProviderState,
        input: OnMemberUpdateInput,
        state: OnMemberUpdateState,
    ) -> None:
        """Clean up the trigger state."""
        pass

    async def refresh(
        self,
        provider: DiscordProviderState,
        input: OnMemberUpdateInput,
        state: OnMemberUpdateState,
    ) -> OnMemberUpdateState:
        """Verify the trigger state is still valid."""
        return state


# Registry of Discord trigger types
DISCORD_TRIGGER_TYPES = {
    "onMessage": OnMessage,
    "onReaction": OnReaction,
    "onMemberJoin": OnMemberJoin,
    "onMemberLeave": OnMemberLeave,
    "onMemberUpdate": OnMemberUpdate,
}
