import structlog
from fastapi import APIRouter

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep
from app.utils.query_helpers import UserAccessibleQuery

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/namespaces", tags=["Namespaces"])


@router.get("")
async def list_namespaces(current_user: CurrentUser, session: SessionDep):
    """List namespaces accessible to the authenticated user."""
    # Query namespaces where user has access
    query = UserAccessibleQuery(current_user.id).namespaces()
    result = await session.execute(query)
    namespaces = result.scalars().all()

    return {
        "results": [
            {
                "id": str(namespace.id),
                "name": namespace.name,
                "display_name": namespace.display_name,
                "user_owner_id": str(namespace.user_owner_id)
                if namespace.user_owner_id
                else None,
                "organization_owner_id": str(namespace.organization_owner_id)
                if namespace.organization_owner_id
                else None,
            }
            for namespace in namespaces
        ],
        "total": len(namespaces),
    }
