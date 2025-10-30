from typing import Optional

import structlog
from sqlalchemy import select
from workos.async_client import AsyncClient as AsyncWorkOSClient

from app.deps.db import SessionDep
from app.models import (
    Namespace,
    Organization,
    OrganizationMember,
    OrganizationRole,
    User,
)
from app.settings import settings

logger = structlog.stdlib.get_logger(__name__)

# Initialize WorkOS async client
workos_client = AsyncWorkOSClient(
    api_key=settings.WORKOS_CLIENT_SECRET,
    client_id=settings.WORKOS_CLIENT_ID,
)

# Verify WorkOS client is properly configured
if not settings.WORKOS_CLIENT_SECRET:
    logger.warning("WORKOS_CLIENT_SECRET is not set - WorkOS integration will not work")
if not settings.WORKOS_CLIENT_ID:
    logger.warning("WORKOS_CLIENT_ID is not set - WorkOS integration will not work")


async def get_or_create_user(
    session: SessionDep,
    workos_user_id: str,
    email: Optional[str] = None,
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
            first_name=first_name,
            last_name=last_name,
        )
        session.add(user)
        await session.flush()

        namespace = Namespace(
            user_owner_id=user.id, name=str(user.id), display_name=str(user.id)
        )
        session.add(namespace)
        await session.flush()
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

    Args:
        session: Database session
        organization_id: If provided, load users from this WorkOS organization.
                        If None, load all users accessible to the application.

    Returns:
        List of User objects that were created or updated
    """
    # Check if WorkOS is properly configured
    if not settings.WORKOS_CLIENT_SECRET or not settings.WORKOS_CLIENT_ID:
        raise ValueError(
            "WorkOS client is not properly configured. Please set WORKOS_CLIENT_SECRET and WORKOS_CLIENT_ID."
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
