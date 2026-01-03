from typing import Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.deps.auth import CurrentUser
from app.deps.db import SessionDep, TransactionSessionDep
from app.models import Namespace, WorkflowFolder
from app.services.crud_helpers import CrudHelper, DeleteResponse, ListResult
from app.utils.query_helpers import UserAccessibleQuery

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/folders", tags=["Folders"])


class FolderRead(BaseModel):
    id: UUID
    namespace_id: UUID
    name: str
    parent_folder_id: Optional[UUID] = None


class FolderCreate(BaseModel):
    namespace_id: UUID
    name: str
    parent_folder_id: Optional[UUID] = None


class FolderUpdate(BaseModel):
    name: Optional[str] = None
    parent_folder_id: Optional[UUID] = None


class FolderWithPath(FolderRead):
    path: list[FolderRead]


def helper_factory(user: CurrentUser, session: SessionDep):
    return CrudHelper(
        session=session,
        resource_name="folder",
        database_model=WorkflowFolder,
        read_model=FolderRead,
        create_model=FolderCreate,
        update_model=FolderUpdate,
        query_builder=lambda: UserAccessibleQuery(user.id).folders(),
    )


@router.get("")
async def list_folders(
    current_user: CurrentUser,
    session: SessionDep,
    namespace_id: Optional[UUID] = None,
    parent_folder_id: Optional[UUID] = None,
):
    """
    List folders accessible to the authenticated user.

    - If namespace_id is provided, filters to that namespace
    - If parent_folder_id is provided, filters to children of that folder
    - If parent_folder_id is not provided, returns root folders (where parent_folder_id is NULL)
    """
    query = UserAccessibleQuery(current_user.id).folders()

    if namespace_id:
        query = query.where(WorkflowFolder.namespace_id == namespace_id)

    if parent_folder_id:
        query = query.where(WorkflowFolder.parent_folder_id == parent_folder_id)
    else:
        # Return root folders only (no parent)
        query = query.where(WorkflowFolder.parent_folder_id.is_(None))

    result = await session.execute(query)
    folders = result.scalars().all()

    folder_reads = [
        FolderRead.model_validate(folder, from_attributes=True) for folder in folders
    ]

    return ListResult(results=folder_reads)


@router.post("")
async def create_folder(
    data: FolderCreate,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Create a new folder."""
    # Verify user has access to the namespace
    namespace_query = (
        UserAccessibleQuery(current_user.id)
        .namespaces()
        .where(Namespace.id == data.namespace_id)
    )
    namespace_result = await session.execute(namespace_query)
    namespace = namespace_result.scalar_one_or_none()

    if not namespace:
        raise HTTPException(status_code=400, detail="Namespace not found")

    # If parent_folder_id is provided, verify it exists and is in the same namespace
    if data.parent_folder_id:
        parent_query = (
            UserAccessibleQuery(current_user.id)
            .folders()
            .where(WorkflowFolder.id == data.parent_folder_id)
        )
        parent_result = await session.execute(parent_query)
        parent_folder = parent_result.scalar_one_or_none()

        if not parent_folder:
            raise HTTPException(status_code=400, detail="Parent folder not found")

        if parent_folder.namespace_id != data.namespace_id:
            raise HTTPException(
                status_code=400,
                detail="Parent folder must be in the same namespace",
            )

    folder = WorkflowFolder(
        name=data.name,
        namespace_id=data.namespace_id,
        parent_folder_id=data.parent_folder_id,
    )

    session.add(folder)
    await session.flush()

    logger.info("Created new folder", folder_id=str(folder.id), name=folder.name)

    return FolderRead.model_validate(folder, from_attributes=True)


@router.get("/{folder_id}")
async def get_folder(
    folder_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
):
    """Get a specific folder."""
    helper = helper_factory(current_user, session)
    return await helper.get_response(folder_id)


@router.get("/{folder_id}/path")
async def get_folder_path(
    folder_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
):
    """
    Get the full path from root to this folder.
    Returns the folder with its ancestor chain.
    """
    # Verify access to folder
    query = (
        UserAccessibleQuery(current_user.id)
        .folders()
        .where(WorkflowFolder.id == folder_id)
    )
    result = await session.execute(query)
    folder = result.scalar_one_or_none()

    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    # Build path by walking up the parent chain
    path: list[FolderRead] = []
    current = folder

    while current:
        path.insert(0, FolderRead.model_validate(current, from_attributes=True))

        if current.parent_folder_id:
            parent_result = await session.execute(
                select(WorkflowFolder).where(
                    WorkflowFolder.id == current.parent_folder_id
                )
            )
            current = parent_result.scalar_one_or_none()
        else:
            current = None

    return FolderWithPath(
        id=folder.id,
        namespace_id=folder.namespace_id,
        name=folder.name,
        parent_folder_id=folder.parent_folder_id,
        path=path,
    )


@router.patch("/{folder_id}")
async def update_folder(
    folder_id: UUID,
    data: FolderUpdate,
    current_user: CurrentUser,
    session: TransactionSessionDep,
):
    """Update a folder (rename or move)."""
    # Verify access
    query = (
        UserAccessibleQuery(current_user.id)
        .folders()
        .where(WorkflowFolder.id == folder_id)
    )
    result = await session.execute(query)
    folder = result.scalar_one_or_none()

    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    # If moving to a new parent, validate the move
    if data.parent_folder_id is not None:
        # Cannot move folder to itself
        if data.parent_folder_id == folder_id:
            raise HTTPException(status_code=400, detail="Cannot move folder to itself")

        # Verify new parent exists and is accessible
        parent_query = (
            UserAccessibleQuery(current_user.id)
            .folders()
            .where(WorkflowFolder.id == data.parent_folder_id)
        )
        parent_result = await session.execute(parent_query)
        parent_folder = parent_result.scalar_one_or_none()

        if not parent_folder:
            raise HTTPException(status_code=400, detail="Parent folder not found")

        if parent_folder.namespace_id != folder.namespace_id:
            raise HTTPException(
                status_code=400,
                detail="Parent folder must be in the same namespace",
            )

        # Check for circular reference (parent cannot be a descendant of this folder)
        current_parent = parent_folder
        while current_parent:
            if current_parent.id == folder_id:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot move folder into its own descendant",
                )
            if current_parent.parent_folder_id:
                parent_check = await session.execute(
                    select(WorkflowFolder).where(
                        WorkflowFolder.id == current_parent.parent_folder_id
                    )
                )
                current_parent = parent_check.scalar_one_or_none()
            else:
                current_parent = None

    # Apply updates
    if data.name is not None:
        folder.name = data.name
    if data.parent_folder_id is not None:
        folder.parent_folder_id = data.parent_folder_id

    session.add(folder)
    await session.flush()
    await session.refresh(folder)

    logger.info("Updated folder", folder_id=str(folder.id), name=folder.name)

    return FolderRead.model_validate(folder, from_attributes=True)


@router.delete("/{folder_id}")
async def delete_folder(
    folder_id: UUID,
    current_user: CurrentUser,
    session: TransactionSessionDep,
) -> DeleteResponse:
    """Delete a folder. This will cascade delete all contents."""
    helper = helper_factory(current_user, session)
    return await helper.delete_response(folder_id)
