from app.tests.fixtures_clients import UserClient


async def test_list_namespaces_returns_correct_structure(client_a: UserClient):
    response = await client_a.get("/api/namespaces")
    assert response.status_code == 200

    data = response.json()
    assert "results" in data
    assert "total" in data
    assert isinstance(data["results"], list)


async def test_list_namespaces_includes_personal_namespace(client_a: UserClient):
    response = await client_a.get("/api/namespaces")
    assert response.status_code == 200

    data = response.json()
    namespaces = data["results"]
    assert len(namespaces) >= 1

    # Verify personal namespace is included
    personal_namespace_ids = [
        ns["id"] for ns in namespaces if ns["user_owner_id"] == str(client_a.user.id)
    ]
    assert len(personal_namespace_ids) >= 1


async def test_namespace_data_structure_validation(client_a: UserClient):
    response = await client_a.get("/api/namespaces")
    assert response.status_code == 200

    data = response.json()
    namespaces = data["results"]

    if len(namespaces) > 0:
        namespace = namespaces[0]
        assert "id" in namespace
        assert "name" in namespace
        assert "display_name" in namespace
        assert "user_owner_id" in namespace
        assert "organization_owner_id" in namespace


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

    # Users should not share personal namespace access
    a_personal_namespaces = [
        ns for ns in namespaces_a if ns["user_owner_id"] == str(client_a.user.id)
    ]
    b_personal_namespaces = [
        ns for ns in namespaces_b if ns["user_owner_id"] == str(client_b.user.id)
    ]

    # Each user should have their own personal namespace
    assert len(a_personal_namespaces) == 1
    assert len(b_personal_namespaces) == 1

    # Personal namespaces should not overlap
    a_personal_ids = [ns["id"] for ns in a_personal_namespaces]
    b_personal_ids = [ns["id"] for ns in b_personal_namespaces]
    assert not set(a_personal_ids).intersection(set(b_personal_ids))


async def test_namespace_total_count_matches_list_length(client_a: UserClient):
    response = await client_a.get("/api/namespaces")
    assert response.status_code == 200

    data = response.json()
    assert data["total"] == len(data["results"])
