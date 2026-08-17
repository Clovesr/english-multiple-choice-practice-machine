from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from ..database import get_db
from ..schemas import (
    VocabularyCreate,
    VocabularyReview,
    VocabularySelectionCreate,
    VocabularyStateUpdate,
    VocabularyTranslationRunRequest,
    VocabularyUpdate,
)
from ..services.vocabulary import (
    _serialize_entry,
    add_vocabulary,
    local_similar_matches,
    queue_vocabulary_translations,
    review_entry,
    translate_queued_vocabulary,
)
from ..services.learning import utc_now
from ..services.vocabulary_learning import collect_from_selection
from ..services.wordbooks import update_entry_state


router = APIRouter(prefix="/vocabulary", tags=["vocabulary"])


COLLECTED_ENTRY_SQL = """
(
    vocabulary_entries.encounter_count > 0
    OR vocabulary_entries.source_kind = 'user'
    OR vocabulary_entries.user_edited = 1
    OR vocabulary_entries.manually_frequent = 1
    OR EXISTS (
        SELECT 1 FROM vocabulary_cards AS collected_card
        WHERE collected_card.entry_id = vocabulary_entries.id
          AND EXISTS (
              SELECT 1 FROM review_items AS collected_review
              WHERE collected_review.item_type = 'vocabulary_card'
                AND collected_review.ref_id = collected_card.id
                AND collected_review.reps > 0
          )
    )
)
"""


DUE_CARD_ENTRY_SQL = """
EXISTS (
    SELECT 1 FROM vocabulary_cards AS due_card
    WHERE due_card.entry_id = vocabulary_entries.id
      AND EXISTS (
          SELECT 1 FROM review_items AS due_review
          WHERE due_review.item_type = 'vocabulary_card'
            AND due_review.ref_id = due_card.id
            AND due_review.manually_suspended = 0
            AND due_review.due_at <= :due_now
      )
)
"""


