from typing import Annotated, Optional

import jwt
import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.deps.db import SessionDep
from app.models import User
from app.utils.auth import get_user_from_token

security = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    session: SessionDep,
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials], Depends(security)
    ] = None,
) -> User:
    """Validate JWT token from either Bearer header or session cookie and return the authenticated user."""
    from app.routes.admin_auth import get_jwt_from_session_cookie

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    jwt_token = None

    # Try Bearer token first
    if credentials and credentials.credentials:
        jwt_token = credentials.credentials
    else:
        # Try session cookie
        session_cookie = request.cookies.get("session")
        if session_cookie:
            jwt_token = get_jwt_from_session_cookie(session_cookie)

    if not jwt_token:
        raise credentials_exception

    try:
        user = await get_user_from_token(session, jwt_token)
        structlog.contextvars.bind_contextvars(user_id=user.id)
        return user
    except jwt.PyJWTError as e:
        print(f"JWT error: {e}")
        raise credentials_exception


CurrentUser = Annotated[User, Depends(get_current_user)]
