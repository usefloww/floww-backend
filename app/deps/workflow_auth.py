"""
Dependency for authenticating workflows using JWT tokens.

Workflows receive short-lived JWT tokens with their webhook invocations,
which they can use to authenticate requests back to the backend.
"""

from typing import Annotated
from uuid import UUID

import jwt
import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.services.workflow_auth_service import WorkflowAuthService

logger = structlog.stdlib.get_logger(__name__)
security = HTTPBearer(auto_error=True)


class WorkflowContext(BaseModel):
    """Context information extracted from a workflow JWT token."""

    deployment_id: UUID
    workflow_id: UUID
    namespace_id: UUID
    invocation_id: str


async def get_workflow_context(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> WorkflowContext:
    """
    Validate workflow JWT token and extract context.

    Args:
        credentials: HTTP Bearer token from Authorization header

    Returns:
        WorkflowContext with deployment, workflow, and namespace IDs

    Raises:
        HTTPException: 401 if token is invalid or expired
    """
    token = credentials.credentials

    try:
        payload = WorkflowAuthService.validate_token(token)

        return WorkflowContext(
            deployment_id=UUID(payload["deployment_id"]),
            workflow_id=UUID(payload["workflow_id"]),
            namespace_id=UUID(payload["namespace_id"]),
            invocation_id=payload["invocation_id"],
        )
    except jwt.ExpiredSignatureError:
        logger.warning("Workflow token expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as e:
        logger.warning("Invalid workflow token", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (KeyError, ValueError) as e:
        logger.warning("Malformed workflow token claims", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims",
            headers={"WWW-Authenticate": "Bearer"},
        )


# Type annotation for use in route handlers
WorkflowContextDep = Annotated[WorkflowContext, Depends(get_workflow_context)]
