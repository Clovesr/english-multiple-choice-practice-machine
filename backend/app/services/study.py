from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import uuid4

from .fsrs_scheduler import schedule_review
from .vocabulary_cards import (
    CARD_TYPES,
    generate_cards_for_entry,
    normalize_answer,
    utc_now,
)
from .wordbooks import generate_wordbook_card_pool


INELIGIBLE_ENTRY_STATES = ("known", "mastered", "ignored", "paused")
SPELLING_VARIANTS = {
    "colour": "color",
    "favour": "favor",
    "honour": "honor",
    "labour": "labor",
    "centre": "center",
    "theatre": "theater",
    "metre": "meter",
    "litre": "liter",
    "defence": "defense",
    "licence": "license",
    "analyse": "analyze",
    "organise": "organize",
    "realise": "realize",
    "travelling": "traveling",
    "travelled": "traveled",
}
SPELLING_VARIANTS.update({value: key for key, value in tuple(SPELLING_VARIANTS.items())})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _json_object(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _local_day_bounds(now: datetime) -> tuple[datetime, datetime, date]:
    local_now = now.astimezone()
    local_start = datetime.combine(local_now.date(), time.min, tzinfo=local_now.tzinfo)
    local_end = local_start + timedelta(days=1)
    return (
        local_start.astimezone(timezone.utc),
        local_end.astimezone(timezone.utc),
        local_now.date(),
    )


def _ensure_settings(connection: sqlite3.Connection) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM study_settings WHERE id = 1").fetchone()
    if row is None:
        connection.execute(
            "INSERT INTO study_settings(id, updated_at) VALUES (1, ?)",
            (utc_now(),),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM study_settings WHERE id = 1").fetchone()
    if row is None:
        raise RuntimeError("学习设置初始化失败")
    return row


def _enabled_types(settings: sqlite3.Row | dict[str, Any]) -> list[str]:
    raw = settings["enabled_card_types"]
    try:
        values = json.loads(str(raw or "[]"))
    except json.JSONDecodeError:
        values = []
    if not isinstance(values, list):
        values = []
    selected = {str(value) for value in values if str(value) in CARD_TYPES}
    return [card_type for card_type in CARD_TYPES if card_type in selected]


def _settings_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "daily_new": int(row["daily_new"]),
        "daily_review_max": int(row["daily_review_max"]),
        "enabled_card_types": _enabled_types(row),
        "new_card_order": str(row["new_card_order"]),
        "leech_threshold": int(row["leech_threshold"]),
        "backlog_mode": str(row["backlog_mode"]),
    }


def get_settings(connection: sqlite3.Connection) -> dict[str, Any]:
    return _settings_payload(_ensure_settings(connection))


def update_settings(
    connection: sqlite3.Connection,
    changes: dict[str, Any],
) -> dict[str, Any]:
    if not changes:
        return get_settings(connection)
    allowed = {
        "daily_new",
        "daily_review_max",
        "enabled_card_types",
        "new_card_order",
        "leech_threshold",
        "backlog_mode",
    }
    assignments: list[str] = []
    parameters: list[Any] = []
    for key, value in changes.items():
        if key not in allowed:
            continue
        if key == "enabled_card_types":
            requested = [str(item) for item in value]
            invalid = sorted(set(requested) - set(CARD_TYPES))
            if invalid:
                raise ValueError(f"不支持的卡片类型：{', '.join(invalid)}")
            selected = set(requested)
            value = json.dumps(
                [card_type for card_type in CARD_TYPES if card_type in selected],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        assignments.append(f"{key} = ?")
        parameters.append(value)
    if not assignments:
        return get_settings(connection)
    _ensure_settings(connection)
    assignments.append("updated_at = ?")
    parameters.append(utc_now())
    connection.execute(
        f"UPDATE study_settings SET {', '.join(assignments)} WHERE id = 1",
        parameters,
    )
    connection.commit()
    return get_settings(connection)


def _active_plan(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM study_plans WHERE active = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()


def _review_item_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "uuid": row["uuid"],
        "item_type": row["item_type"],
        "ref_id": int(row["ref_id"]),
        "state": row["state"],
        "step": int(row["step"]),
        "due_at": row["due_at"],
        "last_review_at": row["last_review_at"],
        "stability": float(row["stability"]),
        "difficulty": float(row["difficulty"]),
        "scheduled_days": float(row["scheduled_days"]),
        "elapsed_days": float(row["elapsed_days"]),
        "reps": int(row["reps"]),
        "lapses": int(row["lapses"]),
        "manually_suspended": bool(row["manually_suspended"]),
        "scheduler": row["scheduler"],
        "scheduler_version": row["scheduler_version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _expand_accept(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(str(value or "").strip().split())
        if not cleaned:
            continue
        candidates = [cleaned]
        paired = SPELLING_VARIANTS.get(cleaned.casefold())
        if paired:
            candidates.append(paired)
        for candidate in candidates:
            key = normalize_answer(candidate)
            if key and key not in seen:
                seen.add(key)
                result.append(candidate)
    return result


def _card_payload(connection: sqlite3.Connection, card_id: int) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT vc.*, ri.id AS review_item_id, ri.state,
               e.lemma, e.term, e.phonetic_uk, e.phonetic_us,
               e.phonetic, e.part_of_speech, e.common_meaning,
               e.contextual_meaning
        FROM vocabulary_cards AS vc
        JOIN review_items AS ri
          ON ri.item_type = 'vocabulary_card' AND ri.ref_id = vc.id
        JOIN vocabulary_entries AS e ON e.id = vc.entry_id
        WHERE vc.id = ?
        """,
        (card_id,),
    ).fetchone()
    if row is None:
        raise LookupError("学习卡不存在")
    senses = [
        {
            "pos": str(item["pos"] or ""),
            "gloss_zh": str(item["gloss_zh"] or ""),
            "gloss_en": str(item["gloss_en"] or ""),
        }
        for item in connection.execute(
            """
            SELECT pos, gloss_zh, gloss_en
            FROM vocabulary_senses
            WHERE entry_id = ? ORDER BY sequence, id
            """,
            (row["entry_id"],),
        ).fetchall()
    ]
    if not senses:
        fallback = str(row["common_meaning"] or row["contextual_meaning"] or "")
        if fallback:
            senses.append(
                {
                    "pos": str(row["part_of_speech"] or ""),
                    "gloss_zh": fallback,
                    "gloss_en": "",
                }
            )
    contexts: list[dict[str, str]] = []
    for item in connection.execute(
        """
        SELECT o.context_sentence, o.unit_title, o.year, r.title AS resource_title
        FROM vocabulary_occurrences AS o
        LEFT JOIN resources AS r ON r.id = o.resource_id
        WHERE o.entry_id = ? AND trim(o.context_sentence) <> ''
        ORDER BY o.id DESC LIMIT 8
        """,
        (row["entry_id"],),
    ).fetchall():
        source = str(item["resource_title"] or item["unit_title"] or "")
        if not source and item["year"]:
            source = str(item["year"])
        contexts.append({"sentence": str(item["context_sentence"]), "source": source})
    prompt = _json_object(row["prompt_data"])
    answer = _json_object(row["answer_data"])
    answer["accept"] = _expand_accept(answer.get("accept"))
    return {
        "card_id": int(row["id"]),
        "review_item_id": int(row["review_item_id"]),
        "card_type": str(row["card_type"]),
        "state": str(row["state"]),
        "entry": {
            "lemma": str(row["lemma"] or row["term"] or ""),
            "phonetic_uk": str(row["phonetic_uk"] or row["phonetic"] or ""),
            "phonetic_us": str(row["phonetic_us"] or ""),
            "senses": senses,
        },
        "prompt": prompt,
        "answer": answer,
        "contexts": contexts,
    }


def _eligibility_sql(enabled: list[str], *, alias: str = "ri") -> tuple[str, list[Any]]:
    if not enabled:
        return "0 = 1", []
    placeholders = ",".join("?" for _ in enabled)
    return (
        f"""
        {alias}.item_type = 'vocabulary_card'
        AND {alias}.manually_suspended = 0
        AND vc.card_type IN ({placeholders})
        AND e.deleted_at IS NULL
        AND e.study_status NOT IN ('known','mastered','ignored','paused')
        """,
        list(enabled),
    )


def _today_counts(connection: sqlite3.Connection, now: datetime) -> dict[str, int]:
    start, end, _ = _local_day_bounds(now)
    row = connection.execute(
        """
        SELECT COUNT(*) AS done,
               COALESCE(SUM(CASE WHEN state_before = 'new' THEN 1 ELSE 0 END), 0) AS new_done,
               COALESCE(SUM(CASE WHEN state_before <> 'new' THEN 1 ELSE 0 END), 0) AS review_done
        FROM review_logs
        WHERE reviewed_at >= ? AND reviewed_at < ?
        """,
        (_iso(start), _iso(end)),
    ).fetchone()
    return {
        "done": int(row["done"]),
        "new_done": int(row["new_done"]),
        "review_done": int(row["review_done"]),
    }


def _effective_new_target(settings: sqlite3.Row, plan: sqlite3.Row | None) -> int:
    return int(plan["daily_new"] if plan is not None else settings["daily_new"])


def _saved_backlog_plan(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute(
        "SELECT value FROM app_settings WHERE key = 'study_backlog_plan'"
    ).fetchone()
    if row is None:
        return {}
    try:
        payload = json.loads(str(row["value"] or "{}"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _active_spread_extra(connection: sqlite3.Connection) -> int:
    payload = _saved_backlog_plan(connection)
    if payload.get("mode") != "spread":
        return 0
    try:
        created_on = date.fromisoformat(str(payload["created_on"]))
        days = max(1, int(payload["days"]))
        extra = max(0, int(payload["daily_extra_reviews"]))
    except (KeyError, TypeError, ValueError):
        return 0
    return extra if datetime.now().astimezone().date() < created_on + timedelta(days=days) else 0


def _queue_counts(
    connection: sqlite3.Connection,
    *,
    settings: sqlite3.Row,
    plan: sqlite3.Row | None,
    now: datetime,
) -> dict[str, int]:
    enabled = _enabled_types(settings)
    eligibility, params = _eligibility_sql(enabled)
    due = int(
        connection.execute(
            f"""
            SELECT COUNT(*)
            FROM review_items AS ri
            JOIN vocabulary_cards AS vc ON vc.id = ri.ref_id
            JOIN vocabulary_entries AS e ON e.id = vc.entry_id
            WHERE {eligibility} AND ri.state <> 'new' AND ri.due_at <= ?
            """,
            (*params, _iso(now)),
        ).fetchone()[0]
    )
    plan_wordbook_id = int(plan["wordbook_id"]) if plan is not None else 0
    new_available = int(
        connection.execute(
            f"""
            SELECT COUNT(*)
            FROM review_items AS ri
            JOIN vocabulary_cards AS vc ON vc.id = ri.ref_id
            JOIN vocabulary_entries AS e ON e.id = vc.entry_id
            WHERE {eligibility} AND ri.state = 'new'
              AND (
                e.encounter_count > 0
                OR (? > 0 AND EXISTS (
                    SELECT 1 FROM wordbook_entries AS we
                    WHERE we.wordbook_id = ? AND we.entry_id = e.id
                ))
              )
            """,
            (*params, plan_wordbook_id, plan_wordbook_id),
        ).fetchone()[0]
    )
    today = _today_counts(connection, now)
    new_target = _effective_new_target(settings, plan)
    if str(settings["backlog_mode"]) in {"suspend_new", "focus_overdue"} and due:
        new_target = 0
    return {
        "due": due,
        "new_available": new_available,
        "new_remaining": min(new_available, max(0, new_target - today["new_done"])),
        **today,
    }


def _close_ineligible_session_cards(
    connection: sqlite3.Connection,
    session_id: int,
    enabled: list[str],
) -> None:
    rows = connection.execute(
        """
        SELECT sc.card_id, vc.card_type, ri.manually_suspended, e.study_status,
               e.deleted_at
        FROM study_session_cards AS sc
        JOIN vocabulary_cards AS vc ON vc.id = sc.card_id
        JOIN review_items AS ri
          ON ri.item_type = 'vocabulary_card' AND ri.ref_id = vc.id
        JOIN vocabulary_entries AS e ON e.id = vc.entry_id
        WHERE sc.session_id = ? AND sc.status = 'pending'
        """,
        (session_id,),
    ).fetchall()
    enabled_set = set(enabled)
    rejected = [
        int(row["card_id"])
        for row in rows
        if row["card_type"] not in enabled_set
        or bool(row["manually_suspended"])
        or row["deleted_at"] is not None
        or row["study_status"] in INELIGIBLE_ENTRY_STATES
    ]
    if rejected:
        placeholders = ",".join("?" for _ in rejected)
        connection.execute(
            f"""
            UPDATE study_session_cards SET status = 'ineligible'
            WHERE session_id = ? AND card_id IN ({placeholders})
            """,
            (session_id, *rejected),
        )


def _session_payload(
    connection: sqlite3.Connection,
    session: sqlite3.Row,
    *,
    settings: sqlite3.Row,
    plan: sqlite3.Row | None,
    now: datetime,
) -> dict[str, Any]:
    session_id = int(session["id"])
    _close_ineligible_session_cards(connection, session_id, _enabled_types(settings))
    card_ids = [
        int(row["card_id"])
        for row in connection.execute(
            """
            SELECT card_id FROM study_session_cards
            WHERE session_id = ? AND status = 'pending'
            ORDER BY sequence
            """,
            (session_id,),
        ).fetchall()
    ]
    connection.execute(
        "UPDATE study_sessions SET last_accessed_at = ? WHERE id = ?",
        (_iso(now), session_id),
    )
    connection.commit()
    counts = _queue_counts(connection, settings=settings, plan=plan, now=now)
    return {
        "session_id": str(session["uuid"]),
        "counts": {
            "new_remaining": counts["new_remaining"],
            "due_remaining": counts["due"],
            "done_today": counts["done"],
        },
        "cards": [_card_payload(connection, card_id) for card_id in card_ids],
    }


def _new_order_sql(order: str) -> str:
    if order == "random":
        return "RANDOM()"
    if order == "sequence":
        return "COALESCE(we.sequence, 2147483647), vc.entry_id, vc.id"
    return (
        "CASE WHEN COALESCE(we.frequency_rank, 0) > 0 THEN 0 ELSE 1 END, "
        "COALESCE(we.frequency_rank, 2147483647), COALESCE(we.sequence, 2147483647), vc.id"
    )


def _ensure_plan_card_supply(
    connection: sqlite3.Connection,
    plan: sqlite3.Row | None,
    settings: sqlite3.Row,
    *,
    desired: int,
) -> int:
    """Top up only the active plan's near-term new-card pool."""

    if plan is None or desired <= 0:
        return 0
    wordbook = connection.execute(
        "SELECT kind FROM wordbooks WHERE id = ? AND status = 'active'",
        (plan["wordbook_id"],),
    ).fetchone()
    if wordbook is None:
        return 0
    generation_source = (
        "sprint_wordbook"
        if str(plan["mode"]) == "sprint"
        else (
            "builtin_wordbook"
            if str(wordbook["kind"]) == "builtin"
            else "imported_wordbook"
        )
    )
    return generate_wordbook_card_pool(
        connection,
        int(plan["wordbook_id"]),
        new_order=str(plan["new_order"]),
        max_entries=min(500, max(50, desired * 4)),
        generation_source=generation_source,
        target_card_count=desired,
        enabled_card_types=_enabled_types(settings),
    )


def create_or_resume_session(
    connection: sqlite3.Connection,
    *,
    limit: int,
) -> dict[str, Any]:
    now = _utc_now()
    settings = _ensure_settings(connection)
    plan = _active_plan(connection)
    plan_id = int(plan["id"]) if plan is not None else None
    active = connection.execute(
        "SELECT * FROM study_sessions WHERE status = 'active' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if active is not None and active["plan_id"] != plan_id:
        connection.execute(
            "UPDATE study_sessions SET status = 'completed', completed_at = ? WHERE id = ?",
            (_iso(now), active["id"]),
        )
        connection.commit()
        active = None
    if active is not None:
        payload = _session_payload(
            connection,
            active,
            settings=settings,
            plan=plan,
            now=now,
        )
        if payload["cards"]:
            return payload
        connection.execute(
            "UPDATE study_sessions SET status = 'completed', completed_at = ? WHERE id = ?",
            (_iso(now), active["id"]),
        )
        connection.commit()

    counts = _queue_counts(connection, settings=settings, plan=plan, now=now)
    desired_new = max(
        0,
        _effective_new_target(settings, plan) - counts["new_done"],
    )
    if str(settings["backlog_mode"]) in {"suspend_new", "focus_overdue"} and counts["due"]:
        desired_new = 0
    if _ensure_plan_card_supply(
        connection,
        plan,
        settings,
        desired=desired_new,
    ):
        counts = _queue_counts(connection, settings=settings, plan=plan, now=now)
    enabled = _enabled_types(settings)
    eligibility, eligibility_params = _eligibility_sql(enabled)
    review_limit = min(
        limit,
        max(
            0,
            int(settings["daily_review_max"])
            + (
                _active_spread_extra(connection)
                if str(settings["backlog_mode"]) == "spread"
                else 0
            )
            - counts["review_done"],
        ),
    )
    due_cutoff = now
    if str(settings["backlog_mode"]) == "focus_overdue":
        due_cutoff, _, _ = _local_day_bounds(now)
        due_cutoff -= timedelta(seconds=1)
    due_rows = connection.execute(
        f"""
        SELECT vc.id AS card_id
        FROM review_items AS ri
        JOIN vocabulary_cards AS vc ON vc.id = ri.ref_id
        JOIN vocabulary_entries AS e ON e.id = vc.entry_id
        WHERE {eligibility} AND ri.state <> 'new' AND ri.due_at <= ?
        ORDER BY ri.due_at, ri.id
        LIMIT ?
        """,
        (*eligibility_params, _iso(due_cutoff), review_limit),
    ).fetchall()
    selected: list[tuple[int, str]] = [(int(row["card_id"]), "due") for row in due_rows]
    new_limit = min(max(0, limit - len(selected)), counts["new_remaining"])
    if new_limit:
        wordbook_id = int(plan["wordbook_id"]) if plan is not None else 0
        order = str(plan["new_order"] if plan is not None else settings["new_card_order"])
        new_rows = connection.execute(
            f"""
            SELECT vc.id AS card_id
            FROM review_items AS ri
            JOIN vocabulary_cards AS vc ON vc.id = ri.ref_id
            JOIN vocabulary_entries AS e ON e.id = vc.entry_id
            LEFT JOIN wordbook_entries AS we
              ON we.wordbook_id = ? AND we.entry_id = e.id
            WHERE {eligibility} AND ri.state = 'new'
              AND (e.encounter_count > 0 OR we.entry_id IS NOT NULL)
            ORDER BY CASE WHEN e.encounter_count > 0 THEN 0 ELSE 1 END,
                     {_new_order_sql(order)}
            LIMIT ?
            """,
            (wordbook_id, *eligibility_params, new_limit),
        ).fetchall()
        selected.extend((int(row["card_id"]), "new") for row in new_rows)

    timestamp = _iso(now)
    session_uuid = str(uuid4())
    cursor = connection.execute(
        """
        INSERT INTO study_sessions(
            uuid, plan_id, status, card_limit, new_quota,
            started_at, last_accessed_at
        ) VALUES (?, ?, 'active', ?, ?, ?, ?)
        """,
        (session_uuid, plan_id, limit, new_limit, timestamp, timestamp),
    )
    session_id = int(cursor.lastrowid)
    connection.executemany(
        """
        INSERT INTO study_session_cards(session_id, card_id, sequence, bucket)
        VALUES (?, ?, ?, ?)
        """,
        (
            (session_id, card_id, sequence, bucket)
            for sequence, (card_id, bucket) in enumerate(selected, start=1)
        ),
    )
    connection.commit()
    session = connection.execute(
        "SELECT * FROM study_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    return _session_payload(
        connection,
        session,
        settings=settings,
        plan=plan,
        now=now,
    )


def _grade_response(
    connection: sqlite3.Connection,
    log: sqlite3.Row,
) -> dict[str, Any]:
    item = connection.execute(
        "SELECT * FROM review_items WHERE id = ?", (log["review_item_id"],)
    ).fetchone()
    if item is None:
        raise LookupError("复习项不存在")
    return {
        "card_id": int(log["card_id"]),
        "review_item": _review_item_payload(item),
        "review_log_id": int(log["id"]),
        "next_due_at": str(log["due_after"]),
        "auto_correct": (
            None if log["auto_correct"] is None else bool(log["auto_correct"])
        ),
        "final_rating": int(log["final_rating"]),
        "attempt_id": str(log["attempt_id"]),
    }


def grade_card(
    connection: sqlite3.Connection,
    card_id: int,
    *,
    attempt_id: str,
    rating: int,
    answer_given: str | None,
    duration_ms: int,
) -> dict[str, Any]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        duplicate = connection.execute(
            "SELECT * FROM review_logs WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        if duplicate is not None:
            if int(duplicate["card_id"] or 0) != card_id:
                raise ValueError("attempt_id 已用于另一张卡")
            connection.commit()
            return _grade_response(connection, duplicate)

        row = connection.execute(
            """
            SELECT ri.*, vc.card_type, vc.answer_data, vc.entry_id,
                   e.study_status, e.deleted_at
            FROM vocabulary_cards AS vc
            JOIN review_items AS ri
              ON ri.item_type = 'vocabulary_card' AND ri.ref_id = vc.id
            JOIN vocabulary_entries AS e ON e.id = vc.entry_id
            WHERE vc.id = ?
            """,
            (card_id,),
        ).fetchone()
        if row is None:
            raise LookupError("学习卡不存在")
        if bool(row["manually_suspended"]):
            raise ValueError("学习卡已暂停")
        if row["deleted_at"] is not None or row["study_status"] in INELIGIBLE_ENTRY_STATES:
            raise ValueError("该词条当前不在学习队列中")

        answer = _json_object(row["answer_data"])
        accepted = _expand_accept(answer.get("accept"))
        objective = str(row["card_type"]) != "forward" and bool(accepted)
        auto_correct: bool | None = None
        auto_rating: int | None = None
        if objective and answer_given is not None:
            normalized = normalize_answer(answer_given)
            auto_correct = bool(normalized) and any(
                normalized == normalize_answer(candidate) for candidate in accepted
            )
            auto_rating = 3 if auto_correct else 1

        now = _utc_now()
        before = dict(row)
        scheduled = schedule_review(
            before,
            rating=rating,
            reviewed_at=now,
            duration_ms=duration_ms,
        )
        connection.execute(
            """
            UPDATE review_items
            SET state = ?, step = ?, due_at = ?, last_review_at = ?,
                stability = ?, difficulty = ?, scheduled_days = ?, elapsed_days = ?,
                reps = ?, lapses = ?, scheduler = ?, scheduler_version = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                scheduled["state"],
                scheduled["step"],
                scheduled["due_at"],
                scheduled["last_review_at"],
                scheduled["stability"],
                scheduled["difficulty"],
                scheduled["scheduled_days"],
                scheduled["elapsed_days"],
                scheduled["reps"],
                scheduled["lapses"],
                scheduled["scheduler"],
                scheduled["scheduler_version"],
                scheduled["updated_at"],
                row["id"],
            ),
        )
        log_cursor = connection.execute(
            """
            INSERT INTO review_logs(
                uuid, attempt_id, review_item_id, card_id, answer_given,
                auto_correct, auto_rating, final_rating,
                state_before, state_after, step_before, step_after,
                due_before, due_after, stability_before, stability_after,
                difficulty_before, difficulty_after, elapsed_days, scheduled_days,
                duration_ms, reviewed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                attempt_id,
                row["id"],
                card_id,
                answer_given,
                None if auto_correct is None else int(auto_correct),
                auto_rating,
                rating,
                row["state"],
                scheduled["state"],
                int(row["step"]),
                scheduled["step"],
                row["due_at"],
                scheduled["due_at"],
                float(row["stability"]),
                scheduled["stability"],
                float(row["difficulty"]),
                scheduled["difficulty"],
                scheduled["elapsed_days"],
                scheduled["scheduled_days"],
                duration_ms,
                _iso(now),
            ),
        )
        connection.execute(
            """
            UPDATE study_session_cards
            SET status = 'graded', graded_at = ?
            WHERE card_id = ? AND status = 'pending'
            """,
            (_iso(now), card_id),
        )
        connection.execute(
            """
            UPDATE study_sessions
            SET status = 'completed', completed_at = ?
            WHERE status = 'active' AND NOT EXISTS (
                SELECT 1 FROM study_session_cards AS sc
                WHERE sc.session_id = study_sessions.id AND sc.status = 'pending'
            )
            """,
            (_iso(now),),
        )
        if str(row["card_type"]) == "forward":
            connection.execute(
                """
                UPDATE vocabulary_entries
                SET next_review_at = ?, last_reviewed_at = ?,
                    study_status = CASE
                        WHEN study_status IN ('known','ignored','paused') THEN study_status
                        ELSE 'learning' END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    scheduled["due_at"],
                    scheduled["last_review_at"],
                    scheduled["updated_at"],
                    row["entry_id"],
                ),
            )
        connection.commit()
        log = connection.execute(
            "SELECT * FROM review_logs WHERE id = ?", (log_cursor.lastrowid,)
        ).fetchone()
        return _grade_response(connection, log)
    except Exception:
        connection.rollback()
        raise


def set_card_suspended(
    connection: sqlite3.Connection,
    card_id: int,
    *,
    suspended: bool,
) -> dict[str, Any]:
    item = connection.execute(
        """
        SELECT ri.* FROM review_items AS ri
        JOIN vocabulary_cards AS vc ON vc.id = ri.ref_id
        WHERE ri.item_type = 'vocabulary_card' AND vc.id = ?
        """,
        (card_id,),
    ).fetchone()
    if item is None:
        raise LookupError("学习卡不存在")
    connection.execute(
        "UPDATE review_items SET manually_suspended = ?, updated_at = ? WHERE id = ?",
        (int(suspended), utc_now(), item["id"]),
    )
    if suspended:
        connection.execute(
            """
            UPDATE study_session_cards SET status = 'suspended'
            WHERE card_id = ? AND status = 'pending'
            """,
            (card_id,),
        )
    else:
        connection.execute(
            """
            UPDATE study_session_cards
            SET status = 'pending', graded_at = NULL
            WHERE card_id = ? AND status = 'suspended'
              AND EXISTS (
                  SELECT 1 FROM study_sessions AS s
                  WHERE s.id = study_session_cards.session_id AND s.status = 'active'
              )
            """,
            (card_id,),
        )
    connection.commit()
    updated = connection.execute(
        "SELECT * FROM review_items WHERE id = ?", (item["id"],)
    ).fetchone()
    return {"card_id": card_id, "review_item": _review_item_payload(updated)}


def get_overview(connection: sqlite3.Connection) -> dict[str, Any]:
    now = _utc_now()
    settings = _ensure_settings(connection)
    plan = _active_plan(connection)
    queue = _queue_counts(connection, settings=settings, plan=plan, now=now)
    start, _, local_today = _local_day_bounds(now)
    enabled = _enabled_types(settings)
    eligibility, params = _eligibility_sql(enabled)
    overdue_total = int(
        connection.execute(
            f"""
            SELECT COUNT(*) FROM review_items AS ri
            JOIN vocabulary_cards AS vc ON vc.id = ri.ref_id
            JOIN vocabulary_entries AS e ON e.id = vc.entry_id
            WHERE {eligibility} AND ri.state <> 'new' AND ri.due_at < ?
            """,
            (*params, _iso(start)),
        ).fetchone()[0]
    )

    def retention(days: int) -> float | None:
        since = _iso(now - timedelta(days=days))
        row = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(CASE WHEN final_rating > 1 THEN 1 ELSE 0 END), 0) AS kept
            FROM review_logs WHERE reviewed_at >= ?
            """,
            (since,),
        ).fetchone()
        return round(int(row["kept"]) / int(row["total"]), 4) if row["total"] else None

    reviewed_dates: set[date] = set()
    for row in connection.execute(
        "SELECT reviewed_at FROM review_logs ORDER BY reviewed_at DESC"
    ).fetchall():
        try:
            reviewed_dates.add(datetime.fromisoformat(str(row["reviewed_at"])).astimezone().date())
        except ValueError:
            continue
    streak = 0
    cursor_date = local_today
    while cursor_date in reviewed_dates:
        streak += 1
        cursor_date -= timedelta(days=1)

    due_dates: list[date] = []
    for row in connection.execute(
        f"""
        SELECT ri.due_at FROM review_items AS ri
        JOIN vocabulary_cards AS vc ON vc.id = ri.ref_id
        JOIN vocabulary_entries AS e ON e.id = vc.entry_id
        WHERE {eligibility} AND ri.state <> 'new'
        """,
        params,
    ).fetchall():
        try:
            due_dates.append(datetime.fromisoformat(str(row["due_at"])).astimezone().date())
        except ValueError:
            continue
    forecast = [
        {
            "date": (local_today + timedelta(days=offset)).isoformat(),
            "due": sum(1 for item in due_dates if item == local_today + timedelta(days=offset)),
        }
        for offset in range(7)
    ]
    leeches = int(
        connection.execute(
            f"""
            SELECT COUNT(*) FROM review_items AS ri
            JOIN vocabulary_cards AS vc ON vc.id = ri.ref_id
            JOIN vocabulary_entries AS e ON e.id = vc.entry_id
            WHERE {eligibility} AND ri.lapses >= ?
            """,
            (*params, int(settings["leech_threshold"])),
        ).fetchone()[0]
    )
    return {
        "today": {
            "new_done": queue["new_done"],
            "new_target": _effective_new_target(settings, plan),
            "reviews_done": queue["review_done"],
            "due_left": queue["due"],
        },
        "overdue_total": overdue_total,
        "streak_days": streak,
        "retention_7d": retention(7),
        "retention_30d": retention(30),
        "forecast_7d": forecast,
        "leeches": leeches,
    }


def apply_backlog_plan(
    connection: sqlite3.Connection,
    *,
    mode: str,
    days: int | None,
) -> dict[str, Any]:
    overview = get_overview(connection)
    spread_days = days or 7
    daily_extra = (
        math.ceil(int(overview["overdue_total"]) / spread_days)
        if overview["overdue_total"]
        else 0
    )
    settings = update_settings(connection, {"backlog_mode": mode})
    saved = {
        "mode": mode,
        "days": spread_days,
        "daily_extra_reviews": daily_extra if mode == "spread" else 0,
        "created_on": datetime.now().astimezone().date().isoformat(),
    }
    connection.execute(
        """
        INSERT INTO app_settings(key, value)
        VALUES ('study_backlog_plan', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (json.dumps(saved, ensure_ascii=False, separators=(",", ":")),),
    )
    connection.commit()
    return {
        "mode": mode,
        "days": spread_days if mode == "spread" else days,
        "overdue_total": int(overview["overdue_total"]),
        "daily_extra_reviews": daily_extra if mode == "spread" else 0,
        "new_cards_suspended": mode in {"suspend_new", "focus_overdue"},
        "settings": settings,
        "applied": True,
    }


def _sprint_payload(
    connection: sqlite3.Connection,
    plan: sqlite3.Row,
) -> dict[str, Any]:
    wordbook = connection.execute(
        "SELECT id, uuid, name FROM wordbooks WHERE id = ?", (plan["wordbook_id"],)
    ).fetchone()
    remaining = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM wordbook_entries AS we
            JOIN vocabulary_entries AS e ON e.id = we.entry_id
            WHERE we.wordbook_id = ?
              AND e.study_status NOT IN ('known','mastered','ignored')
              AND NOT EXISTS (
                  SELECT 1 FROM vocabulary_cards AS vc
                  JOIN review_items AS ri
                    ON ri.item_type = 'vocabulary_card' AND ri.ref_id = vc.id
                  WHERE vc.entry_id = e.id AND ri.reps > 0
              )
            """,
            (plan["wordbook_id"],),
        ).fetchone()[0]
    )
    exam_date = date.fromisoformat(str(plan["exam_date"]))
    days_left = max(0, (exam_date - datetime.now().astimezone().date()).days)
    required = math.ceil(remaining / days_left) if remaining and days_left else remaining
    preview = {
        "wordbook": dict(wordbook) if wordbook is not None else None,
        "exam_date": exam_date.isoformat(),
        "days_left": days_left,
        "remaining_words": remaining,
        "daily_required": required,
        "daily_new": int(plan["daily_new"]),
        "feasible": bool(days_left and required <= 500),
        "warning": "" if days_left and required <= 500 else "剩余时间不足，建议减少范围或延后考试日期。",
    }
    return {
        "active": bool(plan["active"]),
        "plan": {
            "id": int(plan["id"]),
            "uuid": str(plan["uuid"]),
            "wordbook_id": int(plan["wordbook_id"]),
            "mode": str(plan["mode"]),
            "daily_new": int(plan["daily_new"]),
            "new_order": str(plan["new_order"]),
            "exam_date": str(plan["exam_date"]),
            "active": bool(plan["active"]),
            "created_at": str(plan["created_at"]),
            "updated_at": str(plan["updated_at"]),
        },
        "preview": preview,
    }


def start_sprint(
    connection: sqlite3.Connection,
    *,
    wordbook_id: int,
    exam_date: date,
) -> dict[str, Any]:
    today = datetime.now().astimezone().date()
    if exam_date <= today:
        raise ValueError("考试日期必须晚于今天")
    wordbook = connection.execute(
        "SELECT id FROM wordbooks WHERE id = ? AND status = 'active'", (wordbook_id,)
    ).fetchone()
    if wordbook is None:
        raise LookupError("词书不存在")
    remaining = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM wordbook_entries AS we
            JOIN vocabulary_entries AS e ON e.id = we.entry_id
            WHERE we.wordbook_id = ?
              AND e.study_status NOT IN ('known','mastered','ignored')
              AND NOT EXISTS (
                  SELECT 1 FROM vocabulary_cards AS vc
                  JOIN review_items AS ri
                    ON ri.item_type = 'vocabulary_card' AND ri.ref_id = vc.id
                  WHERE vc.entry_id = e.id AND ri.reps > 0
              )
            """,
            (wordbook_id,),
        ).fetchone()[0]
    )
    days_left = (exam_date - today).days
    daily_required = max(1, math.ceil(remaining / days_left)) if remaining else 0
    timestamp = utc_now()
    connection.execute("BEGIN IMMEDIATE")
    try:
        generate_wordbook_card_pool(
            connection,
            wordbook_id,
            new_order="frequency",
            max_entries=min(50, daily_required),
            generation_source="sprint_wordbook",
        )
        connection.execute(
            "UPDATE study_plans SET active = 0, updated_at = ? WHERE active = 1",
            (timestamp,),
        )
        plan = connection.execute(
            """
            SELECT * FROM study_plans
            WHERE wordbook_id = ? AND mode = 'sprint'
            ORDER BY id DESC LIMIT 1
            """,
            (wordbook_id,),
        ).fetchone()
        if plan is None:
            cursor = connection.execute(
                """
                INSERT INTO study_plans(
                    uuid, wordbook_id, mode, daily_new, new_order,
                    exam_date, active, created_at, updated_at
                ) VALUES (?, ?, 'sprint', ?, 'frequency', ?, 1, ?, ?)
                """,
                (
                    str(uuid4()),
                    wordbook_id,
                    min(500, daily_required),
                    exam_date.isoformat(),
                    timestamp,
                    timestamp,
                ),
            )
            plan_id = int(cursor.lastrowid)
        else:
            plan_id = int(plan["id"])
            connection.execute(
                """
                UPDATE study_plans
                SET daily_new = ?, exam_date = ?, active = 1, updated_at = ?
                WHERE id = ?
                """,
                (min(500, daily_required), exam_date.isoformat(), timestamp, plan_id),
            )
        connection.execute(
            "UPDATE study_sessions SET status = 'completed', completed_at = ? WHERE status = 'active'",
            (timestamp,),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    plan = connection.execute("SELECT * FROM study_plans WHERE id = ?", (plan_id,)).fetchone()
    return _sprint_payload(connection, plan)


def get_sprint(connection: sqlite3.Connection) -> dict[str, Any]:
    plan = connection.execute(
        "SELECT * FROM study_plans WHERE mode = 'sprint' AND active = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return {"active": False} if plan is None else _sprint_payload(connection, plan)


def delete_sprint(connection: sqlite3.Connection) -> dict[str, Any]:
    sprint = connection.execute(
        "SELECT * FROM study_plans WHERE mode = 'sprint' AND active = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if sprint is None:
        return {"active": False, "restored_plan": None}
    timestamp = utc_now()
    connection.execute(
        "UPDATE study_plans SET active = 0, updated_at = ? WHERE id = ?",
        (timestamp, sprint["id"]),
    )
    normal = connection.execute(
        """
        SELECT * FROM study_plans
        WHERE mode = 'normal' AND wordbook_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        (sprint["wordbook_id"],),
    ).fetchone()
    if normal is not None:
        connection.execute(
            "UPDATE study_plans SET active = 1, updated_at = ? WHERE id = ?",
            (timestamp, normal["id"]),
        )
    connection.execute(
        "UPDATE study_sessions SET status = 'completed', completed_at = ? WHERE status = 'active'",
        (timestamp,),
    )
    connection.commit()
    restored = None
    if normal is not None:
        restored_row = connection.execute(
            "SELECT * FROM study_plans WHERE id = ?", (normal["id"],)
        ).fetchone()
        restored = {
            "id": int(restored_row["id"]),
            "uuid": str(restored_row["uuid"]),
            "wordbook_id": int(restored_row["wordbook_id"]),
            "mode": str(restored_row["mode"]),
            "daily_new": int(restored_row["daily_new"]),
            "new_order": str(restored_row["new_order"]),
            "exam_date": restored_row["exam_date"],
            "active": bool(restored_row["active"]),
            "created_at": str(restored_row["created_at"]),
            "updated_at": str(restored_row["updated_at"]),
        }
    return {"active": False, "restored_plan": restored}


def review_legacy_entry(
    connection: sqlite3.Connection,
    entry_id: int,
    rating: str,
) -> dict[str, Any]:
    from .vocabulary import _serialize_entry
    from .vocabulary_cards import generate_cards_for_entry

    mapping = {"again": 1, "hard": 2, "mastered": 4}
    if rating not in mapping:
        raise ValueError("不支持的旧评分")
    if connection.execute(
        "SELECT 1 FROM vocabulary_entries WHERE id = ?", (entry_id,)
    ).fetchone() is None:
        raise LookupError("单词不存在")
    card = connection.execute(
        """
        SELECT id FROM vocabulary_cards
        WHERE entry_id = ? AND card_type = 'forward'
        ORDER BY CASE WHEN variant_key = 'primary' THEN 0 ELSE 1 END, id
        LIMIT 1
        """,
        (entry_id,),
    ).fetchone()
    if card is None:
        generate_cards_for_entry(connection, entry_id, generation_source="legacy_compat")
        connection.commit()
        card = connection.execute(
            """
            SELECT id FROM vocabulary_cards
            WHERE entry_id = ? AND card_type = 'forward'
            ORDER BY CASE WHEN variant_key = 'primary' THEN 0 ELSE 1 END, id
            LIMIT 1
            """,
            (entry_id,),
        ).fetchone()
    if card is None:
        raise LookupError("单词缺少可复习的正向卡")
    result = grade_card(
        connection,
        int(card["id"]),
        attempt_id=str(uuid4()),
        rating=mapping[rating],
        answer_given=None,
        duration_ms=0,
    )
    reviewed_at = str(result["review_item"]["last_review_at"])
    connection.execute(
        """
        UPDATE vocabulary_entries
        SET study_status = ?, last_reviewed_at = ?, next_review_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            "mastered" if rating == "mastered" else "learning",
            reviewed_at,
            result["next_due_at"],
            reviewed_at,
            entry_id,
        ),
    )
    connection.commit()
    return _serialize_entry(connection, entry_id)
