"""
Tool for asking clarifying questions to the user.
"""

from typing import TYPE_CHECKING

from app.packages.ai_generator.tools.base import (
    TerminalReason,
    ToolResult,
    register_tool,
)

if TYPE_CHECKING:
    from app.packages.ai_generator.agentic_workflow_builder import AgentContext

ASK_QUESTION_TOOL = {
    "type": "function",
    "function": {
        "name": "ask_clarifying_question",
        "description": (
            "Ask the user for more information needed to build their workflow. "
            "Use this when you need specific details like channel names, repository names, "
            "schedule frequency, API endpoints, etc. "
            "IMPORTANT: Always provide 'options' with 2-4 common choices when possible "
            "(e.g., for weather APIs: OpenWeatherMap, WeatherAPI, AccuWeather, Other; "
            "for yes/no questions: Yes/I have one, No/I need to set one up). "
            "Only use plain text questions when structured options truly don't make sense. "
            "Never use placeholders like 'myorg/myrepo' or '#channel'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user",
                },
                "options": {
                    "type": "array",
                    "description": "Optional: Pre-defined options for the user to choose from",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "label": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["id", "label"],
                    },
                },
                "allow_multiple": {
                    "type": "boolean",
                    "description": "Whether the user can select multiple options",
                    "default": False,
                },
            },
            "required": ["question"],
        },
    },
}


@register_tool("ask_clarifying_question", ASK_QUESTION_TOOL)
async def ask_clarifying_question(args: dict, ctx: "AgentContext") -> ToolResult:
    """Ask the user for clarification."""
    question = args["question"]
    options = args.get("options", [])
    allow_multiple = args.get("allow_multiple", False)

    parts = []

    if options:
        parts.append(
            {
                "type": "data-question",
                "data": {
                    "question": question,
                    "options": options,
                    "allow_multiple": allow_multiple,
                },
            }
        )
    else:
        parts.append({"type": "text", "text": question})

    return ToolResult(
        data={"status": "question_sent", "question": question},
        parts=parts,
        is_terminal=True,
        terminal_reason=TerminalReason.USER_RESPONSE,
    )
