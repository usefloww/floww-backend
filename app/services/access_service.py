from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import aliased
from sqlalchemy.sql.expression import literal_column

from app.deps.db import SessionDep
from app.models import (
    AccessRole,
    AccessTuple,
    Namespace,
    PrincipleType,
    ResourceType,
    Workflow,
    WorkflowFolder,
)
from app.utils.query_helpers import UserAccessibleQuery

# Role priority for comparison (higher = more permissive)
ROLE_PRIORITY: dict[AccessRole, int] = {
    AccessRole.USER: 1,
    AccessRole.OWNER: 2,
}


def get_higher_role(role1: AccessRole, role2: AccessRole) -> AccessRole:
    return role1 if ROLE_PRIORITY[role1] >= ROLE_PRIORITY[role2] else role2


def role_meets_minimum(role: AccessRole, min_role: AccessRole) -> bool:
    return ROLE_PRIORITY[role] >= ROLE_PRIORITY[min_role]


class ResolvedAccess(BaseModel):
    principal_type: PrincipleType
    principal_id: UUID
    resource_type: ResourceType
    resource_id: UUID
    role: AccessRole
    inherited_from: UUID | None = None

    model_config = {"from_attributes": True}


async def has_workflow_access(
    session: SessionDep, user_id: UUID, workflow_id: UUID
) -> bool:
    query = UserAccessibleQuery(user_id).workflows().where(Workflow.id == workflow_id)
    result = await session.execute(query)
    workflow = result.scalar_one_or_none()
    return workflow is not None


async def has_namespace_access(
    session: SessionDep, user_id: UUID, namespace_id: UUID
) -> bool:
    """Check if user has access to a namespace via ownership or membership."""
    query = (
        UserAccessibleQuery(user_id).namespaces().where(Namespace.id == namespace_id)
    )
    result = await session.execute(query)
    namespace = result.scalar_one_or_none()
    return namespace is not None


class AccessResponse(BaseModel):
    role: AccessRole


def _get_folder_ancestors_cte(folder_id: UUID):
    """
    Build a recursive CTE that returns all ancestor folder IDs for a given folder.
    Returns (folder_id, depth) pairs where depth 0 is the folder itself.
    """
    # Base case: the folder itself
    base = (
        select(
            WorkflowFolder.id.label("folder_id"),
            WorkflowFolder.parent_folder_id,
            literal_column("0").label("depth"),
        )
        .where(WorkflowFolder.id == folder_id)
        .cte(name="folder_ancestors", recursive=True)
    )

    # Recursive case: parent folders
    parent = aliased(WorkflowFolder)
    recursive = select(
        parent.id.label("folder_id"),
        parent.parent_folder_id,
        (base.c.depth + 1).label("depth"),
    ).where(parent.id == base.c.parent_folder_id)

    return base.union_all(recursive)


def _get_folder_descendants_cte(folder_id: UUID):
    """
    Build a recursive CTE that returns all descendant folder IDs for a given folder.
    Returns (folder_id, depth) pairs where depth 0 is the folder itself.
    """
    # Base case: the folder itself
    base = (
        select(
            WorkflowFolder.id.label("folder_id"),
            literal_column("0").label("depth"),
        )
        .where(WorkflowFolder.id == folder_id)
        .cte(name="folder_descendants", recursive=True)
    )

    # Recursive case: child folders
    child = aliased(WorkflowFolder)
    recursive = select(
        child.id.label("folder_id"),
        (base.c.depth + 1).label("depth"),
    ).where(child.parent_folder_id == base.c.folder_id)

    return base.union_all(recursive)


