import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import IncomingWebhook, Provider, Trigger
from app.services.trigger_service import TriggerService
from app.utils.encryption import encrypt_secret


@pytest.mark.asyncio
async def test_sync_triggers_create_new_gitlab_trigger(session):
    """Test creating a new GitLab trigger."""
    # Create test workflow and namespace
    workflow_id = uuid4()
    namespace_id = uuid4()

    # Create a provider
    provider = Provider(
        id=uuid4(),
        namespace_id=namespace_id,
        type="gitlab",
        alias="default",
        encrypted_config=encrypt_secret(
            json.dumps({"url": "https://gitlab.com", "token": "test-token"})
        ),
    )
    session.add(provider)
    await session.flush()

    # Mock the GitLab API calls
    with patch("httpx.AsyncClient") as mock_client:
        mock_response = AsyncMock()
        mock_response.json.return_value = {"id": 12345}
        mock_response.raise_for_status = MagicMock()

        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        # Prepare trigger metadata
        triggers_metadata = [
            {
                "type": "webhook",
                "provider_type": "gitlab",
                "provider_alias": "default",
                "trigger_type": "onMergeRequestComment",
                "input": {"projectId": "123456"},
            }
        ]

        # Execute
        trigger_service = TriggerService(session)
        webhooks_info = await trigger_service.sync_triggers(
            workflow_id=workflow_id,
            namespace_id=namespace_id,
            new_triggers_metadata=triggers_metadata,
        )

        # Verify webhook info returned
        assert len(webhooks_info) == 1
        assert webhooks_info[0]["method"] == "POST"
        assert "/webhook/" in webhooks_info[0]["path"]

        # Verify Trigger created in DB
        result = await session.execute(
            select(Trigger).where(Trigger.workflow_id == workflow_id)
        )
        triggers = list(result.scalars().all())
        assert len(triggers) == 1
        assert triggers[0].trigger_type == "onMergeRequestComment"
        assert triggers[0].input == {"projectId": "123456"}
        assert triggers[0].state["webhook_id"] == 12345
        assert triggers[0].state["project_id"] == "123456"

        # Verify IncomingWebhook created
        result = await session.execute(
            select(IncomingWebhook).where(IncomingWebhook.trigger_id == triggers[0].id)
        )
        webhook = result.scalar_one()
        assert webhook.method == "POST"
        assert "/webhook/" in webhook.path


@pytest.mark.asyncio
async def test_builtin_webhook_custom_path_respected(session):
    """Test that builtin webhook triggers honor custom paths."""
    workflow_id = uuid4()
    namespace_id = uuid4()

    trigger_service = TriggerService(session)
    webhooks_info = await trigger_service.sync_triggers(
        workflow_id=workflow_id,
        namespace_id=namespace_id,
        new_triggers_metadata=[
            {
                "type": "webhook",
                "provider_type": "builtin",
                "provider_alias": "default",
                "trigger_type": "onWebhook",
                "input": {"path": "/custom", "method": "post"},
            }
        ],
    )

    assert len(webhooks_info) == 1
    assert webhooks_info[0]["path"] == "/webhook/custom"
    assert webhooks_info[0]["method"] == "POST"

    trigger_result = await session.execute(
        select(Trigger).where(Trigger.workflow_id == workflow_id)
    )
    trigger = trigger_result.scalar_one()
    assert trigger.state["path"] == "/webhook/custom"
    assert trigger.state["method"] == "POST"
    assert trigger.state["webhook_url"].endswith("/webhook/custom")

    webhook_result = await session.execute(
        select(IncomingWebhook).where(IncomingWebhook.trigger_id == trigger.id)
    )
    incoming_webhook = webhook_result.scalar_one()
    assert incoming_webhook.path == "/webhook/custom"
    assert incoming_webhook.method == "POST"


@pytest.mark.asyncio
async def test_sync_triggers_remove_old_trigger(session):
    """Test removing a trigger that no longer exists."""
    workflow_id = uuid4()
    namespace_id = uuid4()

    # Create provider
    provider = Provider(
        id=uuid4(),
        namespace_id=namespace_id,
        type="gitlab",
        alias="default",
        encrypted_config=encrypt_secret(
            json.dumps({"url": "https://gitlab.com", "token": "test-token"})
        ),
    )
    session.add(provider)
    await session.flush()

    # Create existing trigger
    trigger = Trigger(
        id=uuid4(),
        workflow_id=workflow_id,
        provider_id=provider.id,
        trigger_type="onMergeRequestComment",
        input={"projectId": "123456"},
        state={"webhook_id": 12345, "project_id": "123456"},
    )
    session.add(trigger)

    # Create associated webhook
    incoming_webhook = IncomingWebhook(
        id=uuid4(), trigger_id=trigger.id, path="/webhook/test", method="POST"
    )
    session.add(incoming_webhook)
    await session.flush()

    # Mock GitLab API delete call
    with patch("httpx.AsyncClient") as mock_client:
        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()

        mock_client.return_value.__aenter__.return_value.delete = AsyncMock(
            return_value=mock_response
        )

        # Execute with empty triggers (removing all)
        trigger_service = TriggerService(session)
        webhooks_info = await trigger_service.sync_triggers(
            workflow_id=workflow_id, namespace_id=namespace_id, new_triggers_metadata=[]
        )

        # Verify no webhooks returned
        assert len(webhooks_info) == 0

        # Verify trigger deleted
        result = await session.execute(select(Trigger).where(Trigger.id == trigger.id))
        assert result.scalar_one_or_none() is None

        # Verify GitLab API was called to delete webhook
        mock_client.return_value.__aenter__.return_value.delete.assert_called_once()


