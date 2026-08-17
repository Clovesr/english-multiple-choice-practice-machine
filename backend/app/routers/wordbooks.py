from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from ..database import get_db
from ..schemas import WordbookImportRequest, WordbookPlanRequest
from ..services.wordbooks import (
    activate_wordbook_plan,
    deactivate_wordbook_plan,
    import_wordbook,
    list_wordbook_entries,
    list_wordbooks,
    parse_wordbook_text,
)


router = APIRouter(prefix="/wordbooks", tags=["wordbooks"])


@router.get("")
def wordbooks(connection: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    return list_wordbooks(connection)


@router.post("/import", status_code=201)
async def import_terms(
    request: Request,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "").casefold()
    try:
        if content_type.startswith("application/json"):
            payload = WordbookImportRequest.model_validate(await request.json())
            terms = [
                (item, "") if isinstance(item, str) else (item.term, item.meaning)
                for item in payload.terms
            ]
            name = payload.name
        elif content_type.startswith("multipart/form-data"):
            form = await request.form()
            upload = form.get("file")
            if not isinstance(upload, UploadFile):
                raise ValueError("请选择 TXT 或 CSV 词表文件")
            terms = parse_wordbook_text(await upload.read(), upload.filename or "wordbook.txt")
            name = str(form.get("name") or "").strip() or (upload.filename or "自定义词书").rsplit(".", 1)[0]
        else:
            raise ValueError("请使用 JSON 或 multipart/form-data 导入词表")
        return import_wordbook(connection, name=name, terms=terms)
    except (ValidationError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@router.get("/{wordbook_id}/entries")
def entries(
    wordbook_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    state: str = "",
    connection: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    try:
        return list_wordbook_entries(
            connection,
            wordbook_id,
            offset=offset,
            limit=limit,
            state=state,
        )
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.post("/{wordbook_id}/plan")
def activate_plan(
    wordbook_id: int,
    request: WordbookPlanRequest,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    try:
        return activate_wordbook_plan(
            connection,
            wordbook_id,
            daily_new=request.daily_new,
            new_order=request.new_order,
        )
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.delete("/{wordbook_id}/plan")
def deactivate_plan(
    wordbook_id: int,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    try:
        return {"wordbook": deactivate_wordbook_plan(connection, wordbook_id)}
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
