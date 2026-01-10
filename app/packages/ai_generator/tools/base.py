"""
Base tool infrastructure for the agentic workflow builder.

Provides the ToolResult dataclass, TerminalReason enum, and tool registry
for registering and executing tools.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

if TYPE_CHECKING:
    from app.packages.ai_generator.agentic_workflow_builder import AgentContext


class TerminalReason(str, Enum):
    """Reason why the agent loop terminated."""

    USER_RESPONSE = "user_response"
    CODE_GENERATED = "code_generated"
    PLAN_SUBMITTED = "plan_submitted"
    ERROR = "error"


@dataclass
class ToolResult:
    """Result from executing a tool."""

    data: dict
    """Data to feed back to the LLM as tool result."""

    parts: list[dict] = field(default_factory=list)
    """MessageParts to show the user."""

    is_terminal: bool = False
    """Should the agent loop stop after this tool?"""

    terminal_reason: Optional[TerminalReason] = None
    """Why the agent loop is terminating (if is_terminal)."""

    code: Optional[str] = None
    """Generated/updated code (if any)."""

    plan: Optional[dict] = None
    """Submitted plan (if any)."""


ToolExecutor = Callable[..., Awaitable[ToolResult]]

TOOL_REGISTRY: dict[str, ToolExecutor] = {}
TOOL_DEFINITIONS: list[dict[str, Any]] = []


def register_tool(name: str, definition: dict[str, Any]):
    """
    Decorator to register a tool executor with its JSON Schema definition.

    Usage:
        @register_tool("my_tool", MY_TOOL_DEFINITION)
        async def my_tool(args: dict, ctx: AgentContext) -> ToolResult:
            ...
    """

    def decorator(func: ToolExecutor) -> ToolExecutor:
        TOOL_REGISTRY[name] = func
        TOOL_DEFINITIONS.append(definition)
        return func

    return decorator


async def execute_tool(name: str, args: dict, ctx: "AgentContext") -> ToolResult:
    """Execute a tool by name with the given arguments and context."""
    if name not in TOOL_REGISTRY:
        return ToolResult(
            data={"error": f"Unknown tool: {name}"},
            parts=[{"type": "text", "text": f"Internal error: Unknown tool '{name}'"}],
            is_terminal=True,
            terminal_reason=TerminalReason.ERROR,
        )

    executor = TOOL_REGISTRY[name]
    try:
        return await executor(args, ctx)
    except Exception as e:
        return ToolResult(
            data={"error": str(e)},
            parts=[{"type": "text", "text": f"Error executing tool: {e}"}],
            is_terminal=True,
            terminal_reason=TerminalReason.ERROR,
        )
