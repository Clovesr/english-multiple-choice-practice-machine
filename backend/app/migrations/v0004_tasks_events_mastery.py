from __future__ import annotations

import sqlite3

from .runner import Migration


STATEMENTS = (
    """
    CREATE TABLE daily_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_date TEXT NOT NULL,
        task_type TEXT NOT NULL,
        target_type TEXT NOT NULL DEFAULT '',
        target_id INTEGER,
        title TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        generated_by_rule TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        completed_at TEXT,
        UNIQUE (task_date, task_type, target_type, target_id)
    )
    """,
    """
    CREATE TABLE learning_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verb TEXT NOT NULL,
        object_type TEXT NOT NULL,
        object_id INTEGER,
        result_json TEXT NOT NULL DEFAULT '{}',
        duration_ms INTEGER NOT NULL DEFAULT 0,
        occurred_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX idx_events_time ON learning_events(occurred_at)
    """,
    """
    CREATE TABLE mastery_states (
        skill_id INTEGER PRIMARY KEY REFERENCES skills(id) ON DELETE CASCADE,
        mastery_score REAL NOT NULL DEFAULT 0,
        evidence_count INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE metrics_daily (
        metric_date TEXT PRIMARY KEY,
        data TEXT NOT NULL DEFAULT '{}',
        computed_at TEXT NOT NULL
    )
    """,
)


FINGERPRINT = "\n-- statement --\n".join(
    " ".join(statement.split()) for statement in STATEMENTS
)


def apply(connection: sqlite3.Connection) -> None:
    for statement in STATEMENTS:
        connection.execute(statement)


MIGRATION = Migration(
    version=4,
    name="tasks_events_and_mastery",
    fingerprint=FINGERPRINT,
    apply=apply,
)
