"""
Conversational AI for workflow building.

Implements a multi-step generation flow:
1. Extract Intent - Understand what the user wants
2. Validate Providers - Check required providers are available
3. Plan Architecture - Design the workflow structure
4. Generate Code - Produce TypeScript code
5. Verify & Iterate - Validate and refine based on feedback
"""

import json
from enum import Enum
from typing import Optional
from uuid import UUID

import structlog
from litellm import completion
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.packages.ai_generator.context_builder import (
    build_available_provider_types,
    build_provider_context,
    build_sdk_context,
    build_system_prompt,
)
from app.packages.ai_generator.platform_validation import validate_platforms
from app.settings import settings

logger = structlog.stdlib.get_logger(__name__)


class ConversationStage(str, Enum):
    GATHERING_REQUIREMENTS = "gathering_requirements"
    MISSING_PROVIDERS = "missing_providers"
    GENERATING = "generating"
    ITERATING = "iterating"


class MessagePart(BaseModel):
    type: str
    text: Optional[str] = None
    data: Optional[dict] = None


class AIResponse(BaseModel):
    parts: list[MessagePart]
    code: Optional[str] = None
    stage: ConversationStage = ConversationStage.GATHERING_REQUIREMENTS


class QuestionOption(BaseModel):
    id: str
    label: str
    description: Optional[str] = None


class StructuredQuestion(BaseModel):
    question: str
    options: list[QuestionOption]
    allow_multiple: bool = False


class ExtractedIntent(BaseModel):
    summary: str
    platforms: list[str]
    trigger_type: Optional[str] = None
    actions: list[str] = []
    needs_clarification: bool = False
    clarifying_questions: list[
        str
    ] = []  # Legacy format, kept for backward compatibility
    structured_questions: list[StructuredQuestion] = []  # New structured format


def llm_json(model: str, messages: list[dict], temperature: float = 0.0) -> dict:
    """Call LLM and parse JSON response."""
    resp = completion(model=model, messages=messages, temperature=temperature)
    raw = resp.choices[0].message["content"]

    # Handle <think> tags from some models
    if "<think>" in raw:
        think_end = raw.find("</think>")
        if think_end != -1:
            raw = raw[think_end + 8 :].strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON from response
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
        raise


def llm_text(model: str, messages: list[dict], temperature: float = 0.1) -> str:
    """Call LLM and return text response."""
    resp = completion(model=model, messages=messages, temperature=temperature)
    raw = resp.choices[0].message["content"]

    # Handle <think> tags from some models
    if "<think>" in raw:
        think_end = raw.find("</think>")
        if think_end != -1:
            raw = raw[think_end + 8 :].strip()

    return raw


def extract_intent(
    user_message: str, conversation_history: list[dict]
) -> ExtractedIntent:
    """Extract user intent from the conversation."""
    prompt = f"""Analyze the user's request for a workflow automation.
Extract the following information as JSON:
{{
  "summary": "Brief description of what the user wants",
  "platforms": ["list", "of", "platforms", "mentioned"],
  "trigger_type": "webhook|schedule|event|null",
  "actions": ["list", "of", "actions", "to", "perform"],
  "needs_clarification": true/false,
  "clarifying_questions": ["specific questions to ask - legacy format, prefer structured_questions"],
  "structured_questions": [
    {{
      "question": "The question text",
      "options": [
        {{"id": "option1", "label": "Option 1", "description": "Optional description"}},
        {{"id": "option2", "label": "Option 2"}}
      ],
      "allow_multiple": false
    }}
  ]
}}

IMPORTANT: You MUST ask for specific configuration details before generating code.
Set needs_clarification to TRUE and ask questions if ANY of these are missing:

- **Slack**: Which channel to send to? (e.g., "#general", "#alerts", "#dev-notifications")
- **GitHub**: Which repository? (format: owner/repo) Which branch? What events?
- **GitLab**: Which project ID or path? Which branch? What events?
- **Discord**: Which channel ID or server?
- **Jira**: Which project key? What issue types?
- **Schedule/Cron**: How often? What time? (be specific: "every hour", "daily at 9am UTC")
- **Webhooks**: What kind of data will be received?

Look at the conversation history - if the user already provided these details in previous 
messages, don't ask again. But if specific values like channel names, repository names, 
or schedule frequencies are missing, you MUST ask.

PREFER structured_questions over clarifying_questions. For structured_questions:
- Provide common options when possible (e.g., for ticket systems: Jira, Linear, GitHub Issues, Asana)
- For timezone questions, provide common timezones (UTC, America/New_York, Europe/London, etc.)
- For schedule frequency, provide common options (hourly, daily, weekly, etc.)
- Set allow_multiple to true only if the user can select multiple options
- If options don't make sense, provide at least 2-3 common examples

Examples of good structured questions:
1. Ticket system question:
   {{
     "question": "Which ticket system are you using?",
     "options": [
       {{"id": "jira", "label": "Jira"}},
       {{"id": "linear", "label": "Linear"}},
       {{"id": "github", "label": "GitHub Issues"}},
       {{"id": "asana", "label": "Asana"}},
       {{"id": "other", "label": "Other"}}
     ],
     "allow_multiple": false
   }}

2. Timezone question:
   {{
     "question": "What timezone should I use for the schedule?",
     "options": [
       {{"id": "utc", "label": "UTC"}},
       {{"id": "ny", "label": "America/New_York"}},
       {{"id": "london", "label": "Europe/London"}},
       {{"id": "tokyo", "label": "Asia/Tokyo"}},
       {{"id": "other", "label": "Other (specify)"}}
     ],
     "allow_multiple": false
   }}

3. Destination question:
   {{
     "question": "Where should I send the message?",
     "options": [
       {{"id": "slack", "label": "Slack channel (e.g., #standup)"}},
       {{"id": "discord", "label": "Discord"}},
       {{"id": "email", "label": "Email"}},
       {{"id": "other", "label": "Other"}}
     ],
     "allow_multiple": false
   }}

Previous conversation:
{json.dumps(conversation_history[-6:], indent=2) if conversation_history else "None"}

Current user message:
{user_message}
"""

    messages = [{"role": "user", "content": prompt}]
    data = llm_json(settings.AI_MODEL_REQUIREMENTS, messages)
    return ExtractedIntent(**data)