async def get_accessible_resources(
    session: SessionDep,
    principal_type: PrincipleType,
    principal_id: UUID,
    resource_type: ResourceType | None = None,
    min_role: AccessRole | None = None,
    expand_hierarchy: bool = False,
) -> list[ResolvedAccess]:
    """
    Get all resources this principal can access.

    When expand_hierarchy=False: Returns only direct access grants.
    When expand_hierarchy=True: Also includes workflows/folders inherited through folder access.
    """
    # Query direct access grants
    query = select(AccessTuple).where(
        AccessTuple.principle_type == principal_type,
        AccessTuple.principle_id == principal_id,
    )

    if resource_type is not None:
        query = query.where(AccessTuple.resource_type == resource_type)

    result = await session.execute(query)
    access_tuples = result.scalars().all()

    # Build results from direct access
    results: dict[tuple[ResourceType, UUID], ResolvedAccess] = {}

    for at in access_tuples:
        if min_role and not role_meets_minimum(at.role, min_role):
            continue

        key = (at.resource_type, at.resource_id)
        if (
            key not in results
            or ROLE_PRIORITY[at.role] > ROLE_PRIORITY[results[key].role]
        ):
            results[key] = ResolvedAccess(
                principal_type=principal_type,
                principal_id=principal_id,
                resource_type=at.resource_type,
                resource_id=at.resource_id,
                role=at.role,
                inherited_from=None,
            )

    if not expand_hierarchy:
        return list(results.values())

    # Expand folder access to include contained workflows and nested folders
    folder_accesses = [
        ra for ra in results.values() if ra.resource_type == ResourceType.FOLDER
    ]

    for folder_access in folder_accesses:
        # Get all descendant folders
        descendants_cte = _get_folder_descendants_cte(folder_access.resource_id)
        descendant_folders_query = select(descendants_cte.c.folder_id).where(
            descendants_cte.c.depth > 0  # Exclude the folder itself
        )
        desc_result = await session.execute(descendant_folders_query)
        descendant_folder_ids = [row[0] for row in desc_result.fetchall()]

        # Add descendant folders as inherited access (if not filtered out by resource_type)
        if resource_type is None or resource_type == ResourceType.FOLDER:
            for folder_id in descendant_folder_ids:
                key = (ResourceType.FOLDER, folder_id)
                if (
                    key not in results
                    or ROLE_PRIORITY[folder_access.role]
                    > ROLE_PRIORITY[results[key].role]
                ):
                    if min_role is None or role_meets_minimum(
                        folder_access.role, min_role
                    ):
                        results[key] = ResolvedAccess(
                            principal_type=principal_type,
                            principal_id=principal_id,
                            resource_type=ResourceType.FOLDER,
                            resource_id=folder_id,
                            role=folder_access.role,
                            inherited_from=folder_access.resource_id,
                        )

        # Get workflows in the folder and all descendant folders
        if resource_type is None or resource_type == ResourceType.WORKFLOW:
            all_folder_ids = [folder_access.resource_id] + descendant_folder_ids
            workflows_query = select(Workflow.id).where(
                Workflow.parent_folder_id.in_(all_folder_ids)
            )
            wf_result = await session.execute(workflows_query)
            workflow_ids = [row[0] for row in wf_result.fetchall()]

            for workflow_id in workflow_ids:
                key = (ResourceType.WORKFLOW, workflow_id)
                if (
                    key not in results
                    or ROLE_PRIORITY[folder_access.role]
                    > ROLE_PRIORITY[results[key].role]
                ):
                    if min_role is None or role_meets_minimum(
                        folder_access.role, min_role
                    ):
                        results[key] = ResolvedAccess(
                            principal_type=principal_type,
                            principal_id=principal_id,
                            resource_type=ResourceType.WORKFLOW,
                            resource_id=workflow_id,
                            role=folder_access.role,
                            inherited_from=folder_access.resource_id,
                        )

    return list(results.values())


async def get_resource_principals(
    session: SessionDep,
    resource_type: ResourceType,
    resource_id: UUID,
    principal_type: PrincipleType | None = None,
    min_role: AccessRole | None = None,
) -> list[ResolvedAccess]:
    """
    Get all principals that have access to this resource.

    For workflows/folders: also considers inherited access through parent folder hierarchy.
    """
    results: dict[tuple[PrincipleType, UUID], ResolvedAccess] = {}

    # Direct access to this resource
    query = select(AccessTuple).where(
        AccessTuple.resource_type == resource_type,
        AccessTuple.resource_id == resource_id,
    )

    if principal_type is not None:
        query = query.where(AccessTuple.principle_type == principal_type)

    result = await session.execute(query)
    access_tuples = result.scalars().all()

    for at in access_tuples:
        if min_role and not role_meets_minimum(at.role, min_role):
            continue

        key = (at.principle_type, at.principle_id)
        if (
            key not in results
            or ROLE_PRIORITY[at.role] > ROLE_PRIORITY[results[key].role]
        ):
            results[key] = ResolvedAccess(
                principal_type=at.principle_type,
                principal_id=at.principle_id,
                resource_type=resource_type,
                resource_id=resource_id,
                role=at.role,
                inherited_from=None,
            )

    # For workflows: check access via parent folder hierarchy
    if resource_type == ResourceType.WORKFLOW:
        # Get the workflow's parent folder
        wf_result = await session.execute(
            select(Workflow.parent_folder_id).where(Workflow.id == resource_id)
        )
        parent_folder_id = wf_result.scalar_one_or_none()

        if parent_folder_id:
            # Get all ancestor folders
            ancestors_cte = _get_folder_ancestors_cte(parent_folder_id)
            ancestors_query = select(ancestors_cte.c.folder_id)
            anc_result = await session.execute(ancestors_query)
            ancestor_folder_ids = [row[0] for row in anc_result.fetchall()]

            # Get principals with access to any ancestor folder
            folder_access_query = select(AccessTuple).where(
                AccessTuple.resource_type == ResourceType.FOLDER,
                AccessTuple.resource_id.in_(ancestor_folder_ids),
            )
            if principal_type is not None:
                folder_access_query = folder_access_query.where(
                    AccessTuple.principle_type == principal_type
                )

            folder_result = await session.execute(folder_access_query)
            folder_access_tuples = folder_result.scalars().all()

            for at in folder_access_tuples:
                if min_role and not role_meets_minimum(at.role, min_role):
                    continue

                key = (at.principle_type, at.principle_id)
                if (
                    key not in results
                    or ROLE_PRIORITY[at.role] > ROLE_PRIORITY[results[key].role]
                ):
                    results[key] = ResolvedAccess(
                        principal_type=at.principle_type,
                        principal_id=at.principle_id,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        role=at.role,
                        inherited_from=at.resource_id,
                    )

    # For folders: check access via parent folder hierarchy
    elif resource_type == ResourceType.FOLDER:
        ancestors_cte = _get_folder_ancestors_cte(resource_id)
        # Exclude the folder itself (depth > 0)
        ancestors_query = select(ancestors_cte.c.folder_id).where(
            ancestors_cte.c.depth > 0
        )
        anc_result = await session.execute(ancestors_query)
        ancestor_folder_ids = [row[0] for row in anc_result.fetchall()]

        if ancestor_folder_ids:
            folder_access_query = select(AccessTuple).where(
                AccessTuple.resource_type == ResourceType.FOLDER,
                AccessTuple.resource_id.in_(ancestor_folder_ids),
            )
            if principal_type is not None:
                folder_access_query = folder_access_query.where(
                    AccessTuple.principle_type == principal_type
                )

            folder_result = await session.execute(folder_access_query)
            folder_access_tuples = folder_result.scalars().all()

            for at in folder_access_tuples:
                if min_role and not role_meets_minimum(at.role, min_role):
                    continue

                key = (at.principle_type, at.principle_id)
                if (
                    key not in results
                    or ROLE_PRIORITY[at.role] > ROLE_PRIORITY[results[key].role]
                ):
                    results[key] = ResolvedAccess(
                        principal_type=at.principle_type,
                        principal_id=at.principle_id,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        role=at.role,
                        inherited_from=at.resource_id,
                    )

    return list(results.values())


