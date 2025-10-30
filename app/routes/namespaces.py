import structlog
from fastapi import APIRouter
from sqlalchemy.orm import selectinload

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep
from app.utils.query_helpers import UserAccessibleQuery

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/namespaces", tags=["Namespaces"])


@router.get("")
async def list_namespaces(current_user: CurrentUser, session: SessionDep):
    """List namespaces accessible to the authenticated user."""
    # Import the Namespace model to use it properly
    from app.models import Namespace

    # Query namespaces where user has access with organization details
    query = (
        UserAccessibleQuery(current_user.id)
        .namespaces()
        .options(selectinload(Namespace.organization_owner))
    )
    result = await session.execute(query)
    namespaces = result.scalars().all()

    results = []
    for namespace in namespaces:
        namespace_data = {"id": str(namespace.id)}

        if namespace.user_owner_id:
            # Personal namespace
            namespace_data["user"] = {"id": str(namespace.user_owner_id)}
        elif namespace.organization_owner_id and namespace.organization_owner:
            # Organization namespace
            namespace_data["organization"] = {
                "id": str(namespace.organization_owner.id),
                "name": namespace.organization_owner.name,
                "display_name": namespace.organization_owner.display_name,
            }

        results.append(namespace_data)

    return {
        "results": results,
        "total": len(results),
    }
