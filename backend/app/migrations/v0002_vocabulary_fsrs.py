from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone, tzinfo
from typing import Any
from uuid import uuid4

from .runner import Migration


STATEMENTS = (
    """
    CREATE TABLE vocabulary_senses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid TEXT NOT NULL UNIQUE,
        entry_id INTEGER NOT NULL REFERENCES vocabulary_entries(id) ON DELETE CASCADE,
        pos TEXT NOT NULL DEFAULT '',
        gloss_zh TEXT NOT NULL DEFAULT '',
        gloss_en TEXT NOT NULL DEFAULT '',
        sequence INTEGER NOT NULL DEFAULT 1,
        source TEXT NOT NULL DEFAULT 'user',
        source_ref TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (entry_id, source, source_ref, sequence)
    )
    """,
    """
    CREATE TABLE vocabulary_forms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id INTEGER NOT NULL REFERENCES vocabulary_entries(id) ON DELETE CASCADE,
        form_type TEXT NOT NULL,
        form_text TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'user',
        UNIQUE (entry_id, form_type, form_text)
    )
    """,
    """
    CREATE TABLE vocabulary_relations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id INTEGER NOT NULL REFERENCES vocabulary_entries(id) ON DELETE CASCADE,
        relation_type TEXT NOT NULL,
        related_term TEXT NOT NULL,
        related_entry_id INTEGER REFERENCES vocabulary_entries(id) ON DELETE SET NULL,
        note TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'user',
        UNIQUE (entry_id, relation_type, related_term, source)
    )
    """,
    """
    CREATE TABLE wordbooks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        source_tag TEXT NOT NULL DEFAULT '',
        source_name TEXT NOT NULL DEFAULT '',
        source_version TEXT NOT NULL DEFAULT '',
        license TEXT NOT NULL DEFAULT '',
        checksum TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE UNIQUE INDEX idx_wordbooks_name
        ON wordbooks(name COLLATE NOCASE) WHERE status <> 'deleted'
    """,
    """
    CREATE TABLE wordbook_entries (
        wordbook_id INTEGER NOT NULL REFERENCES wordbooks(id) ON DELETE CASCADE,
        entry_id INTEGER NOT NULL REFERENCES vocabulary_entries(id) ON DELETE CASCADE,
        sequence INTEGER NOT NULL DEFAULT 0,
        frequency_rank INTEGER,
        added_at TEXT NOT NULL,
        PRIMARY KEY (wordbook_id, entry_id)
    )
    """,
    """
    CREATE TABLE study_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid TEXT NOT NULL UNIQUE,
        wordbook_id INTEGER NOT NULL REFERENCES wordbooks(id) ON DELETE CASCADE,
        mode TEXT NOT NULL DEFAULT 'normal',
        daily_new INTEGER NOT NULL DEFAULT 20,
        new_order TEXT NOT NULL DEFAULT 'frequency',
        exam_date TEXT,
        active INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE UNIQUE INDEX idx_study_plans_one_active
        ON study_plans(active) WHERE active = 1
    """,
    """
    CREATE TABLE study_settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        daily_new INTEGER NOT NULL DEFAULT 20,
        daily_review_max INTEGER NOT NULL DEFAULT 200,
        enabled_card_types TEXT NOT NULL DEFAULT
            '["forward","reverse","listening","spelling","cloze","collocation"]',
        new_card_order TEXT NOT NULL DEFAULT 'frequency',
        leech_threshold INTEGER NOT NULL DEFAULT 8,
        backlog_mode TEXT NOT NULL DEFAULT 'spread',
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE vocabulary_cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid TEXT NOT NULL UNIQUE,
        entry_id INTEGER NOT NULL REFERENCES vocabulary_entries(id) ON DELETE CASCADE,
        card_type TEXT NOT NULL,
        variant_key TEXT NOT NULL,
        generation_source TEXT NOT NULL DEFAULT '',
        prompt_data TEXT NOT NULL DEFAULT '{}',
        answer_data TEXT NOT NULL DEFAULT '{}',
        content_version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (entry_id, card_type, variant_key)
    )
    """,
    """
    CREATE INDEX idx_vocabulary_cards_entry
        ON vocabulary_cards(entry_id, card_type)
    """,
    """
    CREATE TABLE review_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid TEXT NOT NULL UNIQUE,
        item_type TEXT NOT NULL,
        ref_id INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT 'new',
        step INTEGER NOT NULL DEFAULT 0,
        due_at TEXT NOT NULL,
        last_review_at TEXT,
        stability REAL NOT NULL DEFAULT 0,
        difficulty REAL NOT NULL DEFAULT 0,
        scheduled_days REAL NOT NULL DEFAULT 0,
        elapsed_days REAL NOT NULL DEFAULT 0,
        reps INTEGER NOT NULL DEFAULT 0,
        lapses INTEGER NOT NULL DEFAULT 0,
        manually_suspended INTEGER NOT NULL DEFAULT 0,
        scheduler TEXT NOT NULL DEFAULT 'fsrs',
        scheduler_version TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (item_type, ref_id)
    )
    """,
    """
    CREATE INDEX idx_review_due
        ON review_items(manually_suspended, due_at)
    """,
    """
    CREATE TRIGGER vocabulary_cards_review_item_delete
    AFTER DELETE ON vocabulary_cards BEGIN
        DELETE FROM review_items
        WHERE item_type = 'vocabulary_card' AND ref_id = old.id;
    END
    """,
    """
    CREATE TABLE review_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid TEXT NOT NULL UNIQUE,
        attempt_id TEXT NOT NULL UNIQUE,
        review_item_id INTEGER NOT NULL REFERENCES review_items(id) ON DELETE CASCADE,
        card_id INTEGER REFERENCES vocabulary_cards(id) ON DELETE SET NULL,
        answer_given TEXT,
        auto_correct INTEGER,
        auto_rating INTEGER,
        final_rating INTEGER NOT NULL,
        state_before TEXT NOT NULL,
        state_after TEXT NOT NULL,
        step_before INTEGER NOT NULL,
        step_after INTEGER NOT NULL,
        due_before TEXT NOT NULL,
        due_after TEXT NOT NULL,
        stability_before REAL NOT NULL,
        stability_after REAL NOT NULL,
        difficulty_before REAL NOT NULL,
        difficulty_after REAL NOT NULL,
        elapsed_days REAL NOT NULL DEFAULT 0,
        scheduled_days REAL NOT NULL DEFAULT 0,
        duration_ms INTEGER NOT NULL DEFAULT 0,
        reviewed_at TEXT NOT NULL,
        CHECK (auto_correct IS NULL OR auto_correct IN (0, 1)),
        CHECK (auto_rating IS NULL OR auto_rating BETWEEN 1 AND 4),
        CHECK (final_rating BETWEEN 1 AND 4)
    )
    """,
    """
    CREATE INDEX idx_review_logs_item
        ON review_logs(review_item_id, reviewed_at)
    """,
    """
    CREATE TABLE study_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid TEXT NOT NULL UNIQUE,
        plan_id INTEGER REFERENCES study_plans(id) ON DELETE SET NULL,
        status TEXT NOT NULL DEFAULT 'active',
        card_limit INTEGER NOT NULL,
        new_quota INTEGER NOT NULL DEFAULT 0,
        started_at TEXT NOT NULL,
        last_accessed_at TEXT NOT NULL,
        completed_at TEXT
    )
    """,
    """
    CREATE TABLE study_session_cards (
        session_id INTEGER NOT NULL REFERENCES study_sessions(id) ON DELETE CASCADE,
        card_id INTEGER NOT NULL REFERENCES vocabulary_cards(id) ON DELETE CASCADE,
        sequence INTEGER NOT NULL,
        bucket TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        graded_at TEXT,
        PRIMARY KEY (session_id, card_id),
        UNIQUE (session_id, sequence)
    )
    """,
)


