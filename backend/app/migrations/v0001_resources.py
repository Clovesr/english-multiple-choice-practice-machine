from __future__ import annotations

import sqlite3

from .runner import Migration


STATEMENTS = (
    """
    CREATE TABLE resources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        type TEXT NOT NULL,
        language TEXT NOT NULL DEFAULT 'en',
        format TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'inbox',
        source TEXT NOT NULL DEFAULT '',
        source_url TEXT NOT NULL DEFAULT '',
        author TEXT NOT NULL DEFAULT '',
        license TEXT NOT NULL DEFAULT '',
        private_only INTEGER NOT NULL DEFAULT 1,
        checksum TEXT NOT NULL,
        original_filename TEXT NOT NULL DEFAULT '',
        stored_path TEXT NOT NULL DEFAULT '',
        media_type TEXT NOT NULL DEFAULT '',
        size_bytes INTEGER NOT NULL DEFAULT 0,
        segment_count INTEGER NOT NULL DEFAULT 0,
        parser_name TEXT NOT NULL DEFAULT '',
        parser_version INTEGER NOT NULL DEFAULT 1,
        parse_error TEXT NOT NULL DEFAULT '',
        imported_at TEXT NOT NULL,
        deleted_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE UNIQUE INDEX idx_resources_checksum
        ON resources(checksum) WHERE deleted_at IS NULL
    """,
    """
    CREATE INDEX idx_resources_list
        ON resources(status, deleted_at, updated_at DESC)
    """,
    """
    CREATE TABLE resource_segments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
        sequence INTEGER NOT NULL,
        kind TEXT NOT NULL DEFAULT 'paragraph',
        heading_level INTEGER NOT NULL DEFAULT 0,
        content TEXT NOT NULL,
        metadata TEXT NOT NULL DEFAULT '{}',
        UNIQUE (resource_id, sequence)
    )
    """,
    """
    CREATE TABLE resource_progress (
        resource_id INTEGER PRIMARY KEY REFERENCES resources(id) ON DELETE CASCADE,
        last_segment_id INTEGER,
        scroll_ratio REAL NOT NULL DEFAULT 0,
        total_reading_ms INTEGER NOT NULL DEFAULT 0,
        opened_count INTEGER NOT NULL DEFAULT 0,
        last_opened_at TEXT,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE VIRTUAL TABLE resource_segments_fts USING fts5(
        content,
        content='resource_segments',
        content_rowid='id',
        tokenize='trigram'
    )
    """,
    """
    CREATE TRIGGER resource_segments_fts_insert AFTER INSERT ON resource_segments BEGIN
        INSERT INTO resource_segments_fts(rowid, content) VALUES (new.id, new.content);
    END
    """,
    """
    CREATE TRIGGER resource_segments_fts_delete AFTER DELETE ON resource_segments BEGIN
        INSERT INTO resource_segments_fts(resource_segments_fts, rowid, content)
        VALUES ('delete', old.id, old.content);
    END
    """,
    """
    CREATE TRIGGER resource_segments_fts_update AFTER UPDATE OF content ON resource_segments BEGIN
        INSERT INTO resource_segments_fts(resource_segments_fts, rowid, content)
        VALUES ('delete', old.id, old.content);
        INSERT INTO resource_segments_fts(rowid, content) VALUES (new.id, new.content);
    END
    """,
)


ALTER_RESOURCE_ID = """
ALTER TABLE vocabulary_occurrences
ADD COLUMN resource_id INTEGER REFERENCES resources(id) ON DELETE SET NULL
"""


ALTER_SEGMENT_ID = """
ALTER TABLE vocabulary_occurrences
ADD COLUMN segment_id INTEGER REFERENCES resource_segments(id) ON DELETE SET NULL
"""


FINGERPRINT = "\n-- statement --\n".join(
    " ".join(statement.split())
    for statement in (*STATEMENTS, ALTER_RESOURCE_ID, ALTER_SEGMENT_ID)
)


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def apply(connection: sqlite3.Connection) -> None:
    for statement in STATEMENTS:
        connection.execute(statement)
    occurrence_columns = _column_names(connection, "vocabulary_occurrences")
    if "resource_id" not in occurrence_columns:
        connection.execute(ALTER_RESOURCE_ID)
    if "segment_id" not in occurrence_columns:
        connection.execute(ALTER_SEGMENT_ID)
    connection.execute(
        "INSERT INTO resource_segments_fts(resource_segments_fts) VALUES ('rebuild')"
    )


MIGRATION = Migration(
    version=1,
    name="resources_and_search",
    fingerprint=FINGERPRINT,
    apply=apply,
)
