import json

from sqlalchemy import select

from app.models import Provider
from app.tests.fixtures_clients import UserClient
from app.utils.encryption import decrypt_secret


async def test_create_and_retrieve_provider(client_a: UserClient):
    provider_data = {
        "namespace_id": str(client_a.personal_namespace.id),
        "type": "gitlab",
        "alias": "my-gitlab",
        "config": {
            "token": "glpat-xxxxxxxxxxxxxxxxxxxx",
            "base_url": "https://gitlab.com",
        },
    }
    response = await client_a.post("/api/providers", json=provider_data)
    assert response.status_code == 200

    created_provider = response.json()
    assert created_provider["type"] == "gitlab"
    assert created_provider["alias"] == "my-gitlab"
    assert created_provider["namespace_id"] == str(client_a.personal_namespace.id)
    assert created_provider["config"]["token"] == "glpat-xxxxxxxxxxxxxxxxxxxx"
    assert created_provider["config"]["base_url"] == "https://gitlab.com"

    # Test: Retrieve providers and verify it appears
    response = await client_a.get("/api/providers")
    assert response.status_code == 200

    providers = response.json()["providers"]
    assert len(providers) == 1
    assert providers[0]["alias"] == "my-gitlab"


async def test_create_multiple_providers(client_a: UserClient):
    # Create first provider
    response1 = await client_a.post(
        "/api/providers",
        json={
            "namespace_id": str(client_a.personal_namespace.id),
            "type": "github",
            "alias": "github-provider",
            "config": {"token": "ghp_xxxxxxxxxxxxxxxxxxxx", "org": "myorg"},
        },
    )
    assert response1.status_code == 200

    # Create second provider
    response2 = await client_a.post(
        "/api/providers",
        json={
            "namespace_id": str(client_a.personal_namespace.id),
            "type": "gitlab",
            "alias": "gitlab-provider",
            "config": {"token": "glpat-xxxxxxxxxxxxxxxxxxxx", "project_id": "12345"},
        },
    )
    assert response2.status_code == 200

    # List providers
    response = await client_a.get("/api/providers")
    assert response.status_code == 200

    providers = response.json()["providers"]
    assert len(providers) == 2

    provider_aliases = [p["alias"] for p in providers]
    assert "github-provider" in provider_aliases
    assert "gitlab-provider" in provider_aliases


async def test_list_providers_returns_correct_structure(client_a: UserClient):
    response = await client_a.get("/api/providers")
    assert response.status_code == 200

    data = response.json()
    assert "providers" in data
    assert isinstance(data["providers"], list)


async def test_provider_creation_includes_metadata(client_a: UserClient):
    provider_data = {
        "namespace_id": str(client_a.personal_namespace.id),
        "type": "docker",
        "alias": "docker-registry",
        "config": {"registry": "docker.io", "username": "myuser", "password": "mypass"},
    }
    response = await client_a.post("/api/providers", json=provider_data)
    assert response.status_code == 200

    created_provider = response.json()
    assert "id" in created_provider
    assert "namespace_id" in created_provider
    assert created_provider["namespace_id"] == str(client_a.personal_namespace.id)


async def test_providers_are_accessible_to_user(
    client_a: UserClient, client_b: UserClient
):
    # Create provider for client A
    provider_data = {
        "namespace_id": str(client_a.personal_namespace.id),
        "type": "aws",
        "alias": "aws-provider",
        "config": {
            "access_key": "AKIAXXXXXXXXXXXXXXXX",
            "secret_key": "xxxxxxxxxxxxxxxxxxxx",
            "region": "us-east-1",
        },
    }
    response = await client_a.post("/api/providers", json=provider_data)
    assert response.status_code == 200

    # Client A should see the provider
    response = await client_a.get("/api/providers")
    assert response.status_code == 200
    providers_a = response.json()["providers"]
    assert len(providers_a) == 1

    # Client B should not see client A's provider
    response = await client_b.get("/api/providers")
    assert response.status_code == 200
    providers_b = response.json()["providers"]
    assert len(providers_b) == 0


async def test_config_encryption_decryption(client_a: UserClient, session):
    """Test that config is encrypted in DB but decrypted in API response."""
    original_config = {
        "api_key": "secret_key_123",
        "endpoint": "https://api.example.com",
        "timeout": 30,
    }

    provider_data = {
        "namespace_id": str(client_a.personal_namespace.id),
        "type": "custom",
        "alias": "test-encryption",
        "config": original_config,
    }

    # Create provider via API
    response = await client_a.post("/api/providers", json=provider_data)
    assert response.status_code == 200

    created_provider = response.json()
    provider_id = created_provider["id"]

    # Verify the API returns decrypted config
    assert created_provider["config"] == original_config

    # Check that data is encrypted in database

    result = await session.execute(select(Provider).where(Provider.id == provider_id))
    db_provider = result.scalar_one()

    # The encrypted_config should not be the same as the original
    assert db_provider.encrypted_config != json.dumps(original_config)

    # But when decrypted, it should match
    decrypted_config = json.loads(decrypt_secret(db_provider.encrypted_config))
    assert decrypted_config == original_config

    # Test retrieval via API also returns decrypted config
    response = await client_a.get(f"/api/providers/{provider_id}")
    assert response.status_code == 200
    retrieved_provider = response.json()
    assert retrieved_provider["config"] == original_config
