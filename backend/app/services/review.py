from __future__ import annotations

import sqlite3
from datetime import datetime, time, timedelta, timezone
from typing import Any
from uuid import uuid4

from .fsrs_scheduler import SCHEDULER_NAME, SCHEDULER_VERSION, schedule_review
from .learning import record_learning_event, utc_now


def _local_day_bounds(now: datetime) -> tuple[str, str]:
    local_now = now.astimezone()
    start = datetime.combine(local_now.date(), time.min, tzinfo=local_now.tzinfo)
    end = start + timedelta(days=1)
    return (
        start.astimezone(timezone.utc).isoformat(timespec="seconds"),
        end.astimezone(timezone.utc).isoformat(timespec="seconds"),
    )


def _item_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "uuid": str(row["uuid"]),
        "item_type": str(row["item_type"]),
        "ref_id": int(row["ref_id"]),
        "state": str(row["state"]),
        "step": int(row["step"]),
        "due_at": str(row["due_at"]),
        "last_review_at": row["last_review_at"],
        "stability": float(row["stability"]),
        "difficulty": float(row["difficulty"]),
        "scheduled_days": float(row["scheduled_days"]),
        "elapsed_days": float(row["elapsed_days"]),
        "reps": int(row["reps"]),
        "lapses": int(row["lapses"]),
        "manually_suspended": bool(row["manually_suspended"]),
        "scheduler": str(row["scheduler"]),
        "scheduler_version": str(row["scheduler_version"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def ensure_wrong_question_item(
    connection: sqlite3.Connection,
    question_id: int,
    *,
    repeated_wrong: bool,
    timestamp: str | None = None,
) -> int:
    """Create the first wrong-question item or make an existing one due again."""

    now = timestamp or utc_now()
    row = connection.execute(
        """
        SELECT * FROM review_items
        WHERE item_type = 'wrong_question' AND ref_id = ?
        """,
        (question_id,),
    ).fetchone()
    if row is None:
        cursor = connection.execute(
            """
            INSERT INTO review_items(
                uuid, item_type, ref_id, state, step, due_at, last_review_at,
                stability, difficulty, scheduled_days, elapsed_days, reps, lapses,
                manually_suspended, scheduler, scheduler_version, created_at, updated_at
            ) VALUES (
                ?, 'wrong_question', ?, 'new', 0, ?, NULL,
                0, 0, 0, 0, 0, 0, 0, ?, ?, ?, ?
            )
            """,
            (str(uuid4()), question_id, now, SCHEDULER_NAME, SCHEDULER_VERSION, now, now),
        )
        return int(cursor.lastrowid)
    connection.execute(
        """
        UPDATE review_items
        SET due_at = ?,
            lapses = lapses + ?,
            updated_at = ?
        WHERE id = ?
        """,
        (now, int(repeated_wrong), now, row["id"]),
    )
    return int(row["id"])


def _wrong_question_payload(
    connection: sqlite3.Connection,
    question_id: int,
) -> dict[str, Any]:
    question = connection.execute(
        """
        SELECT q.id, q.number, q.stem, q.question_type, q.answer,
               q.unit_id, u.title AS unit_title, u.unit_type,
               p.id AS paper_id, p.year, p.title AS paper_title
        FROM questions AS q
        JOIN units AS u ON u.id = q.unit_id
        JOIN papers AS p ON p.id = u.paper_id
        WHERE q.id = ? AND p.deleted_at IS NULL
        """,
        (question_id,),
    ).fetchone()
    if question is None:
        raise LookupError("错题不存在或所属试卷已删除")
    options = connection.execute(
        """
        SELECT id, stable_key, original_label, content, sequence
        FROM options WHERE question_id = ? ORDER BY sequence, id
        """,
        (question_id,),
    ).fetchall()
    latest_wrong = connection.execute(
        """
        SELECT pa.user_answer
        FROM practice_answers AS pa
        LEFT JOIN practice_sessions AS ps ON ps.id = pa.session_id
        WHERE pa.question_id = ? AND pa.is_correct = 0
        ORDER BY COALESCE(ps.submitted_at, pa.answered_at) DESC, pa.id DESC
        LIMIT 1
        """,
        (question_id,),
    ).fetchone()
    return {
        "question_id": int(question["id"]),
        "number": int(question["number"]),
        "stem": str(question["stem"]),
        "question_type": str(question["question_type"]),
        "options": [dict(option) for option in options],
        "original_wrong_answer": (
            str(latest_wrong["user_answer"]) if latest_wrong is not None else ""
        ),
        "correct_answer": str(question["answer"]),
        "unit_id": int(question["unit_id"]),
        "unit_title": str(question["unit_title"]),
        "unit_type": str(question["unit_type"]),
        "paper_id": int(question["paper_id"]),
        "paper_year": int(question["year"]),
        "paper_title": str(question["paper_title"]),
    }


def get_review_queue(
    connection: sqlite3.Connection,
    *,
    limit: int,
    item_types: tuple[str, ...],
) -> dict[str, Any]:
    supported = {"wrong_question"}
    invalid = sorted(set(item_types) - supported)
    if invalid:
        raise ValueError(f"V1 复习中心不支持类型：{', '.join(invalid)}")
    selected_types = item_types or ("wrong_question",)
    now = utc_now()
    placeholders = ",".join("?" for _ in selected_types)
    rows = connection.execute(
        f"""
        SELECT * FROM review_items
        WHERE item_type IN ({placeholders})
          AND manually_suspended = 0
          AND due_at <= ?
        ORDER BY due_at, id
        LIMIT ?
        """,
        (*selected_types, now, limit),
    ).fetchall()
    items = [
        {
            "review_item_id": int(row["id"]),
            "item_type": str(row["item_type"]),
            "state": str(row["state"]),
            "due_at": str(row["due_at"]),
            "payload": _wrong_question_payload(connection, int(row["ref_id"])),
        }
        for row in rows
    ]
    day_start, day_end = _local_day_bounds(datetime.now(timezone.utc))
    due = int(
        connection.execute(
            f"""
            SELECT COUNT(*) FROM review_items
            WHERE item_type IN ({placeholders})
              AND manually_suspended = 0 AND due_at <= ?
            """,
            (*selected_types, now),
        ).fetchone()[0]
    )
    new_count = int(
        connection.execute(
            f"""
            SELECT COUNT(*) FROM review_items
            WHERE item_type IN ({placeholders}) AND state = 'new'
              AND manually_suspended = 0 AND due_at <= ?
            """,
            (*selected_types, now),
        ).fetchone()[0]
    )
    done_today = int(
        connection.execute(
            f"""
            SELECT COUNT(*) FROM review_logs AS rl
            JOIN review_items AS ri ON ri.id = rl.review_item_id
            WHERE ri.item_type IN ({placeholders})
              AND rl.reviewed_at >= ? AND rl.reviewed_at < ?
            """,
            (*selected_types, day_start, day_end),
        ).fetchone()[0]
    )
    vocab_due = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM review_items
            WHERE item_type = 'vocabulary' AND archived_at IS NULL
              AND manually_suspended = 0 AND due_at <= ?
            """,
            (now,),
        ).fetchone()[0]
    )
    return {
        "items": items,
        "counts": {
            "due": due,
            "overdue": int(
                connection.execute(
                    f"""
                    SELECT COUNT(*) FROM review_items
                    WHERE item_type IN ({placeholders})
                      AND manually_suspended = 0 AND due_at < ?
                    """,
                    (*selected_types, day_start),
                ).fetchone()[0]
            ),
            "new": new_count,
            "done_today": done_today,
            "vocab_due": vocab_due,
        },
    }


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
        "review_item": _item_payload(item),
        "review_log_id": int(log["id"]),
        "next_due_at": str(log["due_after"]),
        "attempt_id": str(log["attempt_id"]),
    }


def grade_review_item(
    connection: sqlite3.Connection,
    review_item_id: int,
    *,
    attempt_id: str,
    rating: int,
    duration_ms: int,
) -> dict[str, Any]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        duplicate = connection.execute(
            "SELECT * FROM review_logs WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        if duplicate is not None:
            if int(duplicate["review_item_id"]) != review_item_id:
                raise ValueError("attempt_id 已用于另一复习项")
            connection.commit()
            return _grade_response(connection, duplicate)
        row = connection.execute(
            "SELECT * FROM review_items WHERE id = ?", (review_item_id,)
        ).fetchone()
        if row is None or str(row["item_type"]) != "wrong_question":
            raise LookupError("错题复习项不存在")
        if bool(row["manually_suspended"]):
            raise ValueError("复习项已暂停")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        scheduled = schedule_review(
            dict(row), rating=rating, reviewed_at=now, duration_ms=duration_ms
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
                review_item_id,
            ),
        )
        cursor = connection.execute(
            """
            INSERT INTO review_logs(
                uuid, attempt_id, review_item_id, card_id, answer_given,
                auto_correct, auto_rating, final_rating,
                state_before, state_after, step_before, step_after,
                due_before, due_after, stability_before, stability_after,
                difficulty_before, difficulty_after, elapsed_days, scheduled_days,
                duration_ms, reviewed_at
            ) VALUES (
                ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                str(uuid4()),
                attempt_id,
                review_item_id,
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
                now.isoformat(timespec="seconds"),
            ),
        )
        record_learning_event(
            connection,
            verb="review",
            object_type="wrong_question",
            object_id=int(row["ref_id"]),
            result={
                "attempt_id": attempt_id,
                "correct": rating >= 3,
                "rating": rating,
                "review_item_id": review_item_id,
            },
            duration_ms=duration_ms,
            occurred_at=now.isoformat(timespec="seconds"),
        )
        connection.commit()
        log = connection.execute(
            "SELECT * FROM review_logs WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return _grade_response(connection, log)
    except Exception:
        connection.rollback()
        raise


def set_review_item_suspended(
    connection: sqlite3.Connection,
    review_item_id: int,
    *,
    suspended: bool,
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM review_items WHERE id = ?", (review_item_id,)
    ).fetchone()
    if row is None or str(row["item_type"]) != "wrong_question":
        raise LookupError("错题复习项不存在")
    connection.execute(
        "UPDATE review_items SET manually_suspended = ?, updated_at = ? WHERE id = ?",
        (int(suspended), utc_now(), review_item_id),
    )
    connection.commit()
    updated = connection.execute(
        "SELECT * FROM review_items WHERE id = ?", (review_item_id,)
    ).fetchone()
    return {"review_item": _item_payload(updated)}


def get_review_stats(connection: sqlite3.Connection) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    now_iso = now.isoformat(timespec="seconds")
    day_start, day_end = _local_day_bounds(now)
    retention_start = (now - timedelta(days=30)).isoformat(timespec="seconds")
    retention = connection.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN final_rating >= 3 THEN 1 ELSE 0 END) AS retained
        FROM review_logs WHERE reviewed_at >= ?
        """,
        (retention_start,),
    ).fetchone()
    total = int(retention["total"] or 0)
    return {
        "due": int(
            connection.execute(
                """
                SELECT COUNT(*) FROM review_items
                WHERE manually_suspended = 0 AND archived_at IS NULL AND due_at <= ?
                """,
                (now_iso,),
            ).fetchone()[0]
        ),
        "overdue": int(
            connection.execute(
                """
                SELECT COUNT(*) FROM review_items
                WHERE manually_suspended = 0 AND archived_at IS NULL AND due_at < ?
                """,
                (day_start,),
            ).fetchone()[0]
        ),
        "new_available": int(
            connection.execute(
                """
                SELECT COUNT(*) FROM review_items
                WHERE manually_suspended = 0 AND archived_at IS NULL
                  AND state = 'new' AND due_at <= ?
                """,
                (now_iso,),
            ).fetchone()[0]
        ),
        "reviewed_today": int(
            connection.execute(
                """
                SELECT COUNT(*) FROM review_logs
                WHERE reviewed_at >= ? AND reviewed_at < ?
                """,
                (day_start, day_end),
            ).fetchone()[0]
        ),
        "retention_30d": (
            round(int(retention["retained"] or 0) / total, 4) if total else None
        ),
    }
