from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from app.deps.auth import CurrentUserOptional
from app.models import UserType

router = APIRouter(tags=["Whoami"])


class WhoamiRead(BaseModel):
    id: UUID
    workos_user_id: Optional[str] = None
    user_type: UserType
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    created_at: Optional[datetime] = None


@router.get("/whoami")
async def get_current_user_info(current_user: CurrentUserOptional):
    """Get information about the currently authenticated user."""

    return WhoamiRead(
        id=current_user.id,
        workos_user_id=current_user.workos_user_id,
        user_type=current_user.user_type,
        email=current_user.email,
        first_name=current_user.first_name,
        last_name=current_user.last_name,
        created_at=current_user.created_at,
    )
