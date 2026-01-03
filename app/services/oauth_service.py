"""
OAuth service for handling third-party OAuth flows.

Instance-level OAuth app credentials (client_id, client_secret) are configured
in settings.py. User-level tokens are stored per-provider in encrypted_config.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from app.settings import settings

logger = structlog.get_logger(__name__)


class OAuthTokens:
    """Container for OAuth tokens returned from token exchange."""

    def __init__(
        self,
        access_token: str,
        refresh_token: str | None,
        expires_at: datetime,
        token_type: str = "Bearer",
        scope: str | None = None,
    ):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at = expires_at
        self.token_type = token_type
        self.scope = scope

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.isoformat(),
        }


class OAuthProviderBase(ABC):
    """Base class for OAuth providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name (e.g., 'google')."""
        pass

    @abstractmethod
    def get_authorization_url(
        self, scopes: list[str], state: str, redirect_uri: str
    ) -> str:
        """Generate the OAuth authorization URL."""
        pass

    @abstractmethod
    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens:
        """Exchange authorization code for tokens."""
        pass

    @abstractmethod
    async def refresh_tokens(self, refresh_token: str) -> OAuthTokens:
        """Refresh expired tokens using refresh token."""
        pass


class GoogleOAuthProvider(OAuthProviderBase):
    """Google OAuth 2.0 implementation."""

    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"

    @property
    def name(self) -> str:
        return "google"

    def __init__(self):
        self.client_id = settings.GOOGLE_OAUTH_CLIENT_ID
        self.client_secret = settings.GOOGLE_OAUTH_CLIENT_SECRET

    def get_authorization_url(
        self, scopes: list[str], state: str, redirect_uri: str
    ) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "access_type": "offline",  # Request refresh token
            "prompt": "consent",  # Force consent to always get refresh token
        }
        return f"{self.AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            expires_in = data.get("expires_in", 3600)
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

            logger.info(
                "Exchanged OAuth code for tokens",
                provider=self.name,
                has_refresh_token=bool(data.get("refresh_token")),
            )

            return OAuthTokens(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token"),
                expires_at=expires_at,
                token_type=data.get("token_type", "Bearer"),
                scope=data.get("scope"),
            )

    async def refresh_tokens(self, refresh_token: str) -> OAuthTokens:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            expires_in = data.get("expires_in", 3600)
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

            logger.info("Refreshed OAuth tokens", provider=self.name)

            return OAuthTokens(
                access_token=data["access_token"],
                # Google may not return a new refresh token
                refresh_token=data.get("refresh_token", refresh_token),
                expires_at=expires_at,
                token_type=data.get("token_type", "Bearer"),
                scope=data.get("scope"),
            )


# Registry of OAuth providers
OAUTH_PROVIDERS: dict[str, type[OAuthProviderBase]] = {
    "google": GoogleOAuthProvider,
}


def get_oauth_provider(provider_name: str) -> OAuthProviderBase:
    """Get an OAuth provider instance by name."""
    provider_class = OAUTH_PROVIDERS.get(provider_name)
    if not provider_class:
        raise ValueError(f"Unknown OAuth provider: {provider_name}")
    return provider_class()
