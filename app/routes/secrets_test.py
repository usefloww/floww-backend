from sqlalchemy import select

from app.models import Secret
from app.tests.fixtures_clients import UserClient


# Basic CRUD Flow Tests


async def test_create_and_list_secret(client_a: UserClient):
    """Test creating a secret and verifying it appears in list without value."""
    secret_data = {
        "namespace_id": str(client_a.personal_namespace.id),
        "name": "api_key",
        "provider": "github",
        "value": "ghp_secret123",
    }

    # Create secret
    response = await client_a.post("/api/secrets/", json=secret_data)
    assert response.status_code == 201
    created = response.json()
    assert created["name"] == "api_key"
    assert created["provider"] == "github"
    assert created["namespace_id"] == str(client_a.personal_namespace.id)
    assert "value" not in created  # List response doesn't include value
    assert "id" in created
    assert "created_at" in created

    # List secrets in namespace
    response = await client_a.get(
        f"/api/secrets/namespace/{client_a.personal_namespace.id}"
    )
    assert response.status_code == 200
    secrets = response.json()
    assert len(secrets) == 1
    assert secrets[0]["name"] == "api_key"
    assert secrets[0]["provider"] == "github"
    assert "value" not in secrets[0]  # List doesn't include values


async def test_get_secret_with_decrypted_value(client_a: UserClient):
    """Test getting a secret by ID returns decrypted value."""
    # Create secret
    secret_data = {
        "namespace_id": str(client_a.personal_namespace.id),
        "name": "database_password",
        "provider": "postgres",
        "value": "super_secret_password",
    }
    response = await client_a.post("/api/secrets/", json=secret_data)
    assert response.status_code == 201
    secret_id = response.json()["id"]

    # Get secret with decrypted value
    response = await client_a.get(f"/api/secrets/{secret_id}")
    assert response.status_code == 200
    secret = response.json()
    assert secret["name"] == "database_password"
    assert secret["provider"] == "postgres"
    assert secret["value"] == "super_secret_password"  # Decrypted value