def generate_workflow_code(
    intent: ExtractedIntent,
    sdk_context: str,
    provider_context: str,
    current_code: Optional[str],
    conversation_history: list[dict],
) -> str:
    """Generate TypeScript workflow code based on intent and context."""
    system_prompt = build_system_prompt(
        available_types=build_available_provider_types(),
        sdk_context=sdk_context,
        provider_context=provider_context,
        current_code=current_code,
    )

    user_prompt = f"""Generate a complete Floww workflow based on this requirement:

Summary: {intent.summary}
Platforms: {", ".join(intent.platforms)}
Trigger type: {intent.trigger_type or "to be determined from context"}
Actions: {", ".join(intent.actions) if intent.actions else "to be determined"}

IMPORTANT:
- Output ONLY the TypeScript code, no markdown fences, no explanations
- Use the exact APIs from the SDK documentation provided
- Include all necessary imports
- Instantiate providers correctly with new ProviderName()
- Set up proper trigger handlers

Previous conversation for context:
{json.dumps(conversation_history[-4:], indent=2) if conversation_history else "None"}
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    code = llm_text(settings.AI_MODEL_CODEGEN, messages)

    # Clean up code if it has markdown fences
    if code.startswith("```"):
        lines = code.split("\n")
        # Remove first line (```typescript or ```)
        lines = lines[1:]
        # Remove last line if it's just ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code = "\n".join(lines)

    return code.strip()


def generate_iteration_response(
    user_message: str,
    current_code: str,
    sdk_context: str,
    provider_context: str,
    conversation_history: list[dict],
) -> tuple[str, str]:
    """Generate updated code and explanation based on user feedback."""
    system_prompt = build_system_prompt(
        available_types=build_available_provider_types(),
        sdk_context=sdk_context,
        provider_context=provider_context,
        current_code=current_code,
    )

    user_prompt = f"""The user wants to modify the current workflow code.

User request: {user_message}

Current code is in the context above.

Respond with a JSON object:
{{
  "explanation": "Brief explanation of what you changed",
  "code": "The complete updated TypeScript code"
}}

IMPORTANT:
- Include the COMPLETE updated code, not just the changes
- Preserve existing functionality unless explicitly asked to change it
- Use exact APIs from SDK documentation
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    data = llm_json(settings.AI_MODEL_CODEGEN, messages, temperature=0.1)
    return data.get("explanation", "I've updated the code."), data.get(
        "code", current_code
    )


