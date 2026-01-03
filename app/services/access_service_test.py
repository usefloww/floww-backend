from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AccessRole,
    AccessTuple,
    Namespace,
    Organization,
    PrincipleType,
    ResourceType,
    User,
    Workflow,
    WorkflowFolder,
)
from app.services.access_service import (
    ROLE_PRIORITY,
    get_accessible_resources,
    get_higher_role,
    get_resolved_access,
    get_resource_principals,
    role_meets_minimum,
)

# --- Test Helpers ---


async def create_test_user(session: AsyncSession) -> User:
    user = User(workos_user_id=f"test_user_{uuid4()}", email="test@example.com")
    session.add(user)
    await session.flush()
    return user


async def create_test_namespace(session: AsyncSession) -> Namespace:
    org = Organization(display_name="Test Organization")
    session.add(org)
    await session.flush()

    namespace = Namespace(organization_owner_id=org.id)
    session.add(namespace)
    await session.flush()
    return namespace


async def create_test_folder(
    session: AsyncSession, namespace_id, parent_folder_id=None
) -> WorkflowFolder:
    folder = WorkflowFolder(
        name=f"Folder-{uuid4().hex[:6]}",
        namespace_id=namespace_id,
        parent_folder_id=parent_folder_id,
    )
    session.add(folder)
    await session.flush()
    return folder


async def create_test_workflow(
    session: AsyncSession, namespace_id, parent_folder_id=None
) -> Workflow:
    workflow = Workflow(
        name=f"Workflow-{uuid4().hex[:6]}",
        namespace_id=namespace_id,
        parent_folder_id=parent_folder_id,
    )
    session.add(workflow)
    await session.flush()
    return workflow


async def grant_access(
    session: AsyncSession,
    principal_type: PrincipleType,
    principal_id,
    resource_type: ResourceType,
    resource_id,
    role: AccessRole,
) -> AccessTuple:
    access = AccessTuple(
        principle_type=principal_type,
        principle_id=principal_id,
        resource_type=resource_type,
        resource_id=resource_id,
        role=role,
    )
    session.add(access)
    await session.flush()
    return access


# --- Unit Tests for Utility Functions ---


def test_role_priority_ordering():
    assert ROLE_PRIORITY[AccessRole.OWNER] > ROLE_PRIORITY[AccessRole.USER]


def test_get_higher_role():
    assert get_higher_role(AccessRole.OWNER, AccessRole.USER) == AccessRole.OWNER
    assert get_higher_role(AccessRole.USER, AccessRole.OWNER) == AccessRole.OWNER
    assert get_higher_role(AccessRole.USER, AccessRole.USER) == AccessRole.USER


def test_role_meets_minimum():
    assert role_meets_minimum(AccessRole.OWNER, AccessRole.USER) is True
    assert role_meets_minimum(AccessRole.OWNER, AccessRole.OWNER) is True
    assert role_meets_minimum(AccessRole.USER, AccessRole.OWNER) is False
    assert role_meets_minimum(AccessRole.USER, AccessRole.USER) is True


# --- Integration Tests for get_accessible_resources ---


@pytest.mark.asyncio
async def test_get_accessible_resources_direct_access(session: AsyncSession):
    user = await create_test_user(session)
    namespace = await create_test_namespace(session)
    workflow = await create_test_workflow(session, namespace.id)

    await grant_access(
        session,
        PrincipleType.USER,
        user.id,
        ResourceType.WORKFLOW,
        workflow.id,
        AccessRole.USER,
    )

    results = await get_accessible_resources(
        session, PrincipleType.USER, user.id, expand_hierarchy=False
    )

    assert len(results) == 1
    assert results[0].resource_type == ResourceType.WORKFLOW
    assert results[0].resource_id == workflow.id
    assert results[0].role == AccessRole.USER
    assert results[0].inherited_from is None


@pytest.mark.asyncio
async def test_get_accessible_resources_with_resource_type_filter(
    session: AsyncSession,
):
    user = await create_test_user(session)
    namespace = await create_test_namespace(session)
    workflow = await create_test_workflow(session, namespace.id)
    folder = await create_test_folder(session, namespace.id)

    await grant_access(
        session,
        PrincipleType.USER,
        user.id,
        ResourceType.WORKFLOW,
        workflow.id,
        AccessRole.USER,
    )
    await grant_access(
        session,
        PrincipleType.USER,
        user.id,
        ResourceType.FOLDER,
        folder.id,
        AccessRole.OWNER,
    )

    results = await get_accessible_resources(
        session,
        PrincipleType.USER,
        user.id,
        resource_type=ResourceType.WORKFLOW,
    )

    assert len(results) == 1
    assert results[0].resource_type == ResourceType.WORKFLOW


