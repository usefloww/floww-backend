import structlog
from fastapi import APIRouter
from sqlalchemy import or_, select

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep
from app.models import Namespace, NamespaceMember, OrganizationMember

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/namespaces", tags=["Namespaces"])


@router.get("")
async def list_namespaces(current_user: CurrentUser, session: SessionDep):
    """List namespaces accessible to the authenticated user."""
    # Query namespaces where user has access
    result = await session.execute(
        select(Namespace).where(
            or_(
                # User owns the namespace directly
                Namespace.user_owner_id == current_user.id,
                # User is a member of the namespace
                Namespace.id.in_(
                    select(NamespaceMember.namespace_id).where(
                        NamespaceMember.user_id == current_user.id
                    )
                ),
                # User is a member of organization that owns the namespace
                Namespace.organization_owner_id.in_(
                    select(OrganizationMember.organization_id).where(
                        OrganizationMember.user_id == current_user.id
                    )
                ),
            )
        )
    )
    namespaces = result.scalars().all()

    return {
        "namespaces": [
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
        "user_id": str(current_user.id),
    }
