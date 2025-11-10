"""
KV-store routes with table-based organization and permissions.

Workflows can store and retrieve key-value pairs organized into tables.
Each table has permissions controlling which workflows can read/write.
"""

import re
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert

from app.deps.db import SessionDep, TransactionSessionDep
from app.deps.workflow_auth import WorkflowContextDep
from app.models import KeyValueItem, KeyValueTable, KeyValueTablePermission, Provider

router = APIRouter(tags=["kv-store"])

# Validation constants
KEY_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
TABLE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
MAX_KEY_LENGTH = 255
MAX_TABLE_NAME_LENGTH = 255
MAX_VALUE_SIZE = 1_000_000  # 1MB in bytes (approximate JSON size)


# Pydantic Schemas
class SetValueRequest(BaseModel):
    value: Any = Field(..., description="The value to store (any JSON-serializable data)")

    @field_validator("value")
    @classmethod
    def validate_value_size(cls, v: Any) -> Any:
        import json

        value_str = json.dumps(v)
        if len(value_str) > MAX_VALUE_SIZE:
            raise ValueError(
                f"Value size ({len(value_str)} bytes) exceeds maximum ({MAX_VALUE_SIZE} bytes)"
            )
        return v


class KeyValueResponse(BaseModel):
    key: str
    value: Any
    created_at: str
    updated_at: str


class TableListResponse(BaseModel):
    tables: list[str]


class KeyListResponse(BaseModel):
    keys: list[str]


class KeysWithValuesResponse(BaseModel):
    items: list[KeyValueResponse]


class GrantPermissionRequest(BaseModel):
    workflow_id: UUID
    can_read: bool = True
    can_write: bool = False


class PermissionResponse(BaseModel):
    workflow_id: UUID
    can_read: bool
    can_write: bool
    created_at: str


# Helper functions
async def get_or_create_kv_provider(
    session: TransactionSessionDep,
    namespace_id: UUID,
    provider_credential: str,
) -> Provider:
    """
    Get or create a KV provider instance.
    Provider credentials act as namespaces for KV storage.
    """
    # Try to get existing provider
    stmt = select(Provider).where(
        and_(
            Provider.namespace_id == namespace_id,
            Provider.type == "kvstore",
            Provider.alias == provider_credential,
        )
    )
    result = await session.execute(stmt)
    provider = result.scalar_one_or_none()

    if provider:
        return provider

    # Auto-create provider (empty config for KV)
    from app.services.providers.provider_utils import encrypt_provider_config

    provider = Provider(
        namespace_id=namespace_id,
        type="kvstore",
        alias=provider_credential,
        encrypted_config=encrypt_provider_config({}),
    )
    session.add(provider)
    await session.flush()

    return provider


def validate_table_name(name: str) -> None:
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Table name cannot be empty"
        )
    if len(name) > MAX_TABLE_NAME_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Table name too long (max {MAX_TABLE_NAME_LENGTH} characters)",
        )
    if not TABLE_NAME_PATTERN.match(name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Table name must contain only alphanumeric characters, underscores, and hyphens",
        )


def validate_key(key: str) -> None:
    if not key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Key cannot be empty"
        )
    if len(key) > MAX_KEY_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Key too long (max {MAX_KEY_LENGTH} characters)",
        )
    if not KEY_PATTERN.match(key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Key must contain only alphanumeric characters, underscores, and hyphens",
        )


