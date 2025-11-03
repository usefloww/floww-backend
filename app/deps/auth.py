from typing import Annotated, Optional

import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.deps.db import SessionDep
from app.models import User
from app.utils.auth import get_user_from_token
from app.utils.session import get_jwt_from_session_cookie

security = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    session: SessionDep,
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials], Depends(security)
    ] = None,
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    jwt_token = None

    if credentials and credentials.credentials:
        jwt_token = credentials.credentials
    else:
        session_cookie = request.cookies.get("session")
        if session_cookie:
            jwt_token = get_jwt_from_session_cookie(session_cookie)

    if not jwt_token:
        raise credentials_exception

    try:
        user = await get_user_from_token(session, jwt_token)
        structlog.contextvars.bind_contextvars(user_id=user.id)
        return user
    except HTTPException:
        raise credentials_exception
    except Exception as e:
        print(f"Auth error: {e}")
        raise credentials_exception


CurrentUser = Annotated[User, Depends(get_current_user)]
