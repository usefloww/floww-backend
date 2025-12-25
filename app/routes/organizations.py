from datetime import datetime
from typing import List, Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep, TransactionSessionDep
from app.models import (
    Namespace,
    Organization,
    OrganizationMember,
    OrganizationRole,
    User,
    UserType,
)
from app.services.crud_helpers import CrudHelper
from app.services.user_service import (
    create_workos_organization,
    delete_workos_organization,
    generate_sso_portal_link,
    list_workos_invitations,
    load_users_from_workos,
    revoke_workos_invitation,
    send_workos_invitation,
)
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


async def check_admin_or_owner(
    session: SessionDep,
    organization_id: UUID,
    user_id: UUID,
) -> OrganizationMember:
    """Check if user is an admin or owner of the organization. Returns the membership."""
    result = await session.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Organization not found")
    if member.role not in (OrganizationRole.OWNER, OrganizationRole.ADMIN):
        raise HTTPException(
            status_code=403,
            detail="Only organization owners and admins can perform this action",
        )
    return member


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
    workos_user_id: Optional[str] = None
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


# Invitation models
class InvitationCreate(BaseModel):
    email: str
    role: Optional[str] = None  # Optional role slug
    expires_in_days: int = 7


class InvitationRead(BaseModel):
    id: str
    email: str
    state: str
    created_at: str
    expires_at: str


# SSO models
class SSOSetupRequest(BaseModel):
    return_url: Optional[str] = None
    success_url: Optional[str] = None


class SSOSetupResponse(BaseModel):
    admin_portal_link: str


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

    # Create WorkOS organization first (optional - will continue without if unavailable)
    workos_org = None
    try:
        workos_org = await create_workos_organization(
            name=data.display_name,
        )
    except ValueError as e:
        # WorkOS client not configured - continue without WorkOS integration
        logger.warning(
            "WorkOS client not configured, creating org without WorkOS: %s", e
        )
    except Exception as e:
        # WorkOS API error - log and continue without WorkOS integration
        # This allows organization creation to work even if WorkOS is down
        logger.warning(
            "Failed to create WorkOS organization, continuing without: %s", e
        )

    # Create local organization
    org = Organization(
        name=data.name,
        display_name=data.display_name,
        workos_organization_id=workos_org.id if workos_org else None,
    )
    session.add(org)

    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        # Clean up WorkOS organization if local creation fails
        if workos_org:
            try:
                await delete_workos_organization(workos_org.id)
            except Exception:
                logger.error(
                    "Failed to clean up WorkOS organization after local failure"
                )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Organization with name '{data.name}' already exists",
        )

    # Make the creator the owner
    membership = OrganizationMember(
        organization_id=org.id,
        user_id=current_user.id,
        role=OrganizationRole.OWNER,
    )
    session.add(membership)
    await session.flush()

    # Create a namespace for the organization
    namespace = Namespace(organization_owner_id=org.id)
    session.add(namespace)
    await session.flush()

    return OrganizationRead(
        id=org.id,
        name=org.name,
        display_name=org.display_name,
        created_at=org.created_at,
        updated_at=org.updated_at,
    )


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

    # Verify user has owner access (only owners can delete organizations)
    membership = await check_admin_or_owner(session, organization_id, current_user.id)
    if membership.role != OrganizationRole.OWNER:
        raise HTTPException(
            status_code=403,
            detail="Only organization owners can delete the organization",
        )

    # Get the organization
    org_result = await session.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    org = org_result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Delete WorkOS organization if it exists
    if org.workos_organization_id:
        try:
            await delete_workos_organization(org.workos_organization_id)
        except ValueError as e:
            logger.warning(
                "WorkOS client not configured, skipping WorkOS deletion: %s", e
            )
        except Exception as e:
            logger.error("Failed to delete WorkOS organization: %s", e)
            # Continue with local deletion even if WorkOS deletion fails
            # The WorkOS org may have already been deleted or doesn't exist

    # Delete the local organization (cascades to members, namespaces, etc.)
    await session.delete(org)
    await session.flush()

    return {"message": "Organization deleted successfully"}


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
        .join(User)
        .options(joinedload(OrganizationMember.user))
        .where(
            and_(
                OrganizationMember.organization_id == organization_id,
                User.user_type == UserType.HUMAN,
            )
        )
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

    # Verify user has admin/owner access
    await check_admin_or_owner(session, organization_id, current_user.id)

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

    # Prevent demoting the last owner
    if member.role == OrganizationRole.OWNER and data.role != OrganizationRole.OWNER:
        owner_count_result = await session.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.role == OrganizationRole.OWNER,
            )
        )
        if len(owner_count_result.scalars().all()) <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot demote the last owner. Promote another member to owner first.",
            )

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

    # Verify user has admin/owner access
    await check_admin_or_owner(session, organization_id, current_user.id)

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


