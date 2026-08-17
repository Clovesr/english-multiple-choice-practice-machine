from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from ..database import get_db
from ..schemas import (
    StudyBacklogPlanRequest,
    StudyCardGrade,
    StudySettingsUpdate,
    StudySprintRequest,
)
from ..services.study import (
    apply_backlog_plan,
    create_or_resume_session,
    delete_sprint,
    get_overview,
    get_settings,
    get_sprint,
    grade_card,
    set_card_suspended,
    start_sprint,
    update_settings,
)


router = APIRouter(prefix="/study", tags=["study"])


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


@router.get("/session")
def session(
    limit: int = Query(default=20, ge=1, le=100),
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    return create_or_resume_session(connection, limit=limit)


@router.post("/cards/{card_id}/grade")
def submit_grade(
    card_id: int,
    request: StudyCardGrade,
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    try:
        return grade_card(
            connection,
            card_id,
            attempt_id=str(request.attempt_id),
            rating=request.rating,
            answer_given=request.answer_given,
            duration_ms=request.duration_ms,
        )
    except LookupError as error:
        return _error(404, "study_card_not_found", str(error))
    except ValueError as error:
        return _error(409, "study_card_conflict", str(error))


@router.post("/cards/{card_id}/suspend")
def suspend_card(
    card_id: int,
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    try:
        return set_card_suspended(connection, card_id, suspended=True)
    except LookupError as error:
        return _error(404, "study_card_not_found", str(error))


@router.post("/cards/{card_id}/unsuspend")
def unsuspend_card(
    card_id: int,
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    try:
        return set_card_suspended(connection, card_id, suspended=False)
    except LookupError as error:
        return _error(404, "study_card_not_found", str(error))


@router.get("/settings")
def settings(connection: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    return get_settings(connection)


@router.put("/settings")
def save_settings(
    request: StudySettingsUpdate,
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    try:
        return update_settings(connection, request.model_dump(exclude_none=True))
    except ValueError as error:
        return _error(400, "invalid_study_settings", str(error))


@router.get("/overview")
def overview(connection: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    return get_overview(connection)


@router.post("/backlog/plan")
def backlog_plan(
    request: StudyBacklogPlanRequest,
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    return apply_backlog_plan(
        connection,
        mode=request.mode,
        days=request.days,
    )


@router.post("/sprint")
def create_sprint(
    request: StudySprintRequest,
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    try:
        return start_sprint(
            connection,
            wordbook_id=request.wordbook_id,
            exam_date=request.exam_date,
        )
    except LookupError as error:
        return _error(404, "wordbook_not_found", str(error))
    except ValueError as error:
        return _error(400, "invalid_sprint_plan", str(error))


@router.get("/sprint")
def sprint(connection: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    return get_sprint(connection)


@router.delete("/sprint")
def stop_sprint(connection: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    return delete_sprint(connection)
