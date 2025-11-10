import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    IncomingWebhook,
    Provider,
    Runtime,
    Trigger,
    Workflow,
    WorkflowDeployment,
    WorkflowDeploymentStatus,
)
from app.tests.fixtures_clients import UserClient


@pytest.fixture(scope="function")
async def workflow(client_a: UserClient, session: AsyncSession):
    workflow = Workflow(
        name="Test Webhook Workflow",
        namespace_id=client_a.personal_namespace.id,
        created_by_id=client_a.user.id,
    )
    session.add(workflow)
    await session.flush()
    await session.refresh(workflow)
    return workflow


@pytest.fixture(scope="function")
async def provider(client_a: UserClient, session: AsyncSession):
    provider = Provider(
        namespace_id=client_a.personal_namespace.id,
        type="gitlab",
        alias="test-gitlab",
        encrypted_config="encrypted_config_data",
    )
    session.add(provider)
    await session.flush()
    await session.refresh(provider)
    return provider


@pytest.fixture(scope="function")
async def trigger(workflow: Workflow, provider: Provider, session: AsyncSession):
    trigger = Trigger(
        workflow_id=workflow.id,
        provider_id=provider.id,
        trigger_type="onMergeRequestComment",
        input={"projectId": "123456"},
    )
    session.add(trigger)
    await session.flush()
    await session.refresh(trigger)
    return trigger


@pytest.fixture(scope="function")
async def incoming_webhook(trigger: Trigger, session: AsyncSession):
    webhook = IncomingWebhook(
        trigger_id=trigger.id,
        path="/webhook/test-webhook-path",  # Full path with /webhook prefix
        method="POST",
    )
    session.add(webhook)
    await session.flush()
    await session.refresh(webhook)
    return webhook


@pytest.fixture(scope="function")
async def runtime(session: AsyncSession):
    runtime = Runtime(
        config={"image_hash": "test-hash-123"},
        config_hash=uuid.uuid4(),
    )
    session.add(runtime)
    await session.flush()
    await session.refresh(runtime)
    return runtime


@pytest.fixture(scope="function")
async def active_deployment(
    workflow: Workflow, runtime: Runtime, client_a: UserClient, session: AsyncSession
):
    deployment = WorkflowDeployment(
        workflow_id=workflow.id,
        runtime_id=runtime.id,
        deployed_by_id=client_a.user.id,
        user_code={
            "files": {"main.ts": "console.log('test')"},
            "entrypoint": "main.ts",
        },
        status=WorkflowDeploymentStatus.ACTIVE,
    )
    session.add(deployment)
    await session.flush()
    await session.refresh(deployment)
    return deployment


async def test_webhook_not_found(client_a: UserClient):
    """Test webhook endpoint returns 404 for non-existent webhook path."""
    response = await client_a.post("/webhook/non-existent-path")
    assert response.status_code == 404
    assert response.json() == {"error": "Webhook not found"}


@patch(
    "app.routes.webhooks.centrifugo_service.publish_dev_webhook_event",
    new_callable=AsyncMock,
)
@patch("app.routes.webhooks.runtime_factory")
@patch("app.routes.webhooks.get_image_uri")
async def test_webhook_successful_invocation(
    mock_get_image_uri,
    mock_runtime_factory,
    mock_centrifugo,
    client_a: UserClient,
    session: AsyncSession,
    incoming_webhook: IncomingWebhook,
    active_deployment: WorkflowDeployment,
):
    """Test successful webhook invocation with active deployment."""
    # Mock get_image_uri to return a valid image URI
    mock_get_image_uri.return_value = "test-registry.amazonaws.com/test-image@sha256:abc123"

    # Mock runtime implementation
    mock_runtime_impl = AsyncMock()
    mock_runtime_impl.invoke_trigger.return_value = None
    mock_runtime_factory.return_value = mock_runtime_impl

    # Test webhook payload
    webhook_payload = {
        "event": "merge_request_comment",
        "project_id": "123456",
        "comment": "Test comment",
    }

    response = await client_a.post(
        "/webhook/test-webhook-path",
        json=webhook_payload,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 200
    response_data = response.json()
    assert response_data["status"] == "invoked"
    assert "webhook_id" in response_data
    assert "workflow_id" in response_data

    # Verify Centrifugo was called for dev webhook event
    mock_centrifugo.assert_called_once()
    centrifugo_args = mock_centrifugo.call_args[1]
    assert centrifugo_args["workflow_id"] == incoming_webhook.trigger.workflow_id
    assert centrifugo_args["webhook_data"]["body"] == webhook_payload
    assert centrifugo_args["webhook_data"]["path"] == "/webhook/test-webhook-path"
    assert centrifugo_args["webhook_data"]["method"] == "POST"

    # Verify runtime factory was called
    mock_runtime_factory.assert_called_once()
    # Verify invoke_trigger was called on the runtime implementation
    mock_runtime_impl.invoke_trigger.assert_called_once()
    invoke_trigger_args = mock_runtime_impl.invoke_trigger.call_args[1]
    assert invoke_trigger_args["trigger_id"] == str(incoming_webhook.trigger.id)
    assert invoke_trigger_args["payload"].body == webhook_payload


@patch(
    "app.routes.webhooks.centrifugo_service.publish_dev_webhook_event",
    new_callable=AsyncMock,
)
async def test_webhook_no_active_deployment(
    mock_centrifugo,
    client_a: UserClient,
    session: AsyncSession,
    incoming_webhook: IncomingWebhook,
):
    """Test webhook returns 200 when no active deployment exists (only dev mode)."""
    webhook_payload = {"test": "data"}

    response = await client_a.post(
        "/webhook/test-webhook-path",
        json=webhook_payload,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"message": "No active deployment found, only sent to dev mode."}

    # Verify Centrifugo was still called for dev webhook event
    mock_centrifugo.assert_called_once()