# Invitation endpoints


@router.get("/{organization_id}/invitations")
async def list_invitations(
    organization_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
) -> List[InvitationRead]:
    """List pending invitations for an organization."""
    # Verify user has admin/owner access
    await check_admin_or_owner(session, organization_id, current_user.id)

    # Get the organization to find WorkOS org ID
    org_result = await session.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    org = org_result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    if not org.workos_organization_id:
        raise HTTPException(
            status_code=400,
            detail="Organization does not have a WorkOS organization ID configured",
        )

    try:
        invitations_response = await list_workos_invitations(org.workos_organization_id)

        return [
            InvitationRead(
                id=inv.id,
                email=inv.email,
                state=inv.state,
                created_at=str(inv.created_at),
                expires_at=str(inv.expires_at),
            )
            for inv in invitations_response.data
        ]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(
            "Failed to list invitations",
            organization_id=organization_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to list invitations: {str(e)}"
        )


@router.post("/{organization_id}/invitations")
async def create_invitation(
    organization_id: UUID,
    data: InvitationCreate,
    current_user: CurrentUser,
    session: SessionDep,
) -> InvitationRead:
    """Invite a user to an organization by email."""
    check_single_org_mode()

    # Verify user has admin/owner access
    await check_admin_or_owner(session, organization_id, current_user.id)

    # Get the organization to find WorkOS org ID
    org_result = await session.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    org = org_result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    if not org.workos_organization_id:
        raise HTTPException(
            status_code=400,
            detail="Organization does not have a WorkOS organization ID configured",
        )

    try:
        # Get the current user's WorkOS ID to use as inviter
        inviter_workos_id = current_user.workos_user_id

        invitation = await send_workos_invitation(
            workos_organization_id=org.workos_organization_id,
            email=data.email,
            inviter_user_id=inviter_workos_id,
            role_slug=data.role,
            expires_in_days=data.expires_in_days,
        )

        return InvitationRead(
            id=invitation.id,
            email=invitation.email,
            state=invitation.state,
            created_at=str(invitation.created_at),
            expires_at=str(invitation.expires_at),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(
            "Failed to send invitation",
            organization_id=organization_id,
            email=data.email,
            error=str(e),
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to send invitation: {str(e)}"
        )


@router.delete("/{organization_id}/invitations/{invitation_id}")
async def revoke_invitation(
    organization_id: UUID,
    invitation_id: str,
    current_user: CurrentUser,
    session: SessionDep,
):
    """Revoke a pending invitation."""
    check_single_org_mode()

    # Verify user has admin/owner access
    await check_admin_or_owner(session, organization_id, current_user.id)

    try:
        await revoke_workos_invitation(invitation_id)
        return {"message": "Invitation revoked successfully"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(
            "Failed to revoke invitation",
            organization_id=organization_id,
            invitation_id=invitation_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to revoke invitation: {str(e)}"
        )


# SSO endpoints


@router.post("/{organization_id}/sso/setup")
async def setup_sso(
    organization_id: UUID,
    data: SSOSetupRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> SSOSetupResponse:
    """Generate an Admin Portal link for SSO configuration."""
    check_single_org_mode()

    # Verify user has admin/owner access
    await check_admin_or_owner(session, organization_id, current_user.id)

    # Get the organization to find WorkOS org ID
    org_result = await session.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    org = org_result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    if not org.workos_organization_id:
        raise HTTPException(
            status_code=400,
            detail="Organization does not have a WorkOS organization ID configured",
        )

    try:
        portal_link = generate_sso_portal_link(
            workos_organization_id=org.workos_organization_id,
            return_url=data.return_url,
            success_url=data.success_url,
        )

        return SSOSetupResponse(admin_portal_link=portal_link.link)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(
            "Failed to generate SSO portal link",
            organization_id=organization_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to generate SSO portal link: {str(e)}"
        )