@router.post("/from-selection", status_code=201)
def create_from_selection(
    request: VocabularySelectionCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        return collect_from_selection(connection, request.model_dump())
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.put("/entries/{entry_id}/state")
def set_entry_state(
    entry_id: int,
    request: VocabularyStateUpdate,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        return update_entry_state(connection, entry_id, request.study_status)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.post("")
def create_entry(
    request: VocabularyCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        result = add_vocabulary(connection, request.model_dump())
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return result


@router.get("")
def list_entries(
    status: str = "all",
    search: str = "",
    scope: Literal["collected", "all"] = "collected",
    limit: int = Query(120, ge=1, le=500),
    offset: int = Query(0, ge=0),
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    conditions = ["1 = 1"]
    params: dict[str, object] = {
        "due_now": utc_now(),
        "limit": limit,
        "offset": offset,
    }
    if scope == "collected":
        conditions.append(COLLECTED_ENTRY_SQL)
    if status == "frequent":
        conditions.append("(encounter_count >= 2 OR manually_frequent = 1)")
    elif status == "review":
        conditions.append(DUE_CARD_ENTRY_SQL)
        conditions.append(
            "study_status NOT IN ('known', 'ignored', 'paused', 'mastered')"
        )
    elif status == "learning":
        conditions.append("study_status = 'learning'")
    elif status == "mastered":
        conditions.append("study_status = 'mastered'")
    elif status == "pending":
        conditions.append("translation_status != 'ready'")
    if search.strip():
        conditions.append(
            "(term LIKE :search OR lemma LIKE :search "
            "OR contextual_meaning LIKE :search OR common_meaning LIKE :search)"
        )
        params["search"] = f"%{search.strip()}%"
    where_sql = " AND ".join(conditions)
    filtered_total = int(
        connection.execute(
            f"SELECT COUNT(*) FROM vocabulary_entries WHERE {where_sql}",
            params,
        ).fetchone()[0]
    )
    rows = connection.execute(
        f"""
        SELECT *,
               (
                   SELECT context_sentence
                   FROM vocabulary_occurrences
                   WHERE entry_id = vocabulary_entries.id
                   ORDER BY id DESC LIMIT 1
               ) AS latest_sentence,
               CASE WHEN encounter_count >= 2 OR manually_frequent = 1 THEN 1 ELSE 0 END AS is_frequent,
               CASE WHEN {COLLECTED_ENTRY_SQL} THEN 1 ELSE 0 END AS is_collected
        FROM vocabulary_entries
        WHERE {where_sql}
        ORDER BY
                 CASE WHEN datetime(last_seen_at) >= datetime('now', '-7 days') THEN 0 ELSE 1 END,
                 is_frequent DESC, encounter_count DESC, last_seen_at DESC, id DESC
        LIMIT :limit OFFSET :offset
        """,
        params,
    ).fetchall()
    count_scope_sql = COLLECTED_ENTRY_SQL if scope == "collected" else "1 = 1"
    counts = connection.execute(
        f"""
        SELECT COUNT(*) AS total,
               COALESCE(SUM(CASE WHEN {COLLECTED_ENTRY_SQL} THEN 1 ELSE 0 END), 0)
                   AS collected_total,
               COALESCE(SUM(CASE WHEN NOT {COLLECTED_ENTRY_SQL} THEN 1 ELSE 0 END), 0)
                   AS seeded_total,
               COALESCE(SUM(CASE WHEN {count_scope_sql} THEN 1 ELSE 0 END), 0)
                   AS visible_total,
               COALESCE(SUM(CASE WHEN {count_scope_sql}
                              AND (encounter_count >= 2 OR manually_frequent = 1)
                        THEN 1 ELSE 0 END), 0) AS frequent,
               COALESCE(SUM(CASE WHEN {count_scope_sql}
                              AND study_status = 'mastered'
                        THEN 1 ELSE 0 END), 0) AS mastered,
               COALESCE(SUM(CASE WHEN {count_scope_sql}
                              AND translation_status != 'ready'
                        THEN 1 ELSE 0 END), 0) AS pending,
               COALESCE(SUM(CASE WHEN {count_scope_sql}
                              AND {DUE_CARD_ENTRY_SQL}
                              AND study_status NOT IN ('known', 'ignored', 'paused', 'mastered')
                        THEN 1 ELSE 0 END), 0) AS review
        FROM vocabulary_entries
        """,
        params,
    ).fetchone()
    items = [dict(row) for row in rows]
    local_map = local_similar_matches(connection, [item["id"] for item in items])
    for item in items:
        item["local_similar"] = local_map.get(item["id"], [])
    return {
        "items": items,
        "total": filtered_total,
        "limit": limit,
        "offset": offset,
        "counts": dict(counts),
    }


@router.get("/home")
def home_words(
    limit: int = Query(20, ge=1, le=50),
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    rows = connection.execute(
        f"""
        SELECT id, term, lemma, contextual_meaning, common_meaning,
               encounter_count, study_status,
               CASE WHEN encounter_count >= 2 OR manually_frequent = 1 THEN 1 ELSE 0 END AS is_frequent
        FROM vocabulary_entries
        WHERE translation_status = 'ready'
          AND {COLLECTED_ENTRY_SQL}
        ORDER BY
                 CASE WHEN datetime(created_at) >= datetime('now', '-7 days') THEN 0 ELSE 1 END,
                 is_frequent DESC,
                 CASE WHEN next_review_at IS NULL OR next_review_at <= CURRENT_TIMESTAMP THEN 0 ELSE 1 END,
                 encounter_count DESC,
                 RANDOM()
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return {"items": [dict(row) for row in rows]}


@router.post("/translation-runs")
def create_translation_run(
    request: VocabularyTranslationRunRequest,
    background_tasks: BackgroundTasks,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    queued_ids = queue_vocabulary_translations(
        connection,
        request.entry_ids,
        include_all_pending=request.trigger == "practice_exit",
    )
    if queued_ids:
        background_tasks.add_task(translate_queued_vocabulary)
    return {
        "accepted": True,
        "trigger": request.trigger,
        "queuedCount": len(queued_ids),
    }


@router.get("/{entry_id}")
def read_entry(
    entry_id: int, connection: sqlite3.Connection = Depends(get_db)
) -> dict:
    try:
        return _serialize_entry(connection, entry_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.put("/{entry_id}")
def update_entry(
    entry_id: int,
    request: VocabularyUpdate,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    fields = request.model_dump(exclude_none=True)
    if not fields:
        return _serialize_entry(connection, entry_id)
    allowed = {
        "contextual_meaning",
        "common_meaning",
        "phonetic",
        "part_of_speech",
        "note",
        "study_status",
        "manually_frequent",
    }
    assignments = []
    values: list[object] = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        assignments.append(f"{key} = ?")
        values.append(int(value) if key == "manually_frequent" else value)
    assignments.extend(["user_edited = 1", "updated_at = CURRENT_TIMESTAMP"])
    values.append(entry_id)
    connection.execute(
        f"UPDATE vocabulary_entries SET {', '.join(assignments)} WHERE id = ?",
        values,
    )
    connection.commit()
    return _serialize_entry(connection, entry_id)


@router.delete("/{entry_id}")
def delete_entry(
    entry_id: int, connection: sqlite3.Connection = Depends(get_db)
) -> dict:
    connection.execute("DELETE FROM vocabulary_entries WHERE id = ?", (entry_id,))
    connection.commit()
    return {"ok": True}


@router.post("/{entry_id}/retry")
def retry_translation(
    entry_id: int,
    background_tasks: BackgroundTasks,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    connection.execute(
        """
        UPDATE vocabulary_entries
        SET translation_status = 'queued', translation_error = '',
            user_edited = 0, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (entry_id,),
    )
    connection.commit()
    background_tasks.add_task(translate_queued_vocabulary)
    return {"ok": True}


@router.post("/{entry_id}/review")
def submit_review(
    entry_id: int,
    request: VocabularyReview,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        return review_entry(connection, entry_id, request.rating)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
