import os
from typing import Dict, List, Optional

import boto3
import instructor
from atomic_agents import AgentConfig, AtomicAgent
from atomic_agents.base.base_io_schema import BaseIOSchema
from atomic_agents.context import ChatHistory, SystemPromptGenerator
from pydantic import Field

from app.packages.ai_generator.platform_validation import validate_platforms
from app.packages.ai_generator.provider_docs import load_provider_documentation_batch

# --------------------------
# models
# --------------------------


class RequirementsInput(BaseIOSchema):
    """Input schema for requirements extraction from user messages."""

    user_message: str = Field(...)


class RequirementItem(BaseIOSchema):
    """A single requirement item with ID, description, and category."""

    id: str
    description: str
    category: str


class RequirementsOutput(BaseIOSchema):
    """Output schema containing extracted requirements and clarification needs."""

    summary: str
    requirements: List[RequirementItem]
    platforms: List[str] = Field(default_factory=list)
    clarifying_questions: List[str]
    needs_clarification: bool


class PlanningInput(BaseIOSchema):
    """Input schema for planning automation architecture."""

    requirements: List[RequirementItem]
    stack: str = "floww"


class ConnectorPlan(BaseIOSchema):
    """Plan for a single connector/service integration."""

    service: str
    purpose: str
    required_auth: str
    notes: Optional[str] = None


class PlanningOutput(BaseIOSchema):
    """Output schema containing the automation architecture plan."""

    high_level_architecture: str
    connectors: List[ConnectorPlan]
    data_flow_description: str
    transformation_notes: str
    error_handling_strategy: str


class CodegenInput(BaseIOSchema):
    """Input schema for code generation."""

    planning: PlanningOutput
    language: str = "typescript"
    style_notes: Optional[str] = None
    provider_documentation: Optional[Dict[str, str]] = None


class CodegenOutput(BaseIOSchema):
    """Output schema containing generated code and metadata."""

    code: str
    comments: str
    known_gaps: List[str]


class VerificationInput(BaseIOSchema):
    """Input schema for requirement verification."""

    requirements: List[RequirementItem]
    code: str


class RequirementCheck(BaseIOSchema):
    """Result of checking a single requirement against the code."""

    requirement_id: str
    status: str
    evidence: str


class VerificationOutput(BaseIOSchema):
    """Output schema containing verification results for all requirements."""

    checks: List[RequirementCheck]
    all_requirements_met: bool
    summary: str


# --------------------------
# setup
# --------------------------

# AWS Bedrock configuration
aws_region = os.getenv("AWS_REGION", "us-east-1")
bedrock_model = os.getenv("BEDROCK_MODEL", "qwen.qwen3-32b-v1:0")

# Create Bedrock runtime client
bedrock_runtime = boto3.client(
    service_name="bedrock-runtime",
    region_name=aws_region,
)

# Create instructor client from Bedrock
client = instructor.from_bedrock(
    bedrock_runtime,
    mode=instructor.Mode.BEDROCK_JSON,
)
history = ChatHistory()

# --------------------------
# system prompts
# --------------------------

requirements_system = SystemPromptGenerator(
    background=[
        "you extract automation requirements from the user message.",
    ],
    steps=[
        "1. summarize the request.",
        "2. extract structured requirements.",
        "3. identify all platforms/services mentioned (e.g., salesforce, slack, postgres, jira, gitlab).",
        "4. list identified platforms in the platforms field.",
        "5. find ambiguities.",
        "6. ask clarifying questions.",
        "7. set needs_clarification accordingly.",
    ],
    output_instructions=[
        "respond using the RequirementsOutput schema.",
        "include all platform/service names mentioned in the user message in the platforms field.",
    ],
)

planning_system = SystemPromptGenerator(
    background=[
        "you design integration and automation plans.",
    ],
    steps=[
        "1. find needed connectors.",
        "2. define triggers.",
        "3. plan data flow and transformations.",
        "4. define error handling.",
        "5. include notes on volume and rate limits.",
    ],
    output_instructions=[
        "respond using the PlanningOutput schema.",
    ],
)

