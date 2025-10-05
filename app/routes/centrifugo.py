from typing import Any, Dict, List, Optional

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.deps.db import SessionDep
from app.utils.auth import get_user_from_token

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/centrifugo", tags=["Centrifugo"])


class ConnectRequest(BaseModel):
    client: str
    transport: str
    protocol: str
    encoding: str
    name: Optional[str] = None
    version: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


class ConnectResult(BaseModel):
    user: str
    expire_at: Optional[int] = None
    info: Optional[Dict[str, Any]] = None
    data: Optional[Dict[str, Any]] = None
    channels: Optional[List[str]] = None
    meta: Optional[Dict[str, Any]] = None


class ConnectResponse(BaseModel):
    result: Optional[ConnectResult] = None


class SubscribeRequest(BaseModel):
    client: str
    transport: str
    protocol: str
    encoding: str
    user: str
    channel: str
    meta: Optional[Dict[str, Any]] = None
    data: Optional[Dict[str, Any]] = None


class SubscribeResult(BaseModel):
    info: Optional[Dict[str, Any]] = None
    data: Optional[Dict[str, Any]] = None
    expire_at: Optional[int] = None


class SubscribeResponse(BaseModel):
    result: Optional[SubscribeResult] = None
    error: Optional[Dict[str, Any]] = None


@router.post("/connect")
async def connect_proxy(
    request: ConnectRequest, raw_request: Request, session: SessionDep
) -> ConnectResponse:
    """
    Centrifugo connect proxy endpoint.
    Validates user authentication and allows/denies connection.
    """
    try:
        # Try to get authorization header
        auth_header = raw_request.headers.get("authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            logger.warning("No valid authorization header found in connect proxy")
            # Return empty response to deny connection
            return ConnectResponse()

        # Extract token and validate user
        token = auth_header.split(" ")[1]
        user = await get_user_from_token(session, token)

        if not user:
            logger.warning("User authentication failed in connect proxy")
            return ConnectResponse()

        # User is authenticated, allow connection
        logger.info(
            "User authenticated successfully",
            extra={
                "user_id": str(user.id),
                "client_id": request.client,
                "transport": request.transport,
            },
        )

        connect_result = ConnectResult(
            user=str(user.id),
            info={"user_id": str(user.id)},
            meta={"user_id": str(user.id)},
        )

        return ConnectResponse(result=connect_result)

    except Exception as e:
        logger.error(
            "Error in connect proxy",
            extra={
                "error": str(e),
                "client_id": request.client,
            },
        )
        # Return empty response to deny connection
        return ConnectResponse()


@router.post("/subscribe")
async def subscribe_proxy(
    request: SubscribeRequest, session: SessionDep
) -> SubscribeResponse:
    """
    Centrifugo subscribe proxy endpoint.
    Validates user permissions for channel subscription.
    """
    try:
        # Check if it's a workflow channel
        if not request.channel.startswith("workflow:"):
            logger.warning(
                "Subscription to non-workflow channel denied",
                extra={
                    "user_id": request.user,
                    "channel": request.channel,
                    "client_id": request.client,
                },
            )
            return SubscribeResponse(
                error={"code": 403, "message": "Access denied to this channel"}
            )

        # Extract workflow_id from channel
        print(request.channel)
        workflow_id = request.channel.replace("workflow:", "")

        # For now, allow all workflow subscriptions as requested
        # TODO: Later implement proper access check using user_has_workflow_access

        logger.info(
            "Workflow subscription allowed",
            extra={
                "user_id": request.user,
                "workflow_id": workflow_id,
                "channel": request.channel,
                "client_id": request.client,
            },
        )

        subscribe_result = SubscribeResult(
            info={"workflow_id": workflow_id, "user_id": request.user}
        )

        return SubscribeResponse(result=subscribe_result)

    except Exception as e:
        logger.error(
            "Error in subscribe proxy",
            extra={
                "error": str(e),
                "user_id": request.user,
                "channel": request.channel,
                "client_id": request.client,
            },
        )

        return SubscribeResponse(
            error={"code": 500, "message": "Internal server error"}
        )
