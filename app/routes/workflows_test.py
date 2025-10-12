from app.tests.fixtures_clients import UserClient


async def test_create_and_retrieve_workflow(client_a: UserClient, session):
    workflow_data = {
        "name": "My Test Workflow",
        "namespace_id": str(client_a.personal_namespace.id),
        "description": "A workflow for testing",
    }
    response = await client_a.post("/api/workflows", json=workflow_data)
    assert response.status_code == 200

    created_workflow = response.json()
    assert created_workflow["name"] == "My Test Workflow"
    assert created_workflow["description"] == "A workflow for testing"

    # Test: Retrieve workflows and verify it appears
    response = await client_a.get("/api/workflows")
    assert response.status_code == 200

    workflows = response.json()["workflows"]
    assert len(workflows) == 1
    assert workflows[0]["name"] == "My Test Workflow"


async def test_create_multiple_workflows(client_a: UserClient):
    # Create first workflow
    response1 = await client_a.post(
        "/api/workflows",
        json={
            "name": "Workflow Alpha",
            "namespace_id": str(client_a.personal_namespace.id),
        },
    )
    assert response1.status_code == 200

    # Create second workflow
    response2 = await client_a.post(
        "/api/workflows",
        json={
            "name": "Workflow Beta",
            "namespace_id": str(client_a.personal_namespace.id),
        },
    )
    assert response2.status_code == 200

    # Verify both appear in list
    response = await client_a.get("/api/workflows")
    assert response.status_code == 200

    workflows = response.json()["workflows"]
    assert len(workflows) == 2

    workflow_names = [w["name"] for w in workflows]
    assert "Workflow Alpha" in workflow_names
    assert "Workflow Beta" in workflow_names


async def test_workflow_with_minimal_data(client_a: UserClient):
    workflow_data = {
        "name": "Minimal Workflow",
        "namespace_id": str(client_a.personal_namespace.id),
    }
    response = await client_a.post("/api/workflows", json=workflow_data)
    assert response.status_code == 200

    created_workflow = response.json()
    assert created_workflow["name"] == "Minimal Workflow"


async def test_list_workflows_returns_correct_structure(client_a: UserClient):
    response = await client_a.get("/api/workflows")
    assert response.status_code == 200

    data = response.json()
    assert "workflows" in data
    assert isinstance(data["workflows"], list)


async def test_workflow_creation_includes_metadata(client_a: UserClient):
    # Create workflow
    workflow_data = {
        "name": "Metadata Workflow",
        "namespace_id": str(client_a.personal_namespace.id),
        "description": "Testing metadata",
    }
    response = await client_a.post("/api/workflows", json=workflow_data)
    assert response.status_code == 200

    created_workflow = response.json()
    assert "id" in created_workflow
    assert "created_by_id" in created_workflow
    assert created_workflow["created_by_id"] == str(client_a.user.id)


async def test_workflows_are_accessible_to_user(
    client_a: UserClient, client_b: UserClient
):
    # Create workflow
    workflow_data = {
        "name": "Accessible Workflow",
        "namespace_id": str(client_a.personal_namespace.id),
    }
    response = await client_a.post("/api/workflows", json=workflow_data)
    assert response.status_code == 200

    # Verify it's accessible to the creator
    response = await client_a.get("/api/workflows")
    assert response.status_code == 200

    workflows = response.json()["workflows"]
    assert len(workflows) == 1
    assert workflows[0]["name"] == "Accessible Workflow"

    # Verify it's not accessible to the other user
    response = await client_b.get("/api/workflows")
    assert response.status_code == 200

    workflows = response.json()["workflows"]
    assert len(workflows) == 0