async def process_message(
    session: AsyncSession,
    namespace_id: UUID,
    user_message: str,
    conversation_history: list[dict],
    current_code: Optional[str] = None,
) -> AIResponse:
    """
    Process a user message and return an AI response.

    This is the main entry point for the workflow builder AI.
    """
    parts: list[MessagePart] = []

    # Get provider context for this namespace
    provider_context, configured_providers = await build_provider_context(
        session, namespace_id
    )

    # Determine if we're in iteration mode (code already exists and is substantial)
    is_iterating = (
        current_code and len(current_code.strip()) > 100 and "import" in current_code
    )

    if is_iterating and current_code:
        # User is iterating on existing code
        sdk_context = build_sdk_context(configured_providers)

        explanation, new_code = generate_iteration_response(
            user_message=user_message,
            current_code=current_code,
            sdk_context=sdk_context,
            provider_context=provider_context,
            conversation_history=conversation_history,
        )

        parts.append(MessagePart(type="text", text=explanation))

        return AIResponse(
            parts=parts,
            code=new_code,
            stage=ConversationStage.ITERATING,
        )

    # Extract intent from the message
    intent = extract_intent(user_message, conversation_history)

    # Check if clarification is needed
    if intent.needs_clarification:
        # Prefer structured questions if available
        if intent.structured_questions:
            parts.append(
                MessagePart(
                    type="text",
                    text="I need a bit more information to create your workflow:",
                )
            )
            # Add each structured question as a data-question part
            for question in intent.structured_questions:
                options_data = [
                    {
                        "id": opt.id,
                        "label": opt.label,
                        "description": opt.description,
                    }
                    for opt in question.options
                ]
                parts.append(
                    MessagePart(
                        type="data-question",
                        data={
                            "question": question.question,
                            "options": options_data,
                            "allow_multiple": question.allow_multiple,
                        },
                    )
                )
            parts.append(
                MessagePart(
                    type="text",
                    text="Please select your preferences above and I'll generate the code for you!",
                )
            )
        elif intent.clarifying_questions:
            # Fallback to legacy text questions if structured questions aren't available
            questions_text = "\n".join(
                f"- {q}" for q in intent.clarifying_questions[:4]
            )
            parts.append(
                MessagePart(
                    type="text",
                    text=f"I need a bit more information to create your workflow:\n\n{questions_text}\n\nPlease provide these details and I'll generate the code for you!",
                )
            )

        return AIResponse(
            parts=parts,
            code=None,
            stage=ConversationStage.GATHERING_REQUIREMENTS,
        )

    # Validate required platforms
    if intent.platforms:
        matched, missing = validate_platforms(intent.platforms)

        # Check which matched providers are not configured
        unconfigured = [
            p
            for p in matched
            if p.lower() not in [c.lower() for c in configured_providers]
        ]

        if unconfigured:
            # Prompt user to configure providers
            parts.append(
                MessagePart(
                    type="text",
                    text="Great! To build this workflow, you'll need to set up the following provider(s):",
                )
            )

            for provider_type in unconfigured:
                parts.append(
                    MessagePart(
                        type="data-provider-setup",
                        data={
                            "message": f"Configure {provider_type} to continue",
                            "provider_type": provider_type,
                        },
                    )
                )

            parts.append(
                MessagePart(
                    type="text",
                    text="Once configured, I'll generate your workflow code!",
                )
            )

            return AIResponse(
                parts=parts,
                code=None,
                stage=ConversationStage.MISSING_PROVIDERS,
            )

        if missing:
            # Some platforms are not supported
            parts.append(
                MessagePart(
                    type="data-not-supported",
                    data={
                        "message": f"The following platforms are not currently supported: {', '.join(missing)}. "
                        f"Supported platforms include: Slack, GitHub, GitLab, Discord, Jira, Todoist, and more.",
                    },
                )
            )

            if matched:
                parts.append(
                    MessagePart(
                        type="text",
                        text=f"I can still help you with: {', '.join(matched)}. Should I proceed?",
                    )
                )
            else:
                return AIResponse(
                    parts=parts,
                    code=None,
                    stage=ConversationStage.GATHERING_REQUIREMENTS,
                )

        # Build SDK context for the matched providers
        sdk_context = build_sdk_context(matched)
    else:
        # No specific platforms mentioned, use generic context
        sdk_context = build_sdk_context(
            configured_providers[:3] if configured_providers else []
        )

    # Generate the workflow code
    code = generate_workflow_code(
        intent=intent,
        sdk_context=sdk_context,
        provider_context=provider_context,
        current_code=current_code,
        conversation_history=conversation_history,
    )

    # Build response
    parts.append(
        MessagePart(
            type="text",
            text=f"I've created a workflow based on your requirements: **{intent.summary}**\n\n"
            "The code is shown in the editor. You can:\n"
            "- Ask me to modify it\n"
            "- Add more functionality\n"
            "- Deploy it when you're ready",
        )
    )

    return AIResponse(
        parts=parts,
        code=code,
        stage=ConversationStage.GENERATING,
    )
