import json
import os
from typing import Callable, Dict, List

from litellm import completion
from pydantic import BaseModel

from app.settings import settings

# Configure OpenRouter API key for litellm if available
if settings.OPENROUTER_API_KEY:
    os.environ["OPENROUTER_API_KEY"] = settings.OPENROUTER_API_KEY

# ---------------------------------------------------------------------------
# pydantic schemas
# ---------------------------------------------------------------------------


class RequirementItem(BaseModel):
    id: str
    description: str
    category: str


class RequirementsOutput(BaseModel):
    summary: str
    requirements: List[RequirementItem]
    platforms: List[str]
    clarifying_questions: List[str]
    needs_clarification: bool


class PlanningOutput(BaseModel):
    high_level_architecture: str
    connectors: List[Dict]
    data_flow_description: str
    transformation_notes: str
    error_handling_strategy: str


class CodegenOutput(BaseModel):
    code: str


class RequirementCheck(BaseModel):
    requirement_id: str
    status: str
    evidence: str


class VerificationOutput(BaseModel):
    checks: List[RequirementCheck]
    all_requirements_met: bool
    summary: str


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def llm_json(model: str, prompt: str, temperature=0.0):
    """model must return pure json. we attempt minimal cleanup."""
    resp = completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    raw = resp.choices[0].message["content"]
    try:
        return json.loads(raw)
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        return json.loads(raw[start:end])


def llm_text(model: str, prompt: str, temperature=0.1):
    resp = completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    return resp.choices[0].message["content"]


# ---------------------------------------------------------------------------
# step prompts
# ---------------------------------------------------------------------------


def extract_requirements(model: str, text: str) -> RequirementsOutput:
    prompt = f"""
You extract automation requirements, you are not to be picky on the exact details and if something
is clear enough you pass it on as is.
Follow this format strictly:
{{
  "summary": str,
  "requirements": [{{"id": str, "description": str, "category": str}}],
  "platforms": [str],
  "clarifying_questions": [str],
  "needs_clarification": bool
}}

User message:
{text}
"""
    data = llm_json(model, prompt)
    return RequirementsOutput(**data)


def plan_architecture(model: str, req: RequirementsOutput) -> PlanningOutput:
    prompt = f"""
Design an automation architecture based on these requirements.
Output JSON in this exact schema:
{{
  "high_level_architecture": str,
  "connectors": [{{"service": str, "purpose": str, "required_auth": str, "notes": str}}],
  "data_flow_description": str,
  "transformation_notes": str,
  "error_handling_strategy": str
}}

Requirements:
{req.model_dump_json(indent=2)}
"""
    data = llm_json(model, prompt)
    return PlanningOutput(**data)


def build_codegen_prompt(plan: PlanningOutput, docs: Dict[str, str]):
    providers = "".join(f"# provider {k}\n{v}\n" for k, v in docs.items())
    return f"""
You generate production-quality floww workflows.
Output ONLY code. No markdown fences.

# planning
{plan.model_dump_json(indent=2)}

# provider docs
{providers}

Generate the full implementation:
"""


def generate_code(
    model: str, plan: PlanningOutput, docs: Dict[str, str]
) -> CodegenOutput:
    prompt = build_codegen_prompt(plan, docs)
    code = llm_text(model, prompt)
    return CodegenOutput(code=code)


def verify_requirements(
    model: str, code: str, req: RequirementsOutput
) -> VerificationOutput:
    prompt = f"""
You verify code against requirements. Be strict.
Output strictly this JSON:
{{
  "checks": [{{"requirement_id": str, "status": str, "evidence": str}}],
  "all_requirements_met": bool,
  "summary": str
}}

Requirements:
{req.model_dump_json(indent=2)}

Code:
{code}
"""
    data = llm_json(model, prompt)
    return VerificationOutput(**data)


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------


def run_automation_flow(
    user_description: str,
    *,
    req_model: str,
    plan_model: str,
    code_model: str,
    verify_model: str,
    provider_loader: Callable[[List[str]], Dict[str, str]] = lambda x: {},
    platform_validator: Callable[[List[str]], tuple] = lambda x: (x, []),
):
    req_out = extract_requirements(req_model, user_description)
    if req_out.needs_clarification:
        return {"stage": "needs_clarification", "requirements": req_out}

    matched, missing = platform_validator(req_out.platforms)
    if missing:
        return {
            "stage": "missing_providers",
            "missing_providers": missing,
            "matched_providers": matched,
            "requirements": req_out,
        }

    docs = provider_loader(matched) if matched else {}
    plan_out = plan_architecture(plan_model, req_out)
    code_out = generate_code(code_model, plan_out, docs)
    ver_out = verify_requirements(verify_model, code_out.code, req_out)

    return {
        "requirements": req_out,
        "planning": plan_out,
        "code": code_out,
        "verification": ver_out,
    }