async def get_resolved_access(
    session: SessionDep,
    principal_type: PrincipleType,
    principal_id: UUID,
    resource_type: ResourceType,
    resource_id: UUID,
) -> AccessRole | None:
    """
    Get the effective role for principal->resource, combining:
    - Direct access
    - Inherited access via folder hierarchy
    Returns highest role or None if no access.
    """
    highest_role: AccessRole | None = None

    # Check direct access
    direct_query = select(AccessTuple).where(
        AccessTuple.principle_type == principal_type,
        AccessTuple.principle_id == principal_id,
        AccessTuple.resource_type == resource_type,
        AccessTuple.resource_id == resource_id,
    )
    direct_result = await session.execute(direct_query)
    direct_access = direct_result.scalar_one_or_none()

    if direct_access:
        highest_role = direct_access.role

    # For workflows: check access via parent folder hierarchy
    if resource_type == ResourceType.WORKFLOW:
        wf_result = await session.execute(
            select(Workflow.parent_folder_id).where(Workflow.id == resource_id)
        )
        parent_folder_id = wf_result.scalar_one_or_none()

        if parent_folder_id:
            ancestors_cte = _get_folder_ancestors_cte(parent_folder_id)
            ancestors_query = select(ancestors_cte.c.folder_id)
            anc_result = await session.execute(ancestors_query)
            ancestor_folder_ids = [row[0] for row in anc_result.fetchall()]

            folder_access_query = select(AccessTuple.role).where(
                AccessTuple.principle_type == principal_type,
                AccessTuple.principle_id == principal_id,
                AccessTuple.resource_type == ResourceType.FOLDER,
                AccessTuple.resource_id.in_(ancestor_folder_ids),
            )
            folder_result = await session.execute(folder_access_query)
            folder_roles = folder_result.scalars().all()

            for role in folder_roles:
                if (
                    highest_role is None
                    or ROLE_PRIORITY[role] > ROLE_PRIORITY[highest_role]
                ):
                    highest_role = role

    # For folders: check access via parent folder hierarchy
    elif resource_type == ResourceType.FOLDER:
        ancestors_cte = _get_folder_ancestors_cte(resource_id)
        # Exclude the folder itself (depth > 0)
        ancestors_query = select(ancestors_cte.c.folder_id).where(
            ancestors_cte.c.depth > 0
        )
        anc_result = await session.execute(ancestors_query)
        ancestor_folder_ids = [row[0] for row in anc_result.fetchall()]

        if ancestor_folder_ids:
            folder_access_query = select(AccessTuple.role).where(
                AccessTuple.principle_type == principal_type,
                AccessTuple.principle_id == principal_id,
                AccessTuple.resource_type == ResourceType.FOLDER,
                AccessTuple.resource_id.in_(ancestor_folder_ids),
            )
            folder_result = await session.execute(folder_access_query)
            folder_roles = folder_result.scalars().all()

            for role in folder_roles:
                if (
                    highest_role is None
                    or ROLE_PRIORITY[role] > ROLE_PRIORITY[highest_role]
                ):
                    highest_role = role

    return highest_role
