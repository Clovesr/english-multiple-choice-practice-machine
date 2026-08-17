from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from .learning import utc_now


def _day_bounds(local_date: date) -> tuple[str, str]:
    local_timezone = datetime.now().astimezone().tzinfo
    start = datetime.combine(local_date, time.min, tzinfo=local_timezone)
    end = start + timedelta(days=1)
    return (
        start.astimezone(timezone.utc).isoformat(timespec="seconds"),
        end.astimezone(timezone.utc).isoformat(timespec="seconds"),
    )


def _result(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def compute_daily_report(
    connection: sqlite3.Connection,
    *,
    report_date: date,
) -> dict[str, Any]:
    start, end = _day_bounds(report_date)
    rows = connection.execute(
        """
        SELECT * FROM learning_events
        WHERE occurred_at >= ? AND occurred_at < ?
        ORDER BY id
        """,
        (start, end),
    ).fetchall()
    review_results: list[bool] = []
    question_results: list[bool] = []
    new_words = 0
    for row in rows:
        result = _result(row["result_json"])
        if row["verb"] == "review":
            if isinstance(result.get("correct"), bool):
                review_results.append(bool(result["correct"]))
            if result.get("new_word") is True:
                new_words += 1
        elif row["verb"] == "answer" and isinstance(result.get("correct"), bool):
            question_results.append(bool(result["correct"]))
    report = {
        "study_ms": sum(max(0, int(row["duration_ms"] or 0)) for row in rows),
        "reviews_done": len(review_results),
        "review_accuracy": (
            round(sum(review_results) / len(review_results), 4)
            if review_results
            else None
        ),
        "new_words": new_words,
        "questions_answered": len(question_results),
        "question_accuracy": (
            round(sum(question_results) / len(question_results), 4)
            if question_results
            else None
        ),
    }
    connection.execute(
        """
        INSERT INTO metrics_daily(metric_date, data, computed_at)
        VALUES (?, ?, ?)
        ON CONFLICT(metric_date) DO UPDATE SET
            data = excluded.data,
            computed_at = excluded.computed_at
        """,
        (
            report_date.isoformat(),
            json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            utc_now(),
        ),
    )
    connection.commit()
    return report
