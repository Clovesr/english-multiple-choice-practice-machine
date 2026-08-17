from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..database import get_db
from ..schemas import QuestionSkillsUpdate, SkillCreate
from ..services.skills import create_skill, list_skills, set_question_skills


router = APIRouter(tags=["skills"])


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


@router.get("/skills")
def index(
    type: str = "",
    stage: str = "",
    connection: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    return list_skills(connection, skill_type=type.strip(), stage=stage.strip())


@router.post("/skills", status_code=201)
def create(
    request: SkillCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    try:
        return create_skill(connection, **request.model_dump())
    except LookupError as error:
        return _error(404, "parent_skill_not_found", str(error))
    except ValueError as error:
        return _error(409, "skill_conflict", str(error))


@router.post("/questions/{question_id}/skills")
def associate_question(
    question_id: int,
    request: QuestionSkillsUpdate,
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    try:
        return set_question_skills(connection, question_id, request.skill_ids)
    except LookupError as error:
        return _error(404, "question_or_skill_not_found", str(error))