async def test_update_secret_provider(client_a: UserClient):
    """Test updating a secret's provider."""
    # Create secret
    secret_data = {
        "namespace_id": str(client_a.personal_namespace.id),
        "name": "oauth_token",
        "provider": "google",
        "value": "token123",
    }
    response = await client_a.post("/api/secrets/", json=secret_data)
    assert response.status_code == 201
    secret_id = response.json()["id"]

    # Update provider
    response = await client_a.patch(
        f"/api/secrets/{secret_id}", json={"provider": "github"}
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["provider"] == "github"

    # Verify update persisted
    response = await client_a.get(f"/api/secrets/{secret_id}")
    assert response.status_code == 200
    assert response.json()["provider"] == "github"
    assert response.json()["value"] == "token123"  # Value unchanged


async def test_update_secret_value(client_a: UserClient):
    """Test updating a secret's value."""
    # Create secret
    secret_data = {
        "namespace_id": str(client_a.personal_namespace.id),
        "name": "api_key",
        "provider": "stripe",
        "value": "old_key",
    }
    response = await client_a.post("/api/secrets/", json=secret_data)
    assert response.status_code == 201
    secret_id = response.json()["id"]

    # Update value
    response = await client_a.patch(
        f"/api/secrets/{secret_id}", json={"value": "new_key"}
    )
    assert response.status_code == 200

    # Verify new value is returned decrypted
    response = await client_a.get(f"/api/secrets/{secret_id}")
    assert response.status_code == 200
    assert response.json()["value"] == "new_key"


async def test_update_secret_both_fields(client_a: UserClient):
    """Test updating both provider and value simultaneously."""
    # Create secret
    secret_data = {
        "namespace_id": str(client_a.personal_namespace.id),
        "name": "webhook_secret",
        "provider": "slack",
        "value": "old_webhook",
    }
    response = await client_a.post("/api/secrets/", json=secret_data)
    assert response.status_code == 201
    secret_id = response.json()["id"]

    # Update both fields
    response = await client_a.patch(
        f"/api/secrets/{secret_id}",
        json={"provider": "discord", "value": "new_webhook"},
    )
    assert response.status_code == 200

    # Verify both updated
    response = await client_a.get(f"/api/secrets/{secret_id}")
    assert response.status_code == 200
    secret = response.json()
    assert secret["provider"] == "discord"
    assert secret["value"] == "new_webhook"


async def test_delete_secret(client_a: UserClient):
    """Test deleting a secret."""
    # Create secret
    secret_data = {
        "namespace_id": str(client_a.personal_namespace.id),
        "name": "temp_secret",
        "provider": "temp",
        "value": "temporary",
    }
    response = await client_a.post("/api/secrets/", json=secret_data)
    assert response.status_code == 201
    secret_id = response.json()["id"]

    # Delete secret
    response = await client_a.delete(f"/api/secrets/{secret_id}")
    assert response.status_code == 204

    # Verify it's gone
    response = await client_a.get(f"/api/secrets/{secret_id}")
    assert response.status_code == 404


# Encryption Verification Test


async def test_value_is_encrypted_in_database(client_a: UserClient, session):
    """Test that secret values are encrypted in the database."""
    plaintext_value = "my_secret_password"

    # Create secret
    secret_data = {
        "namespace_id": str(client_a.personal_namespace.id),
        "name": "encrypted_secret",
        "provider": "test",
        "value": plaintext_value,
    }
    response = await client_a.post("/api/secrets/", json=secret_data)
    assert response.status_code == 201
    secret_id = response.json()["id"]

    # Read directly from database
    query = select(Secret).where(Secret.id == secret_id)
    result = await session.execute(query)
    secret_from_db = result.scalar_one()

    # Verify database has encrypted value (not plaintext)
    assert secret_from_db.encrypted_value != plaintext_value
    assert len(secret_from_db.encrypted_value) > 0

    # Verify API returns decrypted value
    response = await client_a.get(f"/api/secrets/{secret_id}")
    assert response.status_code == 200
    assert response.json()["value"] == plaintext_value


# Uniqueness & Conflicts Test


async def test_duplicate_secret_name_returns_conflict(client_a: UserClient):
    """Test that creating a secret with duplicate name returns 409."""
    secret_data = {
        "namespace_id": str(client_a.personal_namespace.id),
        "name": "unique_name",
        "provider": "github",
        "value": "secret1",
    }

    # Create first secret
    response = await client_a.post("/api/secrets/", json=secret_data)
    assert response.status_code == 201

    # Attempt to create duplicate
    duplicate_data = {
        "namespace_id": str(client_a.personal_namespace.id),
        "name": "unique_name",
        "provider": "gitlab",  # Different provider, same name
        "value": "secret2",
    }
    response = await client_a.post("/api/secrets/", json=duplicate_data)
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"].lower()


# Filtering Tests


async def test_filter_secrets_by_provider(client_a: UserClient):
    """Test filtering secrets by provider."""
    # Create multiple secrets with different providers
    secrets = [
        {"name": "github_key", "provider": "github", "value": "gh_secret"},
        {"name": "gitlab_key", "provider": "gitlab", "value": "gl_secret"},
        {"name": "github_token", "provider": "github", "value": "gh_token"},
    ]

    for secret_data in secrets:
        secret_data["namespace_id"] = str(client_a.personal_namespace.id)
        response = await client_a.post("/api/secrets/", json=secret_data)
        assert response.status_code == 201

    # Filter by github provider
    response = await client_a.get(
        f"/api/secrets/namespace/{client_a.personal_namespace.id}?provider=github"
    )
    assert response.status_code == 200
    filtered_secrets = response.json()
    assert len(filtered_secrets) == 2
    assert all(s["provider"] == "github" for s in filtered_secrets)


async def test_filter_secrets_by_name(client_a: UserClient):
    """Test filtering secrets by name."""
    # Create multiple secrets with unique names
    secrets = [
        {"name": "github_api_key", "provider": "github", "value": "secret1"},
        {"name": "webhook_secret", "provider": "slack", "value": "secret2"},
        {"name": "gitlab_token", "provider": "gitlab", "value": "secret3"},
    ]

    for secret_data in secrets:
        secret_data["namespace_id"] = str(client_a.personal_namespace.id)
        response = await client_a.post("/api/secrets/", json=secret_data)
        assert response.status_code == 201

    # Filter by name
    response = await client_a.get(
        f"/api/secrets/namespace/{client_a.personal_namespace.id}?name=webhook_secret"
    )
    assert response.status_code == 200
    filtered_secrets = response.json()
    assert len(filtered_secrets) == 1
    assert filtered_secrets[0]["name"] == "webhook_secret"


async def test_filter_secrets_by_provider_and_name(client_a: UserClient):
    """Test filtering secrets by both provider and name."""
    # Create multiple secrets with unique names
    secrets = [
        {"name": "github_api_key", "provider": "github", "value": "secret1"},
        {"name": "gitlab_api_key", "provider": "gitlab", "value": "secret2"},
        {"name": "github_token", "provider": "github", "value": "secret3"},
    ]

    for secret_data in secrets:
        secret_data["namespace_id"] = str(client_a.personal_namespace.id)
        response = await client_a.post("/api/secrets/", json=secret_data)
        assert response.status_code == 201

    # Filter by both
    response = await client_a.get(
        f"/api/secrets/namespace/{client_a.personal_namespace.id}?provider=github&name=github_api_key"
    )
    assert response.status_code == 200
    filtered_secrets = response.json()
    assert len(filtered_secrets) == 1
    assert filtered_secrets[0]["name"] == "github_api_key"
    assert filtered_secrets[0]["provider"] == "github"


# Cross-User Isolation Tests


async def test_users_cannot_list_other_users_secrets(
    client_a: UserClient, client_b: UserClient
):
    """Test that users can't see secrets from other users' namespaces."""
    # client_a creates a secret
    secret_data = {
        "namespace_id": str(client_a.personal_namespace.id),
        "name": "client_a_secret",
        "provider": "github",
        "value": "secret_a",
    }
    response = await client_a.post("/api/secrets/", json=secret_data)
    assert response.status_code == 201

    # client_a can see their secret
    response = await client_a.get(
        f"/api/secrets/namespace/{client_a.personal_namespace.id}"
    )
    assert response.status_code == 200
    assert len(response.json()) == 1

    # client_b can't see client_a's secrets
    response = await client_b.get(
        f"/api/secrets/namespace/{client_a.personal_namespace.id}"
    )
    assert response.status_code == 200
    assert len(response.json()) == 0  # Empty list due to access control


async def test_users_cannot_access_other_users_secrets(
    client_a: UserClient, client_b: UserClient
):
    """Test that users can't get/update/delete other users' secrets."""
    # client_a creates a secret
    secret_data = {
        "namespace_id": str(client_a.personal_namespace.id),
        "name": "private_secret",
        "provider": "github",
        "value": "secret_value",
    }
    response = await client_a.post("/api/secrets/", json=secret_data)
    assert response.status_code == 201
    secret_id = response.json()["id"]

    # client_b can't get the secret
    response = await client_b.get(f"/api/secrets/{secret_id}")
    assert response.status_code == 404

    # client_b can't update the secret
    response = await client_b.patch(
        f"/api/secrets/{secret_id}", json={"value": "hacked"}
    )
    assert response.status_code == 404

    # client_b can't delete the secret
    response = await client_b.delete(f"/api/secrets/{secret_id}")
    assert response.status_code == 404

    # Verify secret is still intact for client_a
    response = await client_a.get(f"/api/secrets/{secret_id}")
    assert response.status_code == 200
    assert response.json()["value"] == "secret_value"


# Access Control Test


async def test_cannot_create_secret_in_inaccessible_namespace(
    client_a: UserClient, client_b: UserClient
):
    """Test that users can't create secrets in namespaces they don't have access to."""
    # Try to create secret in client_b's namespace using client_a
    secret_data = {
        "namespace_id": str(client_b.personal_namespace.id),
        "name": "unauthorized_secret",
        "provider": "github",
        "value": "secret",
    }
    response = await client_a.post("/api/secrets/", json=secret_data)
    assert response.status_code == 400
    assert "namespace not found" in response.json()["detail"].lower()
