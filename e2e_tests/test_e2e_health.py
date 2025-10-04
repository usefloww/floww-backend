from conftest import client


def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
