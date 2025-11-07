from typing import Any, Dict, List, Optional

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

from app.deps.auth import CurrentUser, CurrentUserOptional
from app.deps.db import SessionDep
from app.models import Workflow
from app.utils.query_helpers import UserAccessibleQuery

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
    request: ConnectRequest,
    user: CurrentUser,
) -> ConnectResponse:
    """
    Centrifugo connect proxy endpoint.
    Validates user authentication and allows/denies connection.
    """

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


@router.post("/subscribe")
async def subscribe_proxy(
    request: SubscribeRequest,
    session: SessionDep,
    user: CurrentUser,
) -> SubscribeResponse:
    """
    Centrifugo subscribe proxy endpoint.
    Validates user permissions for channel subscription.
    """

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
    workflow_id = request.channel.replace("workflow:", "")

    workflows_query = (
        UserAccessibleQuery(user.id).workflows().where(Workflow.id == workflow_id)
    )
    workflows_result = await session.execute(workflows_query)
    workflow = workflows_result.scalar_one_or_none()

    if not workflow:
        logger.warning(
            "Subscription to workflow denied",
            extra={
                "user_id": request.user,
                "workflow_id": workflow_id,
                "channel": request.channel,
            },
        )
        return SubscribeResponse(
            error={"code": 403, "message": "Access denied to this channel"}
        )

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
