"""
Context builder for AI workflow generation.

Builds the context needed for the AI to generate valid Floww workflows,
including SDK documentation, provider information, and available capabilities.
"""

from typing import Optional
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Provider
from app.packages.ai_generator.provider_docs import (
    get_provider_capabilities,
    load_common_types,
    load_provider_documentation_batch,
)
from app.services.providers.provider_registry import ALL_PROVIDER_TYPES

logger = structlog.stdlib.get_logger(__name__)


def build_available_provider_types() -> str:
    """Build a summary of all available provider types with their capabilities."""
    lines = ["# Available Provider Types\n"]

    for provider_type in ALL_PROVIDER_TYPES:
        name = provider_type.name
        capabilities = get_provider_capabilities(name)

        if capabilities:
            triggers = ", ".join(capabilities["triggers"][:5]) or "none"
            actions = ", ".join(capabilities["actions"][:5]) or "none"
            if len(capabilities["triggers"]) > 5:
                triggers += ", ..."
            if len(capabilities["actions"]) > 5:
                actions += ", ..."
            lines.append(f"- **{name}**: triggers=[{triggers}], actions=[{actions}]")
        else:
            lines.append(f"- **{name}**: (documentation not available)")

    return "\n".join(lines)


def build_sdk_context(provider_names: list[str]) -> str:
    """
    Build SDK documentation context for the specified providers.

    Loads the TypeScript source files for each provider plus common types and secrets.
    """
    context_parts = []

    # Load common types first
    common_types = load_common_types()
    if common_types:
        context_parts.append("# SDK Common Types (common.ts)\n")
        context_parts.append("```typescript")
        context_parts.append(common_types)
        context_parts.append("```\n")

    # Load Secret class documentation
    from app.packages.ai_generator.provider_docs import load_secret_documentation

    secret_docs = load_secret_documentation()
    if secret_docs:
        context_parts.append("# SDK Secret Class (for custom credentials)\n")
        context_parts.append("```typescript")
        context_parts.append(secret_docs)
        context_parts.append("```\n")

    # Load provider documentation
    docs = load_provider_documentation_batch(provider_names)

    for name, content in docs.items():
        context_parts.append(f"# SDK Provider: {name}\n")
        context_parts.append("```typescript")
        context_parts.append(content)
        context_parts.append("```\n")

    return "\n".join(context_parts)


async def get_namespace_providers(
    session: AsyncSession, namespace_id: UUID
) -> list[dict]:
    """Fetch configured providers for a namespace."""
    result = await session.execute(
        select(Provider).where(Provider.namespace_id == namespace_id)
    )
    providers = result.scalars().all()

    return [
        {
            "type": p.type,
            "alias": p.alias,
        }
        for p in providers
    ]


async def build_provider_context(
    session: AsyncSession, namespace_id: UUID
) -> tuple[str, list[str]]:
    """
    Build context about configured providers for a namespace.

    Returns:
        Tuple of (context_string, list_of_configured_provider_types)
    """
    providers = await get_namespace_providers(session, namespace_id)

    if not providers:
        return "# Configured Providers\nNo providers configured yet.\n", []

    lines = ["# Configured Providers\n"]
    configured_types = []

    for p in providers:
        lines.append(f"- {p['type']} (alias: {p['alias']})")
        if p["type"] not in configured_types:
            configured_types.append(p["type"])

    return "\n".join(lines), configured_types


def build_system_prompt(
    available_types: str,
    sdk_context: str,
    provider_context: str,
    current_code: Optional[str] = None,
) -> str:
    """Construct the full system prompt with all context."""
    parts = [
        """You are an expert Floww workflow builder assistant. You help users create
automation workflows using the Floww SDK.

CRITICAL: ASK CLARIFYING QUESTIONS
Before generating code, you MUST ask for specific details that are required by the SDK.
Do NOT use placeholder values. Ask the user for:

- **Slack**: Which channel? (e.g., "#general", "#alerts")
- **GitHub/GitLab**: Which repository? (owner/repo format) Which branch?
- **Discord**: Which channel ID or server?
- **Jira**: Which project key? Which issue types?
- **Schedules**: How often? What time? What timezone?
- **Webhooks**: What data format do you expect?

Examples of good clarifying questions:
- "Which Slack channel should I send messages to?"
- "What's the GitHub repository (e.g., 'myorg/myrepo')? Any specific branch?"
- "How often should this run? (e.g., every hour, daily at 9am)"

Only generate code once you have the specific values needed. Never use "myorg/myrepo"
or "#channel" as placeholders - always ask first!

IMPORTANT RULES:
1. Generate TypeScript code that uses the floww package
2. Use the exact API from the SDK documentation provided
3. Providers must be instantiated with their class name (e.g., `new Slack()`)
4. Trigger handlers receive (ctx, event) parameters
5. Always use proper error handling
6. Keep code concise and focused on the user's requirements

WORKFLOW STRUCTURE:
- Import providers from floww
- Instantiate providers you need
- Set up triggers using provider.triggers.onXxx()
- Use provider.actions.xxx() for actions within handlers

CUSTOM SECRETS:
- Use the Secret class to store custom credentials (API keys, database passwords, etc.)
- Secrets are defined with Zod schemas and stored securely
- Access secret values with .value() method
- Example:
```typescript
import { Secret } from "floww";
import { z } from "zod";

const apiKey = new Secret("my-api", z.object({
  key: z.string(),
  endpoint: z.string(),
}));

// Use in your workflow
const config = apiKey.value();
fetch(config.endpoint, { headers: { "X-API-Key": config.key } });
```

EXAMPLE WORKFLOW:
```typescript
import { Slack, GitHub } from "floww";

const slack = new Slack();
const github = new GitHub();

github.triggers.onPush({
  owner: "myorg",
  repository: "myrepo",
  handler: async (ctx, event) => {
    await slack.actions.sendMessage({
      channel: "#notifications",
      text: `New push to ${event.body.ref}`,
    });
  },
});
```
""",
        available_types,
        provider_context,
        sdk_context,
    ]

    if current_code:
        parts.append(f"# Current Code\n```typescript\n{current_code}\n```\n")

    return "\n\n".join(parts)
