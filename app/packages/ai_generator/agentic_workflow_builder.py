"""
Agentic workflow builder using native tool calling.

This module provides the main entry point for the workflow builder AI,
using an agent loop that executes tools until a terminal state is reached.
"""

import json
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

import structlog
import litellm
from litellm import completion
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.packages.ai_generator.context_builder import build_provider_context
from app.packages.ai_generator.prompts import build_system_prompt
from app.packages.ai_generator.tools import (
    TOOL_DEFINITIONS,
    TerminalReason,
    execute_tool,
)
from app.settings import settings

logger = structlog.stdlib.get_logger(__name__)

litellm.callbacks = ["langfuse_otel"]


@dataclass
class AgentContext:
    """Runtime context passed to tool executions."""

    session: AsyncSession
    namespace_id: UUID
    configured_providers: list[str] = field(default_factory=list)
    current_code: Optional[str] = None


class MessagePart(BaseModel):
    """A part of an AI response message."""

    type: str
    text: Optional[str] = None
    data: Optional[dict] = None


class AgentResponse(BaseModel):
    """Response from the agent loop."""

    parts: list[MessagePart]
    code: Optional[str] = None
    terminal_reason: TerminalReason = TerminalReason.USER_RESPONSE
    plan: Optional[dict] = None


def _convert_message_to_dict(msg) -> dict:
    """Convert a litellm message object to a dict for the messages array."""
    if hasattr(msg, "model_dump"):
        return msg.model_dump()
    if isinstance(msg, dict):
        return msg
    return {"role": str(getattr(msg, "role", "assistant")), "content": str(msg)}


async def run_agent_loop(
    session: AsyncSession,
    namespace_id: UUID,
    messages: list[dict],
    current_code: Optional[str] = None,
    max_iterations: int = 10,
) -> AgentResponse:
    """
    Main agent loop. Runs until a terminal tool is called or max iterations reached.

    Args:
        session: Database session
        namespace_id: The namespace ID for provider lookup
        messages: Conversation history as list of {"role": ..., "content": ...}
        current_code: Existing workflow code if iterating
        max_iterations: Maximum tool call iterations

    Returns:
        AgentResponse with parts, code, terminal reason, and optional plan
    """
    provider_context, configured_providers = await build_provider_context(
        session, namespace_id
    )

    ctx = AgentContext(
        session=session,
        namespace_id=namespace_id,
        configured_providers=configured_providers,
        current_code=current_code,
    )

    system_prompt = build_system_prompt(
        provider_context=provider_context,
        configured_providers=configured_providers,
        current_code=current_code,
    )

    full_messages = [{"role": "system", "content": system_prompt}] + messages

    response_parts: list[MessagePart] = []
    final_code: Optional[str] = current_code
    final_plan: Optional[dict] = None
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        logger.info(
            "agent_loop_iteration",
            iteration=iteration,
            message_count=len(full_messages),
        )

        try:
            response = completion(
                model=settings.AI_MODEL_CODEGEN,
                messages=full_messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
                temperature=0.1,
                timeout=180,
            )
        except Exception as e:
            logger.error("llm_call_failed", error=str(e))
            return AgentResponse(
                parts=[
                    MessagePart(type="text", text=f"Sorry, I encountered an error: {e}")
                ],
                code=final_code,
                terminal_reason=TerminalReason.ERROR,
            )

        assistant_message = response.choices[0].message

        if assistant_message.content:
            response_parts.append(
                MessagePart(type="text", text=assistant_message.content)
            )

        if not assistant_message.tool_calls:
            logger.info("agent_loop_complete", reason="no_tool_calls")
            return AgentResponse(
                parts=response_parts,
                code=final_code,
                terminal_reason=TerminalReason.USER_RESPONSE,
                plan=final_plan,
            )

        full_messages.append(_convert_message_to_dict(assistant_message))

        for tool_call in assistant_message.tool_calls:
            tool_name = tool_call.function.name
            try:
                tool_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                logger.error(
                    "tool_args_parse_failed",
                    tool=tool_name,
                    args=tool_call.function.arguments,
                )
                tool_args = {}

            logger.info(
                "executing_tool", tool=tool_name, args_keys=list(tool_args.keys())
            )

            result = await execute_tool(tool_name, tool_args, ctx)

            full_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result.data),
                }
            )

            for part_dict in result.parts:
                response_parts.append(
                    MessagePart(
                        type=part_dict.get("type", "text"),
                        text=part_dict.get("text"),
                        data=part_dict.get("data"),
                    )
                )

            if result.code:
                final_code = result.code
                ctx.current_code = result.code

            if result.plan:
                final_plan = result.plan

            if result.is_terminal:
                logger.info(
                    "agent_loop_complete",
                    reason="terminal_tool",
                    tool=tool_name,
                    terminal_reason=result.terminal_reason,
                )
                return AgentResponse(
                    parts=response_parts,
                    code=final_code,
                    terminal_reason=result.terminal_reason
                    or TerminalReason.USER_RESPONSE,
                    plan=final_plan,
                )

    logger.warning("agent_loop_max_iterations", max_iterations=max_iterations)
    response_parts.append(
        MessagePart(
            type="text",
            text="I'm having trouble completing this request. Could you try rephrasing?",
        )
    )
    return AgentResponse(
        parts=response_parts,
        code=final_code,
        terminal_reason=TerminalReason.ERROR,
        plan=final_plan,
    )


