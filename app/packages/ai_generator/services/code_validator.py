"""
TypeScript code validation service.

Calls the runtime to validate TypeScript code and returns formatted errors.
"""

from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.factories import runtime_factory
from app.models import Runtime
from app.packages.runtimes.runtime_types import RuntimeConfig
from app.services.default_runtime import get_default_runtime_id

logger = structlog.stdlib.get_logger(__name__)


async def validate_typescript(
    session: AsyncSession,
    namespace_id: UUID,
    code: str,
) -> dict:
    """
    Validate TypeScript code by calling the runtime.

    Args:
        session: Database session
        namespace_id: The namespace ID (for future per-namespace runtimes)
        code: The TypeScript code to validate

    Returns:
        {
            "success": bool,
            "errors": [
                {
                    "file": "...",
                    "line": int,
                    "column": int,
                    "message": "...",
                    "code": "TS..."
                },
                ...
            ]
        }
    """
    runtime_id = await get_default_runtime_id()
    if not runtime_id:
        logger.warning("no_default_runtime_for_validation")
        return {"success": True, "errors": []}

    # Get the runtime record to get the image_digest
    result = await session.execute(select(Runtime).where(Runtime.id == runtime_id))
    runtime = result.scalar_one_or_none()

    if not runtime:
        logger.warning("runtime_not_found_for_validation", runtime_id=str(runtime_id))
        return {"success": True, "errors": []}

    # Get image_digest from runtime config
    image_digest = runtime.config.get("image_uri", "")
    if not image_digest:
        logger.warning(
            "runtime_missing_image_uri", runtime_id=str(runtime_id), config=runtime.config
        )
        return {"success": True, "errors": []}

    runtime_impl = runtime_factory()
    runtime_config = RuntimeConfig(
        runtime_id=str(runtime_id),
        image_digest=image_digest,
    )

    user_code = {
        "files": {"main.ts": code},
        "entrypoint": "main.ts",
    }

    try:
        result = await runtime_impl.validate_code(runtime_config, user_code)
        logger.info(
            "typescript_validation_complete",
            success=result.get("success", True),
            error_count=len(result.get("errors", [])),
        )
        return result
    except Exception as e:
        logger.error("typescript_validation_failed", error=str(e))
        # On error, allow the code through (fail open)
        return {"success": True, "errors": []}


def format_errors_for_llm(errors: list[dict]) -> str:
    """
    Format validation errors as a string for the LLM to understand and fix.

    Args:
        errors: List of error dicts from validation

    Returns:
        Formatted string describing the errors
    """
    if not errors:
        return ""

    lines = ["TypeScript compilation errors found:"]
    for error in errors:
        file = error.get("file", "unknown")
        line = error.get("line", 0)
        column = error.get("column", 0)
        message = error.get("message", "Unknown error")
        code = error.get("code", "")

        lines.append(f"  - {file}:{line}:{column}: {message} ({code})")

    lines.append("\nPlease fix these errors and try again.")
    return "\n".join(lines)
