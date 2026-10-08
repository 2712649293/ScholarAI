"""基础 health endpoint 冒烟测试。"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_alive() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "alive"
    assert "version" in data


def test_root_returns_metadata() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "ScholarAI"
    assert data["docs"] == "/docs"


def test_cors_allows_frontend_origin() -> None:
    # 预检请求
    resp = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code in (200, 204)
    assert "access-control-allow-origin" in {k.lower() for k in resp.headers.keys()}
