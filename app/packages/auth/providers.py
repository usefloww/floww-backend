import urllib.parse
from abc import ABC, abstractmethod

import jwt as pyjwt
from pydantic import BaseModel
from workos import WorkOSClient

from app.packages.auth.utils import (
    exchange_code_for_token as _exchange_code_for_token,
)
from app.packages.auth.utils import (
    get_authorization_url as _get_authorization_url,
)
from app.packages.auth.utils import (
    get_jwks,
    get_oidc_discovery,
)
from app.packages.auth.utils import (
    validate_jwt as _validate_jwt,
)
from app.settings import settings


class AuthConfig(BaseModel):
    client_id: str
    client_secret: str
    device_authorization_endpoint: str
    token_endpoint: str
    authorization_endpoint: str
    issuer: str
    jwks_uri: str
    audience: str | None


class AuthProvider(ABC):
    @abstractmethod
    async def get_config(self) -> AuthConfig: ...

    @abstractmethod
    async def validate_token(self, token: str) -> str: ...

    @abstractmethod
    async def get_authorization_url(
        self, redirect_uri: str, state: str, prompt: str | None = None
    ) -> str: ...

    @abstractmethod
    async def exchange_code_for_token(self, code: str, redirect_uri: str) -> dict: ...

    @abstractmethod
    async def revoke_session(self, jwt_token: str) -> None: ...


class OIDCProvider(AuthProvider):
    def __init__(self, client_id: str, client_secret: str, issuer_url: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.issuer_url = issuer_url

    async def get_config(self) -> AuthConfig:
        discovery = await get_oidc_discovery(self.issuer_url)
        return AuthConfig(
            client_id=self.client_id,
            client_secret=self.client_secret,
            device_authorization_endpoint=discovery.get("device_authorization_endpoint")
            or "",
            token_endpoint=discovery.get("token_endpoint") or "",
            authorization_endpoint=discovery.get("authorization_endpoint") or "",
            issuer=discovery.get("issuer") or self.issuer_url,
            jwks_uri=discovery.get("jwks_uri") or "",
            audience=discovery.get("audience"),
        )

    async def validate_token(self, token: str) -> str:
        config = await self.get_config()
        jwks = await get_jwks(config.jwks_uri)
        jwks_keys = jwks.get("keys", [])
        return await _validate_jwt(
            token=token,
            jwks_keys=jwks_keys,
            issuer=config.issuer,
            audience=config.audience,
            algorithm=settings.JWT_ALGORITHM,
        )

    async def get_authorization_url(
        self, redirect_uri: str, state: str, prompt: str | None = None
    ) -> str:
        config = await self.get_config()
        return _get_authorization_url(
            authorization_endpoint=config.authorization_endpoint,
            client_id=self.client_id,
            redirect_uri=redirect_uri,
            state=state,
            prompt=prompt,
        )

    async def exchange_code_for_token(self, code: str, redirect_uri: str) -> dict:
        config = await self.get_config()
        return await _exchange_code_for_token(
            token_endpoint=config.token_endpoint,
            client_id=self.client_id,
            client_secret=self.client_secret,
            code=code,
            redirect_uri=redirect_uri,
        )

    async def revoke_session(self, jwt_token: str) -> None:
        """Generic OIDC providers may not support session revocation."""
        # Most OIDC providers don't have a standard session revocation endpoint
        # This would need to be implemented per-provider if needed
        pass


class WorkOSProvider(AuthProvider):
    def __init__(self, client_id: str, client_secret: str, issuer_url: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.issuer_url = issuer_url
        # Initialize WorkOS client without API key for client-side operations
        self.workos = WorkOSClient(api_key=self.client_secret, client_id=client_id)

    async def get_config(self) -> AuthConfig:
        return AuthConfig(
            client_id=self.client_id,
            client_secret=self.client_secret,
            device_authorization_endpoint="https://api.workos.com/user_management/authorize/device",
            token_endpoint="https://api.workos.com/user_management/authenticate",
            authorization_endpoint="https://api.workos.com/user_management/authorize",
            issuer=f"https://api.workos.com/user_management/{self.client_id}",
            jwks_uri=self.workos.user_management.get_jwks_url(),
            audience=None,
        )

    async def validate_token(self, token: str) -> str:
        config = await self.get_config()
        jwks = await get_jwks(config.jwks_uri)
        jwks_keys = jwks.get("keys", [])
        return await _validate_jwt(
            token=token,
            jwks_keys=jwks_keys,
            issuer=config.issuer,
            audience=config.audience,
            algorithm=settings.JWT_ALGORITHM,
        )

    async def get_authorization_url(
        self, redirect_uri: str, state: str, prompt: str | None = None
    ) -> str:
        # Use WorkOS SDK to generate authorization URL
        authorization_url = self.workos.user_management.get_authorization_url(
            provider="authkit",
            redirect_uri=redirect_uri,
            state=state,
        )

        # Manually append OAuth2 prompt parameter if provided
        # WorkOS AuthKit supports standard OAuth2 parameters
        if prompt:
            separator = "&" if "?" in authorization_url else "?"
            authorization_url = (
                f"{authorization_url}{separator}prompt={urllib.parse.quote(prompt)}"
            )

        return authorization_url

    async def exchange_code_for_token(self, code: str, redirect_uri: str) -> dict:
        result = self.workos.user_management.authenticate_with_code(
            code=code,
        )
        return {
            "access_token": result.access_token,
            "refresh_token": result.refresh_token,
            "id_token": result.access_token,
        }

    async def revoke_session(self, jwt_token: str) -> None:
        """Revoke WorkOS session by extracting session ID from JWT and calling WorkOS API."""
        try:
            # Decode JWT without verification to extract session ID
            decoded = pyjwt.decode(jwt_token, options={"verify_signature": False})
            session_id = decoded.get("sid")

            if not session_id:
                print("No session ID found in JWT token")
                return

            # Revoke the session using WorkOS SDK
            # Note: This is a synchronous call, WorkOS SDK doesn't provide async methods
            self.workos.user_management.revoke_session(session_id=session_id)
        except Exception as e:
            # Don't fail logout if revocation fails
            print(f"Failed to revoke WorkOS session: {e}")
