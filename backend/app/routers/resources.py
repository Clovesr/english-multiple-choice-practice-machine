from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from ..database import get_db
from ..schemas import ResourceProgressUpdate, ResourceTextCreate, ResourceUpdate
from ..services.resources import (
    DuplicateResourceError,
    ResourceError,
    ResourceNotFoundError,
    create_resource,
    get_resource,
    list_resources,
    list_segments,
    rebuild_search,
    save_progress,
    search_resources,
    trash_resource,
    update_resource,
)


router = APIRouter(tags=["resources"])


def _error(
    status: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "code": code,
            "message": message,
            "details": details or {},
            "recoverable": status < 500,
        },
    )


@router.post("/resources/import", status_code=201)
async def import_resource(
    request: Request,
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    content_type = request.headers.get("content-type", "").lower()
    try:
        if content_type.startswith("multipart/form-data"):
            form = await request.form()
            upload = form.get("file")
            if not isinstance(upload, UploadFile):
                return _error(400, "missing_file", "请选择 TXT 或 Markdown 文件")
            raw = await upload.read()
            arguments = {
                "raw": raw,
                "filename": upload.filename or "resource.txt",
                "title": str(form.get("title") or ""),
                "resource_type": str(form.get("type") or "article"),
                "source": str(form.get("source") or ""),
                "source_url": str(form.get("source_url") or ""),
                "author": str(form.get("author") or ""),
                "language": str(form.get("language") or "en"),
            }
        elif content_type.startswith("application/json"):
            payload = ResourceTextCreate.model_validate(await request.json())
            arguments = {
                "raw": payload.content.encode("utf-8"),
                "filename": f"{payload.title}.{payload.format}",
                "title": payload.title,
                "resource_type": payload.type,
                "source": payload.source,
                "source_url": payload.source_url,
                "author": payload.author,
                "language": payload.language,
                "requested_format": payload.format,
            }
        else:
            return _error(415, "unsupported_media_type", "请使用 multipart/form-data 或 JSON")
        return {"resource": create_resource(connection, **arguments)}
    except DuplicateResourceError as error:
        return _error(
            409,
            "duplicate_resource",
            str(error),
            {"existing_id": error.existing_id},
        )
    except (ResourceError, ValidationError, ValueError) as error:
        return _error(400, "invalid_resource", str(error))


@router.get("/resources")
def resources(
    status: str = "",
    type: str = "",
    q: str = "",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    try:
        return list_resources(
            connection,
            status=status,
            resource_type=type,
            query=q,
            limit=limit,
            offset=offset,
        )
    except ResourceError as error:
        return _error(400, "invalid_query", str(error))


@router.get("/resources/{resource_id}")
def resource_detail(
    resource_id: int,
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    try:
        return get_resource(connection, resource_id)
    except ResourceNotFoundError as error:
        return _error(404, "resource_not_found", str(error))


@router.put("/resources/{resource_id}")
def edit_resource(
    resource_id: int,
    request: ResourceUpdate,
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    try:
        return update_resource(connection, resource_id, request.model_dump(exclude_unset=True))
    except ResourceNotFoundError as error:
        return _error(404, "resource_not_found", str(error))
    except ResourceError as error:
        return _error(400, "invalid_resource", str(error))


@router.delete(
    "/resources/{resource_id}",
    status_code=204,
    response_model=None,
)
def delete_resource(
    resource_id: int,
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    try:
        trash_resource(connection, resource_id)
        return Response(status_code=204)
    except ResourceNotFoundError as error:
        return _error(404, "resource_not_found", str(error))


@router.get("/resources/{resource_id}/segments")
def resource_segments(
    resource_id: int,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    try:
        return list_segments(connection, resource_id, limit=limit, offset=offset)
    except ResourceNotFoundError as error:
        return _error(404, "resource_not_found", str(error))


@router.put("/resources/{resource_id}/progress")
def resource_progress(
    resource_id: int,
    request: ResourceProgressUpdate,
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    try:
        progress = save_progress(
            connection,
            resource_id,
            last_segment_id=request.last_segment_id,
            scroll_ratio=request.scroll_ratio,
            reading_ms_delta=request.reading_ms_delta,
        )
        return {"progress": progress}
    except ResourceNotFoundError as error:
        return _error(404, "resource_not_found", str(error))
    except ResourceError as error:
        return _error(400, "invalid_progress", str(error))


@router.get("/search")
def search(
    q: str,
    scope: str = "resources",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    if scope != "resources":
        return _error(400, "invalid_scope", "V1 搜索仅支持 resources 范围")
    try:
        return search_resources(connection, q, limit=limit, offset=offset)
    except (ResourceError, sqlite3.OperationalError) as error:
        return _error(400, "invalid_search", str(error))


@router.post("/search/rebuild")
def rebuild(
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    return {"job": "done", "segments": rebuild_search(connection)}