ENTRY_COLUMNS = (
    ("uuid", "TEXT"),
    ("dictionary_key", "TEXT NOT NULL DEFAULT ''"),
    ("phonetic_uk", "TEXT NOT NULL DEFAULT ''"),
    ("phonetic_us", "TEXT NOT NULL DEFAULT ''"),
    ("source_kind", "TEXT NOT NULL DEFAULT 'user'"),
    ("enrichment_status", "TEXT NOT NULL DEFAULT 'needs_enrichment'"),
    ("deleted_at", "TEXT"),
)


POST_ALTER_STATEMENTS = (
    """
    CREATE UNIQUE INDEX idx_vocabulary_entries_uuid
        ON vocabulary_entries(uuid) WHERE uuid IS NOT NULL
    """,
    """
    CREATE INDEX idx_vocabulary_entries_dictionary_key
        ON vocabulary_entries(dictionary_key) WHERE dictionary_key <> ''
    """,
)


DATA_TRANSFORM_FINGERPRINT = """
legacy-vocabulary-bootstrap-v1:
- populate stable UUIDs and enrichment metadata without rewriting old columns
- copy the legacy phonetic into phonetic_uk when no structured phonetic exists
- backfill one structured sense plus lemma and relation facts where legacy data exists
- generate one primary forward card for every non-deleted legacy entry
- attach that card to one vocabulary_card review item
- preserve reviewed entries' converted next/last-review timestamps
- make never-reviewed entries due immediately and preserve vocabulary_reviews rows
- seed singleton study settings
"""


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _utc_timestamp(
    value: Any,
    *,
    fallback: datetime,
    local_timezone: tzinfo | None = None,
) -> datetime:
    cleaned = str(value or "").strip()
    if not cleaned:
        return fallback
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        if local_timezone is None:
            return parsed.astimezone(timezone.utc)
        parsed = parsed.replace(tzinfo=local_timezone)
    return parsed.astimezone(timezone.utc)


