from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .runner import Migration


ARCHIVE_REASON = "merged_into_word_level_schedule"


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def apply(connection: sqlite3.Connection) -> None:
    """Collapse active vocabulary scheduling from card rows to one row per entry."""

    columns = _column_names(connection, "review_items")
    if "archived_at" not in columns:
        connection.execute("ALTER TABLE review_items ADD COLUMN archived_at TEXT")
    if "archive_reason" not in columns:
        connection.execute(
            "ALTER TABLE review_items ADD COLUMN archive_reason TEXT NOT NULL DEFAULT ''"
        )

    statements = (
        """
        CREATE TABLE IF NOT EXISTS vocabulary_card_type_settings (
            entry_id INTEGER NOT NULL
                REFERENCES vocabulary_entries(id) ON DELETE CASCADE,
            card_type TEXT NOT NULL,
            manually_suspended INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (entry_id, card_type)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_review_vocabulary_due
            ON review_items(item_type, manually_suspended, state, due_at, ref_id)
            WHERE archived_at IS NULL
        """,
        "DROP TRIGGER IF EXISTS vocabulary_cards_review_item_delete",
        """
        CREATE TRIGGER vocabulary_cards_review_item_delete
        AFTER DELETE ON vocabulary_cards BEGIN
            DELETE FROM review_items
            WHERE item_type = 'vocabulary_card'
              AND ref_id = old.id
              AND archived_at IS NULL;
        END
        """,
        "DROP TRIGGER IF EXISTS vocabulary_entries_review_item_delete",
        """
        CREATE TRIGGER vocabulary_entries_review_item_delete
        AFTER DELETE ON vocabulary_entries BEGIN
            DELETE FROM review_items
            WHERE item_type = 'vocabulary'
              AND ref_id = old.id
              AND archived_at IS NULL;
        END
        """,
    )
    for statement in statements:
        connection.execute(statement)

    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    # A paused legacy card becomes a paused presentation type for that word.
    # MAX intentionally widens a paused variant to the whole entry/type pair.
    type_rows = connection.execute(
        """
        SELECT vc.entry_id, vc.card_type,
               MAX(COALESCE(ri.manually_suspended, 0)) AS manually_suspended
        FROM vocabulary_cards AS vc
        LEFT JOIN review_items AS ri
          ON ri.item_type = 'vocabulary_card'
         AND ri.ref_id = vc.id
         AND ri.archived_at IS NULL
        GROUP BY vc.entry_id, vc.card_type
        """
    ).fetchall()
    for entry_id, card_type, manually_suspended in type_rows:
        connection.execute(
            """
            INSERT INTO vocabulary_card_type_settings(
                entry_id, card_type, manually_suspended, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(entry_id, card_type) DO UPDATE SET
                manually_suspended = CASE
                    WHEN vocabulary_card_type_settings.manually_suspended = 1
                      OR excluded.manually_suspended = 1
                    THEN 1 ELSE 0 END,
                updated_at = excluded.updated_at
            """,
            (
                int(entry_id),
                str(card_type),
                int(bool(manually_suspended)),
                timestamp,
                timestamp,
            ),
        )

    existing_word_items = {
        int(ref_id): int(item_id)
        for item_id, ref_id in connection.execute(
            """
            SELECT id, ref_id FROM review_items
            WHERE item_type = 'vocabulary' AND archived_at IS NULL
            """
        ).fetchall()
    }
    legacy_rows = connection.execute(
        """
        SELECT ri.id, vc.entry_id
        FROM review_items AS ri
        JOIN vocabulary_cards AS vc ON vc.id = ri.ref_id
        WHERE ri.item_type = 'vocabulary_card'
          AND ri.archived_at IS NULL
        ORDER BY vc.entry_id, ri.reps DESC, ri.due_at ASC, ri.id ASC
        """
    ).fetchall()

    by_entry: dict[int, list[int]] = {}
    for item_id, entry_id in legacy_rows:
        by_entry.setdefault(int(entry_id), []).append(int(item_id))

    for entry_id, item_ids in by_entry.items():
        active_id = existing_word_items.get(entry_id)
        if active_id is None:
            # The query order encodes the frozen merge rule: greatest reps,
            # then earliest due, then stable primary-key tie break.
            active_id = item_ids.pop(0)
            connection.execute(
                """
                UPDATE review_items
                SET item_type = 'vocabulary', ref_id = ?, manually_suspended = 0,
                    archived_at = NULL, archive_reason = '', updated_at = ?
                WHERE id = ?
                """,
                (entry_id, timestamp, active_id),
            )
            existing_word_items[entry_id] = active_id
        for item_id in item_ids:
            connection.execute(
                """
                UPDATE review_items
                SET archived_at = ?, archive_reason = ?, updated_at = ?
                WHERE id = ?
                """,
                (timestamp, f"{ARCHIVE_REASON}:{active_id}", timestamp, item_id),
            )

    # Preserve even malformed legacy rows whose card disappeared before the
    # migration; they stay auditable but can never enter an active queue.
    connection.execute(
        """
        UPDATE review_items
        SET archived_at = ?, archive_reason = ?, updated_at = ?
        WHERE item_type = 'vocabulary_card'
          AND archived_at IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM vocabulary_cards AS vc
              WHERE vc.id = review_items.ref_id
          )
        """,
        (timestamp, "legacy_card_missing", timestamp),
    )

    # A persisted card batch was selected under the superseded six-clock
    # policy. Closing it prevents same-word siblings from resurfacing once.
    connection.execute(
        """
        UPDATE study_sessions
        SET status = 'completed', completed_at = COALESCE(completed_at, ?),
            last_accessed_at = ?
        WHERE status = 'active'
        """,
        (timestamp, timestamp),
    )


FINGERPRINT = """
word-level-vocabulary-scheduling-v1:
- add review_items archival metadata and per-entry card-type suspension facts
- merge each entry's vocabulary_card items by greatest reps, earliest due, then id
- repurpose the winning item as item_type vocabulary/ref_id entry_id
- archive every losing legacy item without rewriting review_logs
- map any legacy card suspension to the matching entry/card_type switch
- close active sessions selected under card-level scheduling
- keep archived legacy items when cards are physically removed
- remove the active word item when its entry is physically deleted
"""


MIGRATION = Migration(
    version=6,
    name="word_level_vocabulary_scheduling",
    fingerprint=" ".join(FINGERPRINT.split()),
    apply=apply,
)
