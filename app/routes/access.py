from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep, TransactionSessionDep
from app.models import (
    AccessRole,
    AccessTuple,
    PrincipleType,
    Provider,
    ResourceType,
    User,
)
from app.services.access_service import (
    get_resolved_access,
    get_resource_principals,
)
from app.utils.query_helpers import UserAccessibleQuery

router = APIRouter(prefix="/access", tags=["Access Control"])


class AccessGrantRequest(BaseModel):
    principal_type: PrincipleType
    principal_id: UUID
    resource_type: ResourceType
    resource_id: UUID
    role: AccessRole


class AccessGrantResponse(BaseModel):
    id: UUID
    principal_type: PrincipleType
    principal_id: UUID
    resource_type: ResourceType
    resource_id: UUID
    role: AccessRole


class ProviderAccessEntry(BaseModel):
    id: UUID
    user_id: UUID
    user_email: Optional[str]
    user_first_name: Optional[str]
    user_last_name: Optional[str]
    role: AccessRole


class ProviderAccessListResponse(BaseModel):
    results: list[ProviderAccessEntry]


class GrantUserProviderAccessRequest(BaseModel):
    user_id: UUID
    role: AccessRole


class UpdateAccessRoleRequest(BaseModel):
    role: AccessRole


@router.post("/grant", response_model=AccessGrantResponse)
async def grant_access(
    data: AccessGrantRequest,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Grant access to a resource for a principal."""
    # Verify the current user has access to the resource they're granting access to
    user_role = await get_resolved_access(
        session,
        PrincipleType.USER,
        current_user.id,
        data.resource_type,
        data.resource_id,
    )

    if user_role != AccessRole.OWNER:
        raise HTTPException(
            status_code=403,
            detail="You must be an owner of the resource to grant access",
        )

    # Check if access already exists
    existing = await session.execute(
        select(AccessTuple).where(
            AccessTuple.principle_type == data.principal_type,
            AccessTuple.principle_id == data.principal_id,
            AccessTuple.resource_type == data.resource_type,
            AccessTuple.resource_id == data.resource_id,
        )
    )
    existing_access = existing.scalar_one_or_none()

    if existing_access:
        # Update existing access
        existing_access.role = data.role
        await session.flush()
        await session.refresh(existing_access)
        return AccessGrantResponse(
            id=existing_access.id,
            principal_type=existing_access.principle_type,
            principal_id=existing_access.principle_id,
            resource_type=existing_access.resource_type,
            resource_id=existing_access.resource_id,
            role=existing_access.role,
        )

    # Create new access
    access = AccessTuple(
        principle_type=data.principal_type,
        principle_id=data.principal_id,
        resource_type=data.resource_type,
        resource_id=data.resource_id,
        role=data.role,
    )
    session.add(access)
    await session.flush()
    await session.refresh(access)

    return AccessGrantResponse(
        id=access.id,
        principal_type=access.principle_type,
        principal_id=access.principle_id,
        resource_type=access.resource_type,
        resource_id=access.resource_id,
        role=access.role,
    )


@router.delete("/revoke")
async def revoke_access(
    principal_type: PrincipleType,
    principal_id: UUID,
    resource_type: ResourceType,
    resource_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Revoke access to a resource from a principal."""
    # Verify the current user has owner access to the resource
    user_role = await get_resolved_access(
        session,
        PrincipleType.USER,
        current_user.id,
        resource_type,
        resource_id,
    )

    if user_role != AccessRole.OWNER:
        raise HTTPException(
            status_code=403,
            detail="You must be an owner of the resource to revoke access",
        )

    result = await session.execute(
        delete(AccessTuple).where(
            AccessTuple.principle_type == principal_type,
            AccessTuple.principle_id == principal_id,
            AccessTuple.resource_type == resource_type,
            AccessTuple.resource_id == resource_id,
        )
    )

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Access grant not found")

    return {"message": "Access revoked successfully"}


# Provider-specific endpoints for user access management


@router.get("/providers/{provider_id}/users", response_model=ProviderAccessListResponse)
async def list_provider_user_access(
    provider_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
):
    """List all users with access to a provider."""
    # Verify user can access this provider
    provider_query = UserAccessibleQuery(current_user.id).providers()
    result = await session.execute(provider_query.where(Provider.id == provider_id))
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    # Get all principals with access
    access_list = await get_resource_principals(
        session,
        ResourceType.PROVIDER,
        provider_id,
        principal_type=PrincipleType.USER,
    )

    # Fetch user details for each access entry
    user_ids = [a.principal_id for a in access_list]
    if not user_ids:
        return ProviderAccessListResponse(results=[])

    users_result = await session.execute(select(User).where(User.id.in_(user_ids)))
    users_by_id = {u.id: u for u in users_result.scalars().all()}

    # Get access tuple IDs for the response
    access_tuples_result = await session.execute(
        select(AccessTuple).where(
            AccessTuple.resource_type == ResourceType.PROVIDER,
            AccessTuple.resource_id == provider_id,
            AccessTuple.principle_type == PrincipleType.USER,
        )
    )
    access_tuples = {at.principle_id: at for at in access_tuples_result.scalars().all()}

    entries = []
    for access in access_list:
        user = users_by_id.get(access.principal_id)
        access_tuple = access_tuples.get(access.principal_id)
        if user and access_tuple:
            entries.append(
                ProviderAccessEntry(
                    id=access_tuple.id,
                    user_id=user.id,
                    user_email=user.email,
                    user_first_name=user.first_name,
                    user_last_name=user.last_name,
                    role=access.role,
                )
            )

    return ProviderAccessListResponse(results=entries)


@router.post("/providers/{provider_id}/users", response_model=ProviderAccessEntry)
async def grant_user_provider_access(
    provider_id: UUID,
    data: GrantUserProviderAccessRequest,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Grant a user access to a provider."""
    # Verify current user has owner access to this provider
    user_role = await get_resolved_access(
        session,
        PrincipleType.USER,
        current_user.id,
        ResourceType.PROVIDER,
        provider_id,
    )

    if user_role != AccessRole.OWNER:
        raise HTTPException(
            status_code=403,
            detail="You must be an owner of the provider to grant access",
        )

    # Verify the target user exists
    user_result = await session.execute(select(User).where(User.id == data.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if access already exists
    existing = await session.execute(
        select(AccessTuple).where(
            AccessTuple.principle_type == PrincipleType.USER,
            AccessTuple.principle_id == data.user_id,
            AccessTuple.resource_type == ResourceType.PROVIDER,
            AccessTuple.resource_id == provider_id,
        )
    )
    existing_access = existing.scalar_one_or_none()

    if existing_access:
        existing_access.role = data.role
        await session.flush()
        await session.refresh(existing_access)
        access = existing_access
    else:
        access = AccessTuple(
            principle_type=PrincipleType.USER,
            principle_id=data.user_id,
            resource_type=ResourceType.PROVIDER,
            resource_id=provider_id,
            role=data.role,
        )
        session.add(access)
        await session.flush()
        await session.refresh(access)

    return ProviderAccessEntry(
        id=access.id,
        user_id=user.id,
        user_email=user.email,
        user_first_name=user.first_name,
        user_last_name=user.last_name,
        role=access.role,
    )


@router.patch(
    "/providers/{provider_id}/users/{user_id}", response_model=ProviderAccessEntry
)
async def update_user_provider_access(
    provider_id: UUID,
    user_id: UUID,
    data: UpdateAccessRoleRequest,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Update a user's access role for a provider."""
    # Verify current user has owner access
    user_role = await get_resolved_access(
        session,
        PrincipleType.USER,
        current_user.id,
        ResourceType.PROVIDER,
        provider_id,
    )

    if user_role != AccessRole.OWNER:
        raise HTTPException(
            status_code=403,
            detail="You must be an owner of the provider to update access",
        )

    # Find and update the access
    result = await session.execute(
        select(AccessTuple).where(
            AccessTuple.principle_type == PrincipleType.USER,
            AccessTuple.principle_id == user_id,
            AccessTuple.resource_type == ResourceType.PROVIDER,
            AccessTuple.resource_id == provider_id,
        )
    )
    access = result.scalar_one_or_none()
    if not access:
        raise HTTPException(status_code=404, detail="Access grant not found")

    access.role = data.role
    await session.flush()
    await session.refresh(access)

    # Get user details
    user_result = await session.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()

    return ProviderAccessEntry(
        id=access.id,
        user_id=user_id,
        user_email=user.email if user else None,
        user_first_name=user.first_name if user else None,
        user_last_name=user.last_name if user else None,
        role=access.role,
    )


@router.delete("/providers/{provider_id}/users/{user_id}")
async def revoke_user_provider_access(
    provider_id: UUID,
    user_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Revoke a user's access to a provider."""
    # Verify current user has owner access
    user_role = await get_resolved_access(
        session,
        PrincipleType.USER,
        current_user.id,
        ResourceType.PROVIDER,
        provider_id,
    )

    if user_role != AccessRole.OWNER:
        raise HTTPException(
            status_code=403,
            detail="You must be an owner of the provider to revoke access",
        )

    result = await session.execute(
        delete(AccessTuple).where(
            AccessTuple.principle_type == PrincipleType.USER,
            AccessTuple.principle_id == user_id,
            AccessTuple.resource_type == ResourceType.PROVIDER,
            AccessTuple.resource_id == provider_id,
        )
    )

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Access grant not found")

    return {"message": "Access revoked successfully"}
