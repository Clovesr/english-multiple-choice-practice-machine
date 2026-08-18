from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.desktop_sidecar import create_desktop_app


TOKEN = "0123456789abcdef0123456789abcdef"
ORIGIN = "http://127.0.0.1:43123"


def make_client() -> tuple[TestClient, list[bool]]:
    inner = FastAPI()

    @inner.get("/")
    def index() -> dict[str, str]:
        return {"page": "vue-dist"}

    @inner.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    stopped: list[bool] = []
    app = create_desktop_app(
        inner,
        token=TOKEN,
        origin=ORIGIN,
        request_shutdown=lambda: stopped.append(True),
    )
    return TestClient(app), stopped


def test_api_rejects_requests_without_desktop_session() -> None:
    client, _ = make_client()

    response = client.get("/api/health")

    assert response.status_code == 403


def test_bootstrap_exchanges_token_for_http_only_cookie() -> None:
    client, _ = make_client()

    response = client.get(
        "/desktop/bootstrap",
        params={"token": TOKEN},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert client.get("/api/health").json() == {"status": "ok"}


def test_header_auth_and_origin_check_cover_native_control_requests() -> None:
    client, stopped = make_client()
    headers = {"X-Wenqu-Desktop-Token": TOKEN}

    assert client.get("/api/health", headers=headers).status_code == 200
    assert (
        client.get(
            "/api/health",
            headers={**headers, "Origin": "https://attacker.example"},
        ).status_code
        == 403
    )
    assert client.post("/desktop/shutdown", headers=headers).status_code == 204
    assert stopped == [True]
