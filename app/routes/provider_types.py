from uuid import uuid4

import structlog
from fastapi import APIRouter, HTTPException

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep
from app.services.providers.provider_registry import PROVIDER_TYPES_MAP
from app.settings import settings

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/provider_types", tags=["Provider Types"])


@router.get("/{provider_type_str}")
async def get_provider_type(
    current_user: CurrentUser,
    session: SessionDep,
    provider_type_str: str,
):
    provider_type = PROVIDER_TYPES_MAP.get(provider_type_str)
    if provider_type is None:
        raise HTTPException(status_code=404, detail="Provider type not found")

    # Convert setup steps to dictionaries and generate webhook URLs dynamically
    setup_steps = []
    for step in provider_type.setup_steps:
        step_dict = step.model_dump()

        # If this is a webhook step, generate a URL dynamically
        if step.type == "webhook":
            webhook_path = f"/webhook/{uuid4()}"
            generated_url = f"{settings.PUBLIC_API_URL}{webhook_path}"
            step_dict["default"] = generated_url

        setup_steps.append(step_dict)

    return {
        "provider_type": provider_type_str,
        "setup_steps": setup_steps,
    }
