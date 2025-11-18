"""
Single organization mode utilities.

Provides functions to initialize and manage the default organization
when running in single-org mode.
"""

from uuid import UUID

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.db import AsyncSessionLocal
from app.models import (
    Namespace,
    Organization,
    OrganizationMember,
    OrganizationRole,
    User,
)
from app.settings import settings


async def setup_single_org_mode(app: FastAPI):
    """
    Setup single-org mode for the application.

    Args:
        app: FastAPI application
    """
    async with AsyncSessionLocal() as session:
        try:
            org_id, namespace_id = await ensure_default_organization(session)
            # Store in app state for easy access
            app.state.default_organization_id = org_id
            app.state.default_namespace_id = namespace_id
            print(
                f"Single-org mode initialized: org_id={org_id}, namespace_id={namespace_id}"
            )

            # Initialize anonymous user if AUTH_TYPE='none'
            if settings.AUTH_TYPE == "none":
                # Check if anonymous user already exists
                result = await session.execute(
                    select(User).where(User.workos_user_id == "anonymous")
                )
                anonymous_user = result.scalar_one_or_none()

                if anonymous_user is None:
                    # Create anonymous user
                    anonymous_user = User(
                        workos_user_id="anonymous",
                        email="anonymous@floww.local",
                        first_name="Anonymous",
                        last_name="User",
                    )
                    session.add(anonymous_user)
                    await session.flush()

                    # Add anonymous user to default organization as admin
                    org_member = OrganizationMember(
                        organization_id=org_id,
                        user_id=anonymous_user.id,
                        role=OrganizationRole.ADMIN,
                    )
                    session.add(org_member)
                    await session.commit()
                    print(
                        f"Anonymous user created: user_id={anonymous_user.id}, role=admin"
                    )
                else:
                    # Verify anonymous user is member of default organization
                    result = await session.execute(
                        select(OrganizationMember).where(
                            OrganizationMember.organization_id == org_id,
                            OrganizationMember.user_id == anonymous_user.id,
                        )
                    )
                    membership = result.scalar_one_or_none()

                    if membership is None:
                        # Add to organization if not already a member
                        org_member = OrganizationMember(
                            organization_id=org_id,
                            user_id=anonymous_user.id,
                            role=OrganizationRole.ADMIN,
                        )
                        session.add(org_member)
                        await session.commit()
                        print(
                            f"Anonymous user added to default organization: user_id={anonymous_user.id}"
                        )

                # Store anonymous user ID in app state
                app.state.anonymous_user_id = anonymous_user.id
                print(f"Anonymous authentication enabled: user_id={anonymous_user.id}")

        finally:
            await session.close()


async def ensure_default_organization(session: AsyncSession) -> tuple[UUID, UUID]:
    """
    Ensure the default organization exists when running in single-org mode.

    Creates the organization and its default namespace if they don't exist.
    This function is idempotent and safe to call multiple times.

    Args:
        session: Database session

    Returns:
        Tuple of (organization_id, namespace_id)

    Raises:
        RuntimeError: If called when SINGLE_ORG_MODE is not enabled
    """
    if not settings.SINGLE_ORG_MODE:
        raise RuntimeError(
            "ensure_default_organization should only be called when SINGLE_ORG_MODE is enabled"
        )

    # Check if default organization already exists
    result = await session.execute(
        select(Organization).where(Organization.name == settings.SINGLE_ORG_NAME)
    )
    org = result.scalar_one_or_none()

    if org is None:
        # Create the default organization
        org = Organization(
            name=settings.SINGLE_ORG_NAME,
            display_name=settings.SINGLE_ORG_DISPLAY_NAME,
            workos_organization_id=None,  # Not tied to WorkOS in single-org mode
        )
        session.add(org)
        await session.flush()  # Flush to get the org.id

    # Check if default namespace exists for this organization
    result = await session.execute(
        select(Namespace).where(Namespace.organization_owner_id == org.id)
    )
    namespace = result.scalar_one_or_none()

    if namespace is None:
        # Create default namespace for the organization
        namespace = Namespace(
            organization_owner_id=org.id,
            user_owner_id=None,
        )
        session.add(namespace)
        await session.flush()

    await session.commit()

    return org.id, namespace.id


async def get_default_organization_id(session: AsyncSession) -> UUID:
    """
    Get the ID of the default organization.

    Args:
        session: Database session

    Returns:
        The organization ID

    Raises:
        RuntimeError: If single-org mode is not enabled or org doesn't exist
    """
    if not settings.SINGLE_ORG_MODE:
        raise RuntimeError(
            "get_default_organization_id should only be called when SINGLE_ORG_MODE is enabled"
        )

    result = await session.execute(
        select(Organization.id).where(Organization.name == settings.SINGLE_ORG_NAME)
    )
    org_id = result.scalar_one_or_none()

    if org_id is None:
        raise RuntimeError(
            f"Default organization '{settings.SINGLE_ORG_NAME}' not found. "
            "Make sure ensure_default_organization() has been called during startup."
        )

    return org_id
