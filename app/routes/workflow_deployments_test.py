import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Runtime, Workflow
from app.tests.fixtures_clients import UserClient


@pytest.fixture(scope="function")
async def workflow_1(client_a: UserClient, session: AsyncSession):
    workflow = Workflow(
        name="Test Workflow 1",
        namespace_id=client_a.personal_namespace.id,
        created_by_id=client_a.user.id,
    )
    session.add(workflow)
    await session.commit()
    await session.refresh(workflow)
    return workflow


@pytest.fixture(scope="function")
async def workflow_2(client_a: UserClient, session: AsyncSession):
    workflow = Workflow(
        name="Test Workflow 2",
        namespace_id=client_a.personal_namespace.id,
        created_by_id=client_a.user.id,
    )
    session.add(workflow)
    await session.commit()
    await session.refresh(workflow)
    return workflow


@pytest.fixture(scope="function")
async def runtime(session: AsyncSession):
    runtime = Runtime(
        config={"image_uri": "test-image:latest"},
        config_hash=uuid.uuid4(),
    )
    session.add(runtime)
    await session.commit()
    await session.refresh(runtime)
    return runtime


async def test_create_and_retrieve_deployment(
    client_a: UserClient,
    session: AsyncSession,
    workflow_1: Workflow,
    runtime: Runtime,
):
    # Test: Create deployment
    deployment_data = {
        "workflow_id": str(workflow_1.id),
        "runtime_id": str(runtime.id),
        "code": {
            "files": {"main.py": "print('Hello, World!')"},
            "entrypoint": "main.py",
        },
    }
    response = await client_a.post("/api/workflow_deployments", json=deployment_data)
    assert response.status_code == 200

    created_deployment = response.json()
    assert created_deployment["workflow_id"] == str(workflow_1.id)
    assert created_deployment["runtime_id"] == str(runtime.id)

    # Test: Retrieve deployments and verify it appears
    response = await client_a.get("/api/workflow_deployments")
    assert response.status_code == 200

    deployments = response.json()["deployments"]
    assert len(deployments) == 1


async def test_deployment_with_complex_code(
    client_a: UserClient,
    session: AsyncSession,
    workflow_1: Workflow,
    runtime: Runtime,
):
    deployment_data = {
        "workflow_id": str(workflow_1.id),
        "runtime_id": str(runtime.id),
        "code": {
            "files": {
                "index.js": "const helper = require('./helper'); helper.run();",
                "helper.js": "exports.run = () => console.log('Hello from helper');",
                "package.json": '{"name": "test-app", "main": "index.js"}',
            },
            "entrypoint": "index.js",
        },
    }
    response = await client_a.post("/api/workflow_deployments", json=deployment_data)
    assert response.status_code == 200

    created_deployment = response.json()
    # Verify the code structure is preserved
    assert "index.js" in str(created_deployment)


async def test_filter_deployments_by_workflow(
    client_a: UserClient,
    session: AsyncSession,
    workflow_1: Workflow,
    workflow_2: Workflow,
    runtime: Runtime,
):
    # Create deployment for workflow1
    deployment_data1 = {
        "workflow_id": str(workflow_1.id),
        "runtime_id": str(runtime.id),
        "code": {"files": {"main.py": "print('workflow1')"}, "entrypoint": "main.py"},
    }
    response = await client_a.post("/api/workflow_deployments", json=deployment_data1)
    assert response.status_code == 200

    # Create deployment for workflow2
    deployment_data2 = {
        "workflow_id": str(workflow_2.id),
        "runtime_id": str(runtime.id),
        "code": {"files": {"main.py": "print('workflow2')"}, "entrypoint": "main.py"},
    }
    response = await client_a.post("/api/workflow_deployments", json=deployment_data2)
    assert response.status_code == 200

    # Test: Filter by workflow1 - should get 1 deployment
    response = await client_a.get(
        f"/api/workflow_deployments?workflow_id={workflow_1.id}"
    )
    assert response.status_code == 200
    deployments = response.json()["deployments"]
    assert len(deployments) == 1

    # Test: Get all deployments - should get 2
    response = await client_a.get("/api/workflow_deployments")
    assert response.status_code == 200
    deployments = response.json()["deployments"]
    assert len(deployments) == 2


async def test_deployment_includes_metadata(
    client_a: UserClient, session: AsyncSession, workflow_1: Workflow, runtime: Runtime
):
    # Create deployment
    deployment_data = {
        "workflow_id": str(workflow_1.id),
        "runtime_id": str(runtime.id),
        "code": {"files": {"app.py": "print('metadata test')"}, "entrypoint": "app.py"},
    }
    response = await client_a.post("/api/workflow_deployments", json=deployment_data)
    assert response.status_code == 200

    created_deployment = response.json()
    assert "id" in created_deployment
    assert "deployed_by_id" in created_deployment
    assert created_deployment["deployed_by_id"] == str(client_a.user.id)
    assert "status" in created_deployment


async def test_list_deployments_returns_correct_structure(client_a: UserClient):
    response = await client_a.get("/api/workflow_deployments")
    assert response.status_code == 200

    data = response.json()
    assert "deployments" in data
    assert isinstance(data["deployments"], list)
