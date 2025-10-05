from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep
from app.models import Namespace, NamespaceMember, Secret
from app.utils.encryption import decrypt_secret, encrypt_secret

router = APIRouter(prefix="/secrets", tags=["Secrets"])


# Request/Response schemas
class SecretCreate(BaseModel):
    namespace_id: UUID
    name: str
    provider: str
    value: str


class SecretUpdate(BaseModel):
    provider: Optional[str] = None
    value: Optional[str] = None


class SecretResponse(BaseModel):
    id: UUID
    namespace_id: UUID
    name: str
    provider: str
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class SecretWithValueResponse(SecretResponse):
    value: str


async def check_namespace_access(
    session: SessionDep, namespace_id: UUID, user_id: UUID
) -> Namespace:
    """Check if user has access to the namespace and return it."""
    result = await session.execute(
        select(Namespace).where(Namespace.id == namespace_id)
    )
    namespace = result.scalar_one_or_none()

    if not namespace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Namespace not found"
        )

    # Check if user owns the namespace or is a member
    if namespace.user_owner_id == user_id:
        return namespace

    if namespace.organization_owner_id:
        # Check if user is a member of the namespace
        member_result = await session.execute(
            select(NamespaceMember).where(
                NamespaceMember.namespace_id == namespace_id,
                NamespaceMember.user_id == user_id,
            )
        )
        if member_result.scalar_one_or_none():
            return namespace

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You don't have access to this namespace",
    )


@router.post("/", response_model=SecretResponse, status_code=status.HTTP_201_CREATED)
async def create_secret(
    secret_data: SecretCreate, current_user: CurrentUser, session: SessionDep
):
    """Create a new secret in a namespace."""
    # Check namespace access
    await check_namespace_access(session, secret_data.namespace_id, current_user.id)

    # Encrypt the secret value
    encrypted_value = encrypt_secret(secret_data.value)

    # Create the secret
    secret = Secret(
        namespace_id=secret_data.namespace_id,
        name=secret_data.name,
        provider=secret_data.provider,
        encrypted_value=encrypted_value,
    )

    session.add(secret)

    try:
        await session.commit()
        await session.refresh(secret)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Secret with name '{secret_data.name}' already exists in this namespace",
        )

    return SecretResponse(
        id=secret.id,
        namespace_id=secret.namespace_id,
        name=secret.name,
        provider=secret.provider,
        created_at=secret.created_at.isoformat(),
        updated_at=secret.updated_at.isoformat(),
    )


@router.get("/namespace/{namespace_id}", response_model=list[SecretResponse])
async def list_secrets(
    namespace_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
    provider: Optional[str] = None,
    name: Optional[str] = None,
):
    """List all secrets in a namespace (without decrypted values).

    Optionally filter by provider and/or name.
    """
    # Check namespace access
    await check_namespace_access(session, namespace_id, current_user.id)

    # Build query with filters
    query = select(Secret).where(Secret.namespace_id == namespace_id)

    if provider is not None:
        query = query.where(Secret.provider == provider)

    if name is not None:
        query = query.where(Secret.name == name)

    # Query secrets
    result = await session.execute(query)
    secrets = result.scalars().all()

    return [
        SecretResponse(
            id=secret.id,
            namespace_id=secret.namespace_id,
            name=secret.name,
            provider=secret.provider,
            created_at=secret.created_at.isoformat(),
            updated_at=secret.updated_at.isoformat(),
        )
        for secret in secrets
    ]


@router.get("/{secret_id}", response_model=SecretWithValueResponse)
async def get_secret(secret_id: UUID, current_user: CurrentUser, session: SessionDep):
    """Get a specific secret with its decrypted value."""
    # Query the secret
    result = await session.execute(select(Secret).where(Secret.id == secret_id))
    secret = result.scalar_one_or_none()

    if not secret:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret not found"
        )

    # Check namespace access
    await check_namespace_access(session, secret.namespace_id, current_user.id)

    # Decrypt the value
    decrypted_value = decrypt_secret(secret.encrypted_value)

    return SecretWithValueResponse(
        id=secret.id,
        namespace_id=secret.namespace_id,
        name=secret.name,
        provider=secret.provider,
        value=decrypted_value,
        created_at=secret.created_at.isoformat(),
        updated_at=secret.updated_at.isoformat(),
    )


@router.patch("/{secret_id}", response_model=SecretResponse)
async def update_secret(
    secret_id: UUID,
    secret_update: SecretUpdate,
    current_user: CurrentUser,
    session: SessionDep,
):
    """Update a secret's provider or value."""
    # Query the secret
    result = await session.execute(select(Secret).where(Secret.id == secret_id))
    secret = result.scalar_one_or_none()

    if not secret:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret not found"
        )

    # Check namespace access
    await check_namespace_access(session, secret.namespace_id, current_user.id)

    # Update fields
    if secret_update.provider is not None:
        secret.provider = secret_update.provider

    if secret_update.value is not None:
        secret.encrypted_value = encrypt_secret(secret_update.value)

    await session.commit()
    await session.refresh(secret)

    return SecretResponse(
        id=secret.id,
        namespace_id=secret.namespace_id,
        name=secret.name,
        provider=secret.provider,
        created_at=secret.created_at.isoformat(),
        updated_at=secret.updated_at.isoformat(),
    )


@router.delete("/{secret_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_secret(
    secret_id: UUID, current_user: CurrentUser, session: SessionDep
):
    """Delete a secret."""
    # Query the secret
    result = await session.execute(select(Secret).where(Secret.id == secret_id))
    secret = result.scalar_one_or_none()

    if not secret:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret not found"
        )

    # Check namespace access
    await check_namespace_access(session, secret.namespace_id, current_user.id)

    await session.delete(secret)
    await session.commit()

    return None
