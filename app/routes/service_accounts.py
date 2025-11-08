from datetime import datetime
from typing import Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep, TransactionSessionDep
from app.models import (
    ApiKey,
    OrganizationMember,
    OrganizationRole,
    User,
    UserType,
)
from app.utils.encryption import (
    generate_api_key,
    hash_api_key,
)
from app.utils.query_helpers import UserAccessibleQuery

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/service_accounts", tags=["Service Accounts"])


class ServiceAccountCreate(BaseModel):
    name: str
    organization_id: UUID


class ServiceAccountUpdate(BaseModel):
    name: Optional[str] = None


class ApiKeyCreate(BaseModel):
    name: str


class ApiKeyRead(BaseModel):
    id: UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class ApiKeyCreatedResponse(ApiKeyRead):
    api_key: str


class ServiceAccountRead(BaseModel):
    id: UUID
    name: str
    organization_id: UUID
    api_keys: list[ApiKeyRead] = []


class DeleteResponse(BaseModel):
    success: bool
    message: str


@router.post("", response_model=ServiceAccountRead)
async def create_service_account(
    data: ServiceAccountCreate,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Create a new service account in an organization."""
    # Check if user has ADMIN or OWNER role in the organization
    org_query = select(OrganizationMember).where(
        OrganizationMember.organization_id == data.organization_id,
        OrganizationMember.user_id == current_user.id,
        OrganizationMember.role.in_([OrganizationRole.ADMIN, OrganizationRole.OWNER]),
    )
    result = await session.execute(org_query)
    org_membership = result.scalar_one_or_none()

    if not org_membership:
        raise HTTPException(
            status_code=403,
            detail="You must be an admin or owner of the organization to create service accounts",
        )

    # Create the service account user
    service_account = User(
        user_type=UserType.SERVICE_ACCOUNT,
        first_name=data.name,
        workos_user_id=None,  # Service accounts don't have WorkOS IDs
    )
    session.add(service_account)
    await session.flush()  # Get the ID

    # Add the service account as a member of the organization
    org_member = OrganizationMember(
        organization_id=data.organization_id,
        user_id=service_account.id,
        role=OrganizationRole.MEMBER,
    )
    session.add(org_member)
    await session.flush()

    logger.info(
        "Created service account",
        service_account_id=str(service_account.id),
        organization_id=str(data.organization_id),
        created_by=str(current_user.id),
    )

    return ServiceAccountRead(
        id=service_account.id,
        name=service_account.first_name,
        organization_id=data.organization_id,
        api_keys=[],
    )


@router.get("")
async def list_service_accounts(
    current_user: CurrentUser,
    session: SessionDep,
    organization_id: UUID,
):
    """List all service accounts in an organization accessible to the current user."""
    # Use UserAccessibleQuery to only show service accounts in orgs where user is ADMIN/OWNER
    query = (
        UserAccessibleQuery(current_user.id)
        .service_accounts()
        .where(
            User.organization_memberships.any(
                OrganizationMember.organization_id == organization_id
            )
        )
        .options(
            joinedload(User.organization_memberships),
        )
    )

    # Eagerly load API keys for each service account
    result = await session.execute(query)
    service_accounts = result.unique().scalars().all()

    # Load API keys separately to avoid complex joinedload
    service_account_ids = [sa.id for sa in service_accounts]
    if service_account_ids:
        api_keys_query = select(ApiKey).where(ApiKey.user_id.in_(service_account_ids))
        api_keys_result = await session.execute(api_keys_query)
        all_api_keys = api_keys_result.scalars().all()

        # Group API keys by user_id
        api_keys_by_user = {}
        for key in all_api_keys:
            if key.user_id not in api_keys_by_user:
                api_keys_by_user[key.user_id] = []
            api_keys_by_user[key.user_id].append(key)
    else:
        api_keys_by_user = {}

    results = []
    for sa in service_accounts:
        # Get the organization_id from the membership
        org_id = None
        for membership in sa.organization_memberships:
            if membership.organization_id == organization_id:
                org_id = membership.organization_id
                break

        api_keys = api_keys_by_user.get(sa.id, [])

        results.append(
            ServiceAccountRead(
                id=sa.id,
                name=sa.first_name or "",
                organization_id=org_id,
                api_keys=[
                    ApiKeyRead(
                        id=key.id,
                        name=key.name,
                        prefix=key.prefix,
                        created_at=key.created_at,
                        last_used_at=key.last_used_at,
                        revoked_at=key.revoked_at,
                    )
                    for key in api_keys
                ],
            )
        )

    return {"results": results}


@router.get("/{service_account_id}", response_model=ServiceAccountRead)
async def get_service_account(
    service_account_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
):
    """Get a single service account by ID."""
    query = (
        UserAccessibleQuery(current_user.id)
        .service_accounts()
        .where(User.id == service_account_id)
        .options(joinedload(User.organization_memberships))
    )
    result = await session.execute(query)
    service_account = result.unique().scalar_one_or_none()

    if not service_account:
        raise HTTPException(status_code=404, detail="Service account not found")

    # Load API keys
    api_keys_query = select(ApiKey).where(ApiKey.user_id == service_account_id)
    api_keys_result = await session.execute(api_keys_query)
    api_keys = api_keys_result.scalars().all()

    # Get organization_id from membership
    org_id = None
    if service_account.organization_memberships:
        org_id = service_account.organization_memberships[0].organization_id

    return ServiceAccountRead(
        id=service_account.id,
        name=service_account.first_name or "",
        organization_id=org_id,
        api_keys=[
            ApiKeyRead(
                id=key.id,
                name=key.name,
                prefix=key.prefix,
                created_at=key.created_at,
                last_used_at=key.last_used_at,
                revoked_at=key.revoked_at,
            )
            for key in api_keys
        ],
    )


@router.patch("/{service_account_id}", response_model=ServiceAccountRead)
async def update_service_account(
    service_account_id: UUID,
    data: ServiceAccountUpdate,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Update a service account's details."""
    query = (
        UserAccessibleQuery(current_user.id)
        .service_accounts()
        .where(User.id == service_account_id)
        .options(joinedload(User.organization_memberships))
    )
    result = await session.execute(query)
    service_account = result.unique().scalar_one_or_none()

    if not service_account:
        raise HTTPException(status_code=404, detail="Service account not found")

    # Update fields
    if data.name is not None:
        service_account.first_name = data.name

    await session.flush()

    # Load API keys
    api_keys_query = select(ApiKey).where(ApiKey.user_id == service_account_id)
    api_keys_result = await session.execute(api_keys_query)
    api_keys = api_keys_result.scalars().all()

    # Get organization_id from membership
    org_id = None
    if service_account.organization_memberships:
        org_id = service_account.organization_memberships[0].organization_id

    logger.info(
        "Updated service account",
        service_account_id=str(service_account.id),
        updated_by=str(current_user.id),
    )

    return ServiceAccountRead(
        id=service_account.id,
        name=service_account.first_name or "",
        organization_id=org_id,
        api_keys=[
            ApiKeyRead(
                id=key.id,
                name=key.name,
                prefix=key.prefix,
                created_at=key.created_at,
                last_used_at=key.last_used_at,
                revoked_at=key.revoked_at,
            )
            for key in api_keys
        ],
    )


@router.delete("/{service_account_id}", response_model=DeleteResponse)
async def delete_service_account(
    service_account_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Delete a service account and all its API keys."""
    query = (
        UserAccessibleQuery(current_user.id)
        .service_accounts()
        .where(User.id == service_account_id)
    )
    result = await session.execute(query)
    service_account = result.scalar_one_or_none()

    if not service_account:
        raise HTTPException(status_code=404, detail="Service account not found")

    await session.delete(service_account)

    logger.info(
        "Deleted service account",
        service_account_id=str(service_account_id),
        deleted_by=str(current_user.id),
    )

    return DeleteResponse(
        success=True, message=f"Service account {service_account_id} deleted"
    )


@router.post("/{service_account_id}/api_keys", response_model=ApiKeyCreatedResponse)
async def create_api_key(
    service_account_id: UUID,
    data: ApiKeyCreate,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Create a new API key for a service account."""
    query = (
        UserAccessibleQuery(current_user.id)
        .service_accounts()
        .where(User.id == service_account_id)
    )
    result = await session.execute(query)
    service_account = result.scalar_one_or_none()

    if not service_account:
        raise HTTPException(status_code=404, detail="Service account not found")

    # Generate a 32-byte cryptographic key
    api_key, prefix = generate_api_key()
    hashed_api_key = hash_api_key(api_key)

    # Create the API key record
    api_key_record = ApiKey(
        name=data.name,
        prefix=prefix,
        hashed_key=hashed_api_key,
        user_id=service_account_id,
    )
    session.add(api_key_record)
    await session.flush()

    logger.info(
        "Created API key",
        api_key_id=str(api_key_record.id),
        service_account_id=str(service_account_id),
        created_by=str(current_user.id),
    )

    # Return the plain API key (this is the ONLY time it will be visible)
    return ApiKeyCreatedResponse(
        id=api_key_record.id,
        name=api_key_record.name,
        prefix=api_key_record.prefix,
        created_at=api_key_record.created_at,
        last_used_at=api_key_record.last_used_at,
        revoked_at=api_key_record.revoked_at,
        api_key=api_key,
    )


@router.post(
    "/{service_account_id}/api_keys/{api_key_id}/revoke", response_model=ApiKeyRead
)
async def revoke_api_key(
    service_account_id: UUID,
    api_key_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Revoke an API key."""
    # First verify the service account is accessible
    sa_query = (
        UserAccessibleQuery(current_user.id)
        .service_accounts()
        .where(User.id == service_account_id)
    )
    sa_result = await session.execute(sa_query)
    service_account = sa_result.scalar_one_or_none()

    if not service_account:
        raise HTTPException(status_code=404, detail="Service account not found")

    # Get the API key
    api_key_query = select(ApiKey).where(
        ApiKey.id == api_key_id, ApiKey.user_id == service_account_id
    )
    api_key_result = await session.execute(api_key_query)
    api_key = api_key_result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")

    if api_key.revoked_at is not None:
        raise HTTPException(status_code=400, detail="API key is already revoked")

    # Revoke the key
    api_key.revoked_at = datetime.now()
    await session.flush()

    logger.info(
        "Revoked API key",
        api_key_id=str(api_key.id),
        service_account_id=str(service_account_id),
        revoked_by=str(current_user.id),
    )

    return ApiKeyRead(
        id=api_key.id,
        name=api_key.name,
        prefix=api_key.prefix,
        created_at=api_key.created_at,
        last_used_at=api_key.last_used_at,
        revoked_at=api_key.revoked_at,
    )
