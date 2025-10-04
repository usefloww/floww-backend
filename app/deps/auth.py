from typing import Annotated, Optional

import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.deps.db import SessionDep
from app.models import User
from app.settings import settings

security = HTTPBearer()

# Cache for WorkOS public keys
_workos_public_keys: Optional[dict] = None


async def get_workos_public_keys() -> dict:
    """Fetch WorkOS public keys for JWT validation."""
    global _workos_public_keys

    if _workos_public_keys is None:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{settings.WORKOS_API_URL}/.well-known/jwks.json"
            )
            response.raise_for_status()
            _workos_public_keys = response.json()

    return _workos_public_keys


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    session: SessionDep,
) -> User:
    """Validate JWT token and return the authenticated user."""

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        # Get WorkOS public keys for JWT validation
        jwks = await get_workos_public_keys()

        # Decode JWT header to get key ID
        unverified_header = jwt.get_unverified_header(credentials.credentials)
        key_id = unverified_header.get("kid")

        if not key_id:
            raise credentials_exception

        # Find the matching public key
        public_key = None
        for key in jwks.get("keys", []):
            if key.get("kid") == key_id:
                public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key)
                break

        if not public_key:
            raise credentials_exception

        # Decode and validate JWT
        payload = jwt.decode(
            credentials.credentials,
            public_key,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.WORKOS_CLIENT_ID,
            issuer=settings.WORKOS_API_URL,
        )

        # Extract user ID from JWT
        workos_user_id: str = payload.get("sub")
        if workos_user_id is None:
            raise credentials_exception

    except jwt.PyJWTError:
        raise credentials_exception

    # Get user from database
    result = await session.execute(
        select(User).where(User.workos_user_id == workos_user_id)
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
