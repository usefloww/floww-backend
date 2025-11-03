from functools import lru_cache

from app.packages.auth.providers import AuthProvider, WorkOSProvider
from app.settings import settings


@lru_cache
def get_auth_provider() -> AuthProvider:
    return WorkOSProvider(
        client_id=settings.AUTH_CLIENT_ID,
        client_secret=settings.AUTH_CLIENT_SECRET,
        issuer_url=settings.AUTH_ISSUER_URL,
    )