codegen_system = SystemPromptGenerator(
    background=[
        "you generate production quality workflow code.",
        "IMPORTANT: The code field must contain actual \\n newline characters in the JSON string.",
        "DO NOT output code as a single line - use \\n to separate lines.",
        "",
        "Example 1 - Cron trigger:",
        'import { Builtin } from "floww";',
        "",
        "const builtin = new Builtin();",
        "",
        "builtin.triggers.onCron({",
        '  expression: "*/1 * * * * *",',
        "  handler: (ctx, event) => {",
        '    console.log("Do this every second", event.scheduledTime);',
        "  },",
        "});",
        "",
        "Example 2 - Webhook trigger:",
        'import { Builtin } from "floww";',
        "",
        "export const builtin = new Builtin();",
        "",
        "type CustomBody = {",
        "  message: string;",
        "};",
        "",
        "builtin.triggers.onWebhook<CustomBody>({",
        "  handler: (ctx, event) => {",
        '    console.log("Webhook received:", event.body.message);',
        '    console.log("Headers:", event.headers);',
        "  },",
        '  path: "/custom",',
        "});",
        "",
        "Example 3 - Provider trigger (Gitlab):",
        'import { Gitlab } from "floww";',
        "",
        'const gitlab = new Gitlab("asdfasdf");',
        "",
        "gitlab.triggers.onMergeRequest({",
        '  projectId: "19677180",',
        "  handler: async (ctx, event) => {",
        "    console.log(event.body.reviewers);",
        "  },",
        "});",
    ],
    steps=[
        "1. rest internal plan.",
        "2. review provider documentation if provided to understand available APIs, triggers, and actions.",
        "3. design top level trigger using correct provider APIs from documentation.",
        "4. add utils.",
        "5. handle logging and errors.",
        "6. list any assumptions.",
        "7. CRITICAL: format code with \\n between every line - imports, declarations, function calls, everything.",
    ],
    output_instructions=[
        "respond using the CodegenOutput schema.",
        "use the provider documentation to ensure correct API usage, method names, and parameter structures.",
        "",
        "CRITICAL FORMATTING RULES FOR THE CODE FIELD:",
        "- The code field MUST be a multi-line string with \\n characters separating each line.",
        "- Each import statement must be on its own line followed by \\n",
        "- Each variable declaration must be on its own line followed by \\n",
        "- Each opening brace { must be followed by \\n",
        "- Each closing brace } must be on its own line with \\n before and after",
        "- Use proper indentation with spaces (2 or 4 spaces per level)",
        "",
        "Example JSON output format:",
        "{",
        '  "code": "import { Builtin } from \\"floww\\";\\n\\nconst builtin = new Builtin();\\n\\nbuiltin.triggers.onCron({\\n  expression: \\"*/5 * * * *\\",\\n  handler: (ctx, event) => {\\n    console.log(\\"Running\\");\\n  }\\n});",',
        '  "comments": "This code runs every 5 minutes",',
        '  "known_gaps": []',
        "}",
        "",
        "DO NOT output code as a single line without \\n characters.",
        "Each line of code must be separated by \\n in the JSON string.",
    ],
)

verification_system = SystemPromptGenerator(
    background=[
        "you verify whether code meets the requirements. You are very nit picky and will not accept any code that does not meet the requirements exactly.",
        "If additional behaviour is added, which is not required by the requirements, you will mark it as not met.",
    ],
    steps=[
        "1. compare each requirement to the code.",
        "2. mark met / not met / partial.",
        "3. give evidence.",
        "4. set all_requirements_met accordingly.",
    ],
    output_instructions=[
        "respond using the VerificationOutput schema.",
    ],
)

# --------------------------
# agents
# --------------------------

requirements_agent = AtomicAgent[RequirementsInput, RequirementsOutput](
    config=AgentConfig(
        client=client,
        model=bedrock_model,
        system_prompt_generator=requirements_system,
        history=history,
        model_api_parameters={},
    )
)

planning_agent = AtomicAgent[PlanningInput, PlanningOutput](
    config=AgentConfig(
        client=client,
        model=bedrock_model,
        system_prompt_generator=planning_system,
        history=history,
        model_api_parameters={},
    )
)

codegen_agent = AtomicAgent[CodegenInput, CodegenOutput](
    config=AgentConfig(
        client=client,
        model=bedrock_model,
        system_prompt_generator=codegen_system,
        history=history,
        model_api_parameters={},
    )
)

verification_agent = AtomicAgent[VerificationInput, VerificationOutput](
    config=AgentConfig(
        client=client,
        model=bedrock_model,
        system_prompt_generator=verification_system,
        history=history,
        model_api_parameters={},
    )
)

# --------------------------
# orchestrator
# --------------------------


