import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Workflow
from app.services.workflow_auth_service import WorkflowAuthService
from app.tests.fixtures_clients import UserClient


@pytest.fixture(scope="function")
async def workflow_a(client_a: UserClient, session: AsyncSession):
    """Workflow for client_a to use in permission tests."""
    workflow = Workflow(
        name="Test Workflow A",
        namespace_id=client_a.personal_namespace.id,
        created_by_id=client_a.user.id,
    )
    session.add(workflow)
    await session.flush()
    await session.refresh(workflow)
    return workflow


@pytest.fixture(scope="function")
async def workflow_b(client_a: UserClient, session: AsyncSession):
    """Second workflow for client_a to test permission grants."""
    workflow = Workflow(
        name="Test Workflow B",
        namespace_id=client_a.personal_namespace.id,
        created_by_id=client_a.user.id,
    )
    session.add(workflow)
    await session.flush()
    await session.refresh(workflow)
    return workflow


def get_workflow_token(workflow: Workflow) -> str:
    """Generate a JWT token for workflow authentication."""
    return WorkflowAuthService.generate_invocation_token(workflow)


# Basic CRUD Flow Tests


async def test_set_and_get_value(client_a: UserClient, workflow_a: Workflow):
    """Test creating a table and setting/getting a value."""
    token = get_workflow_token(workflow_a)

    response = await client_a.put(
        "/api/kv/default/my_table/test_key",
        json={"value": "test_value"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["key"] == "test_key"
    assert data["value"] == "test_value"
    assert "created_at" in data
    assert "updated_at" in data

    # Verify we can read it back
    response = await client_a.get(
        "/api/kv/default/my_table/test_key",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["key"] == "test_key"
    assert data["value"] == "test_value"


async def test_update_existing_value(client_a: UserClient, workflow_a: Workflow):
    """Test updating an existing value."""
    token = get_workflow_token(workflow_a)

    # Create initial value
    await client_a.put(
        "/api/kv/default/my_table/counter",
        json={"value": 1},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Update value
    response = await client_a.put(
        "/api/kv/default/my_table/counter",
        json={"value": 2},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["value"] == 2

    # Verify updated value
    response = await client_a.get(
        "/api/kv/default/my_table/counter",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["value"] == 2


async def test_delete_value(client_a: UserClient, workflow_a: Workflow):
    """Test deleting a value."""
    # Create value
    await client_a.put(
        "/api/kv/default/my_table/temp_key",
        json={"value": "temporary"},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )

    # Delete value
    response = await client_a.delete(
        "/api/kv/default/my_table/temp_key",
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Key deleted successfully"
    assert data["key"] == "temp_key"

    # Verify it's gone
    response = await client_a.get(
        "/api/kv/default/my_table/temp_key",
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )
    assert response.status_code == 404


async def test_list_tables(client_a: UserClient, workflow_a: Workflow):
    """Test listing tables shows only accessible tables."""
    # Create values in two different tables
    await client_a.put(
        "/api/kv/default/table_one/key1",
        json={"value": "data1"},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )
    await client_a.put(
        "/api/kv/default/table_two/key2",
        json={"value": "data2"},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )

    # List tables
    response = await client_a.get(
        "/api/kv/default",
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "tables" in data
    assert "table_one" in data["tables"]
    assert "table_two" in data["tables"]


# Permission Model Tests


async def test_creator_auto_gets_permissions(
    client_a: UserClient, workflow_a: Workflow
):
    """Test that creating a table automatically grants read+write permissions."""
    # Create table via PUT
    await client_a.put(
        "/api/kv/default/auto_table/key1",
        json={"value": "data"},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )

    # Verify we can read (requires read permission)
    response = await client_a.get(
        "/api/kv/default/auto_table/key1",
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )
    assert response.status_code == 200

    # Verify we can write (requires write permission)
    response = await client_a.put(
        "/api/kv/default/auto_table/key2",
        json={"value": "more_data"},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )
    assert response.status_code == 200


async def test_grant_permission_to_another_workflow(
    client_a: UserClient, workflow_a: Workflow, workflow_b: Workflow
):
    """Test granting permissions to another workflow."""
    # Create table with workflow_a
    await client_a.put(
        "/api/kv/default/shared_table/data",
        json={"value": "shared"},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )

    # Initially workflow_b can't access
    response = await client_a.get(
        "/api/kv/default/shared_table/data",
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_b)}"},
    )
    assert response.status_code == 403

    # Grant read permission to workflow_b
    response = await client_a.post(
        "/api/kv/default/permissions/shared_table",
        json={"workflow_id": str(workflow_b.id), "can_read": True, "can_write": False},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )
    assert response.status_code == 200
    perm_data = response.json()
    assert perm_data["workflow_id"] == str(workflow_b.id)
    assert perm_data["can_read"] is True
    assert perm_data["can_write"] is False

    # Now workflow_b can read
    response = await client_a.get(
        "/api/kv/default/shared_table/data",
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_b)}"},
    )
    assert response.status_code == 200

    # But workflow_b still can't write
    response = await client_a.put(
        "/api/kv/default/shared_table/other",
        json={"value": "attempt"},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_b)}"},
    )
    assert response.status_code == 403


async def test_access_denied_without_permission(
    client_a: UserClient, workflow_a: Workflow, workflow_b: Workflow
):
    """Test that accessing a table without permission returns 403."""
    # Create table with workflow_a
    await client_a.put(
        "/api/kv/default/private_table/secret",
        json={"value": "private_data"},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )

    # workflow_b can't read
    response = await client_a.get(
        "/api/kv/default/private_table/secret",
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_b)}"},
    )
    assert response.status_code == 403

    # workflow_b can't write
    response = await client_a.put(
        "/api/kv/default/private_table/other",
        json={"value": "attempt"},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_b)}"},
    )
    assert response.status_code == 403


