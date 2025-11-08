"""
Service for generating and validating JWT tokens for workflow authentication.

These tokens are short-lived and passed to workflow invocations, allowing
workflows to authenticate back to the backend for operations like KV-store access.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.models import Workflow
from app.settings import settings


class WorkflowAuthService:
    """Service for managing workflow authentication tokens."""

    @staticmethod
    def generate_invocation_token(
        workflow: Workflow,
        invocation_id: str | None = None,
    ) -> str:
        """
        Generate a short-lived JWT token for a workflow invocation.

        Args:
            workflow: The workflow being invoked
            invocation_id: Unique identifier for this invocation (generated if not provided)

        Returns:
            Signed JWT token string

        Token claims:
            - sub: "workflow:<workflow_id>"
            - workflow_id: UUID of the workflow
            - namespace_id: UUID of the namespace
            - invocation_id: Unique identifier for this invocation
            - iat: Issued at timestamp
            - exp: Expiration timestamp
            - aud: Audience ("floww-workflow")
            - iss: Issuer ("floww-backend")
        """
        if invocation_id is None:
            invocation_id = str(uuid.uuid4())

        now = datetime.now(timezone.utc)
        expiration = now + timedelta(seconds=settings.WORKFLOW_JWT_EXPIRATION_SECONDS)

        payload = {
            "sub": f"workflow:{workflow.id}",
            "workflow_id": str(workflow.id),
            "namespace_id": str(workflow.namespace_id),
            "invocation_id": invocation_id,
            "iat": now,
            "exp": expiration,
            "aud": "floww-workflow",
            "iss": "floww-backend",
        }

        token = jwt.encode(
            payload,
            settings.WORKFLOW_JWT_SECRET,
            algorithm=settings.WORKFLOW_JWT_ALGORITHM,
        )
        print("token", token)
        return token

    @staticmethod
    def validate_token(token: str) -> dict[str, Any]:
        """
        Validate a workflow JWT token and return its claims.

        Args:
            token: JWT token string to validate

        Returns:
            Dictionary of token claims

        Raises:
            jwt.ExpiredSignatureError: Token has expired
            jwt.InvalidTokenError: Token is invalid
        """
        payload = jwt.decode(
            token,
            settings.WORKFLOW_JWT_SECRET,
            algorithms=[settings.WORKFLOW_JWT_ALGORITHM],
            audience="floww-workflow",
            issuer="floww-backend",
        )

        return payload
