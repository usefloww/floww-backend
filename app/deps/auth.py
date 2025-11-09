from typing import Annotated, Optional

import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.deps.db import SessionDep
from app.models import User
from app.settings import settings
from app.utils.auth import get_user_from_api_key, get_user_from_token
from app.utils.session import get_jwt_from_session_cookie

security = HTTPBearer(auto_error=False)


def _credentials_exception(message: str):
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=message,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    request: Request,
    session: SessionDep,
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials], Depends(security)
    ] = None,
) -> User:
    jwt_token = None

    if credentials and credentials.credentials:
        jwt_token = credentials.credentials
    else:
        session_cookie = request.cookies.get("session")
        if session_cookie:
            jwt_token = get_jwt_from_session_cookie(session_cookie)

    if not jwt_token:
        raise _credentials_exception("No credentials provided")

    # Check if this is a service account API key
    if jwt_token.startswith("floww_sa_"):
        try:
            user = await get_user_from_api_key(session, jwt_token)
            structlog.contextvars.bind_contextvars(user_id=user.id)
            return user
        except HTTPException:
            raise _credentials_exception("Invalid API key")

    # Otherwise, try JWT authentication
    try:
        user = await get_user_from_token(session, jwt_token)
        structlog.contextvars.bind_contextvars(user_id=user.id)
        return user
    except HTTPException:
        raise _credentials_exception("Invalid JWT token")
    except Exception as e:
        print(f"Auth error: {e}")
        raise _credentials_exception("Authentication error")


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_user_optional(
    request: Request,
    session: SessionDep,
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials], Depends(security)
    ] = None,
) -> User:
    """
    Get current user with support for anonymous authentication.

    If AUTH_TYPE='none', returns the anonymous user without requiring a token.
    Otherwise, follows the standard authentication flow.
    """
    # If AUTH_TYPE is 'none', return the anonymous user
    if settings.AUTH_TYPE == "none":
        # Retrieve anonymous user from database
        anonymous_user_id = request.app.state.anonymous_user_id
        result = await session.execute(select(User).where(User.id == anonymous_user_id))
        user = result.scalar_one_or_none()

        if user is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Anonymous user not properly initialized",
            )

        structlog.contextvars.bind_contextvars(user_id=user.id)
        return user

    # Otherwise, use the standard authentication flow
    return await get_current_user(request, session, credentials)


CurrentUserOptional = Annotated[User, Depends(get_current_user_optional)]
