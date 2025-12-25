from app.tests.fixtures_clients import UserClient


async def test_list_namespaces_returns_correct_structure(client_a: UserClient):
    response = await client_a.get("/api/namespaces")
    assert response.status_code == 200

    data = response.json()
    assert "results" in data
    assert "total" in data
    assert isinstance(data["results"], list)


async def test_namespace_data_structure_validation(client_a: UserClient):
    response = await client_a.get("/api/namespaces")
    assert response.status_code == 200

    data = response.json()
    namespaces = data["results"]

    if len(namespaces) > 0:
        namespace = namespaces[0]
        assert "id" in namespace
        # Namespaces are now organization-owned, not user-owned
        assert "organization" in namespace


async def test_namespaces_access_control_between_users(
    client_a: UserClient, client_b: UserClient
):
    # Get namespaces for user A
    response_a = await client_a.get("/api/namespaces")
    assert response_a.status_code == 200
    namespaces_a = response_a.json()["results"]

    # Get namespaces for user B
    response_b = await client_b.get("/api/namespaces")
    assert response_b.status_code == 200
    namespaces_b = response_b.json()["results"]

    # Users should not share organization namespace access
    a_org_namespaces = [
        ns
        for ns in namespaces_a
        if ns.get("organization")
        and ns["organization"]["id"] == str(client_a.organization.id)
    ]
    b_org_namespaces = [
        ns
        for ns in namespaces_b
        if ns.get("organization")
        and ns["organization"]["id"] == str(client_b.organization.id)
    ]

    # Each user should have their own organization namespace
    assert len(a_org_namespaces) == 1
    assert len(b_org_namespaces) == 1

    # Organization namespaces should not overlap
    a_org_ids = [ns["id"] for ns in a_org_namespaces]
    b_org_ids = [ns["id"] for ns in b_org_namespaces]
    assert not set(a_org_ids).intersection(set(b_org_ids))


async def test_namespace_total_count_matches_list_length(client_a: UserClient):
    response = await client_a.get("/api/namespaces")
    assert response.status_code == 200

    data = response.json()
    assert data["total"] == len(data["results"])
