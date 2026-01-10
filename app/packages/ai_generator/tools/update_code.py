"""
Tool for updating existing workflow code.
"""

from typing import TYPE_CHECKING

from app.packages.ai_generator.tools.base import (
    TerminalReason,
    ToolResult,
    register_tool,
)
from app.packages.ai_generator.tools.generate_code import clean_code, extract_secrets_from_code

if TYPE_CHECKING:
    from app.packages.ai_generator.agentic_workflow_builder import AgentContext

UPDATE_CODE_TOOL = {
    "type": "function",
    "function": {
        "name": "update_workflow_code",
        "description": (
            "Update existing workflow code based on user feedback. "
            "Use this when the user wants to modify an already generated workflow. "
            "Always provide the complete updated code, not just the changes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The complete updated TypeScript code",
                },
                "changes_made": {
                    "type": "string",
                    "description": "Summary of what was changed",
                },
            },
            "required": ["code", "changes_made"],
        },
    },
}


@register_tool("update_workflow_code", UPDATE_CODE_TOOL)
async def update_workflow_code(args: dict, ctx: "AgentContext") -> ToolResult:
    """Update existing workflow code."""
    code = clean_code(args["code"])
    changes_made = args["changes_made"]

    secrets = extract_secrets_from_code(code)
    old_secrets = extract_secrets_from_code(ctx.current_code or "")

    old_secret_names = {s["name"] for s in old_secrets}
    new_secrets = [s for s in secrets if s["name"] not in old_secret_names]

    parts = [{"type": "text", "text": f"I've updated the code. {changes_made}"}]

    if new_secrets:
        parts.append(
            {
                "type": "text",
                "text": "\n\nThis update requires new secrets to be configured:",
            }
        )
        for secret in new_secrets:
            parts.append(
                {
                    "type": "data-secret-setup",
                    "data": {
                        "message": f"Configure secret '{secret['name']}'",
                        "secret_name": secret["name"],
                    },
                }
            )

    return ToolResult(
        data={"status": "code_updated", "changes": changes_made},
        parts=parts,
        code=code,
        is_terminal=True,
        terminal_reason=TerminalReason.CODE_GENERATED,
    )
