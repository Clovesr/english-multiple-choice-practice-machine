from __future__ import annotations

import sqlite3
import unittest
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

    def test_initialization_applies_0001_and_keeps_fts_in_sync(self) -> None:
        from backend.app.database import connect, initialize_database

        initialize_database()
        with connect() as connection:
            migration = connection.execute(
                "SELECT version, name, checksum FROM schema_migrations"
            ).fetchone()
            self.assertEqual((migration["version"], migration["name"]), (1, "resources_and_search"))
            self.assertEqual(len(migration["checksum"]), 64)
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
                1,
            )
            self.assertEqual(pending_migrations(connection, MIGRATIONS), ())

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