async def get_table_with_permission(
    session: SessionDep,
    provider_id: UUID,
    table_name: str,
    workflow_id: UUID,
    require_read: bool = False,
    require_write: bool = False,
) -> tuple[KeyValueTable, Optional[KeyValueTablePermission]]:
    """
    Get a table and check permissions.
    Returns (table, permission) tuple.
    Raises 404 if table doesn't exist, 403 if permission denied.
    """
    # Get table
    stmt = select(KeyValueTable).where(
        and_(
            KeyValueTable.provider_id == provider_id,
            KeyValueTable.name == table_name,
        )
    )
    result = await session.execute(stmt)
    table = result.scalar_one_or_none()

    if not table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Table '{table_name}' not found",
        )

    # Get permission
    perm_stmt = select(KeyValueTablePermission).where(
        and_(
            KeyValueTablePermission.table_id == table.id,
            KeyValueTablePermission.workflow_id == workflow_id,
        )
    )
    perm_result = await session.execute(perm_stmt)
    permission = perm_result.scalar_one_or_none()

    # Check permissions
    if require_read and (not permission or not permission.can_read):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No read permission for table '{table_name}'",
        )

    if require_write and (not permission or not permission.can_write):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No write permission for table '{table_name}'",
        )

    return table, permission


async def get_or_create_table(
    session: TransactionSessionDep,
    provider_id: UUID,
    table_name: str,
    workflow_id: UUID,
) -> KeyValueTable:
    """
    Get or create a table. If created, grants full permissions to the creating workflow.
    """
    # Try to get existing table
    stmt = select(KeyValueTable).where(
        and_(
            KeyValueTable.provider_id == provider_id,
            KeyValueTable.name == table_name,
        )
    )
    result = await session.execute(stmt)
    table = result.scalar_one_or_none()

    if table:
        return table

    # Create new table
    table = KeyValueTable(
        provider_id=provider_id,
        name=table_name,
    )
    session.add(table)
    await session.flush()

    # Grant full permissions to creating workflow
    permission = KeyValueTablePermission(
        table_id=table.id, workflow_id=workflow_id, can_read=True, can_write=True
    )
    session.add(permission)
    await session.flush()

    return table


# CRUD Endpoints


@router.get("/kv")
async def list_tables(
    ctx: WorkflowContextDep,
    session: TransactionSessionDep,
    x_kv_provider: str = Header(alias="X-KV-Provider"),
) -> TableListResponse:
    """
    List all tables in the KV provider namespace that this workflow has access to.
    """
    # Get or create the KV provider
    provider = await get_or_create_kv_provider(session, ctx.namespace_id, x_kv_provider)

    stmt = (
        select(KeyValueTable.name)
        .join(KeyValueTablePermission)
        .where(
            and_(
                KeyValueTable.provider_id == provider.id,
                KeyValueTablePermission.workflow_id == ctx.workflow_id,
            )
        )
        .order_by(KeyValueTable.name)
    )
    result = await session.execute(stmt)
    tables = [row[0] for row in result.all()]

    return TableListResponse(tables=tables)


# Permission Management Endpoints
# Note: These must be defined BEFORE the /kv/{table}/{key} endpoint to match first


@router.get("/kv/permissions/{table}")
async def list_permissions(
    table: str,
    ctx: WorkflowContextDep,
    session: TransactionSessionDep,
    x_kv_provider: str = Header(alias="X-KV-Provider"),
) -> list[PermissionResponse]:
    """
    List all permissions for a table. Requires read access to the table.
    """
    validate_table_name(table)

    # Get or create the KV provider
    provider = await get_or_create_kv_provider(session, ctx.namespace_id, x_kv_provider)

    # Check table and read permission
    kv_table, _ = await get_table_with_permission(
        session, provider.id, table, ctx.workflow_id, require_read=True
    )

    # Get all permissions
    stmt = select(KeyValueTablePermission).where(
        KeyValueTablePermission.table_id == kv_table.id
    )
    result = await session.execute(stmt)
    permissions = result.scalars().all()

    return [
        PermissionResponse(
            workflow_id=perm.workflow_id,
            can_read=perm.can_read,
            can_write=perm.can_write,
            created_at=perm.created_at.isoformat(),
        )
        for perm in permissions
    ]


