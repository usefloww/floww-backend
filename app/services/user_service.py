from typing import Optional

import structlog
from sqlalchemy import func, select

from app.deps.db import SessionDep
from app.models import (
    Namespace,
    Organization,
    OrganizationMember,
    OrganizationRole,
    User,
)
from app.settings import settings
from app.utils.password import hash_password
from app.utils.single_org import get_default_organization_id

logger = structlog.stdlib.get_logger(__name__)


def _generate_org_name(
    first_name: str | None,
    last_name: str | None,
    email: str | None,
    username: str | None,
    user_id: str,
) -> tuple[str, str]:
    """Generate organization name and display_name from user info."""
    import re

    # Generate display name
    if first_name and last_name:
        display_name = f"{first_name} {last_name}"
    elif first_name:
        display_name = first_name
    elif last_name:
        display_name = last_name
    elif username:
        display_name = username
    elif email:
        display_name = email.split("@")[0]
    else:
        display_name = f"User {str(user_id)[:8]}"

    # Create slug from display name
    name = re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-")
    if not name:
        name = f"user-{str(user_id)[:8]}"

    return name, display_name


async def create_user_organization(
    session: SessionDep,
    user: User,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    username: str | None = None,
) -> tuple[Organization, Namespace]:
    """
    Create an organization and namespace for a new user.

    Returns:
        Tuple of (Organization, Namespace)
    """
    base_name, display_name = _generate_org_name(
        first_name, last_name, email, username, str(user.id)
    )

    # Ensure uniqueness by checking and appending suffix
    name = base_name
    suffix = 0
    while True:
        existing = await session.execute(
            select(Organization).where(Organization.name == name)
        )
        if not existing.scalar_one_or_none():
            break
        suffix += 1
        name = f"{base_name}-{suffix}"

    # Create organization
    org = Organization(name=name, display_name=display_name)
    session.add(org)
    await session.flush()

    # Create organization membership (owner)
    org_member = OrganizationMember(
        organization_id=org.id,
        user_id=user.id,
        role=OrganizationRole.OWNER,
    )
    session.add(org_member)
    await session.flush()

    # Create namespace for the organization
    namespace = Namespace(organization_owner_id=org.id)
    session.add(namespace)
    await session.flush()

    return org, namespace


# Initialize WorkOS clients as optional feature for user sync
# This is independent of the authentication method (which uses OIDC)
workos_client = None
workos_sync_client = (
    None  # Sync client needed for Portal APIs (not yet supported in async)
)
try:
    from workos import WorkOSClient
    from workos.async_client import AsyncClient as AsyncWorkOSClient

    if settings.AUTH_CLIENT_SECRET and settings.AUTH_CLIENT_ID:
        workos_client = AsyncWorkOSClient(
            api_key=settings.AUTH_CLIENT_SECRET,
            client_id=settings.AUTH_CLIENT_ID,
        )
        workos_sync_client = WorkOSClient(
            api_key=settings.AUTH_CLIENT_SECRET,
            client_id=settings.AUTH_CLIENT_ID,
        )
except ImportError:
    logger.debug("WorkOS SDK not installed - user sync feature will not be available")


