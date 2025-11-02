import urllib.parse
from typing import Any

import httpx
from fastapi import HTTPException, status

from app.auth.provider_base import AuthProvider
from app.settings import settings

_discovery_cache: dict[str, dict[str, Any]] = {}


async def _fetch_discovery_document(issuer_url: str) -> dict[str, Any]:
    if issuer_url in _discovery_cache:
        return _discovery_cache[issuer_url]

    try:
        discovery_url = f"{issuer_url}/.well-known/openid-configuration"

        async with httpx.AsyncClient() as client:
            response = await client.get(discovery_url, timeout=10.0)

            if response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to fetch OIDC discovery document: {response.text}",
                )

            _discovery_cache[issuer_url] = response.json()
            return _discovery_cache[issuer_url]

    except httpx.RequestError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch OIDC discovery document: {str(e)}",
        )


class OIDCProvider(AuthProvider):
    """Generic OIDC provider supporting Auth0, Keycloak, Authentik, etc."""

    def __init__(self):
        self.client_id = settings.AUTH_CLIENT_ID
        self.client_secret = settings.AUTH_CLIENT_SECRET
        self.issuer_url = settings.AUTH_ISSUER_URL.rstrip("/")
        self.jwks_url_override = settings.AUTH_JWKS_URL.rstrip("/")

    def get_authorization_url(
        self, redirect_uri: str, state: str, scope: str = "openid profile email"
    ) -> str:
        auth_params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "scope": scope,
        }
        auth_endpoint = f"{self.issuer_url}/authorize"
        return auth_endpoint + "?" + urllib.parse.urlencode(auth_params)

    async def exchange_code_for_token(
        self, code: str, redirect_uri: str
    ) -> dict[str, Any]:
        try:
            discovery = await _fetch_discovery_document(self.issuer_url)
            token_endpoint = discovery.get("token_endpoint")

            if not token_endpoint:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Token endpoint not found in OIDC discovery document",
                )

            async with httpx.AsyncClient() as client:
                token_response = await client.post(
                    token_endpoint,
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": redirect_uri,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )

                if token_response.status_code != 200:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Failed to exchange code for token: {token_response.text}",
                    )

                token_data = token_response.json()

                if not token_data.get("access_token") and not token_data.get(
                    "id_token"
                ):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="No access_token or id_token received from OIDC provider",
                    )

                if not token_data.get("access_token") and token_data.get("id_token"):
                    token_data["access_token"] = token_data["id_token"]

                return token_data

        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to communicate with OIDC provider: {str(e)}",
            )

    async def get_jwks_url(self) -> str:
        if self.jwks_url_override:
            return self.jwks_url_override

        discovery = await _fetch_discovery_document(self.issuer_url)
        jwks_uri = discovery.get("jwks_uri")

        if not jwks_uri:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="JWKS URI not found in OIDC discovery document",
            )

        return jwks_uri

    async def get_issuer(self) -> str:
        try:
            discovery = await _fetch_discovery_document(self.issuer_url)
            issuer = discovery.get("issuer")
            if issuer:
                return issuer
        except Exception:
            pass

        return self.issuer_url

    async def get_audience(self) -> str:
        return self.client_id

    def validate_provider_config(self) -> None:
        if not self.client_id:
            raise ValueError("AUTH_CLIENT_ID is required for OIDC provider")
        if not self.client_secret:
            raise ValueError("AUTH_CLIENT_SECRET is required for OIDC provider")
        if not self.issuer_url:
            raise ValueError("AUTH_ISSUER_URL is required for OIDC provider")