@pytest.mark.asyncio
async def test_get_accessible_resources_expand_hierarchy(session: AsyncSession):
    user = await create_test_user(session)
    namespace = await create_test_namespace(session)

    # Create folder hierarchy: parent_folder -> child_folder
    parent_folder = await create_test_folder(session, namespace.id)
    child_folder = await create_test_folder(session, namespace.id, parent_folder.id)
    workflow_in_child = await create_test_workflow(
        session, namespace.id, child_folder.id
    )

    # Grant access to parent folder only
    await grant_access(
        session,
        PrincipleType.USER,
        user.id,
        ResourceType.FOLDER,
        parent_folder.id,
        AccessRole.OWNER,
    )

    # Without expansion: only parent folder
    results_no_expand = await get_accessible_resources(
        session, PrincipleType.USER, user.id, expand_hierarchy=False
    )
    assert len(results_no_expand) == 1

    # With expansion: parent folder, child folder, and workflow
    results_expanded = await get_accessible_resources(
        session, PrincipleType.USER, user.id, expand_hierarchy=True
    )
    assert len(results_expanded) == 3

    resource_ids = {r.resource_id for r in results_expanded}
    assert parent_folder.id in resource_ids
    assert child_folder.id in resource_ids
    assert workflow_in_child.id in resource_ids

    # Check inheritance tracking
    child_result = next(r for r in results_expanded if r.resource_id == child_folder.id)
    assert child_result.inherited_from == parent_folder.id


# --- Integration Tests for get_resource_principals ---


@pytest.mark.asyncio
async def test_get_resource_principals_direct_access(session: AsyncSession):
    user = await create_test_user(session)
    namespace = await create_test_namespace(session)
    workflow = await create_test_workflow(session, namespace.id)

    await grant_access(
        session,
        PrincipleType.USER,
        user.id,
        ResourceType.WORKFLOW,
        workflow.id,
        AccessRole.OWNER,
    )

    results = await get_resource_principals(session, ResourceType.WORKFLOW, workflow.id)

    assert len(results) == 1
    assert results[0].principal_type == PrincipleType.USER
    assert results[0].principal_id == user.id
    assert results[0].role == AccessRole.OWNER


@pytest.mark.asyncio
async def test_get_resource_principals_inherited_from_folder(session: AsyncSession):
    user = await create_test_user(session)
    namespace = await create_test_namespace(session)

    folder = await create_test_folder(session, namespace.id)
    workflow = await create_test_workflow(session, namespace.id, folder.id)

    # Grant access to folder (not workflow directly)
    await grant_access(
        session,
        PrincipleType.USER,
        user.id,
        ResourceType.FOLDER,
        folder.id,
        AccessRole.USER,
    )

    results = await get_resource_principals(session, ResourceType.WORKFLOW, workflow.id)

    assert len(results) == 1
    assert results[0].principal_id == user.id
    assert results[0].role == AccessRole.USER
    assert results[0].inherited_from == folder.id


# --- Integration Tests for get_resolved_access ---


@pytest.mark.asyncio
async def test_get_resolved_access_direct(session: AsyncSession):
    user = await create_test_user(session)
    namespace = await create_test_namespace(session)
    workflow = await create_test_workflow(session, namespace.id)

    await grant_access(
        session,
        PrincipleType.USER,
        user.id,
        ResourceType.WORKFLOW,
        workflow.id,
        AccessRole.USER,
    )

    role = await get_resolved_access(
        session,
        PrincipleType.USER,
        user.id,
        ResourceType.WORKFLOW,
        workflow.id,
    )

    assert role == AccessRole.USER


@pytest.mark.asyncio
async def test_get_resolved_access_no_access(session: AsyncSession):
    user = await create_test_user(session)
    namespace = await create_test_namespace(session)
    workflow = await create_test_workflow(session, namespace.id)

    # No access granted
    role = await get_resolved_access(
        session,
        PrincipleType.USER,
        user.id,
        ResourceType.WORKFLOW,
        workflow.id,
    )

    assert role is None


@pytest.mark.asyncio
async def test_get_resolved_access_combines_highest_role(session: AsyncSession):
    user = await create_test_user(session)
    namespace = await create_test_namespace(session)

    folder = await create_test_folder(session, namespace.id)
    workflow = await create_test_workflow(session, namespace.id, folder.id)

    # Direct access: USER role
    await grant_access(
        session,
        PrincipleType.USER,
        user.id,
        ResourceType.WORKFLOW,
        workflow.id,
        AccessRole.USER,
    )
    # Folder access: OWNER role
    await grant_access(
        session,
        PrincipleType.USER,
        user.id,
        ResourceType.FOLDER,
        folder.id,
        AccessRole.OWNER,
    )

    role = await get_resolved_access(
        session,
        PrincipleType.USER,
        user.id,
        ResourceType.WORKFLOW,
        workflow.id,
    )

    # Should return OWNER (highest role)
    assert role == AccessRole.OWNER


@pytest.mark.asyncio
async def test_get_resolved_access_nested_folder_hierarchy(session: AsyncSession):
    user = await create_test_user(session)
    namespace = await create_test_namespace(session)

    # Create nested hierarchy: grandparent -> parent -> child folder -> workflow
    grandparent = await create_test_folder(session, namespace.id)
    parent = await create_test_folder(session, namespace.id, grandparent.id)
    child = await create_test_folder(session, namespace.id, parent.id)
    workflow = await create_test_workflow(session, namespace.id, child.id)

    # Grant access only to grandparent
    await grant_access(
        session,
        PrincipleType.USER,
        user.id,
        ResourceType.FOLDER,
        grandparent.id,
        AccessRole.OWNER,
    )

    role = await get_resolved_access(
        session,
        PrincipleType.USER,
        user.id,
        ResourceType.WORKFLOW,
        workflow.id,
    )

    # Should inherit OWNER from grandparent folder
    assert role == AccessRole.OWNER
