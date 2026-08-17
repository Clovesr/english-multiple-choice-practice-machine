from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..database import get_db
from ..services.tasks import generate_daily_tasks, update_task_status


router = APIRouter(prefix="/tasks", tags=["tasks"])


def _not_found(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "code": "daily_task_not_found",
            "message": message,
            "details": {},
            "recoverable": True,
        },
    )


@router.get("/today")
def today(connection: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    return generate_daily_tasks(connection)


@router.post("/generate")
def generate(connection: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    return generate_daily_tasks(connection)


@router.put("/{task_id}/complete")
def complete(
    task_id: int,
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    try:
        return update_task_status(connection, task_id, status="done")
    except LookupError as error:
        return _not_found(str(error))


@router.put("/{task_id}/skip")
def skip(
    task_id: int,
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    try:
        return update_task_status(connection, task_id, status="skipped")
    except LookupError as error:
        return _not_found(str(error))
