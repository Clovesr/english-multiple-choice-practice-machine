from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from ..database import get_db
from ..schemas import ReviewItemGrade
from ..services.review import (
    get_review_queue,
    get_review_stats,
    grade_review_item,
    set_review_item_suspended,
)


router = APIRouter(prefix="/review", tags=["review"])


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "code": code,
            "message": message,
            "details": {},
            "recoverable": status < 500,
        },
    )


@router.get("/queue")
def queue(
    limit: int = Query(default=20, ge=1, le=100),
    types: str = "wrong_question",
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    selected = tuple(value.strip() for value in types.split(",") if value.strip())
    try:
        return get_review_queue(connection, limit=limit, item_types=selected)
    except ValueError as error:
        return _error(400, "invalid_review_type", str(error))


@router.post("/items/{review_item_id}/grade")
def grade(
    review_item_id: int,
    request: ReviewItemGrade,
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    try:
        return grade_review_item(
            connection,
            review_item_id,
            attempt_id=str(request.attempt_id),
            rating=request.rating,
            duration_ms=request.duration_ms,
        )
    except LookupError as error:
        return _error(404, "review_item_not_found", str(error))
    except ValueError as error:
        return _error(409, "review_item_conflict", str(error))


@router.post("/items/{review_item_id}/suspend")
def suspend(
    review_item_id: int,
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    try:
        return set_review_item_suspended(connection, review_item_id, suspended=True)
    except LookupError as error:
        return _error(404, "review_item_not_found", str(error))


@router.post("/items/{review_item_id}/unsuspend")
def unsuspend(
    review_item_id: int,
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    try:
        return set_review_item_suspended(connection, review_item_id, suspended=False)
    except LookupError as error:
        return _error(404, "review_item_not_found", str(error))


@router.get("/stats")
def stats(connection: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    return get_review_stats(connection)
