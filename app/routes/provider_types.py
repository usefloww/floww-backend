import structlog
from fastapi import APIRouter, HTTPException

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep
from app.services.providers.provider_registry import PROVIDER_TYPES_MAP

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

    return {
        "provider_type": provider_type_str,
        "setup_steps": [step.model_dump() for step in provider_type.setup_steps],
    }
