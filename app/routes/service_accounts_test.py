from app.tests.fixtures_clients import UserClient


async def test_create_and_list_service_account(client_a: UserClient):
    """Test creating a service account and verifying it appears in the list."""
    # First create an organization
    org_data = {"name": "test-org-sa", "display_name": "Test Org for SA"}
    org_response = await client_a.post("/api/organizations", json=org_data)
    assert org_response.status_code == 200
    org_id = org_response.json()["id"]

    # Create a service account
    sa_data = {"name": "Test Service Account", "organization_id": org_id}
    response = await client_a.post("/api/service_accounts", json=sa_data)
    assert response.status_code == 200

    created_sa = response.json()
    assert created_sa["name"] == "Test Service Account"
    assert created_sa["organization_id"] == org_id
    assert "id" in created_sa
    assert created_sa["api_keys"] == []

    # List service accounts and verify it appears
    response = await client_a.get(
        "/api/service_accounts", params={"organization_id": org_id}
    )
    assert response.status_code == 200

    service_accounts = response.json()["results"]
    assert len(service_accounts) == 1
    assert service_accounts[0]["name"] == "Test Service Account"
    assert service_accounts[0]["api_keys"] == []


async def test_create_api_key_returns_plain_key_once(client_a: UserClient):
    """Test that creating an API key returns the plain key once, but subsequent GETs don't include it."""
    # Create org and service account
    org_data = {"name": "test-org-apikey", "display_name": "Test Org API Key"}
    org_response = await client_a.post("/api/organizations", json=org_data)
    org_id = org_response.json()["id"]

    sa_data = {"name": "SA with API Key", "organization_id": org_id}
    sa_response = await client_a.post("/api/service_accounts", json=sa_data)
    sa_id = sa_response.json()["id"]

    # Create an API key
    api_key_data = {"name": "Production Key"}
    response = await client_a.post(
        f"/api/service_accounts/{sa_id}/api_keys", json=api_key_data
    )
    assert response.status_code == 200

    created_key = response.json()
    assert "api_key" in created_key
    assert created_key["name"] == "Production Key"
    assert created_key["prefix"].startswith("floww_sa_")
    assert len(created_key["api_key"]) > 0  # Plain key is present

    api_key_id = created_key["id"]
    # plain_key = created_key["api_key"]

    # Get the service account and verify the plain key is NOT in the response
    response = await client_a.get(f"/api/service_accounts/{sa_id}")
    assert response.status_code == 200

    sa = response.json()
    assert len(sa["api_keys"]) == 1
    assert "api_key" not in sa["api_keys"][0]  # Plain key should not be present
    assert sa["api_keys"][0]["id"] == api_key_id
    assert sa["api_keys"][0]["name"] == "Production Key"


