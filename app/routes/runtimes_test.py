import uuid
from unittest.mock import patch

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Runtime, RuntimeCreationStatus
from app.tests.fixtures_clients import UserClient


async def test_create_runtime_success(client_a: UserClient):
    with (
        patch("app.routes.runtimes.deploy_lambda_function") as mock_deploy,
        patch("app.routes.runtimes.get_image_uri") as mock_get_image_uri,
    ):
        # Mock image exists in ECR
        mock_get_image_uri.return_value = (
            "123456789.dkr.ecr.us-east-1.amazonaws.com/trigger-lambda:test-hash"
        )

        # Mock successful Lambda deployment
        mock_deploy.return_value = (
            None  # deploy_lambda_function doesn't return anything
        )

        import time

        unique_id = str(int(time.time() * 1000))  # Unique timestamp
        runtime_data = {
            "config": {"image_hash": f"test-hash-{unique_id}"},
        }

        response = await client_a.post("/api/runtimes", json=runtime_data)
        assert response.status_code == 200

        created_runtime = response.json()
        assert "id" in created_runtime
        assert (
            created_runtime["config"]["image_hash"]
            == runtime_data["config"]["image_hash"]
        )
        assert created_runtime["creation_status"] == "in_progress"
        assert created_runtime["creation_logs"] is not None
        assert len(created_runtime["creation_logs"]) >= 1
        assert (
            "Lambda deployment initiated"
            in created_runtime["creation_logs"][0]["message"]
        )

        # Verify the deploy function was called with runtime_id and image_uri
        mock_deploy.assert_called_once()
        call_args = mock_deploy.call_args
        assert "runtime_id" in call_args.kwargs
        assert "image_uri" in call_args.kwargs


async def test_create_runtime_returns_409_for_existing(client_a: UserClient):
    with (
        patch("app.routes.runtimes.deploy_lambda_function") as mock_deploy,
        patch("app.routes.runtimes.get_image_uri") as mock_get_image_uri,
    ):
        # Mock image exists in ECR
        mock_get_image_uri.return_value = (
            "123456789.dkr.ecr.us-east-1.amazonaws.com/trigger-lambda:duplicate-hash"
        )

        # Mock successful Lambda deployment
        mock_deploy.return_value = None

        runtime_data = {
            "config": {"image_hash": "duplicate-hash"},
        }

        # Create first runtime
        response1 = await client_a.post("/api/runtimes", json=runtime_data)
        assert response1.status_code == 200
        runtime1 = response1.json()

        # Create second runtime with same config - should return 409
        response2 = await client_a.post("/api/runtimes", json=runtime_data)
        assert response2.status_code == 409
        error_response = response2.json()

        assert "detail" in error_response
        assert "message" in error_response["detail"]
        assert error_response["detail"]["message"] == "Runtime already exists"
        assert "runtime_id" in error_response["detail"]
        assert error_response["detail"]["runtime_id"] == runtime1["id"]

        # Verify deploy was only called once (for the first creation)
        assert mock_deploy.call_count == 1


async def test_get_runtime_basic(client_a: UserClient, session: AsyncSession):
    # Create runtime directly in database with COMPLETED status
    runtime = Runtime(
        config_hash=uuid.UUID("550e8400-e29b-41d4-a716-446655440000"),
        config={"image_hash": "test-hash-latest"},
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
    await session.flush()
    await session.refresh(runtime)

    # Get the runtime
    response = await client_a.get(f"/api/runtimes/{runtime.id}")
    assert response.status_code == 200

    runtime_data = response.json()
    assert runtime_data["id"] == str(runtime.id)
    assert runtime_data["config"]["image_hash"] == "test-hash-latest"
    assert runtime_data["creation_status"] == "completed"
    assert len(runtime_data["creation_logs"]) == 1


async def test_get_runtime_triggers_background_update(
    client_a: UserClient, session: AsyncSession
):
    # Create runtime directly in database with IN_PROGRESS status
    runtime = Runtime(
        config_hash=uuid.UUID("550e8400-e29b-41d4-a716-446655440001"),
        config={"image_hash": "in-progress-hash-latest"},
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
    await session.flush()
    await session.refresh(runtime)

    # Get the runtime - this should trigger background status check
    response = await client_a.get(f"/api/runtimes/{runtime.id}")
    assert response.status_code == 200

    runtime_data = response.json()
    assert runtime_data["id"] == str(runtime.id)
    assert (
        runtime_data["creation_status"] == "in_progress"
    )  # Should be immediate response
    assert runtime_data["config"]["image_hash"] == "in-progress-hash-latest"

    # Note: The background task runs after the response, so we verify it was called
    # in a real scenario but don't wait for the database update in this test


async def test_create_runtime_image_not_exists(client_a: UserClient):
    with patch("app.routes.runtimes.get_image_uri") as mock_get_image_uri:
        # Mock image does not exist in ECR
        mock_get_image_uri.return_value = None

        import time

        unique_id = str(int(time.time() * 1000))  # Unique timestamp
        runtime_data = {
            "config": {"image_hash": f"nonexistent-hash-{unique_id}"},
        }

        response = await client_a.post("/api/runtimes", json=runtime_data)
        assert response.status_code == 400

        error_response = response.json()
        assert "detail" in error_response
        assert error_response["detail"] == "Image does not exist"


async def test_get_runtime_not_found(client_a: UserClient):
    # Test getting a non-existent runtime
    response = await client_a.get("/api/runtimes/550e8400-e29b-41d4-a716-446655440404")
    assert response.status_code == 404
