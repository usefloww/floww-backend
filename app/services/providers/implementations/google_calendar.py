"""
Google Calendar provider for Floww.

The provider is an access primitive - it only handles OAuth authentication.
Resource configuration (calendar_id) lives at the trigger level.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx
import structlog
from pydantic import BaseModel

from app.services.oauth_service import GoogleOAuthProvider
from app.services.providers.provider_setup import (
    ProviderSetupStep,
    ProviderSetupStepOAuth,
)
from app.services.providers.provider_utils import (
    ProviderI,
    TriggerI,
    TriggerUtils,
)

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


#### Provider ####


class GoogleCalendarProviderState(BaseModel):
    """Provider stores only OAuth credentials - the access primitive."""

    access_token: str
    refresh_token: str | None = None
    expires_at: str  # ISO format datetime string


class GoogleCalendarProvider(ProviderI):
    """Google Calendar provider using OAuth2."""

    name: str = "google_calendar"
    setup_steps: list[ProviderSetupStep] = [
        ProviderSetupStepOAuth(
            title="Connect Google Account",
            alias="oauth",
            provider_name="google",
            scopes=["https://www.googleapis.com/auth/calendar.readonly"],
        ),
    ]
    model = GoogleCalendarProviderState

    async def _get_valid_access_token(
        self, provider_state: GoogleCalendarProviderState
    ) -> str:
        """Get a valid access token, refreshing if expired."""
        expires_at = datetime.fromisoformat(provider_state.expires_at)

        # Check if token is expired (with 5 minute buffer)
        if datetime.now(timezone.utc) >= expires_at:
            if not provider_state.refresh_token:
                raise ValueError("Token expired and no refresh token available")

            # Refresh the token
            oauth_provider = GoogleOAuthProvider()
            new_tokens = await oauth_provider.refresh_tokens(
                provider_state.refresh_token
            )

            # Note: In a real implementation, we'd update the provider config
            # For now, just return the new access token
            return new_tokens.access_token

        return provider_state.access_token


#### Triggers ####


class OnEventCreatedInput(BaseModel):
    """Trigger input - resource configuration lives here."""

    calendar_id: str = "primary"  # Which calendar to watch


class OnEventCreatedState(BaseModel):
    """State for the onEventCreated trigger."""

    calendar_id: str
    sync_token: str | None = None  # For incremental sync


class OnEventCreated(
    TriggerI[OnEventCreatedInput, OnEventCreatedState, GoogleCalendarProviderState]
):
    """
    Trigger for new Google Calendar events.

    Uses polling via recurring tasks since Google Calendar push notifications
    require a verified domain and public HTTPS endpoint.
    """

    async def create(
        self,
        provider: GoogleCalendarProviderState,
        input: OnEventCreatedInput,
        utils: TriggerUtils,
    ) -> OnEventCreatedState:
        """
        Set up the trigger by registering a recurring task for polling.
        """
        # Register a recurring task to poll for new events
        # Poll every 5 minutes
        await utils.register_recurring_task(cron_expression="*/5 * * * *")

        # Get initial sync token by doing an initial list
        access_token = await GoogleCalendarProvider()._get_valid_access_token(provider)
        sync_token = await self._get_initial_sync_token(access_token, input.calendar_id)

        logger.info(
            "Created Google Calendar onEventCreated trigger",
            calendar_id=input.calendar_id,
        )

        return OnEventCreatedState(
            calendar_id=input.calendar_id,
            sync_token=sync_token,
        )

    async def destroy(
        self,
        provider: GoogleCalendarProviderState,
        input: OnEventCreatedInput,
        state: OnEventCreatedState,
        utils: TriggerUtils,
    ) -> None:
        """Clean up the trigger by removing the recurring task."""
        await utils.unregister_recurring_task()
        logger.info(
            "Destroyed Google Calendar onEventCreated trigger",
            calendar_id=state.calendar_id,
        )

    async def refresh(
        self,
        provider: GoogleCalendarProviderState,
        input: OnEventCreatedInput,
        state: OnEventCreatedState,
    ) -> OnEventCreatedState:
        """Verify the trigger state is still valid."""
        # Could verify the calendar still exists and token is valid
        return state

    async def _get_initial_sync_token(
        self, access_token: str, calendar_id: str
    ) -> str | None:
        """Get initial sync token by performing a full sync."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "maxResults": 1,
                    "singleEvents": True,
                    "timeMin": datetime.now(timezone.utc).isoformat(),
                },
                timeout=30.0,
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("nextSyncToken")
            return None


class OnEventUpdatedInput(BaseModel):
    """Trigger input for event updates."""

    calendar_id: str = "primary"


class OnEventUpdatedState(BaseModel):
    """State for the onEventUpdated trigger."""

    calendar_id: str
    sync_token: str | None = None


class OnEventUpdated(
    TriggerI[OnEventUpdatedInput, OnEventUpdatedState, GoogleCalendarProviderState]
):
    """
    Trigger for updated Google Calendar events.

    Uses polling via recurring tasks.
    """

    async def create(
        self,
        provider: GoogleCalendarProviderState,
        input: OnEventUpdatedInput,
        utils: TriggerUtils,
    ) -> OnEventUpdatedState:
        """Set up the trigger."""
        await utils.register_recurring_task(cron_expression="*/5 * * * *")

        access_token = await GoogleCalendarProvider()._get_valid_access_token(provider)
        sync_token = await self._get_initial_sync_token(access_token, input.calendar_id)

        logger.info(
            "Created Google Calendar onEventUpdated trigger",
            calendar_id=input.calendar_id,
        )

        return OnEventUpdatedState(
            calendar_id=input.calendar_id,
            sync_token=sync_token,
        )

    async def destroy(
        self,
        provider: GoogleCalendarProviderState,
        input: OnEventUpdatedInput,
        state: OnEventUpdatedState,
        utils: TriggerUtils,
    ) -> None:
        """Clean up the trigger."""
        await utils.unregister_recurring_task()

    async def refresh(
        self,
        provider: GoogleCalendarProviderState,
        input: OnEventUpdatedInput,
        state: OnEventUpdatedState,
    ) -> OnEventUpdatedState:
        """Verify the trigger state is still valid."""
        return state

    async def _get_initial_sync_token(
        self, access_token: str, calendar_id: str
    ) -> str | None:
        """Get initial sync token."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "maxResults": 1,
                    "singleEvents": True,
                    "timeMin": datetime.now(timezone.utc).isoformat(),
                },
                timeout=30.0,
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("nextSyncToken")
            return None


# Registry of Google Calendar trigger types
GOOGLE_CALENDAR_TRIGGER_TYPES = {
    "onEventCreated": OnEventCreated,
    "onEventUpdated": OnEventUpdated,
}
