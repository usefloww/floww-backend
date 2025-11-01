"""Docker Registry authentication dependency.

Docker CLI uses Basic Auth for registry operations. This module extracts
the WorkOS token from the Basic Auth password field and validates it.
"""

import base64
from typing import Annotated, Optional

import jwt
import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.deps.db import SessionDep
from app.models import User
from app.utils.auth import get_user_from_token

logger = structlog.stdlib.get_logger(__name__)


def docker_error_response(
    status_code: int,
    error_code: str,
    message: str,
    detail: Optional[str] = None,
) -> JSONResponse:
    """Create a Docker Registry v2 API compliant error response.

    See: https://docs.docker.com/registry/spec/api/#errors
    """
    error_obj = {
        "code": error_code,
        "message": message,
    }
    if detail:
        error_obj["detail"] = detail

    return JSONResponse(
        status_code=status_code,
        content={"errors": [error_obj]},
        headers={"Docker-Distribution-API-Version": "registry/2.0"},
    )


async def get_docker_user(
    request: Request,
    session: SessionDep,
) -> User:
    """Extract and validate WorkOS token from Docker Basic Auth.

    Docker CLI sends credentials as:
        Authorization: Basic base64(username:password)

    We expect:
        username: any value (typically "token" or user email)
        password: WorkOS JWT token

    Returns:
        Authenticated User object

    Raises:
        HTTPException with Docker-compliant error format
    """
    auth_header = request.headers.get("Authorization")

    if not auth_header:
        logger.warning("Missing Authorization header in Docker request")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
            headers={
                "WWW-Authenticate": 'Basic realm="Docker Registry"',
                "Docker-Distribution-API-Version": "registry/2.0",
            },
        )

    # Parse Basic Auth header
    if not auth_header.startswith("Basic "):
        logger.warning(
            "Invalid Authorization header format", auth_header=auth_header[:20]
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid authorization header",
            headers={
                "WWW-Authenticate": 'Basic realm="Docker Registry"',
                "Docker-Distribution-API-Version": "registry/2.0",
            },
        )

    # Decode base64 credentials
    try:
        encoded_credentials = auth_header[6:]  # Remove "Basic " prefix
        decoded = base64.b64decode(encoded_credentials).decode("utf-8")

        # Split on first colon only (password might contain colons)
        if ":" not in decoded:
            raise ValueError("Invalid credentials format")

        username, workos_token = decoded.split(":", 1)

        logger.debug("Docker auth attempt", username=username)

    except Exception as e:
        logger.error("Failed to decode Basic Auth credentials", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials format",
            headers={
                "WWW-Authenticate": 'Basic realm="Docker Registry"',
                "Docker-Distribution-API-Version": "registry/2.0",
            },
        )

    # Validate WorkOS token
    try:
        user = await get_user_from_token(session, workos_token)
        logger.info(
            "Docker authentication successful",
            user_id=str(user.id),
            username=username,
        )
        structlog.contextvars.bind_contextvars(user_id=user.id)
        return user

    except jwt.PyJWTError as e:
        logger.warning("Invalid WorkOS token in Docker auth", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
            headers={
                "WWW-Authenticate": 'Basic realm="Docker Registry"',
                "Docker-Distribution-API-Version": "registry/2.0",
            },
        )
    except Exception as e:
        logger.error("Unexpected error during Docker authentication", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication failed",
            headers={
                "WWW-Authenticate": 'Basic realm="Docker Registry"',
                "Docker-Distribution-API-Version": "registry/2.0",
            },
        )


# Type alias for dependency injection
DockerUser = Annotated[User, Depends(get_docker_user)]
