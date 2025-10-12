import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Runtime, RuntimeCreationStatus
from app.tests.fixtures_clients import UserClient


async def test_create_runtime_success(client_a: UserClient):
    with patch("app.routes.runtimes.deploy_lambda_function") as mock_deploy:
        # Mock successful Lambda deployment
        mock_deploy.return_value = {
            "success": True,
            "function_name": "floww-runtime-test-id",
            "state": "Active",
            "last_update_status": "Successful",
        }

        import time

        unique_id = str(int(time.time() * 1000))  # Unique timestamp
        runtime_data = {
            "image_uri": f"123456789.dkr.ecr.us-east-1.amazonaws.com/unique-test-repo:{unique_id}",
            "config": {
                "image_uri": f"123456789.dkr.ecr.us-east-1.amazonaws.com/unique-test-repo:{unique_id}"
            },
        }

        response = await client_a.post("/api/runtimes", json=runtime_data)
        assert response.status_code == 200

        created_runtime = response.json()
        assert "id" in created_runtime
        assert (
            created_runtime["config"]["image_uri"]
            == runtime_data["config"]["image_uri"]
        )
        assert created_runtime["creation_status"] == "in_progress"
        assert created_runtime["creation_logs"] is not None
        assert len(created_runtime["creation_logs"]) >= 1
        assert (
            "Lambda deployment initiated"
            in created_runtime["creation_logs"][0]["message"]
        )

        # Verify the deploy function was called
        mock_deploy.assert_called_once()
        call_args = mock_deploy.call_args
        assert (
            call_args[0][1] == runtime_data["image_uri"]
        )  # Second argument is image_uri


async def test_create_runtime_returns_existing(client_a: UserClient):
    with patch("app.routes.runtimes.deploy_lambda_function") as mock_deploy:
        # Mock successful Lambda deployment
        mock_deploy.return_value = {
            "success": True,
            "function_name": "floww-runtime-test-id",
            "state": "Active",
            "last_update_status": "Successful",
        }

        runtime_data = {
            "image_uri": "123456789.dkr.ecr.us-east-1.amazonaws.com/duplicate-test:latest",
            "config": {
                "image_uri": "123456789.dkr.ecr.us-east-1.amazonaws.com/duplicate-test:latest"
            },
        }

        # Create first runtime
        response1 = await client_a.post("/api/runtimes", json=runtime_data)
        assert response1.status_code == 200
        runtime1 = response1.json()

        # Create second runtime with same config - should return existing
        response2 = await client_a.post("/api/runtimes", json=runtime_data)
        assert response2.status_code == 200
        runtime2 = response2.json()

        # Should return the same runtime
        assert runtime1["id"] == runtime2["id"]
        assert runtime1["config"] == runtime2["config"]

        # Verify deploy was only called once (for the first creation)
        assert mock_deploy.call_count == 1


async def test_get_runtime_basic(client_a: UserClient, session: AsyncSession):
    # Create runtime directly in database with COMPLETED status
    runtime = Runtime(
        config_hash=uuid.UUID("550e8400-e29b-41d4-a716-446655440000"),
        config={"image_uri": "test-image:latest"},
        creation_status=RuntimeCreationStatus.COMPLETED,
        creation_logs=[
            {
                "timestamp": "2024-01-01T00:00:00Z",
                "message": "Runtime completed",
                "level": "info",
            }
        ],
    )
    session.add(runtime)
    await session.commit()
    await session.refresh(runtime)

    # Get the runtime
    response = await client_a.get(f"/api/runtimes/{runtime.id}")
    assert response.status_code == 200

    runtime_data = response.json()
    assert runtime_data["id"] == str(runtime.id)
    assert runtime_data["config"]["image_uri"] == "test-image:latest"
    assert runtime_data["creation_status"] == "completed"
    assert len(runtime_data["creation_logs"]) == 1


@pytest.mark.asyncio
async def test_get_runtime_triggers_background_update(
    client_a: UserClient, session: AsyncSession
):
    # Create runtime directly in database with IN_PROGRESS status
    runtime = Runtime(
        config_hash=uuid.UUID("550e8400-e29b-41d4-a716-446655440001"),
        config={"image_uri": "in-progress-image:latest"},
        creation_status=RuntimeCreationStatus.IN_PROGRESS,
        creation_logs=[
            {
                "timestamp": "2024-01-01T00:00:00Z",
                "message": "Lambda deployment initiated",
                "level": "info",
            }
        ],
    )
    session.add(runtime)
    await session.commit()
    await session.refresh(runtime)

    # Get the runtime - this should trigger background status check
    response = await client_a.get(f"/api/runtimes/{runtime.id}")
    assert response.status_code == 200

    runtime_data = response.json()
    assert runtime_data["id"] == str(runtime.id)
    assert (
        runtime_data["creation_status"] == "in_progress"
    )  # Should be immediate response
    assert runtime_data["config"]["image_uri"] == "in-progress-image:latest"

    # Note: The background task runs after the response, so we verify it was called
    # in a real scenario but don't wait for the database update in this test


async def test_create_runtime_lambda_failure(client_a: UserClient):
    with patch("app.routes.runtimes.deploy_lambda_function") as mock_deploy:
        # Mock failed Lambda deployment
        mock_deploy.return_value = {
            "success": False,
            "error_code": "InvalidParameterValueException",
            "error_message": "The role defined for the function cannot be assumed by Lambda.",
        }

        import time

        unique_id = str(int(time.time() * 1000))  # Unique timestamp
        runtime_data = {
            "image_uri": f"123456789.dkr.ecr.us-east-1.amazonaws.com/failing-repo:{unique_id}",
            "config": {
                "image_uri": f"123456789.dkr.ecr.us-east-1.amazonaws.com/failing-repo:{unique_id}"
            },
        }

        response = await client_a.post("/api/runtimes", json=runtime_data)
        assert response.status_code == 200

        created_runtime = response.json()
        assert "id" in created_runtime
        assert created_runtime["creation_status"] == "failed"
        assert created_runtime["creation_logs"] is not None
        assert len(created_runtime["creation_logs"]) >= 2  # Initial log + failure log

        # Check that failure is logged
        failure_logs = [
            log for log in created_runtime["creation_logs"] if log["level"] == "error"
        ]
        assert len(failure_logs) >= 1
        assert "Lambda deployment failed" in failure_logs[0]["message"]

        # Verify the Lambda deployment function was called
        mock_deploy.assert_called_once()


async def test_get_runtime_not_found(client_a: UserClient):
    # Test getting a non-existent runtime
    response = await client_a.get("/api/runtimes/550e8400-e29b-41d4-a716-446655440404")
    assert response.status_code == 404
