import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    IncomingWebhook,
    Namespace,
    Organization,
    OrganizationMember,
    OrganizationRole,
    Provider,
    Trigger,
    User,
    Workflow,
)
from app.services.trigger_service import TriggerService
from app.utils.encryption import encrypt_secret


async def create_test_namespace(session: AsyncSession) -> tuple[UUID, UUID]:
    """Create a test user and organization-owned namespace, returning (user_id, namespace_id)."""
    user = User(
        workos_user_id=f"test_user_{uuid4()}",
        email="test@example.com",
    )
    session.add(user)
    await session.flush()

    # Create organization for the user
    org = Organization(
        name=f"test-org-{uuid4().hex[:8]}",
        display_name="Test Organization",
    )
    session.add(org)
    await session.flush()

    # Add user as org member
    org_member = OrganizationMember(
        organization_id=org.id,
        user_id=user.id,
        role=OrganizationRole.OWNER,
    )
    session.add(org_member)
    await session.flush()

    # Create organization-owned namespace
    namespace = Namespace(organization_owner_id=org.id)
    session.add(namespace)
    await session.flush()

    return user.id, namespace.id


async def create_test_workflow(
    session: AsyncSession, namespace_id: UUID, user_id: UUID | None = None
) -> UUID:
    """Create a test workflow, returning workflow_id."""
    workflow = Workflow(
        name="Test Workflow",
        namespace_id=namespace_id,
        created_by_id=user_id,
    )
    session.add(workflow)
    await session.flush()
    return workflow.id


@pytest.mark.asyncio
async def test_sync_triggers_create_new_gitlab_trigger(session):
    """Test creating a new GitLab trigger."""
    # Create test workflow and namespace
    user_id, namespace_id = await create_test_namespace(session)
    workflow_id = await create_test_workflow(session, namespace_id, user_id)

    # Create a provider
    provider = Provider(
        id=uuid4(),
        namespace_id=namespace_id,
        type="gitlab",
        alias="default",
        encrypted_config=encrypt_secret(
            json.dumps({"url": "https://gitlab.com", "accessToken": "test-token"})
        ),
    )
    session.add(provider)
    await session.flush()

    # Mock the GitLab API calls
    with patch("httpx.AsyncClient") as mock_client:
        mock_response = MagicMock()
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
    user_id, namespace_id = await create_test_namespace(session)
    workflow_id = await create_test_workflow(session, namespace_id, user_id)

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
    assert webhooks_info[0]["path"] == f"/webhook/{workflow_id}/custom"
    assert webhooks_info[0]["method"] == "POST"

    trigger_result = await session.execute(
        select(Trigger).where(Trigger.workflow_id == workflow_id)
    )
    trigger = trigger_result.scalar_one()
    assert trigger.state["path"] == f"/webhook/{workflow_id}/custom"
    assert trigger.state["method"] == "POST"
    assert trigger.state["webhook_url"].endswith(f"/webhook/{workflow_id}/custom")

    webhook_result = await session.execute(
        select(IncomingWebhook).where(IncomingWebhook.trigger_id == trigger.id)
    )
    incoming_webhook = webhook_result.scalar_one()
    assert incoming_webhook.path == f"/webhook/{workflow_id}/custom"
    assert incoming_webhook.method == "POST"