async def process_message(
    session: AsyncSession,
    namespace_id: UUID,
    user_message: str,
    conversation_history: list[dict],
    current_code: Optional[str] = None,
    plan: Optional[dict] = None,
) -> AgentResponse:
    """
    Process a user message and return an AI response.

    This is the main entry point for the workflow builder AI,
    providing a similar interface to the original workflow_builder_ai.py.

    Args:
        session: Database session
        namespace_id: The namespace ID for provider lookup
        user_message: The current user message
        conversation_history: Previous messages in the conversation
        current_code: Existing workflow code if iterating
        plan: The plan from the previous message if user is approving

    Returns:
        AgentResponse with parts, code, terminal reason, and optional plan
    """
    # Check if this is a plan approval
    approval_keywords = [
        "yes",
        "approve",
        "looks good",
        "proceed",
        "generate",
        "correct",
        "perfect",
    ]
    is_approval = any(keyword in user_message.lower() for keyword in approval_keywords)

    logger.info(
        "checking_plan_approval",
        user_message=user_message,
        is_approval=is_approval,
        has_plan=bool(plan),
        plan_summary=plan.get("summary") if plan else None,
    )

    if is_approval and plan:
        # User is approving a plan - directly generate code
        logger.info("plan_approval_detected", has_plan=bool(plan))

        from litellm import completion
        from app.settings import settings
        from app.packages.ai_generator.tools.generate_code import (
            generate_workflow_code,
            extract_secrets_from_code,
        )

        provider_context, configured_providers = await build_provider_context(
            session, namespace_id
        )

        ctx = AgentContext(
            session=session,
            namespace_id=namespace_id,
            configured_providers=configured_providers,
            current_code=current_code,
        )

        # Build code generation prompt
        system_prompt = build_system_prompt(
            provider_context=provider_context,
            configured_providers=configured_providers,
            current_code=None,
        )

        plan_summary = plan.get("summary", "")
        trigger = plan.get("trigger", {})
        actions = plan.get("actions", [])

        user_prompt = f"""The user has approved the workflow plan. Generate the complete TypeScript code now.

Plan Summary: {plan_summary}
Trigger: {trigger}
Actions: {actions}

CRITICAL INSTRUCTIONS:
- Generate ONLY TypeScript code using the Floww SDK
- DO NOT generate JSON
- DO NOT generate explanations
- DO NOT use markdown code fences
- The output must be valid TypeScript that imports from "floww" and implements the workflow
- Include proper imports, provider instantiation, trigger setup, and action calls

Example structure:
import {{ GitLab, Slack }} from "floww";

const gitlab = new GitLab();
const slack = new Slack();

gitlab.triggers.onMergeRequestMerged({{
  project: "usefloww/floww-sdk",
  handler: async (ctx, event) => {{
    await slack.actions.sendMessage({{
      channel: "#deployments",
      text: `Merge request merged: ${{event.title}}`
    }});
  }}
}});

Now generate the complete TypeScript code for the approved plan above."""

        # Call LLM to generate code
        response = completion(
            model=settings.AI_MODEL_CODEGEN,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            timeout=180,
        )

        code = response.choices[0].message.content.strip()

        # Clean markdown fences if present
        if code.startswith("```"):
            lines = code.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            code = "\n".join(lines).strip()

        # Detect if AI returned JSON instead of code
        if code.startswith("{") and code.endswith("}"):
            logger.warning("ai_returned_json_instead_of_code", code_preview=code[:200])
            # Try to regenerate with even more explicit instructions
            retry_prompt = """You MUST generate TypeScript code, NOT JSON.

Example of CORRECT output (TypeScript code):
import { GitLab, Slack } from "floww";
const gitlab = new GitLab();
gitlab.triggers.onMergeRequestMerged({ ... });

Example of WRONG output (JSON - DO NOT DO THIS):
{"plan_id": "...", "trigger": {...}}

Now generate TypeScript code for the workflow plan."""

            response = completion(
                model=settings.AI_MODEL_CODEGEN,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": code},
                    {"role": "user", "content": retry_prompt},
                ],
                temperature=0.1,
                timeout=180,
            )
            code = response.choices[0].message.content.strip()

            # Clean again
            if code.startswith("```"):
                lines = code.split("\n")
                lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                code = "\n".join(lines).strip()

        # Extract secrets from code
        secrets = extract_secrets_from_code(code)

        # Build response parts
        parts = [
            MessagePart(
                type="text",
                text="I've generated the workflow code based on your approved plan!",
            )
        ]

        if secrets:
            parts.append(
                MessagePart(
                    type="text",
                    text="\n\nThis workflow requires the following secrets to be configured:",
                )
            )
            for secret in secrets:
                parts.append(
                    MessagePart(
                        type="data-secret-setup",
                        data={
                            "message": f"Configure secret '{secret['name']}'",
                            "secret_name": secret["name"],
                        },
                    )
                )

        parts.append(
            MessagePart(
                type="text",
                text="\n\nThe code is shown in the editor. You can ask me to modify it or deploy when ready.",
            )
        )

        return AgentResponse(
            parts=parts,
            code=code,
            terminal_reason=TerminalReason.USER_RESPONSE,
        )

    messages = []

    for msg in conversation_history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_message})

    return await run_agent_loop(
        session=session,
        namespace_id=namespace_id,
        messages=messages,
        current_code=current_code,
    )