@pytest.mark.asyncio
async def test_sync_triggers_keep_unchanged_trigger(session):
    """Test that unchanged triggers are refreshed but not recreated."""
    workflow_id = uuid4()
    namespace_id = uuid4()

    # Create provider
    provider = Provider(
        id=uuid4(),
        namespace_id=namespace_id,
        type="gitlab",
        alias="default",
        encrypted_config=encrypt_secret(
            json.dumps({"url": "https://gitlab.com", "token": "test-token"})
        ),
    )
    session.add(provider)
    await session.flush()

    # Create existing trigger
    trigger = Trigger(
        id=uuid4(),
        workflow_id=workflow_id,
        provider_id=provider.id,
        trigger_type="onMergeRequestComment",
        input={"projectId": "123456"},
        state={"webhook_id": 12345, "project_id": "123456"},
    )
    session.add(trigger)

    # Create associated webhook
    incoming_webhook = IncomingWebhook(
        id=uuid4(), trigger_id=trigger.id, path="/webhook/test", method="POST"
    )
    session.add(incoming_webhook)
    await session.flush()

    # Mock GitLab API get call (for refresh)
    with patch("httpx.AsyncClient") as mock_client:
        mock_response = AsyncMock()
        mock_response.json.return_value = {"id": 12345}
        mock_response.raise_for_status = MagicMock()

        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=mock_response
        )

        # Execute with same trigger
        trigger_service = TriggerService(session)
        webhooks_info = await trigger_service.sync_triggers(
            workflow_id=workflow_id,
            namespace_id=namespace_id,
            new_triggers_metadata=[
                {
                    "type": "webhook",
                    "provider_type": "gitlab",
                    "provider_alias": "default",
                    "trigger_type": "onMergeRequestComment",
                    "input": {"projectId": "123456"},
                }
            ],
        )

        # Verify webhook info returned
        assert len(webhooks_info) == 1

        # Verify trigger still exists with same ID
        result = await session.execute(select(Trigger).where(Trigger.id == trigger.id))
        assert result.scalar_one() is not None

        # Verify GitLab API was called to refresh (GET)
        mock_client.return_value.__aenter__.return_value.get.assert_called_once()


@pytest.mark.asyncio
async def test_sync_triggers_handles_group_webhooks(session):
    """Test creating a group-level GitLab webhook."""
    workflow_id = uuid4()
    namespace_id = uuid4()

    # Create provider
    provider = Provider(
        id=uuid4(),
        namespace_id=namespace_id,
        type="gitlab",
        alias="default",
        encrypted_config=encrypt_secret(
            json.dumps({"url": "https://gitlab.com", "token": "test-token"})
        ),
    )
    session.add(provider)
    await session.flush()

    # Mock GitLab API calls for group webhook
    with patch("httpx.AsyncClient") as mock_client:
        mock_response = AsyncMock()
        mock_response.json.return_value = {"id": 99999}
        mock_response.raise_for_status = MagicMock()

        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        # Prepare trigger metadata with groupId
        triggers_metadata = [
            {
                "type": "webhook",
                "provider_type": "gitlab",
                "provider_alias": "default",
                "trigger_type": "onMergeRequestComment",
                "input": {"groupId": "my-group"},
            }
        ]

        # Execute
        trigger_service = TriggerService(session)
        webhooks_info = await trigger_service.sync_triggers(
            workflow_id=workflow_id,
            namespace_id=namespace_id,
            new_triggers_metadata=triggers_metadata,
        )

        # Verify webhook created
        assert len(webhooks_info) == 1

        # Verify Trigger state has group_id
        result = await session.execute(
            select(Trigger).where(Trigger.workflow_id == workflow_id)
        )
        trigger = result.scalar_one()
        assert trigger.state["webhook_id"] == 99999
        assert trigger.state["group_id"] == "my-group"
        assert trigger.state.get("project_id") is None


@pytest.mark.asyncio
async def test_sync_triggers_provider_not_found(session):
    """Test error handling when provider is not configured."""
    workflow_id = uuid4()
    namespace_id = uuid4()

    triggers_metadata = [
        {
            "type": "webhook",
            "provider_type": "gitlab",
            "provider_alias": "nonexistent",
            "trigger_type": "onMergeRequestComment",
            "input": {"projectId": "123456"},
        }
    ]

    trigger_service = TriggerService(session)

    with pytest.raises(ValueError, match="Provider gitlab:nonexistent not found"):
        await trigger_service.sync_triggers(
            workflow_id=workflow_id,
            namespace_id=namespace_id,
            new_triggers_metadata=triggers_metadata,
        )
