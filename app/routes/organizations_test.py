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
    organization_id = created_organization["id"]

    # Test: Retrieve organizations and verify it appears
    # User has their auto-created personal org plus the new one
    response = await client_a.get("/api/organizations")
    assert response.status_code == 200

    organizations = response.json()["results"]
    # User has 2 orgs: auto-created personal org + new test-org
    assert len(organizations) == 2
    org_names = [org["name"] for org in organizations]
    assert "test-org" in org_names

    # Test: Verify a namespace was automatically created for the organization
    response = await client_a.get("/api/namespaces")
    assert response.status_code == 200

    namespaces = response.json()["results"]
    org_namespaces = [
        ns
        for ns in namespaces
        if ns.get("organization") and ns["organization"]["id"] == organization_id
    ]
    assert len(org_namespaces) == 1
    assert org_namespaces[0]["organization"]["name"] == "test-org"
    assert org_namespaces[0]["organization"]["display_name"] == "My Test Organization"


async def test_list_organizations_returns_correct_structure(client_a: UserClient):
    response = await client_a.get("/api/organizations")
    assert response.status_code == 200

    data = response.json()
    assert "results" in data
    assert isinstance(data["results"], list)


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

    # Verify it's accessible to the creator (along with their personal org)
    response = await client_a.get("/api/organizations")
    assert response.status_code == 200

    organizations = response.json()["results"]
    # User has 2 orgs: auto-created personal org + new user-a-org
    assert len(organizations) == 2
    org_names = [org["name"] for org in organizations]
    assert "user-a-org" in org_names

    # Verify user-a-org is not accessible to the other user
    response = await client_b.get("/api/organizations")
    assert response.status_code == 200

    organizations = response.json()["results"]
    # client_b only has their personal org (auto-created)
    assert len(organizations) == 1
    org_names = [org["name"] for org in organizations]
    assert "user-a-org" not in org_names


async def test_create_duplicate_organization_name_returns_409(client_a: UserClient):
    organization_data = {
        "name": "duplicate-org",
        "display_name": "Duplicate Organization",
    }
    # Create first organization
    response = await client_a.post("/api/organizations", json=organization_data)
    assert response.status_code == 200

    # Try to create another organization with the same name
    response = await client_a.post("/api/organizations", json=organization_data)
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"].lower()


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
    response = await client_a.patch(
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


# Member management tests


async def test_update_member_role_requires_admin_or_owner(
    client_a: UserClient, client_b: UserClient
):
    # Create organization (client_a is owner)
    organization_data = {
        "name": "role-test-org",
        "display_name": "Role Test Organization",
    }
    response = await client_a.post("/api/organizations", json=organization_data)
    assert response.status_code == 200
    organization_id = response.json()["id"]

    # Add client_b as a regular member
    add_member_data = {"user_id": str(client_b.user.id), "role": "member"}
    response = await client_a.post(
        f"/api/organizations/{organization_id}/members", json=add_member_data
    )
    assert response.status_code == 200

    # client_b (member) should not be able to update roles
    update_data = {"role": "admin"}
    response = await client_b.patch(
        f"/api/organizations/{organization_id}/members/{client_b.user.id}",
        json=update_data,
    )
    assert response.status_code == 403
    assert "owners and admins" in response.json()["detail"].lower()

    # client_a (owner) should be able to update roles
    response = await client_a.patch(
        f"/api/organizations/{organization_id}/members/{client_b.user.id}",
        json=update_data,
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"


async def test_remove_member_requires_admin_or_owner(
    client_a: UserClient, client_b: UserClient
):
    # Create organization (client_a is owner)
    organization_data = {
        "name": "remove-test-org",
        "display_name": "Remove Test Organization",
    }
    response = await client_a.post("/api/organizations", json=organization_data)
    assert response.status_code == 200
    organization_id = response.json()["id"]

    # Add client_b as a regular member
    add_member_data = {"user_id": str(client_b.user.id), "role": "member"}
    response = await client_a.post(
        f"/api/organizations/{organization_id}/members", json=add_member_data
    )
    assert response.status_code == 200

    # client_b (member) should not be able to remove themselves
    response = await client_b.delete(
        f"/api/organizations/{organization_id}/members/{client_b.user.id}"
    )
    assert response.status_code == 403

    # client_a (owner) should be able to remove members
    response = await client_a.delete(
        f"/api/organizations/{organization_id}/members/{client_b.user.id}"
    )
    assert response.status_code == 200


async def test_cannot_remove_last_owner(client_a: UserClient):
    # Create organization
    organization_data = {
        "name": "last-owner-org",
        "display_name": "Last Owner Organization",
    }
    response = await client_a.post("/api/organizations", json=organization_data)
    assert response.status_code == 200
    organization_id = response.json()["id"]

    # Try to remove the only owner (self)
    response = await client_a.delete(
        f"/api/organizations/{organization_id}/members/{client_a.user.id}"
    )
    assert response.status_code == 400
    assert "last owner" in response.json()["detail"].lower()


async def test_cannot_demote_last_owner(client_a: UserClient):
    # Create organization (client_a is owner)
    organization_data = {
        "name": "demote-owner-org",
        "display_name": "Demote Owner Organization",
    }
    response = await client_a.post("/api/organizations", json=organization_data)
    assert response.status_code == 200
    organization_id = response.json()["id"]

    # Try to demote the only owner to member
    response = await client_a.patch(
        f"/api/organizations/{organization_id}/members/{client_a.user.id}",
        json={"role": "member"},
    )
    assert response.status_code == 400
    assert "last owner" in response.json()["detail"].lower()

    # Try to demote to admin (also not allowed)
    response = await client_a.patch(
        f"/api/organizations/{organization_id}/members/{client_a.user.id}",
        json={"role": "admin"},
    )
    assert response.status_code == 400
    assert "last owner" in response.json()["detail"].lower()


async def test_can_demote_owner_if_another_owner_exists(
    client_a: UserClient, client_b: UserClient
):
    # Create organization (client_a is owner)
    organization_data = {
        "name": "multi-owner-org",
        "display_name": "Multi Owner Organization",
    }
    response = await client_a.post("/api/organizations", json=organization_data)
    assert response.status_code == 200
    organization_id = response.json()["id"]

    # Add client_b as another owner
    add_member_data = {"user_id": str(client_b.user.id), "role": "owner"}
    response = await client_a.post(
        f"/api/organizations/{organization_id}/members", json=add_member_data
    )
    assert response.status_code == 200

    # Now client_a should be able to demote themselves since there's another owner
    response = await client_a.patch(
        f"/api/organizations/{organization_id}/members/{client_a.user.id}",
        json={"role": "admin"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"


async def test_list_organization_members(client_a: UserClient, client_b: UserClient):
    # Create organization
    organization_data = {
        "name": "members-list-org",
        "display_name": "Members List Organization",
    }
    response = await client_a.post("/api/organizations", json=organization_data)
    assert response.status_code == 200
    organization_id = response.json()["id"]

    # Add client_b as a member
    add_member_data = {"user_id": str(client_b.user.id), "role": "member"}
    response = await client_a.post(
        f"/api/organizations/{organization_id}/members", json=add_member_data
    )
    assert response.status_code == 200

    # List members
    response = await client_a.get(f"/api/organizations/{organization_id}/members")
    assert response.status_code == 200

    members = response.json()
    assert len(members) == 2

    # Check that both users are in the list with correct roles
    roles = {m["user"]["id"]: m["role"] for m in members}
    assert roles[str(client_a.user.id)] == "owner"
    assert roles[str(client_b.user.id)] == "member"
