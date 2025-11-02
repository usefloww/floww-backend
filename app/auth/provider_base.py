from abc import ABC, abstractmethod
from typing import Any


class AuthProvider(ABC):
    """Abstract base class for OAuth/OIDC authentication providers."""

    @abstractmethod
    def get_authorization_url(
        self, redirect_uri: str, state: str, scope: str = "openid profile email"
    ) -> str:
        pass

    @abstractmethod
    async def exchange_code_for_token(
        self, code: str, redirect_uri: str
    ) -> dict[str, Any]:
        pass

    @abstractmethod
    async def get_jwks_url(self) -> str:
        pass

    @abstractmethod
    async def get_issuer(self) -> str:
        pass

    @abstractmethod
    async def get_audience(self) -> str:
        pass

    @abstractmethod
    def validate_provider_config(self) -> None:
        pass