def run_automation_flow(user_description: str):
    req_input = RequirementsInput(user_message=user_description)
    req_out = requirements_agent.run(req_input)

    if req_out.needs_clarification:
        print("needs clarification:")
        for q in req_out.clarifying_questions:
            print("-", q)
        return {"stage": "needs_clarification", "requirements": req_out}

    # Validate platforms
    if req_out.platforms:
        matched_providers, missing_providers = validate_platforms(req_out.platforms)

        if missing_providers:
            error_message = (
                f"Missing providers: {', '.join(missing_providers)}. "
                f"Available providers: {', '.join(matched_providers) if matched_providers else 'none'}."
            )
            print(f"Error: {error_message}")
            return {
                "stage": "missing_providers",
                "error": error_message,
                "missing_providers": missing_providers,
                "matched_providers": matched_providers,
                "requirements": req_out,
            }

        # Load provider documentation for matched providers
        provider_docs = load_provider_documentation_batch(matched_providers)
    else:
        matched_providers = []
        provider_docs = {}

    plan_in = PlanningInput(requirements=req_out.requirements)
    plan_out = planning_agent.run(plan_in)

    code_in = CodegenInput(
        planning=plan_out,
        provider_documentation=provider_docs if provider_docs else None,
    )
    code_out = codegen_agent.run(code_in)

    ver_in = VerificationInput(
        requirements=req_out.requirements,
        code=code_out.code,
    )
    ver_out = verification_agent.run(ver_in)

    return {
        "requirements": req_out,
        "planning": plan_out,
        "code": code_out,
        "verification": ver_out,
    }


def nice_output_print(result: Dict):
    """Print the automation flow results in a human-readable format."""
    separator = "=" * 80

    # Handle early returns (needs_clarification, missing_providers)
    if "stage" in result:
        if result["stage"] == "needs_clarification":
            print("\n" + separator)
            print("⚠️  CLARIFICATION NEEDED")
            print(separator)
            print("\nClarifying Questions:")
            for q in result["requirements"].clarifying_questions:
                print(f"  • {q}")
            print()
            return

        if result["stage"] == "missing_providers":
            print("\n" + separator)
            print("❌ MISSING PROVIDERS")
            print(separator)
            print(f"\nError: {result.get('error', 'Unknown error')}")
            print()
            return

    # Requirements Section
    if "requirements" in result:
        req = result["requirements"]
        print("\n" + separator)
        print("📋 REQUIREMENTS")
        print(separator)
        print(f"\nSummary: {req.summary}\n")

        if req.platforms:
            print(f"Platforms: {', '.join(req.platforms)}\n")

        if req.requirements:
            print("Requirements:")
            for req_item in req.requirements:
                print(f"  [{req_item.id}] ({req_item.category})")
                print(f"    {req_item.description}")
            print()

    # Planning Section
    if "planning" in result:
        plan = result["planning"]
        print(separator)
        print("🏗️  PLANNING & ARCHITECTURE")
        print(separator)
        print(f"\n{plan.high_level_architecture}\n")

        if plan.connectors:
            print("Connectors:")
            for conn in plan.connectors:
                print(f"  • {conn.service}")
                print(f"    Purpose: {conn.purpose}")
                print(f"    Auth: {conn.required_auth}")
                if conn.notes:
                    print(f"    Notes: {conn.notes}")
            print()

        print("Data Flow:")
        print(f"  {plan.data_flow_description}\n")

        print("Transformation Notes:")
        print(f"  {plan.transformation_notes}\n")

        print("Error Handling:")
        print(f"  {plan.error_handling_strategy}\n")

    # Code Section
    if "code" in result:
        code = result["code"]
        print(separator)
        print("💻 GENERATED CODE")
        print(separator)

        if code.comments:
            print("\nComments:")
            print("-" * 80)
            print(code.comments)
            print("-" * 80)

        print("\nCode:")
        print("-" * 80)
        # Print code directly - Python's print() will handle newlines correctly
        # If code appears on one line, the model output doesn't contain newlines
        print(code.code, end="")
        if not code.code.endswith("\n"):
            print()  # Add newline before separator if code doesn't end with one
        print("-" * 80)

        if code.known_gaps:
            print("\n⚠️  Known Gaps:")
            for gap in code.known_gaps:
                print(f"  • {gap}")
        print()

    # Verification Section
    if "verification" in result:
        ver = result["verification"]
        print(separator)
        print("✅ VERIFICATION")
        print(separator)

        if ver.checks:
            print("\nRequirement Checks:")
            for check in ver.checks:
                status_icon = "✓" if check.status == "met" else "✗"
                print(
                    f"  {status_icon} [{check.requirement_id}] {check.status.upper()}"
                )
                print(f"    Evidence: {check.evidence}")
            print()

        print(
            f"All Requirements Met: {'✓ YES' if ver.all_requirements_met else '✗ NO'}"
        )
        print(f"\nSummary: {ver.summary}\n")

    print(separator)
    print()


# --------------------------
# entrypoint
# --------------------------

if __name__ == "__main__":
    user_text = "when a gitlab merge request is created, send a slack message with the title to the channel #code-reviews. In the thread of the slack message you create you should add a comment with the description of the merge request."

    result = run_automation_flow(user_text)
    nice_output_print(result)
