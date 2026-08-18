from __future__ import annotations

from io import BytesIO
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.app.desktop_sidecar import (
    _bound_loopback_socket,
    _observe_parent_pipe,
    create_desktop_app,
)


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
    assert client.get("/").status_code == 403


def test_bootstrap_exchanges_token_for_http_only_cookie() -> None:
    client, _ = make_client()

    response = client.get(
        "/desktop/bootstrap",
        params={"token": TOKEN},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert client.get("/").json() == {"page": "vue-dist"}
    assert client.get("/api/health").json() == {"status": "ok"}


def test_bootstrap_rejects_wrong_token_without_setting_cookie() -> None:
    client, _ = make_client()

    response = client.get(
        "/desktop/bootstrap",
        params={"token": "not-the-session-token"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert "set-cookie" not in response.headers


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
    assert (
        client.post(
            "/desktop/shutdown",
            headers={**headers, "Origin": "https://attacker.example"},
        ).status_code
        == 403
    )
    assert stopped == []
    assert client.post("/desktop/shutdown", headers=headers).status_code == 204
    assert stopped == [True]


@pytest.mark.parametrize(
    "token, origin",
    [
        ("too-short", ORIGIN),
        (TOKEN, "http://0.0.0.0:43123"),
        (TOKEN, "https://127.0.0.1:43123"),
        (TOKEN, "http://127.0.0.1"),
    ],
)
def test_desktop_boundary_rejects_weak_tokens_and_non_loopback_origins(
    token: str, origin: str
) -> None:
    inner = FastAPI()

    with pytest.raises(ValueError):
        create_desktop_app(
            inner,
            token=token,
            origin=origin,
            request_shutdown=lambda: None,
        )


def test_parent_pipe_eof_requests_sidecar_shutdown() -> None:
    shutdown_requested = threading.Event()

    _observe_parent_pipe(shutdown_requested, BytesIO(b"parent-owned-pipe"))

    assert shutdown_requested.is_set()


def test_operating_system_assigns_distinct_loopback_ports() -> None:
    first = _bound_loopback_socket()
    second = _bound_loopback_socket()
    try:
        first_host, first_port = first.getsockname()
        second_host, second_port = second.getsockname()
        assert first_host == second_host == "127.0.0.1"
        assert first_port > 0
        assert second_port > 0
        assert first_port != second_port
    finally:
        first.close()
        second.close()
