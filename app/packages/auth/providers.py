import urllib.parse
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

import httpx
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


class TokenUser(BaseModel):
    """User information extracted from a validated JWT token.

    Follows OpenID Connect (OIDC) standard claims specification.
    All fields except 'sub' are optional to support different auth providers.
    """

    id: str
    email: str | None
    username: str | None
    first_name: str | None
    last_name: str | None
    email_verified: bool
    picture: str | None = None


class TokenExchangeResponse(BaseModel):
    access_token: str
    refresh_token: str
    id_token: str
    user: TokenUser


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
    async def validate_token(self, token: str) -> TokenUser: ...

    @abstractmethod
    async def get_authorization_url(
        self, redirect_uri: str, state: str, prompt: str | None = None
    ) -> str: ...

    @abstractmethod
    async def exchange_code_for_token(
        self, code: str, redirect_uri: str
    ) -> TokenExchangeResponse: ...

    @abstractmethod
    async def revoke_session(self, jwt_token: str) -> None: ...


class OIDCProvider(AuthProvider):
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        issuer_url: str,
        jwt_algorithm: str,
        device_auth_audience: str | None = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.issuer_url = issuer_url
        self.jwt_algorithm = jwt_algorithm
        self.allowed_audiences = [client_id]
        if device_auth_audience:
            self.allowed_audiences.append(device_auth_audience)

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
            audience=self.client_id,
        )

    async def validate_token(self, token: str) -> TokenUser:
        config = await self.get_config()
        jwks = await get_jwks(config.jwks_uri)
        jwks_keys = jwks.get("keys", [])
        payload = await _validate_jwt(
            token=token,
            jwks_keys=jwks_keys,
            issuer=config.issuer,
            allowed_audiences=self.allowed_audiences,
            algorithm=self.jwt_algorithm,
        )

        return TokenUser(
            id=payload["sub"],
            email=payload.get("email"),
            email_verified=payload.get("email_verified", False),
            username=payload.get("preferred_username"),
            first_name=payload.get("given_name"),
            last_name=payload.get("family_name"),
            picture=payload.get("picture"),
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

    async def exchange_code_for_token(
        self, code: str, redirect_uri: str
    ) -> TokenExchangeResponse:
        config = await self.get_config()
        token_data = await _exchange_code_for_token(
            token_endpoint=config.token_endpoint,
            client_id=self.client_id,
            client_secret=self.client_secret,
            code=code,
            redirect_uri=redirect_uri,
        )

        # Extract tokens
        access_token = token_data.get("access_token", "")
        refresh_token = token_data.get("refresh_token", "")
        id_token = token_data.get("id_token") or access_token

        # Validate token to get user info
        token_user = await self.validate_token(id_token)

        return TokenExchangeResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            id_token=id_token,
            user=token_user,
        )

    async def revoke_session(self, jwt_token: str) -> None:
        """
        Perform OIDC RP-Initiated Logout if provider supports end_session_endpoint.

        Follows the OIDC RP-Initiated Logout 1.0 specification.
        If the provider doesn't support end_session_endpoint, this is a graceful no-op.
        """
        try:
            # Get OIDC discovery to check for end_session_endpoint
            discovery = await get_oidc_discovery(self.issuer_url)
            end_session_endpoint = discovery.get("end_session_endpoint")

            if not end_session_endpoint:
                # Provider doesn't support RP-initiated logout - graceful no-op
                return

            # Build logout request with id_token_hint (recommended by OIDC spec)
            logout_params = {
                "id_token_hint": jwt_token,
                "client_id": self.client_id,
            }

            # Make GET request to end_session_endpoint
            # Note: We don't wait for response or handle redirects
            # The session is revoked on the provider side
            async with httpx.AsyncClient() as client:
                await client.get(
                    end_session_endpoint,
                    params=logout_params,
                    timeout=5.0,
                    follow_redirects=False,
                )
        except Exception as e:
            # Don't fail logout if revocation fails - follows same pattern as WorkOSProvider
            print(f"Failed to revoke OIDC session: {e}")


