"""
Workflow Builder AI Chat Endpoint.

Provides an AI-driven chat interface for building workflows interactively.
Uses a multi-step generation flow with context-aware code generation.
"""

from typing import Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep
from app.models import Workflow
from app.packages.ai_generator.agentic_workflow_builder import process_message
from app.utils.query_helpers import UserAccessibleQuery

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/workflows", tags=["Workflow Builder"])


# Request/Response models
class ChatMessage(BaseModel):
    role: str
    content: str


class BuilderChatRequest(BaseModel):
    messages: list[ChatMessage]
    user_message: str
    current_code: Optional[str] = None
    namespace_id: Optional[UUID] = None
    plan: Optional[dict] = None


class QuestionOption(BaseModel):
    id: str
    label: str
    description: Optional[str] = None


class MessagePart(BaseModel):
    type: str  # "text", "data-question", "data-not-supported", "data-provider-setup", "data-code"
    text: Optional[str] = None
    data: Optional[dict] = None


class AssistantMessage(BaseModel):
    role: str = "assistant"
    parts: list[MessagePart]


class BuilderChatResponse(BaseModel):
    message: AssistantMessage
    code: Optional[str] = None
    plan: Optional[dict] = None


@router.post("/{workflow_id}/builder/chat")
async def builder_chat(
    workflow_id: UUID,
    data: BuilderChatRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> BuilderChatResponse:
    """
    AI chat endpoint for the workflow builder.

    Accepts conversation history and the current user message,
    returns an assistant response with potentially multiple parts
    (text, questions, warnings, code updates, etc.)
    """
    # Verify user has access to the workflow
    workflow_query = (
        UserAccessibleQuery(current_user.id)
        .workflows()
        .where(Workflow.id == workflow_id)
    )
    result = await session.execute(workflow_query)
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Get namespace_id from request or from workflow
    namespace_id = data.namespace_id or workflow.namespace_id

    logger.info(
        "Builder chat message received",
        workflow_id=str(workflow_id),
        namespace_id=str(namespace_id),
        user_message=data.user_message[:100],
        history_length=len(data.messages),
        has_current_code=bool(data.current_code),
    )

    # Convert conversation history to the format expected by AI
    conversation_history = [
        {"role": msg.role, "content": msg.content} for msg in data.messages
    ]

    # Process message with AI orchestrator
    try:
        ai_response = await process_message(
            session=session,
            namespace_id=namespace_id,
            user_message=data.user_message,
            conversation_history=conversation_history,
            current_code=data.current_code,
            plan=data.plan,
        )

        # Convert AI response parts to endpoint format
        parts = [
            MessagePart(
                type=part.type,
                text=part.text,
                data=part.data,
            )
            for part in ai_response.parts
        ]

        return BuilderChatResponse(
            message=AssistantMessage(parts=parts),
            code=ai_response.code,
            plan=ai_response.plan,
        )

    except Exception as e:
        logger.exception(
            "Error processing builder chat message",
            workflow_id=str(workflow_id),
            error=str(e),
        )
        # Return a friendly error message
        return BuilderChatResponse(
            message=AssistantMessage(
                parts=[
                    MessagePart(
                        type="text",
                        text="I encountered an issue processing your request. "
                        "Please try rephrasing or simplifying your request.",
                    ),
                    MessagePart(
                        type="data-not-supported",
                        data={"message": f"Error: {str(e)[:200]}"},
                    ),
                ]
            ),
            code=data.current_code,  # Preserve current code on error
        )
