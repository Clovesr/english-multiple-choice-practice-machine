from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.app.database import SCHEMA
from backend.app.migrations import MIGRATIONS
from backend.app.migrations.runner import (
    Migration,
    MigrationApplyError,
    MigrationChecksumError,
    backup_database,
    pending_migrations,
    run_pending_migrations,
)
from backend.app.migrations.v0002_vocabulary_fsrs import _utc_timestamp


class VersionedMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.database_path = Path(self.temp.name) / "migration-test.db"
        self.database_patch = patch(
            "backend.app.database.DATABASE_PATH", self.database_path
        )
        self.database_patch.start()

    def tearDown(self) -> None:
        self.database_patch.stop()
        self.temp.cleanup()

    def test_initialization_applies_registered_migrations_and_keeps_fts_in_sync(self) -> None:
        from backend.app.database import connect, initialize_database

        initialize_database()
        with connect() as connection:
            migrations = connection.execute(
                """
                SELECT version, name, checksum FROM schema_migrations
                ORDER BY version
                """
            ).fetchall()
            self.assertEqual(
                [(row["version"], row["name"]) for row in migrations],
                [
                    (1, "resources_and_search"),
                    (2, "vocabulary_cards_and_fsrs"),
                    (3, "courses_and_skills"),
                    (4, "tasks_events_and_mastery"),
                    (5, "backup_catalog"),
                ],
            )
            self.assertTrue(all(len(row["checksum"]) == 64 for row in migrations))
            self.assertEqual(
                connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                "wal",
            )
            occurrence_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(vocabulary_occurrences)"
                )
            }
            self.assertTrue({"resource_id", "segment_id"} <= occurrence_columns)
            now = "2026-08-17T11:00:00+00:00"
            resource_id = connection.execute(
                """
                INSERT INTO resources(
                    uuid, title, type, format, checksum, imported_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("resource-1", "Article", "article", "txt", "checksum-1", now, now, now),
            ).lastrowid
            segment_id = connection.execute(
                """
                INSERT INTO resource_segments(resource_id, sequence, content)
                VALUES (?, 1, 'resilience improves with deliberate practice')
                """,
                (resource_id,),
            ).lastrowid
            self.assertEqual(
                connection.execute(
                    "SELECT rowid FROM resource_segments_fts WHERE resource_segments_fts MATCH 'resilience'"
                ).fetchone()[0],
                segment_id,
            )
            connection.execute(
                "UPDATE resource_segments SET content = 'durable learning' WHERE id = ?",
                (segment_id,),
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT rowid FROM resource_segments_fts WHERE resource_segments_fts MATCH 'resilience'"
                ).fetchone()
            )
            self.assertEqual(
                connection.execute(
                    "SELECT rowid FROM resource_segments_fts WHERE resource_segments_fts MATCH 'durable'"
                ).fetchone()[0],
                segment_id,
            )
            connection.execute(
                "DELETE FROM resource_segments WHERE id = ?", (segment_id,)
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT rowid FROM resource_segments_fts WHERE resource_segments_fts MATCH 'durable'"
                ).fetchone()
            )

    def test_repeated_initialization_is_idempotent(self) -> None:
        from backend.app.database import connect, initialize_database

        initialize_database()
        initialize_database()
        with connect() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],
                5,
            )
            self.assertEqual(pending_migrations(connection, MIGRATIONS), ())

    def test_0002_migrates_legacy_vocabulary_to_forward_cards(self) -> None:
        legacy = sqlite3.connect(self.database_path)
        legacy.executescript(SCHEMA)
        entry_id = int(
            legacy.execute(
                """
                INSERT INTO vocabulary_entries(
                    term, normalized_term, lemma, phonetic, part_of_speech,
                    contextual_meaning, common_meaning, synonyms, antonyms,
                    similar_forms, translation_status, next_review_at,
                    last_reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)
                """,
                (
                    "resilience",
                    "resilience",
                    "resilient",
                    "/rɪˈzɪliəns/",
                    "n.",
                    "韧性",
                    "恢复力；韧性",
                    '[{"word":"tenacity","note":"更强调坚持"}]',
                    '[{"word":"fragility","note":"脆弱"}]',
                    '[{"word":"resistance","note":"形近"}]',
                    "2026-08-17T08:00:00+08:00",
                    "2026-08-10T08:00:00+08:00",
                ),
            ).lastrowid
        )
        new_entry_id = int(
            legacy.execute(
                """
                INSERT INTO vocabulary_entries(
                    term, normalized_term, next_review_at, last_reviewed_at
                ) VALUES (
                    'untranslated', 'untranslated',
                    '2030-01-01T08:00:00+08:00', 'broken'
                )
                """
            ).lastrowid
        )
        legacy.executemany(
            """
            INSERT INTO vocabulary_reviews(
                entry_id, rating, reviewed_at, next_review_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                (
                    entry_id,
                    "again",
                    "2026-08-03T08:00:00",
                    "2026-08-04T08:00:00",
                ),
                (
                    entry_id,
                    "mastered",
                    "2026-08-10T08:00:00",
                    "2026-08-17T08:00:00",
                ),
            ),
        )
        legacy.commit()
        legacy.close()

        from backend.app.database import connect, initialize_database

        initialize_database()
        with connect() as connection:
            entry = connection.execute(
                "SELECT * FROM vocabulary_entries WHERE id = ?", (entry_id,)
            ).fetchone()
            self.assertEqual(len(entry["uuid"]), 36)
            self.assertEqual(entry["phonetic"], "/rɪˈzɪliəns/")
            self.assertEqual(entry["phonetic_uk"], "/rɪˈzɪliəns/")
            self.assertEqual(entry["enrichment_status"], "ready")
            self.assertEqual(entry["contextual_meaning"], "韧性")

            sense = connection.execute(
                "SELECT * FROM vocabulary_senses WHERE entry_id = ?", (entry_id,)
            ).fetchone()
            self.assertEqual(sense["gloss_zh"], "恢复力；韧性")
            self.assertEqual(sense["source"], "legacy")
            form = connection.execute(
                "SELECT form_text FROM vocabulary_forms WHERE entry_id = ?",
                (entry_id,),
            ).fetchone()
            self.assertEqual(form["form_text"], "resilient")
            relations = connection.execute(
                """
                SELECT relation_type, related_term FROM vocabulary_relations
                WHERE entry_id = ? ORDER BY relation_type
                """,
                (entry_id,),
            ).fetchall()
            self.assertEqual(
                {(row["relation_type"], row["related_term"]) for row in relations},
                {
                    ("synonym", "tenacity"),
                    ("antonym", "fragility"),
                    ("similar", "resistance"),
                },
            )

            card = connection.execute(
                "SELECT * FROM vocabulary_cards WHERE entry_id = ?", (entry_id,)
            ).fetchone()
            self.assertEqual(
                (card["card_type"], card["variant_key"]),
                ("forward", "primary"),
            )
            migrated_card_id = int(card["id"])
            item = connection.execute(
                """
                SELECT * FROM review_items
                WHERE item_type = 'vocabulary_card' AND ref_id = ?
                """,
                (card["id"],),
            ).fetchone()
            self.assertEqual(item["state"], "review")
            self.assertEqual(item["last_review_at"], "2026-08-10T00:00:00+00:00")
            self.assertEqual(item["due_at"], "2026-08-17T00:00:00+00:00")
            self.assertEqual(item["scheduled_days"], 7)
            self.assertEqual(item["reps"], 2)
            self.assertEqual(item["lapses"], 1)
            self.assertEqual(item["scheduler_version"], "legacy-bootstrap-v1")
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM vocabulary_reviews").fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM review_logs").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT daily_new FROM study_settings WHERE id = 1"
                ).fetchone()[0],
                20,
            )
            new_card = connection.execute(
                "SELECT id FROM vocabulary_cards WHERE entry_id = ?",
                (new_entry_id,),
            ).fetchone()
            new_item = connection.execute(
                "SELECT * FROM review_items WHERE ref_id = ?",
                (new_card["id"],),
            ).fetchone()
            self.assertEqual(new_item["state"], "new")
            self.assertIsNone(new_item["last_review_at"])
            self.assertNotEqual(new_item["due_at"], "2030-01-01T00:00:00+00:00")
            self.assertEqual(
                connection.execute(
                    "SELECT enrichment_status FROM vocabulary_entries WHERE id = ?",
                    (new_entry_id,),
                ).fetchone()[0],
                "needs_enrichment",
            )
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

        initialize_database()
        with connect() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM vocabulary_cards").fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM review_items").fetchone()[0],
                2,
            )
            connection.execute(
                "DELETE FROM vocabulary_entries WHERE id = ?", (entry_id,)
            )
            connection.commit()
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM review_items WHERE ref_id = ?",
                    (migrated_card_id,),
                ).fetchone()
            )

    def test_legacy_naive_timestamp_uses_explicit_local_timezone(self) -> None:
        fallback = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)
        converted = _utc_timestamp(
            "2026-08-17T08:30:00",
            fallback=fallback,
            local_timezone=timezone(timedelta(hours=8)),
        )
        self.assertEqual(converted.isoformat(), "2026-08-17T00:30:00+00:00")

    def test_0002_restores_callers_row_factory(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.executescript(SCHEMA)
        self.assertIsNone(connection.row_factory)
        run_pending_migrations(connection, ":memory:", MIGRATIONS)
        self.assertIsNone(connection.row_factory)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],
            5,
        )
        connection.close()

    def test_existing_database_is_backed_up_before_migration(self) -> None:
        legacy = sqlite3.connect(self.database_path)
        legacy.executescript(SCHEMA)
        legacy.execute(
            """
            INSERT INTO vocabulary_entries(term, normalized_term, contextual_meaning)
            VALUES ('resilience', 'resilience', '韧性')
            """
        )
        legacy.commit()
        legacy.close()

        from backend.app.database import initialize_database

        initialize_database()
        backups = list(
            (self.database_path.parent / "backups" / "pre-migration").glob("*.sqlite3")
        )
        self.assertEqual(len(backups), 1)
        snapshot = sqlite3.connect(backups[0])
        try:
            snapshot_row = snapshot.execute(
                """
                SELECT contextual_meaning, translation_status
                FROM vocabulary_entries WHERE normalized_term = 'resilience'
                """
            ).fetchone()
            self.assertEqual(
                snapshot_row,
                ("韧性", "pending"),
            )
            self.assertIsNone(
                snapshot.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'resources'"
                ).fetchone()
            )
        finally:
            snapshot.close()
        migrated = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                migrated.execute(
                    "SELECT translation_status FROM vocabulary_entries WHERE normalized_term = 'resilience'"
                ).fetchone()[0],
                "queued",
            )
        finally:
            migrated.close()

    def test_failed_migration_rolls_back_and_keeps_verified_backup(self) -> None:
        connection = sqlite3.connect(self.database_path)
        connection.execute("CREATE TABLE sentinel(value TEXT NOT NULL)")
        connection.execute("INSERT INTO sentinel VALUES ('safe')")
        connection.commit()

        def fail_after_write(active: sqlite3.Connection) -> None:
            active.execute("CREATE TABLE should_rollback(id INTEGER PRIMARY KEY)")
            raise RuntimeError("forced failure")

        migration = Migration(1, "forced_failure", "forced failure v1", fail_after_write)
        with self.assertRaises(MigrationApplyError):
            run_pending_migrations(connection, self.database_path, (migration,))
        self.assertEqual(connection.execute("SELECT value FROM sentinel").fetchone()[0], "safe")
        self.assertIsNone(
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'should_rollback'"
            ).fetchone()
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],
            0,
        )
        connection.close()
        backups = list(
            (self.database_path.parent / "backups" / "pre-migration").glob("*.sqlite3")
        )
        self.assertEqual(len(backups), 1)

    def test_applied_checksum_drift_is_rejected(self) -> None:
        connection = sqlite3.connect(self.database_path)
        migration = Migration(
            1,
            "one",
            "first definition",
            lambda active: active.execute("CREATE TABLE one(id INTEGER PRIMARY KEY)"),
        )
        run_pending_migrations(connection, self.database_path, (migration,))
        drifted = Migration(1, "one", "changed definition", lambda active: None)
        with self.assertRaises(MigrationChecksumError):
            pending_migrations(connection, (drifted,))
        connection.close()

    def test_backup_helper_skips_new_empty_database(self) -> None:
        connection = sqlite3.connect(self.database_path)
        self.assertIsNone(
            backup_database(
                connection,
                self.database_path,
                from_version=0,
                to_version=1,
            )
        )
        connection.close()


if __name__ == "__main__":
    unittest.main()
