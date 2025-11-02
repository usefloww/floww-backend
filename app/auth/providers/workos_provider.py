import urllib.parse
from typing import Any

import httpx
from fastapi import HTTPException, status

from app.auth.provider_base import AuthProvider
from app.settings import settings


class WorkOSProvider(AuthProvider):
    """WorkOS authentication provider using AuthKit."""

    def __init__(self):
        self.client_id = settings.AUTH_CLIENT_ID
        self.client_secret = settings.AUTH_CLIENT_SECRET
        self.api_url = settings.AUTH_API_URL

    def get_authorization_url(
        self, redirect_uri: str, state: str, scope: str = "openid profile email"
    ) -> str:
        auth_params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "scope": "profile email",
            "provider": "authkit",
        }

        return f"{self.api_url}/user_management/authorize?" + urllib.parse.urlencode(
            auth_params
        )

    async def exchange_code_for_token(
        self, code: str, redirect_uri: str
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient() as client:
                token_response = await client.post(
                    f"{self.api_url}/user_management/authenticate",
                    json={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "grant_type": "authorization_code",
                        "code": code,
                    },
                    headers={"Content-Type": "application/json"},
                )

                if token_response.status_code != 200:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Failed to exchange code for token: {token_response.text}",
                    )

                token_data = token_response.json()

                if not token_data.get("access_token"):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="No access token received from WorkOS",
                    )

                return token_data

        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to communicate with WorkOS: {str(e)}",
            )

    async def get_audience(self) -> str:
        return self.client_id

    async def get_jwks_url(self) -> str:
        return f"{self.api_url}/sso/jwks/{self.client_id}"

    async def get_issuer(self) -> str:
        return f"{self.api_url}/user_management/{self.client_id}"

    def validate_provider_config(self) -> None:
        if not self.client_id:
            raise ValueError("AUTH_CLIENT_ID is required for WorkOS provider")
        if not self.client_secret:
            raise ValueError("AUTH_CLIENT_SECRET is required for WorkOS provider")
        if not self.api_url:
            raise ValueError("AUTH_API_URL is required for WorkOS provider")
