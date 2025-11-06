from fastapi import APIRouter

from app.deps.auth import CurrentUserOptional

router = APIRouter(tags=["Whoami"])


@router.get("/whoami")
async def get_current_user_info(current_user: CurrentUserOptional):
    """Get information about the currently authenticated user."""
    return {
        "id": str(current_user.id),
        "workos_user_id": current_user.workos_user_id,
        "email": current_user.email,
        "first_name": current_user.first_name,
        "last_name": current_user.last_name,
        "created_at": current_user.created_at.isoformat()
        if current_user.created_at
        else None,
    }
