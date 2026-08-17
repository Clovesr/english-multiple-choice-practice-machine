from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def record_learning_event(
    connection: sqlite3.Connection,
    *,
    verb: str,
    object_type: str,
    object_id: int | None = None,
    result: dict[str, Any] | None = None,
    duration_ms: int = 0,
    occurred_at: str | None = None,
) -> int:
    """Append one immutable source event without committing its caller's transaction."""

    cursor = connection.execute(
        """
        INSERT INTO learning_events(
            verb, object_type, object_id, result_json, duration_ms, occurred_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            verb,
            object_type,
            object_id,
            json.dumps(
                result or {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            max(0, int(duration_ms)),
            occurred_at or utc_now(),
        ),
    )
    return int(cursor.lastrowid)
