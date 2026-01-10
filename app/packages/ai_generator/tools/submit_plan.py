"""
Tool for submitting a workflow plan for user approval.
"""

from typing import TYPE_CHECKING

from app.packages.ai_generator.tools.base import (
    TerminalReason,
    ToolResult,
    register_tool,
)

if TYPE_CHECKING:
    from app.packages.ai_generator.agentic_workflow_builder import AgentContext

SUBMIT_PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_plan",
        "description": (
            "Submit a structured workflow plan for user approval before generating code. "
            "Use this ONCE after gathering all requirements to confirm understanding. "
            "The plan should describe what the workflow will do, how it will be triggered, "
            "and what actions it will perform. "
            "IMPORTANT: After submitting a plan, when user approves, call generate_workflow_code instead of submitting another plan."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "plan": {
                    "type": "object",
                    "description": (
                        "The workflow plan with this exact structure: "
                        "{ "
                        "  'summary': 'Brief description of workflow', "
                        "  'trigger': { 'type': 'schedule|webhook|event', 'source': 'provider name', 'details': 'specific details' }, "
                        "  'actions': [{ 'provider': 'Provider', 'description': 'what it does' }, ...], "
                        "  'required_providers': ['Provider1', 'Provider2'], "
                        "  'required_secrets': ['SECRET_NAME1', 'SECRET_NAME2'] "
                        "}"
                    ),
                    "properties": {
                        "summary": {"type": "string", "description": "Brief description of what the workflow does"},
                        "trigger": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "description": "Trigger type (schedule, webhook, event, etc.)"},
                                "source": {"type": "string", "description": "Where the trigger comes from"},
                                "details": {"type": "string", "description": "Specific trigger details"}
                            }
                        },
                        "actions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "provider": {"type": "string"},
                                    "description": {"type": "string"}
                                }
                            }
                        },
                        "required_providers": {"type": "array", "items": {"type": "string"}},
                        "required_secrets": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["summary", "trigger", "actions"],
                    "additionalProperties": False,
                },
            },
            "required": ["plan"],
        },
    },
}


def format_plan_for_display(plan: dict) -> str:
    """Format a plan dictionary into readable text."""
    lines = []

    summary = plan.get("summary", plan.get("description", "Workflow"))
    lines.append(f"**Workflow Plan: {summary}**\n")

    if "trigger" in plan:
        trigger = plan["trigger"]
        if isinstance(trigger, dict):
            trigger_type = trigger.get("type", "unknown")
            trigger_details = trigger.get("details", trigger.get("description", ""))
            source = trigger.get("source", trigger.get("provider", ""))
            if source:
                lines.append(f"**Trigger:** {trigger_type} from {source}")
            else:
                lines.append(f"**Trigger:** {trigger_type}")
            if trigger_details:
                lines.append(f"  - {trigger_details}")
        else:
            lines.append(f"**Trigger:** {trigger}")
        lines.append("")

    if "actions" in plan:
        lines.append("**Actions:**")
        actions = plan["actions"]
        if isinstance(actions, list):
            for i, action in enumerate(actions, 1):
                if isinstance(action, dict):
                    desc = action.get(
                        "description", action.get("action", str(action))
                    )
                    provider = action.get("provider", "")
                    prefix = f"[{provider}] " if provider else ""
                    lines.append(f"{i}. {prefix}{desc}")
                else:
                    lines.append(f"{i}. {action}")
        else:
            lines.append(f"  {actions}")
        lines.append("")

    if "required_providers" in plan:
        providers = plan["required_providers"]
        if providers:
            lines.append(f"**Required Providers:** {', '.join(providers)}")

    if "required_secrets" in plan:
        secrets = plan["required_secrets"]
        if secrets:
            lines.append(f"**Required Secrets:** {', '.join(secrets)}")

    return "\n".join(lines)


@register_tool("submit_plan", SUBMIT_PLAN_TOOL)
async def submit_plan(args: dict, ctx: "AgentContext") -> ToolResult:
    """Submit workflow plan for user approval."""
    plan = args["plan"]

    parts = [
        {
            "type": "text",
            "text": "I've prepared a workflow plan for your approval:",
        },
        {
            "type": "data-plan-confirmation",
            "data": {
                "plan": plan,
                "awaiting_approval": True,
            },
        },
    ]

    return ToolResult(
        data={"status": "plan_submitted", "plan": plan},
        parts=parts,
        is_terminal=True,
        terminal_reason=TerminalReason.PLAN_SUBMITTED,
        plan=plan,
    )
