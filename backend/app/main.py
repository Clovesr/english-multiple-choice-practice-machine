from __future__ import annotations

import json
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import FRONTEND_DIST
from .database import connect, initialize_database
from .routers import (
    ai,
    dashboard,
    dictionary,
    imports,
    papers,
    practice,
    question_bank_profiles,
    question_banks,
    resources,
    study,
    vocabulary,
    wordbooks,
    wrong,
)
from .services.ai_client import ensure_ai_model_catalog
from .services.bundled_banks import install_bundled_question_banks
from .services.listening import repair_published_listening_assets
from .services.vocabulary import clean_machine_meanings, translate_queued_vocabulary
from .services.wordbooks import install_bundled_wordbooks
from .services.trash import purge_expired


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    install_bundled_question_banks()
    with connect() as connection:
        ensure_ai_model_catalog(connection)
        clean_machine_meanings(connection)
        install_bundled_wordbooks(connection)
        purge_expired(connection)
        repair_published_listening_assets(connection)
    threading.Thread(
        target=translate_queued_vocabulary,
        name="vocabulary-translation-recovery",
        daemon=True,
    ).start()
    yield


app = FastAPI(
    title="英语刷题机",
    version="0.1.0",
    contact={"name": "往事随风k"},
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard.router, prefix="/api")
app.include_router(papers.router, prefix="/api")
app.include_router(practice.router, prefix="/api")
app.include_router(wrong.router, prefix="/api")
app.include_router(imports.router, prefix="/api")
app.include_router(question_banks.router, prefix="/api")
app.include_router(question_bank_profiles.router, prefix="/api")
app.include_router(ai.router, prefix="/api")
app.include_router(vocabulary.router, prefix="/api")
app.include_router(resources.router, prefix="/api")
app.include_router(dictionary.router, prefix="/api")
app.include_router(wordbooks.router, prefix="/api")
app.include_router(study.router, prefix="/api")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.api_route(
    "/api/{api_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
@app.api_route(
    "/api",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
def api_not_found(api_path: str = "") -> JSONResponse:
    """Keep unknown API calls out of the SPA HTML fallback (KI-7)."""

    return JSONResponse(
        status_code=404,
        content={
            "code": "api_not_found",
            "message": f"接口不存在：/api{'/' + api_path if api_path else ''}",
            "details": {"path": f"/api{'/' + api_path if api_path else ''}"},
            "recoverable": False,
        },
    )


if FRONTEND_DIST.exists():
    assets = FRONTEND_DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def frontend(full_path: str):
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        html = (FRONTEND_DIST / "index.html").read_text(encoding="utf-8")
        with connect() as connection:
            startup_data = dashboard.dashboard(connection)
        serialized = json.dumps(startup_data, ensure_ascii=False).replace("<", "\\u003c")
        html = html.replace(
            "</head>",
            f'<script>window.__LINJIAN_STARTUP__={serialized};</script></head>',
        )
        return HTMLResponse(
            html,
            headers={"Cache-Control": "no-store, max-age=0"},
        )
