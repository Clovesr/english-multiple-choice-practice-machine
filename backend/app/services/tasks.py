from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from typing import Any

from .learning import utc_now


def _task_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "task_date": str(row["task_date"]),
        "task_type": str(row["task_type"]),
        "target_type": str(row["target_type"]),
        "target_id": row["target_id"],
        "title": str(row["title"]),
        "priority": int(row["priority"]),
        "status": str(row["status"]),
        "generated_by_rule": str(row["generated_by_rule"]),
        "created_at": str(row["created_at"]),
        "completed_at": row["completed_at"],
    }


def _upsert_task(
    connection: sqlite3.Connection,
    *,
    task_date: str,
    task_type: str,
    target_type: str,
    target_id: int,
    title: str,
    priority: int,
    status: str = "pending",
    generated_by_rule: str,
    created_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO daily_tasks(
            task_date, task_type, target_type, target_id, title,
            priority, status, generated_by_rule, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_date, task_type, target_type, target_id) DO UPDATE SET
            title = excluded.title,
            priority = excluded.priority,
            generated_by_rule = CASE
                WHEN daily_tasks.status IN ('done', 'skipped')
                THEN daily_tasks.generated_by_rule
                ELSE excluded.generated_by_rule
            END
        """,
        (
            task_date,
            task_type,
            target_type,
            target_id,
            title,
            priority,
            status,
            generated_by_rule,
            created_at,
        ),
    )


def _carry_incomplete(
    connection: sqlite3.Connection,
    *,
    task_date: str,
    created_at: str,
) -> None:
    rows = connection.execute(
        """
        SELECT dt.* FROM daily_tasks AS dt
        WHERE dt.task_date < ? AND dt.status IN ('pending', 'carried')
          AND dt.task_date = (
              SELECT MAX(previous.task_date)
              FROM daily_tasks AS previous
              WHERE previous.task_date < ?
                AND previous.status IN ('pending', 'carried')
                AND previous.task_type = dt.task_type
                AND previous.target_type = dt.target_type
                AND COALESCE(previous.target_id, 0) = COALESCE(dt.target_id, 0)
          )
        ORDER BY dt.priority DESC, dt.id
        """,
        (task_date, task_date),
    ).fetchall()
    for row in rows:
        connection.execute(
            "UPDATE daily_tasks SET status = 'carried' WHERE id = ?",
            (row["id"],),
        )
        _upsert_task(
            connection,
            task_date=task_date,
            task_type=str(row["task_type"]),
            target_type=str(row["target_type"]),
            target_id=int(row["target_id"] or 0),
            title=str(row["title"]),
            priority=int(row["priority"]),
            status="carried",
            generated_by_rule=f"carried:{row['task_date']}",
            created_at=created_at,
        )


def generate_daily_tasks(
    connection: sqlite3.Connection,
    *,
    for_date: date | None = None,
) -> dict[str, Any]:
    local_date = for_date or datetime.now().astimezone().date()
    task_date = local_date.isoformat()
    created_at = utc_now()
    _carry_incomplete(connection, task_date=task_date, created_at=created_at)

    due = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM review_items
            WHERE manually_suspended = 0 AND due_at <= ?
            """,
            (created_at,),
        ).fetchone()[0]
    )
    if due:
        _upsert_task(
            connection,
            task_date=task_date,
            task_type="review_due",
            target_type="review_queue",
            target_id=0,
            title=f"完成 {due} 项到期复习",
            priority=100,
            generated_by_rule="due_reviews_v1",
            created_at=created_at,
        )

    resource = connection.execute(
        """
        SELECT r.id, r.title, p.scroll_ratio
        FROM resource_progress AS p
        JOIN resources AS r ON r.id = p.resource_id
        WHERE r.deleted_at IS NULL
          AND r.status <> 'archived'
          AND p.scroll_ratio < 1
        ORDER BY p.last_opened_at DESC, p.updated_at DESC, r.id DESC
        LIMIT 1
        """
    ).fetchone()
    if resource is not None:
        _upsert_task(
            connection,
            task_date=task_date,
            task_type="continue_reading",
            target_type="resource",
            target_id=int(resource["id"]),
            title=f"继续阅读《{resource['title']}》",
            priority=60,
            generated_by_rule="last_incomplete_resource_v1",
            created_at=created_at,
        )

    weak_count = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM wrong_stats
            WHERE wrong_count > 0 AND consecutive_correct < 2
            """
        ).fetchone()[0]
    )
    if weak_count:
        _upsert_task(
            connection,
            task_date=task_date,
            task_type="redo_wrong",
            target_type="wrong_queue",
            target_id=0,
            title=f"重做 {weak_count} 道薄弱错题",
            priority=80,
            generated_by_rule="weak_wrong_questions_v1",
            created_at=created_at,
        )

    connection.commit()
    return list_daily_tasks(connection, for_date=local_date)


def list_daily_tasks(
    connection: sqlite3.Connection,
    *,
    for_date: date | None = None,
) -> dict[str, Any]:
    local_date = for_date or datetime.now().astimezone().date()
    rows = connection.execute(
        """
        SELECT * FROM daily_tasks WHERE task_date = ?
        ORDER BY
            CASE status WHEN 'pending' THEN 0 WHEN 'carried' THEN 1 ELSE 2 END,
            priority DESC,
            id
        """,
        (local_date.isoformat(),),
    ).fetchall()
    return {
        "date": local_date.isoformat(),
        "items": [_task_payload(row) for row in rows],
    }


def update_task_status(
    connection: sqlite3.Connection,
    task_id: int,
    *,
    status: str,
) -> dict[str, Any]:
    if status not in {"done", "skipped"}:
        raise ValueError("任务状态只能是 done 或 skipped")
    row = connection.execute(
        "SELECT * FROM daily_tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if row is None:
        raise LookupError("今日任务不存在")
    timestamp = utc_now()
    connection.execute(
        """
        UPDATE daily_tasks
        SET status = ?, completed_at = ?
        WHERE id = ?
        """,
        (status, timestamp, task_id),
    )
    connection.commit()
    updated = connection.execute(
        "SELECT * FROM daily_tasks WHERE id = ?", (task_id,)
    ).fetchone()
    return _task_payload(updated)