class WorkOSProvider(AuthProvider):
    def __init__(
        self, client_id: str, client_secret: str, issuer_url: str, jwt_algorithm: str
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.issuer_url = issuer_url
        self.jwt_algorithm = jwt_algorithm
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

    async def validate_token(self, token: str) -> TokenUser:
        config = await self.get_config()
        jwks = await get_jwks(config.jwks_uri)
        jwks_keys = jwks.get("keys", [])
        payload = await _validate_jwt(
            token=token,
            jwks_keys=jwks_keys,
            issuer=config.issuer,
            allowed_audiences=[config.audience],
            algorithm=self.jwt_algorithm,
        )

        return TokenUser(
            id=payload["sub"],
            email=payload.get("email"),
            email_verified=payload.get("email_verified", False),
            username=payload.get("preferred_username"),
            first_name=payload.get("given_name"),
            last_name=payload.get("family_name"),
            picture=payload.get("picture"),
        )

    async def get_authorization_url(
        self, redirect_uri: str, state: str, prompt: str | None = None
    ) -> str:
        # Use WorkOS SDK to generate authorization URL
        authorization_url = self.workos.user_management.get_authorization_url(
            provider="authkit",
            redirect_uri=redirect_uri,
            state=state,
            provider_scopes=["openid", "email", "profile"],
        )

        # Manually append OAuth2 prompt parameter if provided
        # WorkOS AuthKit supports standard OAuth2 parameters
        if prompt:
            separator = "&" if "?" in authorization_url else "?"
            authorization_url = (
                f"{authorization_url}{separator}prompt={urllib.parse.quote(prompt)}"
            )

        return authorization_url

    async def exchange_code_for_token(
        self, code: str, redirect_uri: str
    ) -> TokenExchangeResponse:
        result = self.workos.user_management.authenticate_with_code(
            code=code,
        )

        # Extract tokens
        access_token = result.access_token
        refresh_token = result.refresh_token
        id_token = result.access_token  # WorkOS uses access_token as id_token

        return TokenExchangeResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            id_token=id_token,
            user=TokenUser(
                id=result.user.id,
                email=result.user.email,
                username=result.user.first_name,
                first_name=result.user.first_name,
                last_name=result.user.last_name,
                email_verified=result.user.email_verified,
                picture=result.user.profile_picture_url,
            ),
        )

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


class PasswordAuthProvider(AuthProvider):
    """
    Password-based authentication provider.

    Uses JWT tokens signed with HS256 algorithm for session management.
    Does not support OAuth flows (authorization URL, code exchange).
    """

    def __init__(self, jwt_secret: str, api_base_url: str):
        self.api_base_url = api_base_url
        self.jwt_secret = jwt_secret
        self.jwt_algorithm = "HS256"
        self.jwt_expiration = timedelta(days=30)  # Match session cookie expiration

    async def get_config(self) -> AuthConfig:
        """
        Password auth doesn't use OAuth config, but we need to implement this
        for interface compatibility. Returns minimal config.

        Note: device_authorization_endpoint returns a relative path for self-hosted setup.
        Clients should construct the full URL using their API base URL.
        """
        return AuthConfig(
            client_id="password-auth",
            client_secret=self.jwt_secret,
            device_authorization_endpoint=f"{self.api_base_url}/auth/device/authorize",
            token_endpoint=f"{self.api_base_url}/auth/device/token",
            authorization_endpoint="",
            issuer="floww-password-auth",
            jwks_uri="",
            audience=None,
        )

    async def validate_token(self, token: str) -> TokenUser:
        """
        Validate JWT token signed with HS256.

        Args:
            token: The JWT token to validate

        Returns:
            TokenUser with only the user ID populated (other fields are None)

        Raises:
            jwt.InvalidTokenError: If token is invalid or expired
        """
        try:
            decoded = pyjwt.decode(
                token,
                self.jwt_secret,
                algorithms=[self.jwt_algorithm],
                options={"verify_signature": True, "verify_exp": True},
            )
            # Password auth tokens don't contain user profile information
            # Return TokenUser with only sub populated
            return TokenUser(
                id=decoded["sub"],
                email=None,
                username=None,
                first_name=None,
                last_name=None,
                email_verified=False,
            )
        except pyjwt.InvalidTokenError as e:
            raise e

    def create_token(self, user_id: str) -> str:
        """
        Create a JWT token for an authenticated user.

        Args:
            user_id: The user's UUID as a string

        Returns:
            Encoded JWT token
        """
        now = datetime.now(timezone.utc)
        payload = {
            "sub": user_id,  # Subject (user ID)
            "iat": now,  # Issued at
            "exp": now + self.jwt_expiration,  # Expiration
            "iss": "floww-password-auth",  # Issuer
        }
        return pyjwt.encode(payload, self.jwt_secret, algorithm=self.jwt_algorithm)

    async def get_authorization_url(
        self, redirect_uri: str, state: str, prompt: str | None = None
    ) -> str:
        """
        Password auth doesn't use OAuth authorization flow.
        This method should not be called for password auth.
        """
        raise NotImplementedError(
            "Password authentication does not support OAuth authorization flow"
        )

    async def exchange_code_for_token(
        self, code: str, redirect_uri: str
    ) -> TokenExchangeResponse:
        """
        Password auth doesn't use OAuth code exchange.
        This method should not be called for password auth.
        """
        raise NotImplementedError(
            "Password authentication does not support OAuth code exchange"
        )

    async def revoke_session(self, jwt_token: str) -> None:
        """
        Password auth uses stateless JWT tokens, so there's no server-side session to revoke.
        The client should simply delete the session cookie.
        """
        # No-op for stateless JWT auth
        pass
