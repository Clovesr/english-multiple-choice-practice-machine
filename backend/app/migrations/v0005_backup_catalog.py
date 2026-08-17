from __future__ import annotations

import sqlite3

from .runner import Migration


STATEMENT = """
CREATE TABLE backup_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'manual',
    checksum TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    app_version TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok',
    created_at TEXT NOT NULL
)
"""


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(STATEMENT)


MIGRATION = Migration(
    version=5,
    name="backup_catalog",
    fingerprint=" ".join(STATEMENT.split()),
    apply=apply,
)
