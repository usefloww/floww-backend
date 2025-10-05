from typing import Optional

import httpx
import jwt
from sqlalchemy.orm import Session

from app.models import User
from app.services.user_service import get_or_create_user
from app.settings import settings

# Cache for WorkOS public keys
_workos_public_keys: Optional[dict] = None

issuer_url = f"{settings.WORKOS_API_URL}/user_management/{settings.WORKOS_CLIENT_ID}"
jwks_url = f"{settings.WORKOS_API_URL}/sso/jwks/{settings.WORKOS_CLIENT_ID}"


async def get_workos_public_keys() -> dict:
    """Fetch WorkOS public keys for JWT validation."""
    global _workos_public_keys

    if _workos_public_keys is None:
        async with httpx.AsyncClient() as client:
            response = await client.get(jwks_url)
            response.raise_for_status()
            _workos_public_keys = response.json()

    return _workos_public_keys


async def validate_jwt_token(token: str) -> str:
    """Validate JWT token and return the WorkOS user ID."""
    # Get WorkOS public keys for JWT validation
    jwks = await get_workos_public_keys()

    # Decode JWT header to get key ID
    unverified_header = jwt.get_unverified_header(token)
    key_id = unverified_header.get("kid")

    if not key_id:
        raise jwt.PyJWTError("No key ID found in JWT header")

    # Find the matching public key
    public_key = None
    for key in jwks.get("keys", []):
        if key.get("kid") == key_id:
            public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key)
            break

    if not public_key:
        raise jwt.PyJWTError("No matching public key found")

    # Decode and validate JWT
    payload = jwt.decode(
        token,
        public_key,
        algorithms=[settings.JWT_ALGORITHM],
        issuer=issuer_url,
    )

    # Extract user ID from JWT
    workos_user_id: str = payload.get("sub")
    if workos_user_id is None:
        raise jwt.PyJWTError("No subject found in JWT payload")

    return workos_user_id


async def get_user_from_token(session: Session, token: str) -> User:
    """Get user from JWT token."""
    workos_user_id = await validate_jwt_token(token)
    return await get_or_create_user(session, workos_user_id)


def get_user_from_cookie(token: str) -> User:
    """Synchronous wrapper for getting user from cookie token."""
    import asyncio

    from app.deps.db import get_session

    session = next(get_session())
    try:
        return asyncio.run(get_user_from_token(session, token))
    finally:
        session.close()
