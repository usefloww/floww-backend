from typing import Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep
from app.models import Organization, OrganizationMember, OrganizationRole
from app.utils.query_helpers import UserAccessibleQuery, get_organization_or_404
from app.utils.response_helpers import (
    create_creation_response,
    create_organizations_response,
)

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/organizations", tags=["Organizations"])


@router.get("")
async def list_organizations(current_user: CurrentUser, session: SessionDep):
    """List organizations accessible to the authenticated user."""
    query = UserAccessibleQuery(current_user.id).organizations()
    result = await session.execute(query)
    organizations = result.scalars().all()

    return create_organizations_response(list(organizations), current_user)


class OrganizationCreate(BaseModel):
    name: str
    display_name: str


@router.post("")
async def create_organization(
    organization_data: OrganizationCreate,
    current_user: CurrentUser,
    session: SessionDep,
):
    """Create a new organization."""

    # Create the organization
    organization = Organization(
        name=organization_data.name,
        display_name=organization_data.display_name,
    )

    session.add(organization)
    await session.flush()  # Flush to get the ID

    # Make the creator the owner
    membership = OrganizationMember(
        organization_id=organization.id,
        user_id=current_user.id,
        role=OrganizationRole.OWNER,
    )

    session.add(membership)
    await session.commit()
    await session.refresh(organization)

    logger.info(
        "Created new organization",
        organization_id=str(organization.id),
        name=organization.name,
        owner_id=str(current_user.id),
    )

    return create_creation_response(
        organization,
        name=organization.name,
        display_name=organization.display_name,
    )


class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None


@router.put("/{organization_id}")
async def update_organization(
    organization_id: UUID,
    organization_data: OrganizationUpdate,
    current_user: CurrentUser,
    session: SessionDep,
):
    """Update an organization."""

    # Get the organization and verify access
    organization = await get_organization_or_404(session, organization_id)

    # Check if user has admin/owner access
    user_membership = None
    for member in organization.members:
        if member.user_id == current_user.id:
            user_membership = member
            break

    if not user_membership or user_membership.role not in [
        OrganizationRole.OWNER,
        OrganizationRole.ADMIN,
    ]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Update fields if provided
    if organization_data.name is not None:
        organization.name = organization_data.name
    if organization_data.display_name is not None:
        organization.display_name = organization_data.display_name

    await session.commit()
    await session.refresh(organization)

    logger.info(
        "Updated organization",
        organization_id=str(organization.id),
        updated_by=str(current_user.id),
    )

    return create_creation_response(
        organization,
        name=organization.name,
        display_name=organization.display_name,
    )


@router.get("/{organization_id}")
async def get_organization(
    organization_id: UUID, current_user: CurrentUser, session: SessionDep
):
    """Get a specific organization."""

    organization = await get_organization_or_404(session, organization_id)

    # Check if user has access
    user_has_access = any(
        member.user_id == current_user.id for member in organization.members
    )

    if not user_has_access:
        raise HTTPException(status_code=404, detail="Organization not found")

    return create_creation_response(
        organization,
        name=organization.name,
        display_name=organization.display_name,
    )


@router.delete("/{organization_id}")
async def delete_organization(
    organization_id: UUID, current_user: CurrentUser, session: SessionDep
):
    """Delete an organization."""

    organization = await get_organization_or_404(session, organization_id)

    # Check if user is owner
    user_membership = None
    for member in organization.members:
        if member.user_id == current_user.id:
            user_membership = member
            break

    if not user_membership or user_membership.role != OrganizationRole.OWNER:
        raise HTTPException(
            status_code=403, detail="Only owners can delete organizations"
        )

    await session.delete(organization)
    await session.commit()

    logger.info(
        "Deleted organization",
        organization_id=str(organization.id),
        deleted_by=str(current_user.id),
    )

    return {"message": "Organization deleted successfully"}
