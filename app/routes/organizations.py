from datetime import datetime
from typing import Optional
from uuid import UUID

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep, TransactionSessionDep
from app.models import Organization, OrganizationMember, OrganizationRole
from app.services.crud_helpers import CrudHelper
from app.utils.query_helpers import UserAccessibleQuery

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/organizations", tags=["Organizations"])


class OrganizationRead(BaseModel):
    id: UUID
    name: str
    display_name: str
    created_at: datetime
    updated_at: datetime


class OrganizationCreate(BaseModel):
    name: str
    display_name: str


class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None


def helper_factory(user: CurrentUser, session: SessionDep):
    return CrudHelper(
        session=session,
        resource_name="organization",
        database_model=Organization,
        read_model=OrganizationRead,
        create_model=OrganizationCreate,
        update_model=OrganizationUpdate,
        query_builder=lambda: UserAccessibleQuery(user.id).organizations(),
    )


@router.get("")
async def list_organizations(current_user: CurrentUser, session: SessionDep):
    """List organizations accessible to the authenticated user."""
    helper = helper_factory(current_user, session)
    result = await helper.list_response()
    return result


@router.post("")
async def create_organization(
    data: OrganizationCreate,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Create a new organization."""
    helper = helper_factory(current_user, session)
    response = await helper.create_response(data)

    # Make the creator the owner
    membership = OrganizationMember(
        organization_id=response.id,
        user_id=current_user.id,
        role=OrganizationRole.OWNER,
    )
    session.add(membership)
    await session.flush()

    return response


@router.get("/{organization_id}")
async def get_organization(
    organization_id: UUID, current_user: CurrentUser, session: SessionDep
):
    """Get a specific organization."""
    helper = helper_factory(current_user, session)
    result = await helper.get_response(organization_id)
    return result


@router.patch("/{organization_id}")
async def update_organization(
    organization_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
    data: OrganizationUpdate,
):
    """Update a specific organization."""
    helper = helper_factory(current_user, session)
    result = await helper.update_response(organization_id, data)
    await session.commit()
    return result


@router.delete("/{organization_id}")
async def delete_organization(
    organization_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Delete an organization."""
    helper = helper_factory(current_user, session)
    response = await helper.delete_response(organization_id)
    return response
