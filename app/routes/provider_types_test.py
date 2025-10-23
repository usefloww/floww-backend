from app.tests.fixtures_clients import UserClient


async def test_create_and_retrieve_provider(client_a: UserClient):
    response = await client_a.get("/api/provider_types/gitlab")
    assert response.status_code == 200

    assert response.json()["setup_steps"][0]["title"] == "Instance URL"
    assert response.json()["setup_steps"][1]["title"] == "Access Token"