@pytest.mark.asyncio
async def test_sync_triggers_remove_old_trigger(session):
    """Test removing a trigger that no longer exists."""
    user_id, namespace_id = await create_test_namespace(session)
    workflow_id = await create_test_workflow(session, namespace_id, user_id)

    # Create provider
    provider = Provider(
        id=uuid4(),
        namespace_id=namespace_id,
        type="gitlab",
        alias="default",
        encrypted_config=encrypt_secret(
            json.dumps({"url": "https://gitlab.com", "accessToken": "test-token"})
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
    user_id, namespace_id = await create_test_namespace(session)
    workflow_id = await create_test_workflow(session, namespace_id, user_id)

    # Create provider
    provider = Provider(
        id=uuid4(),
        namespace_id=namespace_id,
        type="gitlab",
        alias="default",
        encrypted_config=encrypt_secret(
            json.dumps({"url": "https://gitlab.com", "accessToken": "test-token"})
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
    user_id, namespace_id = await create_test_namespace(session)
    workflow_id = await create_test_workflow(session, namespace_id, user_id)

    # Create provider
    provider = Provider(
        id=uuid4(),
        namespace_id=namespace_id,
        type="gitlab",
        alias="default",
        encrypted_config=encrypt_secret(
            json.dumps({"url": "https://gitlab.com", "accessToken": "test-token"})
        ),
    )
    session.add(provider)
    await session.flush()

    # Mock GitLab API calls for group webhook
    with patch("httpx.AsyncClient") as mock_client:
        mock_response = MagicMock()
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
    user_id, namespace_id = await create_test_namespace(session)
    workflow_id = await create_test_workflow(session, namespace_id, user_id)

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

    with pytest.raises(HTTPException, match="Provider gitlab:nonexistent not found"):
        await trigger_service.sync_triggers(
            workflow_id=workflow_id,
            namespace_id=namespace_id,
            new_triggers_metadata=triggers_metadata,
        )


@pytest.mark.asyncio
async def test_auto_create_kvstore_provider(session):
    """Test that KVStore provider is auto-created when it doesn't exist."""
    user_id, namespace_id = await create_test_namespace(session)
    workflow_id = await create_test_workflow(session, namespace_id, user_id)

    # Verify kvstore provider doesn't exist yet
    result = await session.execute(
        select(Provider).where(
            Provider.namespace_id == namespace_id,
            Provider.type == "kvstore",
            Provider.alias == "my-store",
        )
    )
    assert result.scalar_one_or_none() is None

    triggers_metadata = []

    trigger_service = TriggerService(session)
    await trigger_service.sync_triggers(
        workflow_id=workflow_id,
        namespace_id=namespace_id,
        new_triggers_metadata=triggers_metadata,
    )

    # Now manually trigger provider creation by using _ensure_provider_exists
    from app.services.trigger_service import _ensure_provider_exists

    await _ensure_provider_exists(session, namespace_id, "kvstore", "my-store")
    await session.flush()

    # Verify kvstore provider was auto-created
    result = await session.execute(
        select(Provider).where(
            Provider.namespace_id == namespace_id,
            Provider.type == "kvstore",
            Provider.alias == "my-store",
        )
    )
    provider = result.scalar_one()
    assert provider is not None
    assert provider.type == "kvstore"
    assert provider.alias == "my-store"


@pytest.mark.asyncio
async def test_builtin_provider_auto_created(session):
    """Test that builtin provider is auto-created when syncing triggers."""
    user_id, namespace_id = await create_test_namespace(session)
    workflow_id = await create_test_workflow(session, namespace_id, user_id)

    # Verify builtin provider doesn't exist yet
    result = await session.execute(
        select(Provider).where(
            Provider.namespace_id == namespace_id,
            Provider.type == "builtin",
            Provider.alias == "default",
        )
    )
    assert result.scalar_one_or_none() is None

    triggers_metadata = [
        {
            "type": "webhook",
            "provider_type": "builtin",
            "provider_alias": "default",
            "trigger_type": "onWebhook",
            "input": {"path": "/test", "method": "POST"},
        }
    ]

    trigger_service = TriggerService(session)
    await trigger_service.sync_triggers(
        workflow_id=workflow_id,
        namespace_id=namespace_id,
        new_triggers_metadata=triggers_metadata,
    )

    # Verify builtin provider was auto-created
    result = await session.execute(
        select(Provider).where(
            Provider.namespace_id == namespace_id,
            Provider.type == "builtin",
            Provider.alias == "default",
        )
    )
    provider = result.scalar_one()
    assert provider is not None
    assert provider.type == "builtin"
    assert provider.alias == "default"
