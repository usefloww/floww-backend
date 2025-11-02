import jwt
from sqlalchemy.orm import Session

from app.auth.provider_base import AuthProvider
from app.models import User
from app.services.user_service import get_or_create_user
from app.settings import settings
from app.utils.jwt_utils import (
    decode_and_validate_jwt,
    fetch_jwks,
    find_public_key,
    get_key_id_from_token,
)


async def validate_jwt_token(token: str, provider: AuthProvider) -> str:
    jwks_url = await provider.get_jwks_url()
    jwks = await fetch_jwks(jwks_url)

    key_id = get_key_id_from_token(token)
    public_key = find_public_key(jwks, key_id)

    issuer = await provider.get_issuer()
    audience = await provider.get_audience()

    payload = decode_and_validate_jwt(
        token, public_key, issuer, audience, [settings.JWT_ALGORITHM]
    )

    external_user_id: str = payload.get("sub")
    if external_user_id is None:
        raise jwt.PyJWTError("No subject found in JWT payload")

    return external_user_id


async def get_user_from_token(
    session: Session, token: str, provider: AuthProvider
) -> User:
    external_user_id = await validate_jwt_token(token, provider)
    return await get_or_create_user(session, external_user_id)


def get_user_from_cookie(token: str) -> User:
    import asyncio

    from app.deps.db import get_session
    from app.deps.provider import get_auth_provider

    session = next(get_session())
    provider = get_auth_provider()

    try:
        return asyncio.run(get_user_from_token(session, token, provider))
    finally:
        session.close()
