"""
Tool for generating workflow TypeScript code.
"""

import re
from typing import TYPE_CHECKING

from app.packages.ai_generator.tools.base import (
    TerminalReason,
    ToolResult,
    register_tool,
)

if TYPE_CHECKING:
    from app.packages.ai_generator.agentic_workflow_builder import AgentContext

GENERATE_CODE_TOOL = {
    "type": "function",
    "function": {
        "name": "generate_workflow_code",
        "description": (
            "Generate complete TypeScript workflow code based on the approved plan. "
            "Call this IMMEDIATELY when the user confirms/approves the plan with phrases like: "
            "'yes', 'Yes, generate this workflow', 'approve', 'looks good', 'proceed', 'generate the code'. "
            "Do NOT submit another plan after approval - generate the code instead. "
            "The code must be valid TypeScript using the Floww SDK."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The complete TypeScript code for the workflow",
                },
                "explanation": {
                    "type": "string",
                    "description": "Brief explanation of what the code does",
                },
            },
            "required": ["code", "explanation"],
        },
    },
}


def clean_code(code: str) -> str:
    """Remove markdown code fences if present."""
    if code.startswith("```"):
        lines = code.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return code.strip()


def extract_secrets_from_code(code: str) -> list[dict]:
    """Extract Secret definitions from generated TypeScript code."""
    secrets = []
    pattern = r'new\s+Secret\s*\(\s*["\']([^"\']+)["\']'
    matches = re.finditer(pattern, code)

    for match in matches:
        secret_name = match.group(1)
        secrets.append({"name": secret_name})

    return secrets


@register_tool("generate_workflow_code", GENERATE_CODE_TOOL)
async def generate_workflow_code(args: dict, ctx: "AgentContext") -> ToolResult:
    """Generate workflow code."""
    from app.packages.ai_generator.services.code_validator import (
        format_errors_for_llm,
        validate_typescript,
    )

    code = clean_code(args["code"])
    explanation = args["explanation"]

    # Validate TypeScript code
    validation_result = await validate_typescript(
        ctx.session, ctx.namespace_id, code
    )

    if not validation_result.get("success", True):
        error_message = format_errors_for_llm(validation_result.get("errors", []))
        return ToolResult(
            data={"status": "validation_failed", "errors": error_message},
            parts=[
                {
                    "type": "text",
                    "text": f"The generated code has TypeScript errors:\n\n{error_message}",
                }
            ],
            is_terminal=False,  # Let agent retry
        )

    secrets = extract_secrets_from_code(code)

    parts = [{"type": "text", "text": explanation}]

    if secrets:
        parts.append(
            {
                "type": "text",
                "text": "\n\nThis workflow requires the following secrets to be configured:",
            }
        )
        for secret in secrets:
            parts.append(
                {
                    "type": "data-secret-setup",
                    "data": {
                        "message": f"Configure secret '{secret['name']}'",
                        "secret_name": secret["name"],
                    },
                }
            )

    parts.append(
        {
            "type": "text",
            "text": "\n\nThe code is shown in the editor. You can ask me to modify it or deploy when ready.",
        }
    )

    return ToolResult(
        data={"status": "code_generated"},
        parts=parts,
        code=code,
        is_terminal=True,
        terminal_reason=TerminalReason.CODE_GENERATED,
    )