def _optional_utc_timestamp(value: Any) -> datetime | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.astimezone(timezone.utc)
    return parsed.astimezone(timezone.utc)


def _relation_values(raw: Any) -> list[tuple[str, str]]:
    try:
        values = json.loads(str(raw or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(values, list):
        return []
    result: list[tuple[str, str]] = []
    for value in values:
        if isinstance(value, str):
            term = value.strip()
            note = ""
        elif isinstance(value, dict):
            term = str(value.get("word") or value.get("term") or "").strip()
            note = str(value.get("note") or "").strip()
        else:
            continue
        if term:
            result.append((term, note))
    return result


def _review_count(connection: sqlite3.Connection, entry_id: int) -> tuple[int, int]:
    row = connection.execute(
        """
        SELECT COUNT(*) AS reviews,
               COALESCE(SUM(CASE WHEN rating = 'again' THEN 1 ELSE 0 END), 0) AS lapses
        FROM vocabulary_reviews WHERE entry_id = ?
        """,
        (entry_id,),
    ).fetchone()
    return int(row[0]), int(row[1])


def _migrate_entry(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    now: datetime,
) -> None:
    entry_id = int(row["id"])
    entry_uuid = str(row["uuid"] or "").strip() or str(uuid4())
    phonetic_uk = str(row["phonetic_uk"] or "").strip()
    legacy_phonetic = str(row["phonetic"] or "").strip()
    common_meaning = str(row["common_meaning"] or "").strip()
    contextual_meaning = str(row["contextual_meaning"] or "").strip()
    enrichment_status = "ready" if common_meaning or contextual_meaning else "needs_enrichment"
    connection.execute(
        """
        UPDATE vocabulary_entries
        SET uuid = ?, phonetic_uk = ?, enrichment_status = ?
        WHERE id = ?
        """,
        (entry_uuid, phonetic_uk or legacy_phonetic, enrichment_status, entry_id),
    )

    timestamp = now.isoformat(timespec="seconds")
    gloss = common_meaning or contextual_meaning
    if gloss:
        connection.execute(
            """
            INSERT INTO vocabulary_senses(
                uuid, entry_id, pos, gloss_zh, sequence, source, source_ref,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, 1, 'legacy', ?, ?, ?)
            """,
            (
                str(uuid4()),
                entry_id,
                str(row["part_of_speech"] or "").strip(),
                gloss,
                f"vocabulary_entries:{entry_id}",
                timestamp,
                timestamp,
            ),
        )

    lemma = str(row["lemma"] or "").strip()
    term = str(row["term"] or "").strip()
    if lemma and lemma.casefold() != term.casefold():
        connection.execute(
            """
            INSERT OR IGNORE INTO vocabulary_forms(
                entry_id, form_type, form_text, source
            ) VALUES (?, 'lemma', ?, 'legacy')
            """,
            (entry_id, lemma),
        )

    for source_column, relation_type in (
        ("synonyms", "synonym"),
        ("antonyms", "antonym"),
        ("similar_forms", "similar"),
    ):
        for related_term, note in _relation_values(row[source_column]):
            connection.execute(
                """
                INSERT OR IGNORE INTO vocabulary_relations(
                    entry_id, relation_type, related_term, note, source
                ) VALUES (?, ?, ?, ?, 'legacy')
                """,
                (entry_id, relation_type, related_term, note),
            )

    answer_text = contextual_meaning or common_meaning
    prompt_data = json.dumps({"text": term}, ensure_ascii=False, sort_keys=True)
    answer_data = json.dumps(
        {"text": answer_text, "accept": []},
        ensure_ascii=False,
        sort_keys=True,
    )
    card_cursor = connection.execute(
        """
        INSERT INTO vocabulary_cards(
            uuid, entry_id, card_type, variant_key, generation_source,
            prompt_data, answer_data, content_version, created_at, updated_at
        ) VALUES (?, ?, 'forward', 'primary', 'legacy_migration', ?, ?, 1, ?, ?)
        """,
        (str(uuid4()), entry_id, prompt_data, answer_data, timestamp, timestamp),
    )
    card_id = int(card_cursor.lastrowid)

    last_review = _optional_utc_timestamp(row["last_reviewed_at"])
    due = _utc_timestamp(row["next_review_at"], fallback=now)
    reviews, lapses = _review_count(connection, entry_id)
    if last_review is None:
        state = "new"
        due = now
        reps = 0
        lapses = 0
        scheduled_days = 0.0
        elapsed_days = 0.0
        stability = 0.0
        difficulty = 0.0
    else:
        state = "review"
        reps = max(1, reviews)
        scheduled_days = max(0.0, (due - last_review).total_seconds() / 86400)
        elapsed_days = max(0.0, (now - last_review).total_seconds() / 86400)
        stability = max(1.0, scheduled_days)
        difficulty = 5.0
    connection.execute(
        """
        INSERT INTO review_items(
            uuid, item_type, ref_id, state, step, due_at, last_review_at,
            stability, difficulty, scheduled_days, elapsed_days, reps, lapses,
            manually_suspended, scheduler, scheduler_version, created_at, updated_at
        ) VALUES (
            ?, 'vocabulary_card', ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?,
            0, 'fsrs', 'legacy-bootstrap-v1', ?, ?
        )
        """,
        (
            str(uuid4()),
            card_id,
            state,
            due.isoformat(timespec="seconds"),
            last_review.isoformat(timespec="seconds") if last_review else None,
            stability,
            difficulty,
            scheduled_days,
            elapsed_days,
            reps,
            lapses,
            timestamp,
            timestamp,
        ),
    )


def apply(connection: sqlite3.Connection) -> None:
    for statement in STATEMENTS:
        connection.execute(statement)
    existing_columns = _column_names(connection, "vocabulary_entries")
    for name, definition in ENTRY_COLUMNS:
        if name not in existing_columns:
            connection.execute(
                f"ALTER TABLE vocabulary_entries ADD COLUMN {name} {definition}"
            )
    for statement in POST_ALTER_STATEMENTS:
        connection.execute(statement)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    previous_row_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT * FROM vocabulary_entries WHERE deleted_at IS NULL ORDER BY id"
        ).fetchall()
    finally:
        connection.row_factory = previous_row_factory
    for row in rows:
        _migrate_entry(connection, row, now=now)
    connection.execute(
        "INSERT INTO study_settings(id, updated_at) VALUES (1, ?)",
        (now.isoformat(timespec="seconds"),),
    )


FINGERPRINT = "\n-- statement --\n".join(
    [
        *(" ".join(statement.split()) for statement in STATEMENTS),
        *(
            f"ALTER vocabulary_entries ADD {name} {definition}"
            for name, definition in ENTRY_COLUMNS
        ),
        *(" ".join(statement.split()) for statement in POST_ALTER_STATEMENTS),
        " ".join(DATA_TRANSFORM_FINGERPRINT.split()),
    ]
)


MIGRATION = Migration(
    version=2,
    name="vocabulary_cards_and_fsrs",
    fingerprint=FINGERPRINT,
    apply=apply,
)
