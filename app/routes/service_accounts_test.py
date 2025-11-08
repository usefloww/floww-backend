import pytest
from httpx import AsyncClient

from app.tests.fixtures_clients import UserClient


@pytest.fixture
async def org_a(client_a: UserClient):
    """Create an organization for client_a."""
    org_data = {"name": "test-org", "display_name": "Test Organization"}
    response = await client_a.post("/api/organizations", json=org_data)
    assert response.status_code == 200
    return response.json()


@pytest.fixture
async def service_account_a(client_a: UserClient, org_a):
    """Create a service account in org_a."""
    sa_data = {"name": "Test Service Account", "organization_id": org_a["id"]}
    response = await client_a.post("/api/service_accounts", json=sa_data)
    assert response.status_code == 200
    return response.json()


@pytest.fixture
async def service_account_with_key(client_a: UserClient, service_account_a):
    """Create an API key for service_account_a and return both."""
    api_key_data = {"name": "Test API Key"}
    response = await client_a.post(
        f"/api/service_accounts/{service_account_a['id']}/api_keys", json=api_key_data
    )
    assert response.status_code == 200
    return {
        "service_account": service_account_a,
        "api_key": response.json(),
    }


async def test_create_and_list_service_account(
    client_a: UserClient, service_account_a, org_a
):
    """Test creating a service account and verifying it appears in the list."""
    # Verify the created service account
    assert service_account_a["name"] == "Test Service Account"
    assert service_account_a["organization_id"] == org_a["id"]
    assert "id" in service_account_a
    assert service_account_a["api_keys"] == []

    # List service accounts and verify it appears
    response = await client_a.get(
        "/api/service_accounts", params={"organization_id": org_a["id"]}
    )
    assert response.status_code == 200

    service_accounts = response.json()["results"]
    assert len(service_accounts) == 1
    assert service_accounts[0]["name"] == "Test Service Account"
    assert service_accounts[0]["api_keys"] == []


async def test_create_api_key_returns_plain_key_once(
    client_a: UserClient, service_account_a
):
    """Test that creating an API key returns the plain key once, but subsequent GETs don't include it."""
    # Create an API key
    api_key_data = {"name": "Production Key"}
    response = await client_a.post(
        f"/api/service_accounts/{service_account_a['id']}/api_keys", json=api_key_data
    )
    assert response.status_code == 200

    created_key = response.json()
    assert "api_key" in created_key
    assert created_key["name"] == "Production Key"
    assert created_key["prefix"].startswith("floww_sa_")
    assert len(created_key["api_key"]) > 0  # Plain key is present

    api_key_id = created_key["id"]

    # Get the service account and verify the plain key is NOT in the response
    response = await client_a.get(f"/api/service_accounts/{service_account_a['id']}")
    assert response.status_code == 200

    sa = response.json()
    assert len(sa["api_keys"]) == 1
    assert "api_key" not in sa["api_keys"][0]  # Plain key should not be present
    assert sa["api_keys"][0]["id"] == api_key_id
    assert sa["api_keys"][0]["name"] == "Production Key"


async def test_list_includes_api_keys(client_a: UserClient, service_account_a, org_a):
    """Test that listing service accounts includes all their API keys."""
    # Create multiple API keys
    key1_data = {"name": "Key 1"}
    await client_a.post(
        f"/api/service_accounts/{service_account_a['id']}/api_keys", json=key1_data
    )

    key2_data = {"name": "Key 2"}
    await client_a.post(
        f"/api/service_accounts/{service_account_a['id']}/api_keys", json=key2_data
    )

    key3_data = {"name": "Key 3"}
    await client_a.post(
        f"/api/service_accounts/{service_account_a['id']}/api_keys", json=key3_data
    )

    # List service accounts
    response = await client_a.get(
        "/api/service_accounts", params={"organization_id": org_a["id"]}
    )
    assert response.status_code == 200

    service_accounts = response.json()["results"]
    assert len(service_accounts) == 1
    assert len(service_accounts[0]["api_keys"]) == 3

    # Verify all key names are present
    key_names = [key["name"] for key in service_accounts[0]["api_keys"]]
    assert "Key 1" in key_names
    assert "Key 2" in key_names
    assert "Key 3" in key_names