@router.post("/kv/permissions/{table}")
async def grant_permission(
    table: str,
    request: GrantPermissionRequest,
    ctx: WorkflowContextDep,
    session: TransactionSessionDep,
    x_kv_provider: str = Header(alias="X-KV-Provider"),
) -> PermissionResponse:
    """
    Grant read/write permissions to a workflow for a table.
    Requires write access to the table.
    """
    validate_table_name(table)

    # Get or create the KV provider
    provider = await get_or_create_kv_provider(session, ctx.namespace_id, x_kv_provider)

    # Check table and write permission
    kv_table, _ = await get_table_with_permission(
        session, provider.id, table, ctx.workflow_id, require_write=True
    )

    # Verify target workflow exists and is in same namespace
    from app.models import Workflow

    workflow_stmt = select(Workflow).where(
        and_(
            Workflow.id == request.workflow_id,
            Workflow.namespace_id == ctx.namespace_id,
        )
    )
    workflow_result = await session.execute(workflow_stmt)
    if not workflow_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {request.workflow_id} not found in this namespace",
        )

    # Upsert permission
    stmt = (
        insert(KeyValueTablePermission)
        .values(
            table_id=kv_table.id,
            workflow_id=request.workflow_id,
            can_read=request.can_read,
            can_write=request.can_write,
        )
        .on_conflict_do_update(
            index_elements=["table_id", "workflow_id"],
            set_={"can_read": request.can_read, "can_write": request.can_write},
        )
        .returning(KeyValueTablePermission)
    )
    result = await session.execute(stmt)
    permission = result.scalar_one()
    await session.flush()

    return PermissionResponse(
        workflow_id=permission.workflow_id,
        can_read=permission.can_read,
        can_write=permission.can_write,
        created_at=permission.created_at.isoformat(),
    )


@router.delete("/kv/permissions/{table}/{workflow_id}")
async def revoke_permission(
    table: str,
    workflow_id: UUID,
    ctx: WorkflowContextDep,
    session: TransactionSessionDep,
    x_kv_provider: str = Header(alias="X-KV-Provider"),
) -> dict:
    """
    Revoke a workflow's permissions for a table.
    Requires write access to the table.
    """
    validate_table_name(table)

    # Get or create the KV provider
    provider = await get_or_create_kv_provider(session, ctx.namespace_id, x_kv_provider)

    # Check table and write permission
    kv_table, _ = await get_table_with_permission(
        session, provider.id, table, ctx.workflow_id, require_write=True
    )

    # Get permission
    stmt = select(KeyValueTablePermission).where(
        and_(
            KeyValueTablePermission.table_id == kv_table.id,
            KeyValueTablePermission.workflow_id == workflow_id,
        )
    )
    result = await session.execute(stmt)
    permission = result.scalar_one_or_none()

    if not permission:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No permission found for workflow {workflow_id} on table '{table}'",
        )

    await session.delete(permission)
    await session.flush()

    return {
        "message": "Permission revoked successfully",
        "table": table,
        "workflow_id": str(workflow_id),
    }


# Table/Key CRUD operations - these come after permission routes


@router.get("/kv/{table}")
async def list_keys(
    table: str,
    ctx: WorkflowContextDep,
    session: TransactionSessionDep,
    include_values: bool = False,
    x_kv_provider: str = Header(alias="X-KV-Provider"),
) -> KeyListResponse | KeysWithValuesResponse:
    """
    List all keys in a table. Optionally include values with ?include_values=true.
    """
    validate_table_name(table)

    # Get or create the KV provider
    provider = await get_or_create_kv_provider(session, ctx.namespace_id, x_kv_provider)

    # Check table and read permission
    kv_table, _ = await get_table_with_permission(
        session, provider.id, table, ctx.workflow_id, require_read=True
    )

    if include_values:
        # Return keys with values
        stmt = (
            select(KeyValueItem)
            .where(KeyValueItem.table_id == kv_table.id)
            .order_by(KeyValueItem.key)
        )
        result = await session.execute(stmt)
        items = result.scalars().all()

        return KeysWithValuesResponse(
            items=[
                KeyValueResponse(
                    key=item.key,
                    value=item.value,
                    created_at=item.created_at.isoformat(),
                    updated_at=item.updated_at.isoformat(),
                )
                for item in items
            ]
        )
    else:
        # Return only keys
        stmt = (
            select(KeyValueItem.key)
            .where(KeyValueItem.table_id == kv_table.id)
            .order_by(KeyValueItem.key)
        )
        result = await session.execute(stmt)
        keys = [row[0] for row in result.all()]

        return KeyListResponse(keys=keys)


