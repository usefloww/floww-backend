"""
Tool for checking provider availability and configuration.
"""

from typing import TYPE_CHECKING

from app.packages.ai_generator.platform_validation import validate_platforms
from app.packages.ai_generator.tools.base import (
    TerminalReason,
    ToolResult,
    register_tool,
)

if TYPE_CHECKING:
    from app.packages.ai_generator.agentic_workflow_builder import AgentContext

CHECK_PROVIDERS_TOOL = {
    "type": "function",
    "function": {
        "name": "check_providers",
        "description": (
            "Check which providers are available and configured for the user's namespace. "
            "Call this before generating code to ensure required integrations are set up. "
            "This will tell you which providers exist, which are configured, and which need setup."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "providers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of provider names to check (e.g., ['slack', 'github', 'jira'])",
                },
            },
            "required": ["providers"],
        },
    },
}


@register_tool("check_providers", CHECK_PROVIDERS_TOOL)
async def check_providers(args: dict, ctx: "AgentContext") -> ToolResult:
    """Check provider availability and configuration."""
    requested = args["providers"]

    matched, missing = validate_platforms(requested)

    configured = [
        p
        for p in matched
        if p.lower() in [c.lower() for c in ctx.configured_providers]
    ]
    unconfigured = [p for p in matched if p not in configured]

    parts = []

    if unconfigured:
        for provider_type in unconfigured:
            parts.append(
                {
                    "type": "data-provider-setup",
                    "data": {
                        "message": f"Configure {provider_type} to continue",
                        "provider_type": provider_type,
                    },
                }
            )

    result_data = {
        "matched": matched,
        "missing": missing,
        "configured": configured,
        "unconfigured": unconfigured,
    }

    if unconfigured:
        return ToolResult(
            data=result_data,
            parts=parts,
            is_terminal=True,
            terminal_reason=TerminalReason.USER_RESPONSE,
        )

    if missing:
        parts.append(
            {
                "type": "text",
                "text": (
                    f"Note: {', '.join(missing)} "
                    f"{'is' if len(missing) == 1 else 'are'} not built-in "
                    f"provider{'s' if len(missing) > 1 else ''}, but I can integrate with "
                    f"{'it' if len(missing) == 1 else 'them'} using the Secret class for credentials "
                    "and standard HTTP requests."
                ),
            }
        )

    return ToolResult(
        data=result_data,
        parts=parts,
        is_terminal=False,
    )