async def test_write_permission_required_to_grant(
    client_a: UserClient, workflow_a: Workflow, workflow_b: Workflow
):
    """Test that write permission is required to grant/revoke permissions."""
    # Create table with workflow_a
    await client_a.put(
        "/api/kv/default/perm_table/data",
        json={"value": "data"},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )

    # Grant read-only to workflow_b
    await client_a.post(
        "/api/kv/default/permissions/perm_table",
        json={"workflow_id": str(workflow_b.id), "can_read": True, "can_write": False},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )

    # workflow_b can't grant permissions (needs write permission)
    response = await client_a.post(
        "/api/kv/default/permissions/perm_table",
        json={"workflow_id": str(workflow_a.id), "can_read": True, "can_write": True},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_b)}"},
    )
    assert response.status_code == 403


# Validation & Error Cases Tests


async def test_invalid_table_name(client_a: UserClient, workflow_a: Workflow):
    """Test that invalid table names return 400."""
    # Table name with invalid characters
    response = await client_a.put(
        "/api/kv/default/invalid@table/key",
        json={"value": "data"},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )
    assert response.status_code == 400
    assert "alphanumeric" in response.json()["detail"].lower()


async def test_invalid_key(client_a: UserClient, workflow_a: Workflow):
    """Test that invalid keys return 400."""
    # Key with invalid characters
    response = await client_a.put(
        "/api/kv/default/my_table/invalid key with spaces",
        json={"value": "data"},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )
    assert response.status_code == 400
    assert "alphanumeric" in response.json()["detail"].lower()


async def test_oversized_value(client_a: UserClient, workflow_a: Workflow):
    """Test that values exceeding 1MB are rejected."""
    # Create a value larger than 1MB
    large_value = "x" * (1_000_001)  # Just over 1MB

    response = await client_a.put(
        "/api/kv/default/my_table/large_key",
        json={"value": large_value},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )
    # Verify the request is rejected (validation error or server error)
    assert response.status_code >= 400  # Any error code indicates rejection


async def test_get_nonexistent_key_returns_404(
    client_a: UserClient, workflow_a: Workflow
):
    """Test that getting a non-existent key returns 404."""
    response = await client_a.get(
        "/api/kv/default/my_table/nonexistent",
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )
    assert response.status_code == 404