@router.get("/kv/{table}/{key}")
async def get_value(
    table: str,
    key: str,
    ctx: WorkflowContextDep,
    session: TransactionSessionDep,
    x_kv_provider: str = Header(alias="X-KV-Provider"),
) -> KeyValueResponse:
    """
    Get a value from the KV store.
    """
    validate_table_name(table)
    validate_key(key)

    # Get or create the KV provider
    provider = await get_or_create_kv_provider(session, ctx.namespace_id, x_kv_provider)

    # Check table and read permission
    kv_table, _ = await get_table_with_permission(
        session, provider.id, table, ctx.workflow_id, require_read=True
    )

    # Get item
    stmt = select(KeyValueItem).where(
        and_(KeyValueItem.table_id == kv_table.id, KeyValueItem.key == key)
    )
    result = await session.execute(stmt)
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Key '{key}' not found in table '{table}'",
        )

    return KeyValueResponse(
        key=item.key,
        value=item.value,
        created_at=item.created_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
    )


@router.put("/kv/{table}/{key}")
async def set_value(
    table: str,
    key: str,
    request: SetValueRequest,
    ctx: WorkflowContextDep,
    session: TransactionSessionDep,
    x_kv_provider: str = Header(alias="X-KV-Provider"),
) -> KeyValueResponse:
    """
    Set a value in the KV store. Creates the table if it doesn't exist.
    """
    validate_table_name(table)
    validate_key(key)

    # Get or create the KV provider
    provider = await get_or_create_kv_provider(session, ctx.namespace_id, x_kv_provider)

    # Get or create table (auto-grants permissions if created)
    kv_table = await get_or_create_table(
        session, provider.id, table, ctx.workflow_id
    )

    # Check write permission (will have it if just created)
    perm_stmt = select(KeyValueTablePermission).where(
        and_(
            KeyValueTablePermission.table_id == kv_table.id,
            KeyValueTablePermission.workflow_id == ctx.workflow_id,
        )
    )
    perm_result = await session.execute(perm_stmt)
    permission = perm_result.scalar_one_or_none()

    if not permission or not permission.can_write:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No write permission for table '{table}'",
        )

    # Upsert item using PostgreSQL INSERT ... ON CONFLICT
    stmt = (
        insert(KeyValueItem)
        .values(table_id=kv_table.id, key=key, value=request.value)
        .on_conflict_do_update(
            index_elements=["table_id", "key"],
            set_={"value": request.value, "updated_at": KeyValueItem.updated_at},
        )
        .returning(KeyValueItem)
    )
    result = await session.execute(stmt)
    item = result.scalar_one()
    await session.flush()

    # Refresh to get updated timestamps
    await session.refresh(item)

    return KeyValueResponse(
        key=item.key,
        value=item.value,
        created_at=item.created_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
    )


@router.delete("/kv/{table}/{key}")
async def delete_value(
    table: str,
    key: str,
    ctx: WorkflowContextDep,
    session: TransactionSessionDep,
    x_kv_provider: str = Header(alias="X-KV-Provider"),
) -> dict:
    """
    Delete a value from the KV store.
    """
    validate_table_name(table)
    validate_key(key)

    # Get or create the KV provider
    provider = await get_or_create_kv_provider(session, ctx.namespace_id, x_kv_provider)

    # Check table and write permission
    kv_table, _ = await get_table_with_permission(
        session, provider.id, table, ctx.workflow_id, require_write=True
    )

    # Get item
    stmt = select(KeyValueItem).where(
        and_(KeyValueItem.table_id == kv_table.id, KeyValueItem.key == key)
    )
    result = await session.execute(stmt)
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Key '{key}' not found in table '{table}'",
        )

    await session.delete(item)
    await session.flush()

    return {"message": "Key deleted successfully", "key": key, "table": table}
