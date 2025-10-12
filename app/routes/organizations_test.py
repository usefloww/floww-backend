from app.tests.fixtures_clients import UserClient


async def test_create_and_retrieve_organization(client_a: UserClient):
    organization_data = {
        "name": "test-org",
        "display_name": "My Test Organization",
    }
    response = await client_a.post("/api/organizations", json=organization_data)
    assert response.status_code == 200

    created_organization = response.json()
    assert created_organization["name"] == "test-org"
    assert created_organization["display_name"] == "My Test Organization"

    # Test: Retrieve organizations and verify it appears
    response = await client_a.get("/api/organizations")
    assert response.status_code == 200

    organizations = response.json()["organizations"]
    assert len(organizations) == 1
    assert organizations[0]["name"] == "test-org"


async def test_list_organizations_returns_correct_structure(client_a: UserClient):
    response = await client_a.get("/api/organizations")
    assert response.status_code == 200

    data = response.json()
    assert "organizations" in data
    assert "total" in data
    assert "user_id" in data
    assert isinstance(data["organizations"], list)


async def test_organization_creation_includes_metadata(client_a: UserClient):
    organization_data = {
        "name": "metadata-org",
        "display_name": "Metadata Organization",
    }
    response = await client_a.post("/api/organizations", json=organization_data)
    assert response.status_code == 200

    created_organization = response.json()
    assert "id" in created_organization
    assert "created_at" in created_organization


async def test_organizations_are_isolated_between_users(
    client_a: UserClient, client_b: UserClient
):
    # Create organization
    organization_data = {
        "name": "user-a-org",
        "display_name": "User A Organization",
    }
    response = await client_a.post("/api/organizations", json=organization_data)
    assert response.status_code == 200

    # Verify it's accessible to the creator
    response = await client_a.get("/api/organizations")
    assert response.status_code == 200

    organizations = response.json()["organizations"]
    assert len(organizations) == 1
    assert organizations[0]["name"] == "user-a-org"

    # Verify it's not accessible to the other user
    response = await client_b.get("/api/organizations")
    assert response.status_code == 200

    organizations = response.json()["organizations"]
    assert len(organizations) == 0


async def test_update_and_delete_organization(client_a: UserClient):
    # Create organization
    organization_data = {
        "name": "crud-org",
        "display_name": "CRUD Organization",
    }
    response = await client_a.post("/api/organizations", json=organization_data)
    assert response.status_code == 200

    organization_id = response.json()["id"]

    # Update organization
    update_data = {"display_name": "Updated Organization"}
    response = await client_a.put(
        f"/api/organizations/{organization_id}", json=update_data
    )
    assert response.status_code == 200
    assert response.json()["display_name"] == "Updated Organization"

    # Delete organization
    response = await client_a.delete(f"/api/organizations/{organization_id}")
    assert response.status_code == 200

    # Verify it's gone
    response = await client_a.get(f"/api/organizations/{organization_id}")
    assert response.status_code == 404
