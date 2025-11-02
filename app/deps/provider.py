from functools import lru_cache

from app.auth.provider_base import AuthProvider
from app.auth.providers.oidc_provider import OIDCProvider
from app.auth.providers.workos_provider import WorkOSProvider
from app.settings import settings

PROVIDER_REGISTRY: dict[str, type[AuthProvider]] = {
    "workos": WorkOSProvider,
    "oidc": OIDCProvider,
    "auth0": OIDCProvider,
}


@lru_cache
def get_auth_provider() -> AuthProvider:
    provider_name = settings.AUTH_PROVIDER

    if provider_name not in PROVIDER_REGISTRY:
        raise ValueError(
            f"Unsupported AUTH_PROVIDER: {provider_name}. "
            f"Supported providers: {', '.join(PROVIDER_REGISTRY.keys())}"
        )

    provider_class = PROVIDER_REGISTRY[provider_name]
    provider = provider_class()
    provider.validate_provider_config()

    return provider
