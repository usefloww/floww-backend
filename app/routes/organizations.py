from datetime import datetime
from typing import List, Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep, TransactionSessionDep
from app.models import Organization, OrganizationMember, OrganizationRole, User
from app.services.crud_helpers import CrudHelper
from app.services.user_service import load_users_from_workos
from app.settings import settings
from app.utils.query_helpers import UserAccessibleQuery

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/organizations", tags=["Organizations"])


def check_single_org_mode():
    """Raise 403 error if single-org mode is enabled and management is disabled."""
    if settings.SINGLE_ORG_MODE:
        raise HTTPException(
            status_code=403,
            detail="Organization management is disabled in single-organization mode",
        )


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


class UserRead(BaseModel):
    id: UUID
    workos_user_id: str
    email: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    created_at: datetime


class OrganizationMemberRead(BaseModel):
    id: UUID
    user_id: UUID
    role: OrganizationRole
    created_at: datetime
    user: UserRead


class OrganizationMemberCreate(BaseModel):
    user_id: UUID
    role: OrganizationRole


class OrganizationMemberUpdate(BaseModel):
    role: OrganizationRole


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
    check_single_org_mode()
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
    check_single_org_mode()
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
    check_single_org_mode()
    helper = helper_factory(current_user, session)
    response = await helper.delete_response(organization_id)
    return response


@router.get("/{organization_id}/members")
async def list_organization_members(
    organization_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
) -> List[OrganizationMemberRead]:
    """List members of an organization."""
    # Verify user has access to the organization
    helper = helper_factory(current_user, session)
    await helper.get_response(organization_id)  # This will raise 404 if not accessible

    # Get members with user information
    result = await session.execute(
        select(OrganizationMember)
        .options(joinedload(OrganizationMember.user))
        .where(OrganizationMember.organization_id == organization_id)
        .order_by(OrganizationMember.created_at.desc())
    )
    members = result.scalars().all()

    return [
        OrganizationMemberRead(
            id=member.id,
            user_id=member.user_id,
            role=member.role,
            created_at=member.created_at,
            user=UserRead(
                id=member.user.id,
                workos_user_id=member.user.workos_user_id,
                email=member.user.email,
                first_name=member.user.first_name,
                last_name=member.user.last_name,
                created_at=member.user.created_at,
            ),
        )
        for member in members
    ]


@router.post("/{organization_id}/members")
async def add_organization_member(
    organization_id: UUID,
    data: OrganizationMemberCreate,
    current_user: CurrentUser,
    session: TransactionSessionDep,
) -> OrganizationMemberRead:
    """Add a member to an organization."""
    check_single_org_mode()
    # Verify user has access to the organization
    helper = helper_factory(current_user, session)
    await helper.get_response(organization_id)  # This will raise 404 if not accessible

    # Check if user is already a member
    existing_member = await session.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.user_id == data.user_id,
        )
    )
    if existing_member.scalar_one_or_none():
        raise HTTPException(
            status_code=400, detail="User is already a member of this organization"
        )

    # Verify the user exists
    user_result = await session.execute(select(User).where(User.id == data.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Create the membership
    member = OrganizationMember(
        organization_id=organization_id,
        user_id=data.user_id,
        role=data.role,
    )
    session.add(member)
    await session.flush()

    # Refresh to get the user relationship
    await session.refresh(member, ["user"])

    return OrganizationMemberRead(
        id=member.id,
        user_id=member.user_id,
        role=member.role,
        created_at=member.created_at,
        user=UserRead(
            id=member.user.id,
            workos_user_id=member.user.workos_user_id,
            email=member.user.email,
            first_name=member.user.first_name,
            last_name=member.user.last_name,
            created_at=member.user.created_at,
        ),
    )


@router.patch("/{organization_id}/members/{user_id}")
async def update_organization_member(
    organization_id: UUID,
    user_id: UUID,
    data: OrganizationMemberUpdate,
    current_user: CurrentUser,
    session: TransactionSessionDep,
) -> OrganizationMemberRead:
    """Update a member's role in an organization."""
    check_single_org_mode()
    # Verify user has access to the organization
    helper = helper_factory(current_user, session)
    await helper.get_response(organization_id)  # This will raise 404 if not accessible

    # Get the member
    result = await session.execute(
        select(OrganizationMember)
        .options(joinedload(OrganizationMember.user))
        .where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Update the role
    member.role = data.role
    await session.flush()

    return OrganizationMemberRead(
        id=member.id,
        user_id=member.user_id,
        role=member.role,
        created_at=member.created_at,
        user=UserRead(
            id=member.user.id,
            workos_user_id=member.user.workos_user_id,
            email=member.user.email,
            first_name=member.user.first_name,
            last_name=member.user.last_name,
            created_at=member.user.created_at,
        ),
    )


@router.delete("/{organization_id}/members/{user_id}")
async def remove_organization_member(
    organization_id: UUID,
    user_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Remove a member from an organization."""
    check_single_org_mode()
    # Verify user has access to the organization
    helper = helper_factory(current_user, session)
    await helper.get_response(organization_id)  # This will raise 404 if not accessible

    # Get the member
    result = await session.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Prevent removing the last owner
    if member.role == OrganizationRole.OWNER:
        owner_count = await session.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.role == OrganizationRole.OWNER,
            )
        )
        if len(owner_count.scalars().all()) <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot remove the last owner from the organization",
            )

    # Remove the member
    await session.delete(member)
    return {"message": "Member removed successfully"}


@router.post("/{organization_id}/sync-users")
async def sync_users_from_workos(
    organization_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """
    Sync users from WorkOS for this organization.

    This is an optional feature that requires the WorkOS SDK and credentials.
    """
    check_single_org_mode()

    # Verify user has access to the organization
    helper = helper_factory(current_user, session)
    await helper.get_response(organization_id)  # This will raise 404 if not accessible

    try:
        # Load the organization to get the WorkOS organization ID
        org_result = await session.execute(
            select(Organization).where(Organization.id == organization_id)
        )
        org = org_result.scalar_one()

        if not org.workos_organization_id:
            raise HTTPException(
                status_code=400,
                detail="Organization does not have a WorkOS organization ID",
            )

        # Sync users from WorkOS
        synced_users = await load_users_from_workos(
            session=session, organization_id=org.workos_organization_id
        )

        return {
            "message": f"Successfully synced {len(synced_users)} users from WorkOS",
            "synced_count": len(synced_users),
        }

    except Exception as e:
        logger.error(
            "Failed to sync users from WorkOS",
            organization_id=organization_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to sync users from WorkOS: {str(e)}"
        )
