from __future__ import annotations

import sqlite3
from typing import Any
from uuid import uuid4

from .learning import utc_now


def _payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "uuid": str(row["uuid"]),
        "parent_id": int(row["parent_id"]) if row["parent_id"] is not None else None,
        "name": str(row["name"]),
        "skill_type": str(row["skill_type"]),
        "stage": str(row["stage"]),
        "difficulty": int(row["difficulty"]),
        "status": str(row["status"]),
        "mastery_score": (
            float(row["mastery_score"])
            if row["mastery_score"] is not None
            else 0.0
        ),
        "evidence_count": int(row["evidence_count"] or 0),
        "mastery_updated_at": row["mastery_updated_at"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


SKILL_SELECT = """
SELECT s.*, m.mastery_score, m.evidence_count,
       m.updated_at AS mastery_updated_at
FROM skills AS s
LEFT JOIN mastery_states AS m ON m.skill_id = s.id
"""


def list_skills(
    connection: sqlite3.Connection,
    *,
    skill_type: str = "",
    stage: str = "",
) -> dict[str, Any]:
    conditions = ["s.deleted_at IS NULL"]
    parameters: list[Any] = []
    if skill_type:
        conditions.append("s.skill_type = ?")
        parameters.append(skill_type)
    if stage:
        conditions.append("s.stage = ?")
        parameters.append(stage)
    rows = connection.execute(
        f"{SKILL_SELECT} WHERE {' AND '.join(conditions)} "
        "ORDER BY s.stage, s.skill_type, s.name COLLATE NOCASE, s.id",
        parameters,
    ).fetchall()
    return {"items": [_payload(row) for row in rows]}


def create_skill(
    connection: sqlite3.Connection,
    *,
    name: str,
    parent_id: int | None,
    skill_type: str,
    stage: str,
    difficulty: int,
) -> dict[str, Any]:
    clean_name = " ".join(name.strip().split())
    if not clean_name:
        raise ValueError("知识点名称不能为空")
    if parent_id is not None:
        parent = connection.execute(
            "SELECT id FROM skills WHERE id = ? AND deleted_at IS NULL",
            (parent_id,),
        ).fetchone()
        if parent is None:
            raise LookupError("父知识点不存在")
    duplicate = connection.execute(
        """
        SELECT id FROM skills
        WHERE name = ? COLLATE NOCASE AND skill_type = ? AND stage = ?
          AND deleted_at IS NULL
        """,
        (clean_name, skill_type.strip(), stage.strip()),
    ).fetchone()
    if duplicate is not None:
        raise ValueError("同阶段、同类型下已存在同名知识点")
    now = utc_now()
    cursor = connection.execute(
        """
        INSERT INTO skills(
            uuid, parent_id, name, skill_type, stage, difficulty,
            status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
        """,
        (
            str(uuid4()),
            parent_id,
            clean_name,
            skill_type.strip(),
            stage.strip(),
            difficulty,
            now,
            now,
        ),
    )
    connection.commit()
    row = connection.execute(
        f"{SKILL_SELECT} WHERE s.id = ?", (cursor.lastrowid,)
    ).fetchone()
    return _payload(row)


def set_question_skills(
    connection: sqlite3.Connection,
    question_id: int,
    skill_ids: list[int],
) -> dict[str, Any]:
    question = connection.execute(
        "SELECT id FROM questions WHERE id = ?", (question_id,)
    ).fetchone()
    if question is None:
        raise LookupError("题目不存在")
    selected = list(dict.fromkeys(int(skill_id) for skill_id in skill_ids))
    if selected:
        placeholders = ",".join("?" for _ in selected)
        rows = connection.execute(
            f"SELECT id FROM skills WHERE id IN ({placeholders}) AND deleted_at IS NULL",
            selected,
        ).fetchall()
        found = {int(row["id"]) for row in rows}
        missing = [skill_id for skill_id in selected if skill_id not in found]
        if missing:
            raise LookupError(f"知识点不存在：{', '.join(map(str, missing))}")
    connection.execute("DELETE FROM question_skills WHERE question_id = ?", (question_id,))
    connection.executemany(
        """
        INSERT INTO question_skills(question_id, skill_id, source)
        VALUES (?, ?, 'manual')
        """,
        ((question_id, skill_id) for skill_id in selected),
    )
    connection.commit()
    return {
        "question_id": question_id,
        "skills": [
            _payload(row)
            for row in connection.execute(
                f"{SKILL_SELECT} JOIN question_skills AS qs ON qs.skill_id = s.id "
                "WHERE qs.question_id = ? ORDER BY s.name COLLATE NOCASE, s.id",
                (question_id,),
            ).fetchall()
        ],
    }


def update_mastery_for_question(
    connection: sqlite3.Connection,
    question_id: int,
    *,
    correct: bool,
    timestamp: str | None = None,
) -> None:
    now = timestamp or utc_now()
    rows = connection.execute(
        "SELECT skill_id FROM question_skills WHERE question_id = ?",
        (question_id,),
    ).fetchall()
    evidence = 1.0 if correct else 0.0
    for row in rows:
        skill_id = int(row["skill_id"])
        state = connection.execute(
            "SELECT mastery_score, evidence_count FROM mastery_states WHERE skill_id = ?",
            (skill_id,),
        ).fetchone()
        if state is None:
            connection.execute(
                """
                INSERT INTO mastery_states(skill_id, mastery_score, evidence_count, updated_at)
                VALUES (?, ?, 1, ?)
                """,
                (skill_id, evidence, now),
            )
            continue
        count = int(state["evidence_count"])
        score = float(state["mastery_score"])
        connection.execute(
            """
            UPDATE mastery_states
            SET mastery_score = ?, evidence_count = ?, updated_at = ?
            WHERE skill_id = ?
            """,
            (((score * count) + evidence) / (count + 1), count + 1, now, skill_id),
        )