async def test_revoke_api_key(client_a: UserClient, service_account_a):
    """Test revoking an API key sets revoked_at timestamp."""
    # Create an API key
    api_key_data = {"name": "Key to Revoke"}
    key_response = await client_a.post(
        f"/api/service_accounts/{service_account_a['id']}/api_keys", json=api_key_data
    )
    api_key_id = key_response.json()["id"]

    # Verify it's not revoked initially
    assert key_response.json()["revoked_at"] is None

    # Revoke the key
    response = await client_a.post(
        f"/api/service_accounts/{service_account_a['id']}/api_keys/{api_key_id}/revoke"
    )
    assert response.status_code == 200

    revoked_key = response.json()
    assert revoked_key["revoked_at"] is not None
    assert revoked_key["id"] == api_key_id

    # Verify the key shows as revoked when getting the service account
    response = await client_a.get(f"/api/service_accounts/{service_account_a['id']}")
    sa = response.json()
    assert sa["api_keys"][0]["revoked_at"] is not None

    # Try to revoke again - should fail
    response = await client_a.post(
        f"/api/service_accounts/{service_account_a['id']}/api_keys/{api_key_id}/revoke"
    )
    assert response.status_code == 400
    assert "already revoked" in response.json()["detail"]


async def test_service_account_isolation(
    client_a: UserClient, client_b: UserClient, service_account_a, org_a
):
    """Test that service accounts are isolated between different organizations."""
    # Client B should not see Client A's service account
    response = await client_b.get(
        "/api/service_accounts", params={"organization_id": org_a["id"]}
    )
    assert response.status_code == 200
    assert len(response.json()["results"]) == 0

    # Client B should not be able to access Client A's service account directly
    response = await client_b.get(f"/api/service_accounts/{service_account_a['id']}")
    assert response.status_code == 404

    # Client B should not be able to create an API key for Client A's service account
    api_key_data = {"name": "Unauthorized Key"}
    response = await client_b.post(
        f"/api/service_accounts/{service_account_a['id']}/api_keys", json=api_key_data
    )
    assert response.status_code == 404


async def test_update_service_account_name(
    client_a: UserClient,
    service_account_a,
):
    """Test updating a service account's name."""
    # Update the name
    update_data = {"name": "Updated Name"}
    response = await client_a.patch(
        f"/api/service_accounts/{service_account_a['id']}", json=update_data
    )
    assert response.status_code == 200

    updated_sa = response.json()
    assert updated_sa["name"] == "Updated Name"
    assert updated_sa["id"] == service_account_a["id"]

    # Verify the update persisted
    response = await client_a.get(f"/api/service_accounts/{service_account_a['id']}")
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Name"


async def test_service_account_can_authenticate_with_api_key(
    client_a: UserClient,
    client: AsyncClient,
    org_a,
    service_account_a,
):
    """Test that a service account can authenticate using an API key and access resources."""
    # Create an API key
    api_key_data = {"name": "Test API Key"}
    key_response = await client_a.post(
        f"/api/service_accounts/{service_account_a['id']}/api_keys", json=api_key_data
    )
    assert key_response.status_code == 200
    plain_api_key = key_response.json()["api_key"]
    assert plain_api_key.startswith("floww_sa_")

    # Now authenticate as the service account using the API key
    client.headers["Authorization"] = f"Bearer {plain_api_key}"

    # Verify whoami returns the service account
    response = await client.get("/api/whoami")
    assert response.status_code == 200
    whoami_data = response.json()
    assert whoami_data["id"] == service_account_a["id"]
    assert whoami_data["user_type"] == "service_account"