async def get_or_create_user(
    session: SessionDep,
    workos_user_id: str,
    email: Optional[str] = None,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    create: bool = True,
) -> User:
    result = await session.execute(
        select(User).where(User.workos_user_id == workos_user_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            workos_user_id=workos_user_id,
            email=email,
            username=None,
            first_name=first_name,
            last_name=last_name,
        )
        session.add(user)
        await session.flush()

        # Handle namespace and organization membership based on mode
        if settings.SINGLE_ORG_MODE:
            # Single-org mode: add user to default org
            default_org_id = await get_default_organization_id(session)

            # Check if user is already a member (shouldn't happen for new user, but be safe)
            existing_membership = await session.execute(
                select(OrganizationMember).where(
                    OrganizationMember.organization_id == default_org_id,
                    OrganizationMember.user_id == user.id,
                )
            )
            if not existing_membership.scalar_one_or_none():
                # Determine role based on whether this is the first user
                # Check total user count (excluding current user being created)
                user_count_result = await session.execute(select(func.count(User.id)))
                user_count = user_count_result.scalar()

                # First user (user_count == 1, since we already flushed the current user) gets OWNER
                # All subsequent users get MEMBER
                role = (
                    OrganizationRole.OWNER
                    if user_count == 1
                    else OrganizationRole.MEMBER
                )

                org_member = OrganizationMember(
                    organization_id=default_org_id,
                    user_id=user.id,
                    role=role,
                )
                session.add(org_member)
                await session.flush()
                logger.info(
                    "Added user to default organization",
                    user_id=user.id,
                    organization_id=default_org_id,
                    role=role,
                )
        else:
            # Multi-tenant mode: create organization + namespace for user
            org, namespace = await create_user_organization(
                session, user, first_name, last_name, email
            )
            logger.info(
                "Created organization for user",
                user_id=user.id,
                organization_id=org.id,
                namespace_id=namespace.id,
            )

        if create:
            await session.commit()
    else:
        # Update user info if provided
        updated = False
        if email and user.email != email:
            user.email = email
            updated = True
        if first_name and user.first_name != first_name:
            user.first_name = first_name
            updated = True
        if last_name and user.last_name != last_name:
            user.last_name = last_name
            updated = True
        if updated and create:
            await session.commit()

    return user


async def load_users_from_workos(
    session: SessionDep, organization_id: Optional[str] = None
) -> list[User]:
    """
    Load users from WorkOS and sync them to the database.

    This is an optional feature that requires the WorkOS SDK and credentials.

    Args:
        session: Database session
        organization_id: If provided, load users from this WorkOS organization.
                        If None, load all users accessible to the application.

    Returns:
        List of User objects that were created or updated

    Raises:
        ValueError: If WorkOS client is not initialized
    """
    if workos_client is None:
        raise ValueError(
            "WorkOS client is not initialized. Please install the WorkOS SDK and "
            "configure AUTH_CLIENT_SECRET and AUTH_CLIENT_ID."
        )

    try:
        users_created_or_updated = []

        if organization_id:
            # Load users from a specific WorkOS organization
            logger.info(
                "Loading users from WorkOS organization",
                organization_id=organization_id,
            )

            # Find the corresponding organization in our database
            org_result = await session.execute(
                select(Organization).where(
                    Organization.workos_organization_id == organization_id
                )
            )
            organization = org_result.scalar_one_or_none()
            if not organization:
                raise ValueError(
                    f"Organization with WorkOS ID {organization_id} not found in database"
                )

            # Get organization members from WorkOS
            organization_memberships = (
                await workos_client.user_management.list_organization_memberships(
                    organization_id=organization_id,
                    limit=100,  # Adjust as needed
                )
            )

            for membership in organization_memberships.data:
                # Get the user details using the user_id from membership
                workos_user = await workos_client.user_management.get_user(
                    membership.user_id
                )

                # Create or update user in database
                user = await get_or_create_user(
                    session=session,
                    workos_user_id=workos_user.id,
                    email=workos_user.email,
                    first_name=workos_user.first_name,
                    last_name=workos_user.last_name,
                    create=False,  # Don't commit individual users
                )
                users_created_or_updated.append(user)

                # Create or update organization membership
                existing_membership = await session.execute(
                    select(OrganizationMember).where(
                        OrganizationMember.organization_id == organization.id,
                        OrganizationMember.user_id == user.id,
                    )
                )
                if not existing_membership.scalar_one_or_none():
                    # Map WorkOS role to our role enum - default to MEMBER if role not found
                    role = OrganizationRole.MEMBER
                    if hasattr(membership, "role") and membership.role:
                        # Handle different possible role structures
                        workos_role = None
                        try:
                            # Try dictionary-style access first (TypedDict)
                            workos_role = membership.role["slug"]
                        except (KeyError, TypeError):
                            try:
                                # Fallback to attribute access
                                workos_role = getattr(membership.role, "slug", None)
                            except AttributeError:
                                # Fallback to string representation
                                workos_role = str(membership.role)

                        if workos_role and workos_role.lower() == "admin":
                            role = OrganizationRole.ADMIN
                        elif workos_role and workos_role.lower() == "owner":
                            role = OrganizationRole.OWNER

                    org_member = OrganizationMember(
                        organization_id=organization.id,
                        user_id=user.id,
                        role=role,
                    )
                    session.add(org_member)
                    logger.debug(
                        "Created organization membership",
                        user_id=user.id,
                        organization_id=organization.id,
                        role=role,
                    )

                logger.debug(
                    "Synced user from WorkOS",
                    workos_user_id=workos_user.id,
                    email=workos_user.email,
                    organization_id=organization_id,
                )
        else:
            # Load all users accessible to the application
            logger.info("Loading all users from WorkOS")

            users = await workos_client.user_management.list_users(
                limit=100  # Adjust as needed
            )

            for workos_user in users.data:
                # Create or update user in database
                user = await get_or_create_user(
                    session=session,
                    workos_user_id=workos_user.id,
                    email=workos_user.email,
                    first_name=workos_user.first_name,
                    last_name=workos_user.last_name,
                    create=False,  # Don't commit individual users
                )
                users_created_or_updated.append(user)

                logger.debug(
                    "Synced user from WorkOS",
                    workos_user_id=workos_user.id,
                    email=workos_user.email,
                )

        # Commit all changes at once
        await session.commit()

        logger.info(
            "Successfully synced users from WorkOS",
            count=len(users_created_or_updated),
            organization_id=organization_id,
        )

        return users_created_or_updated

    except Exception as e:
        logger.error(
            "Failed to load users from WorkOS",
            error=str(e),
            organization_id=organization_id,
        )
        await session.rollback()
        raise


async def create_password_user(
    session: SessionDep,
    username: str,
    password: str,
) -> User:
    """
    Create a new user with password-based authentication.

    Args:
        session: Database session
        username: Username (must be unique)
        password: User's plaintext password (will be hashed)

    Returns:
        The created User object

    Raises:
        ValueError: If a user with this username already exists
    """
    # Check if user with this username already exists
    result = await session.execute(select(User).where(User.username == username))
    existing_user = result.scalar_one_or_none()
    if existing_user:
        raise ValueError(f"Username '{username}' is already taken")

    # Create user without password_hash first to get the user_id
    user = User(
        username=username,
        workos_user_id=None,  # Password users don't have WorkOS ID
    )
    session.add(user)
    await session.flush()  # Flush to get the user.id

    # Now hash the password with the user_id as salt
    user.password_hash = hash_password(password, user.id)
    await session.flush()

    # Handle namespace and organization membership based on mode
    if settings.SINGLE_ORG_MODE:
        # Single-org mode: add user to default org
        default_org_id = await get_default_organization_id(session)

        # Determine role based on whether this is the first user
        # Check total user count (excluding current user being created)
        user_count_result = await session.execute(select(func.count(User.id)))
        user_count = user_count_result.scalar()

        # First user (user_count == 1, since we already flushed the current user) gets OWNER
        # All subsequent users get MEMBER
        role = OrganizationRole.OWNER if user_count == 1 else OrganizationRole.MEMBER

        org_member = OrganizationMember(
            organization_id=default_org_id,
            user_id=user.id,
            role=role,
        )
        session.add(org_member)
        await session.flush()
        logger.info(
            "Added password user to default organization",
            user_id=user.id,
            username=username,
            organization_id=default_org_id,
            role=role,
        )
    else:
        # Multi-tenant mode: create organization + namespace for user
        org, namespace = await create_user_organization(
            session, user, None, None, None, username
        )
        logger.info(
            "Created organization for password user",
            user_id=user.id,
            username=username,
            organization_id=org.id,
            namespace_id=namespace.id,
        )

    await session.commit()

    logger.info("Created password-based user", user_id=user.id, username=username)
    return user


async def get_user_by_username(session: SessionDep, username: str) -> Optional[User]:
    result = await session.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


# WorkOS Invitation and Portal utilities


def get_workos_client():
    if workos_client is None:
        raise ValueError(
            "WorkOS client is not initialized. Please install the WorkOS SDK and "
            "configure AUTH_CLIENT_SECRET and AUTH_CLIENT_ID."
        )
    return workos_client


async def send_workos_invitation(
    workos_organization_id: str,
    email: str,
    inviter_user_id: Optional[str] = None,
    role_slug: Optional[str] = None,
    expires_in_days: int = 7,
):
    """
    Send an invitation to a user via WorkOS.

    Args:
        workos_organization_id: The WorkOS organization ID
        email: Email address to invite
        inviter_user_id: Optional WorkOS user ID of the inviter
        role_slug: Optional role slug for the invitation
        expires_in_days: Days until invitation expires (1-30)

    Returns:
        WorkOS Invitation object
    """
    client = get_workos_client()

    invitation = await client.user_management.send_invitation(
        email=email,
        organization_id=workos_organization_id,
        inviter_user_id=inviter_user_id,
        role_slug=role_slug,
        expires_in_days=expires_in_days,
    )

    logger.info(
        "Sent WorkOS invitation",
        email=email,
        organization_id=workos_organization_id,
        invitation_id=invitation.id,
    )

    return invitation


async def list_workos_invitations(workos_organization_id: str):
    """
    List pending invitations for a WorkOS organization.

    Args:
        workos_organization_id: The WorkOS organization ID

    Returns:
        List of WorkOS Invitation objects
    """
    client = get_workos_client()

    invitations = await client.user_management.list_invitations(
        organization_id=workos_organization_id,
    )

    return invitations


async def revoke_workos_invitation(invitation_id: str):
    """
    Revoke a WorkOS invitation.

    Args:
        invitation_id: The WorkOS invitation ID

    Returns:
        Revoked Invitation object
    """
    client = get_workos_client()

    invitation = await client.user_management.revoke_invitation(invitation_id)

    logger.info("Revoked WorkOS invitation", invitation_id=invitation_id)

    return invitation


def generate_sso_portal_link(
    workos_organization_id: str,
    return_url: Optional[str] = None,
    success_url: Optional[str] = None,
):
    """
    Generate a WorkOS Admin Portal link for SSO configuration.

    Note: This is a synchronous function because the WorkOS async client
    does not yet support Portal APIs.

    Args:
        workos_organization_id: The WorkOS organization ID
        return_url: URL to redirect to after exiting the portal
        success_url: URL to redirect to after successful configuration

    Returns:
        PortalLink object with the admin portal URL
    """
    if workos_sync_client is None:
        raise ValueError(
            "WorkOS client is not initialized. Please install the WorkOS SDK and "
            "configure AUTH_CLIENT_SECRET and AUTH_CLIENT_ID."
        )

    portal_link = workos_sync_client.portal.generate_link(
        intent="sso",
        organization_id=workos_organization_id,
        return_url=return_url,
        success_url=success_url,
    )

    logger.info(
        "Generated SSO portal link",
        organization_id=workos_organization_id,
    )

    return portal_link


async def create_workos_organization(name: str, external_id: Optional[str] = None):
    """
    Create a new organization in WorkOS.

    Args:
        name: The name of the organization
        external_id: Optional external ID to link the organization

    Returns:
        WorkOS Organization object with the new organization's ID
    """
    client = get_workos_client()

    organization = await client.organizations.create_organization(
        name=name,
        external_id=external_id,
    )

    logger.info(
        "Created WorkOS organization",
        workos_org_id=organization.id,
        name=name,
    )

    return organization


async def delete_workos_organization(workos_organization_id: str):
    """
    Delete an organization from WorkOS.

    Args:
        workos_organization_id: The WorkOS organization ID to delete
    """
    client = get_workos_client()

    await client.organizations.delete_organization(workos_organization_id)

    logger.info(
        "Deleted WorkOS organization",
        workos_org_id=workos_organization_id,
    )