async def test_list_includes_api_keys(client_a: UserClient):
    """Test that listing service accounts includes all their API keys."""
    # Create org and service account
    org_data = {"name": "test-org-multikey", "display_name": "Test Org Multi Key"}
    org_response = await client_a.post("/api/organizations", json=org_data)
    org_id = org_response.json()["id"]

    sa_data = {"name": "SA with Multiple Keys", "organization_id": org_id}
    sa_response = await client_a.post("/api/service_accounts", json=sa_data)
    sa_id = sa_response.json()["id"]

    # Create multiple API keys
    key1_data = {"name": "Key 1"}
    await client_a.post(f"/api/service_accounts/{sa_id}/api_keys", json=key1_data)

    key2_data = {"name": "Key 2"}
    await client_a.post(f"/api/service_accounts/{sa_id}/api_keys", json=key2_data)

    key3_data = {"name": "Key 3"}
    await client_a.post(f"/api/service_accounts/{sa_id}/api_keys", json=key3_data)

    # List service accounts
    response = await client_a.get(
        "/api/service_accounts", params={"organization_id": org_id}
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


async def test_revoke_api_key(client_a: UserClient):
    """Test revoking an API key sets revoked_at timestamp."""
    # Create org and service account
    org_data = {"name": "test-org-revoke", "display_name": "Test Org Revoke"}
    org_response = await client_a.post("/api/organizations", json=org_data)
    org_id = org_response.json()["id"]

    sa_data = {"name": "SA for Revoke Test", "organization_id": org_id}
    sa_response = await client_a.post("/api/service_accounts", json=sa_data)
    sa_id = sa_response.json()["id"]

    # Create an API key
    api_key_data = {"name": "Key to Revoke"}
    key_response = await client_a.post(
        f"/api/service_accounts/{sa_id}/api_keys", json=api_key_data
    )
    api_key_id = key_response.json()["id"]

    # Verify it's not revoked initially
    assert key_response.json()["revoked_at"] is None

    # Revoke the key
    response = await client_a.post(
        f"/api/service_accounts/{sa_id}/api_keys/{api_key_id}/revoke"
    )
    assert response.status_code == 200

    revoked_key = response.json()
    assert revoked_key["revoked_at"] is not None
    assert revoked_key["id"] == api_key_id

    # Verify the key shows as revoked when getting the service account
    response = await client_a.get(f"/api/service_accounts/{sa_id}")
    sa = response.json()
    assert sa["api_keys"][0]["revoked_at"] is not None

    # Try to revoke again - should fail
    response = await client_a.post(
        f"/api/service_accounts/{sa_id}/api_keys/{api_key_id}/revoke"
    )
    assert response.status_code == 400
    assert "already revoked" in response.json()["detail"]


async def test_service_account_isolation(client_a: UserClient, client_b: UserClient):
    """Test that service accounts are isolated between different organizations."""
    # Client A creates an organization and service account
    org_data_a = {"name": "org-a-isolation", "display_name": "Org A Isolation"}
    org_response_a = await client_a.post("/api/organizations", json=org_data_a)
    org_id_a = org_response_a.json()["id"]

    sa_data_a = {"name": "Client A Service Account", "organization_id": org_id_a}
    sa_response_a = await client_a.post("/api/service_accounts", json=sa_data_a)
    sa_id_a = sa_response_a.json()["id"]

    # Client B creates their own organization
    # org_data_b = {"name": "org-b-isolation", "display_name": "Org B Isolation"}
    # org_response_b = await client_b.post("/api/organizations", json=org_data_b)
    # org_id_b = org_response_b.json()["id"]

    # Client B should not see Client A's service account
    response = await client_b.get(
        "/api/service_accounts", params={"organization_id": org_id_a}
    )
    assert response.status_code == 200
    assert len(response.json()["results"]) == 0

    # Client B should not be able to access Client A's service account directly
    response = await client_b.get(f"/api/service_accounts/{sa_id_a}")
    assert response.status_code == 404

    # Client B should not be able to create an API key for Client A's service account
    api_key_data = {"name": "Unauthorized Key"}
    response = await client_b.post(
        f"/api/service_accounts/{sa_id_a}/api_keys", json=api_key_data
    )
    assert response.status_code == 404


async def test_update_service_account_name(client_a: UserClient):
    """Test updating a service account's name."""
    # Create org and service account
    org_data = {"name": "test-org-update", "display_name": "Test Org Update"}
    org_response = await client_a.post("/api/organizations", json=org_data)
    org_id = org_response.json()["id"]

    sa_data = {"name": "Original Name", "organization_id": org_id}
    sa_response = await client_a.post("/api/service_accounts", json=sa_data)
    sa_id = sa_response.json()["id"]

    # Update the name
    update_data = {"name": "Updated Name"}
    response = await client_a.patch(f"/api/service_accounts/{sa_id}", json=update_data)
    assert response.status_code == 200

    updated_sa = response.json()
    assert updated_sa["name"] == "Updated Name"
    assert updated_sa["id"] == sa_id

    # Verify the update persisted
    response = await client_a.get(f"/api/service_accounts/{sa_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Name"


async def test_delete_service_account_cascades(client_a: UserClient):
    """Test that deleting a service account also deletes its API keys."""
    # Create org and service account
    org_data = {"name": "test-org-delete", "display_name": "Test Org Delete"}
    org_response = await client_a.post("/api/organizations", json=org_data)
    org_id = org_response.json()["id"]

    sa_data = {"name": "SA to Delete", "organization_id": org_id}
    sa_response = await client_a.post("/api/service_accounts", json=sa_data)
    sa_id = sa_response.json()["id"]

    # Create multiple API keys
    # key1_data = {"name": "Key 1"}
    # key1_response = await client_a.post(
    #     f"/api/service_accounts/{sa_id}/api_keys", json=key1_data
    # )
    # key1_id = key1_response.json()["id"]

    # key2_data = {"name": "Key 2"}
    # key2_response = await client_a.post(
    #     f"/api/service_accounts/{sa_id}/api_keys", json=key2_data
    # )
    # key2_id = key2_response.json()["id"]

    # Verify the service account has 2 keys
    response = await client_a.get(f"/api/service_accounts/{sa_id}")
    assert len(response.json()["api_keys"]) == 2

    # Delete the service account
    response = await client_a.delete(f"/api/service_accounts/{sa_id}")
    assert response.status_code == 200
    assert response.json()["success"] is True

    # Verify the service account is gone
    response = await client_a.get(f"/api/service_accounts/{sa_id}")
    assert response.status_code == 404

    # Verify it doesn't appear in the list
    response = await client_a.get(
        "/api/service_accounts", params={"organization_id": org_id}
    )
    assert response.status_code == 200
    assert len(response.json()["results"]) == 0


async def test_service_account_can_authenticate_with_api_key(client_a: UserClient):
    """Test that a service account can authenticate using an API key and access resources."""
    # Create an organization
    org_data = {"name": "test-org-auth", "display_name": "Test Org Auth"}
    org_response = await client_a.post("/api/organizations", json=org_data)
    assert org_response.status_code == 200
    org_id = org_response.json()["id"]

    # Create a service account
    sa_data = {"name": "Auth Test SA", "organization_id": org_id}
    sa_response = await client_a.post("/api/service_accounts", json=sa_data)
    assert sa_response.status_code == 200
    sa_id = sa_response.json()["id"]

    # Create an API key
    api_key_data = {"name": "Test API Key"}
    key_response = await client_a.post(
        f"/api/service_accounts/{sa_id}/api_keys", json=api_key_data
    )
    assert key_response.status_code == 200
    plain_api_key = key_response.json()["api_key"]
    assert plain_api_key.startswith("floww_sa_")

    # Create a workflow that the service account should be able to access
    # First, get the organization's namespace
    namespace_response = await client_a.get("/api/namespaces")
    namespaces = namespace_response.json()["results"]
    org_namespace = next(
        (ns for ns in namespaces if ns.get("organization_owner_id") == org_id), None
    )
    assert org_namespace is not None
    namespace_id = org_namespace["id"]

    # Create a workflow in the organization's namespace
    workflow_data = {
        "name": "test-workflow",
        "namespace_id": namespace_id,
    }
    workflow_response = await client_a.post("/api/workflows", json=workflow_data)
    assert workflow_response.status_code == 200
    workflow_id = workflow_response.json()["id"]

    # Now authenticate as the service account using the API key
    # Update the client headers to use the API key
    original_auth = client_a.headers.get("Authorization")
    client_a.headers["Authorization"] = f"Bearer {plain_api_key}"

    try:
        # Try to list workflows - should succeed
        response = await client_a.get("/api/workflows")
        assert response.status_code == 200

        # Verify the service account can see the workflow
        workflows = response.json()["results"]
        workflow_ids = [w["id"] for w in workflows]
        assert workflow_id in workflow_ids

        # Try to get the specific workflow
        response = await client_a.get(f"/api/workflows/{workflow_id}")
        assert response.status_code == 200
        assert response.json()["id"] == workflow_id

        # Verify whoami returns the service account
        response = await client_a.get("/api/whoami")
        assert response.status_code == 200
        whoami_data = response.json()
        assert whoami_data["id"] == sa_id
        assert whoami_data["user_type"] == "service_account"

    finally:
        # Restore original authentication
        if original_auth:
            client_a.headers["Authorization"] = original_auth
