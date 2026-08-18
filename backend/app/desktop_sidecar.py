from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import socket
import threading
from collections.abc import Callable
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

import uvicorn
from fastapi import FastAPI
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send


TOKEN_ENV = "WENQU_DESKTOP_TOKEN"
TOKEN_COOKIE = "wenqu_desktop_token"
TOKEN_HEADER = b"x-wenqu-desktop-token"


class DesktopBoundary:
    """Minimal loopback boundary for the Tauri-managed FastAPI process."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str,
        origin: str,
        request_shutdown: Callable[[], None],
    ) -> None:
        if len(token) < 32:
            raise ValueError("desktop session token must contain at least 32 characters")
        self.app = app
        self.token = token
        self.origin = origin
        self.request_shutdown = request_shutdown

    @staticmethod
    def _headers(scope: Scope) -> dict[bytes, bytes]:
        return {key.lower(): value for key, value in scope.get("headers", [])}

    def _authorized(self, scope: Scope) -> bool:
        headers = self._headers(scope)
        supplied = headers.get(TOKEN_HEADER, b"").decode("utf-8", errors="ignore")
        if supplied and hmac.compare_digest(supplied, self.token):
            return True

        raw_cookie = headers.get(b"cookie", b"").decode("latin-1", errors="ignore")
        cookie = SimpleCookie()
        try:
            cookie.load(raw_cookie)
        except Exception:
            return False
        morsel = cookie.get(TOKEN_COOKIE)
        return bool(morsel and hmac.compare_digest(morsel.value, self.token))

    def _same_origin(self, scope: Scope) -> bool:
        headers = self._headers(scope)
        origin = headers.get(b"origin")
        if origin is not None:
            return hmac.compare_digest(
                origin.decode("latin-1", errors="ignore"), self.origin
            )
        referer = headers.get(b"referer")
        if referer is not None:
            value = referer.decode("latin-1", errors="ignore")
            return value == self.origin or value.startswith(f"{self.origin}/")
        return True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()

        if path == "/desktop/bootstrap" and method == "GET":
            query = parse_qs(scope.get("query_string", b"").decode("utf-8"))
            supplied = query.get("token", [""])[0]
            if not hmac.compare_digest(supplied, self.token):
                await JSONResponse({"detail": "invalid desktop session"}, status_code=403)(
                    scope, receive, send
                )
                return
            response = RedirectResponse(url="/", status_code=303)
            response.set_cookie(
                TOKEN_COOKIE,
                self.token,
                httponly=True,
                samesite="strict",
                path="/",
            )
            await response(scope, receive, send)
            return

        if path == "/desktop/shutdown" and method == "POST":
            if not self._authorized(scope):
                await JSONResponse({"detail": "invalid desktop session"}, status_code=403)(
                    scope, receive, send
                )
                return
            self.request_shutdown()
            await Response(status_code=204)(scope, receive, send)
            return

        if path == "/api" or path.startswith("/api/"):
            if not self._authorized(scope) or not self._same_origin(scope):
                await JSONResponse({"detail": "invalid desktop session"}, status_code=403)(
                    scope, receive, send
                )
                return

        await self.app(scope, receive, send)


def create_desktop_app(
    app: ASGIApp,
    *,
    token: str,
    origin: str,
    request_shutdown: Callable[[], None],
) -> DesktopBoundary:
    return DesktopBoundary(
        app,
        token=token,
        origin=origin,
        request_shutdown=request_shutdown,
    )


def _bound_loopback_socket() -> socket.socket:
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("127.0.0.1", 0))
    server_socket.listen(128)
    return server_socket


def run_sidecar(token: str) -> None:
    from backend.app.main import app

    server_socket = _bound_loopback_socket()
    port = int(server_socket.getsockname()[1])
    origin = f"http://127.0.0.1:{port}"
    shutdown_requested = threading.Event()
    protected_app = create_desktop_app(
        app,
        token=token,
        origin=origin,
        request_shutdown=shutdown_requested.set,
    )
    config = uvicorn.Config(
        protected_app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)

    def observe_shutdown() -> None:
        shutdown_requested.wait()
        server.should_exit = True

    threading.Thread(
        target=observe_shutdown,
        name="desktop-sidecar-shutdown",
        daemon=True,
    ).start()
    print(
        json.dumps(
            {"event": "sidecar_ready", "pid": os.getpid(), "port": port},
            separators=(",", ":"),
        ),
        flush=True,
    )
    asyncio.run(server.serve(sockets=[server_socket]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Tauri-managed FastAPI sidecar PoC")
    parser.parse_args()
    token = os.environ.get(TOKEN_ENV, "")
    if len(token) < 32:
        raise SystemExit(f"{TOKEN_ENV} must contain at least 32 characters")
    run_sidecar(token)


if __name__ == "__main__":
    main()
