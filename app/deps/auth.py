from typing import Annotated

import jwt
import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.deps.db import SessionDep
from app.models import User
from app.utils.auth import get_user_from_token

security = HTTPBearer()


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
        user = await get_user_from_token(session, credentials.credentials)
        structlog.contextvars.bind_contextvars(user_id=user.id)
        return user
    except jwt.PyJWTError as e:
        print(f"JWT error: {e}")
        raise credentials_exception


CurrentUser = Annotated[User, Depends(get_current_user)]