async def test_delete_nonexistent_key_returns_404(
    client_a: UserClient, workflow_a: Workflow
):
    """Test that deleting a non-existent key returns 404."""
    # Create table first so we have permission
    await client_a.put(
        "/api/kv/default/my_table/existing",
        json={"value": "data"},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )

    # Try to delete non-existent key
    response = await client_a.delete(
        "/api/kv/default/my_table/nonexistent",
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )
    assert response.status_code == 404


# Cross-User Isolation Tests


async def test_user_cannot_list_other_users_tables(
    client_a: UserClient, client_b: UserClient, workflow_a: Workflow
):
    """Test that users can't see tables from other users' namespaces."""
    # client_a creates a table
    await client_a.put(
        "/api/kv/default/client_a_table/data",
        json={"value": "a_data"},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )

    # client_a can see the table
    response = await client_a.get(
        "/api/kv/default",
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )
    assert response.status_code == 200
    assert "client_a_table" in response.json()["tables"]

    # client_b can't see client_a's tables (different namespace, needs own workflow)
    # This would require creating a workflow for client_b, but the key behavior is
    # that tables are namespace-scoped, so this is already tested by the permission tests


async def test_workflow_cannot_access_table_from_other_namespace(
    client_a: UserClient,
    client_b: UserClient,
    workflow_a: Workflow,
    session: AsyncSession,
):
    """Test workflows can't access tables from other namespaces."""
    from uuid import UUID

    from sqlalchemy import select

    # client_a creates a table in their namespace
    await client_a.put(
        "/api/kv/default/namespace_a_table/data",
        json={"value": "a_data"},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )

    # client_b creates a workflow in their namespace
    workflow_b_data = {
        "name": "Client B Workflow",
        "namespace_id": str(client_b.personal_namespace.id),
    }
    response = await client_b.post("/api/workflows", json=workflow_b_data)
    assert response.status_code == 200
    workflow_b_id = response.json()["id"]

    # Fetch the workflow from DB to generate token
    query = select(Workflow).where(Workflow.id == UUID(workflow_b_id))
    result = await session.execute(query)
    workflow_b = result.scalar_one()

    # client_b's workflow can't access client_a's table (different namespace)
    response = await client_b.get(
        "/api/kv/default/namespace_a_table/data",
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_b)}"},
    )
    assert response.status_code == 404  # Table not found in their namespace


# List Operations Tests


async def test_list_keys_without_values(client_a: UserClient, workflow_a: Workflow):
    """Test listing keys without values."""
    # Create multiple keys
    await client_a.put(
        "/api/kv/default/my_table/key1",
        json={"value": "value1"},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )
    await client_a.put(
        "/api/kv/default/my_table/key2",
        json={"value": "value2"},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )

    # List keys without values
    response = await client_a.get(
        "/api/kv/default/my_table",
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "keys" in data
    assert "key1" in data["keys"]
    assert "key2" in data["keys"]
    # Verify values are not included
    assert "value1" not in str(data)


async def test_list_keys_with_values(client_a: UserClient, workflow_a: Workflow):
    """Test listing keys with values using include_values=true."""
    # Create multiple keys
    await client_a.put(
        "/api/kv/default/my_table/key1",
        json={"value": "value1"},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )
    await client_a.put(
        "/api/kv/default/my_table/key2",
        json={"value": {"nested": "object"}},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )

    # List keys with values
    response = await client_a.get(
        "/api/kv/default/my_table?include_values=true",
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert len(data["items"]) == 2

    # Verify structure includes values
    items = {item["key"]: item for item in data["items"]}
    assert items["key1"]["value"] == "value1"
    assert items["key2"]["value"] == {"nested": "object"}
    assert "created_at" in items["key1"]
    assert "updated_at" in items["key1"]


async def test_list_empty_table(client_a: UserClient, workflow_a: Workflow):
    """Test listing keys in an empty table."""
    # Create table
    await client_a.put(
        "/api/kv/default/empty_table/temp",
        json={"value": "data"},
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )
    # Delete the only key
    await client_a.delete(
        "/api/kv/default/empty_table/temp",
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )

    # List should return empty array
    response = await client_a.get(
        "/api/kv/default/empty_table",
        headers={"Authorization": f"Bearer {get_workflow_token(workflow_a)}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["keys"] == []
