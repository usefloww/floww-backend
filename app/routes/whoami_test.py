from httpx import AsyncClient


async def test_whoami_401(client: AsyncClient):
    response = await client.get("/api/whoami")
    assert response.status_code == 401


async def test_whoami_200(client_a: AsyncClient, client_b: AsyncClient):
    response = await client_a.get("/api/whoami")
    assert response.status_code == 200
    assert response.json()["workos_user_id"] == "test_user_a"

    response = await client_b.get("/api/whoami")
    assert response.status_code == 200
    assert response.json()["workos_user_id"] == "test_user_b"
