from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient


FIXTURES = Path(__file__).resolve().parents[1] / "test-fixtures"


class BackupApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.database_path = Path(self.temp.name) / "backup-test.db"
        self.database_patch = patch(
            "backend.app.database.DATABASE_PATH", self.database_path
        )
        self.database_patch.start()
        from backend.app.database import initialize_database
        from backend.app.main import app

        initialize_database()
        self.client = TestClient(app)
        self.first = self._import("Original resource content.", "原始资料.txt")

    def tearDown(self) -> None:
        self.client.close()
        self.database_patch.stop()
        self.temp.cleanup()

    def _import(self, content: str, filename: str) -> dict:
        response = self.client.post(
            "/api/resources/import",
            files={"file": (filename, content.encode("utf-8"), "text/plain")},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["resource"]

    def _stored_file(self, resource: dict) -> Path:
        return next(
            (self.database_path.parent / "resources" / resource["uuid"]).glob(
                "original.*"
            )
        )

    def _seed_linked_learning_data(self) -> None:
        segment_id = self.client.get(
            f"/api/resources/{self.first['id']}/segments"
        ).json()["items"][0]["id"]
        collected = self.client.post(
            "/api/vocabulary/from-selection",
            json={
                "term": "original",
                "context_sentence": "Original resource content.",
                "resource_id": self.first["id"],
                "segment_id": segment_id,
            },
        )
        self.assertEqual(collected.status_code, 201, collected.text)

        from backend.app.database import connect

        with connect() as connection:
            paper_id = int(
                connection.execute(
                    """
                    INSERT INTO papers(profile_id, year, title, status)
                    VALUES (1, 2026, '备份关联测试卷', 'published')
                    """
                ).lastrowid
            )
            unit_id = int(
                connection.execute(
                    """
                    INSERT INTO units(
                        paper_id, unit_type, title, sequence, passage
                    ) VALUES (?, 'reading', '备份关联阅读', 1, 'Backup passage.')
                    """,
                    (paper_id,),
                ).lastrowid
            )
            question_id = int(
                connection.execute(
                    """
                    INSERT INTO questions(
                        unit_id, number, stem, answer, score, sequence
                    ) VALUES (?, 1, 'Choose the right answer.', 'A', 2, 1)
                    """,
                    (unit_id,),
                ).lastrowid
            )
            connection.executemany(
                """
                INSERT INTO options(
                    question_id, stable_key, original_label, content, sequence
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (question_id, "A", "A", "Correct", 1),
                    (question_id, "B", "B", "Wrong", 2),
                ),
            )
            connection.commit()
        session = self.client.post(
            "/api/practice/sessions",
            json={
                "mode": "unit",
                "unit_ids": [unit_id],
                "shuffle_options": False,
            },
        )
        self.assertEqual(session.status_code, 200, session.text)
        session_id = session.json()["id"]
        answer = self.client.put(
            f"/api/practice/sessions/{session_id}/answers/{question_id}",
            json={"answer": "B", "option_order": ["A", "B"]},
        )
        self.assertEqual(answer.status_code, 200, answer.text)
        submitted = self.client.post(f"/api/practice/sessions/{session_id}/submit")
        self.assertEqual(submitted.status_code, 200, submitted.text)

    def test_create_list_and_verify_backup_package(self) -> None:
        created = self.client.post("/api/backup/create", json={"kind": "manual"})
        self.assertEqual(created.status_code, 201, created.text)
        backup = created.json()
        package = self.database_path.parent / backup["path"]
        self.assertTrue(package.is_file())
        self.assertEqual(package.stat().st_size, backup["size_bytes"])
        self.assertEqual(
            hashlib.sha256(package.read_bytes()).hexdigest(), backup["checksum"]
        )
        listed = self.client.get("/api/backup/list")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["items"][0]["id"], backup["id"])
        verified = self.client.post(f"/api/backup/{backup['id']}/verify")
        self.assertEqual(verified.status_code, 200, verified.text)
        self.assertTrue(verified.json()["verified"])
        self.assertEqual(verified.json()["backup"]["status"], "verified")
        self.assertEqual(verified.json()["object_counts"]["resources"], 1)

    def test_dry_run_and_restore_replace_database_and_user_files(self) -> None:
        original_file = self._stored_file(self.first)
        original_bytes = original_file.read_bytes()
        created = self.client.post("/api/backup/create", json={"kind": "manual"})
        backup = created.json()

        original_file.write_text("mutated on disk", encoding="utf-8")
        edited = self.client.put(
            f"/api/resources/{self.first['id']}", json={"title": "Mutated title"}
        )
        self.assertEqual(edited.status_code, 200, edited.text)
        extra = self._import("Extra state after backup.", "额外资料.txt")
        extra_file = self._stored_file(extra)

        preview = self.client.post(
            f"/api/backup/{backup['id']}/restore", json={"dry_run": True}
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertTrue(preview.json()["dry_run"])
        self.assertTrue(preview.json()["can_restore"])
        self.assertEqual(
            preview.json()["object_counts"]["resources"],
            {"current": 2, "backup": 1, "delta": -1},
        )
        self.assertEqual(
            self.client.get(f"/api/resources/{self.first['id']}").json()["title"],
            "Mutated title",
        )

        restored = self.client.post(
            f"/api/backup/{backup['id']}/restore", json={"dry_run": False}
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertTrue(restored.json()["restored"])
        self.assertNotEqual(
            restored.json()["backup_id"], restored.json()["pre_restore_backup_id"]
        )
        listed = self.client.get("/api/resources").json()
        self.assertEqual(listed["total"], 1)
        self.assertEqual(listed["items"][0]["title"], "原始资料")
        self.assertEqual(original_file.read_bytes(), original_bytes)
        self.assertFalse(extra_file.exists())
        catalog = self.client.get("/api/backup/list").json()["items"]
        self.assertGreaterEqual(len(catalog), 2)
        pre_restore = next(
            item
            for item in catalog
            if item["id"] == restored.json()["pre_restore_backup_id"]
        )
        self.assertTrue((self.database_path.parent / pre_restore["path"]).is_file())

    def test_portable_package_restores_linked_data_into_empty_environment(self) -> None:
        self._seed_linked_learning_data()
        from backend.app.database import connect, initialize_database
        from backend.app.main import app

        compared_tables = (
            "resources",
            "resource_segments",
            "vocabulary_entries",
            "vocabulary_cards",
            "review_items",
            "practice_sessions",
            "practice_answers",
        )
        with connect() as connection:
            source_counts = {
                table: int(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
                for table in compared_tables
            }
        self.assertTrue(all(count > 0 for count in source_counts.values()))
        created = self.client.post("/api/backup/create", json={"kind": "manual"})
        self.assertEqual(created.status_code, 201, created.text)
        source_package = self.database_path.parent / created.json()["path"]
        package_bytes = source_package.read_bytes()

        self.client.close()
        empty_root = self.database_path.parent / "empty-environment"
        empty_root.mkdir()
        empty_database = empty_root / "restored.db"
        import backend.app.database as database_module

        database_module.DATABASE_PATH = empty_database
        initialize_database()
        self.client = TestClient(app)
        imported = self.client.post(
            "/api/backup/import",
            files={"file": ("portable-backup.zip", package_bytes, "application/zip")},
        )
        self.assertEqual(imported.status_code, 201, imported.text)
        restored = self.client.post(
            f"/api/backup/{imported.json()['id']}/restore",
            json={"dry_run": False},
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertTrue(restored.json()["restored"])

        with connect() as connection:
            target_counts = {
                table: int(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
                for table in compared_tables
            }
            self.assertEqual(target_counts, source_counts)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            occurrence = connection.execute(
                """
                SELECT vo.resource_id, vo.segment_id
                FROM vocabulary_occurrences vo
                JOIN resources r ON r.id = vo.resource_id
                JOIN resource_segments rs ON rs.id = vo.segment_id
                WHERE r.title = '原始资料' AND rs.resource_id = r.id
                """
            ).fetchone()
            self.assertIsNotNone(occurrence)
            linked_review = connection.execute(
                """
                SELECT ri.id
                FROM review_items ri
                JOIN vocabulary_cards vc ON vc.id = ri.ref_id
                JOIN vocabulary_entries ve ON ve.id = vc.entry_id
                WHERE ri.item_type = 'vocabulary_card'
                  AND ve.normalized_term = 'original'
                """
            ).fetchone()
            self.assertIsNotNone(linked_review)
            linked_answer = connection.execute(
                """
                SELECT pa.id
                FROM practice_answers pa
                JOIN practice_sessions ps ON ps.id = pa.session_id
                JOIN questions q ON q.id = pa.question_id
                WHERE ps.status = 'submitted' AND pa.is_correct = 0
                """
            ).fetchone()
            self.assertIsNotNone(linked_answer)
            resource_uuid = str(
                connection.execute(
                    "SELECT uuid FROM resources WHERE title = '原始资料'"
                ).fetchone()[0]
            )
        restored_file = next((empty_root / "resources" / resource_uuid).glob("original.*"))
        self.assertEqual(restored_file.read_text(encoding="utf-8"), "Original resource content.")

    def test_corrupt_fixture_is_rejected_without_touching_database(self) -> None:
        imported = self.client.post(
            "/api/backup/import",
            files={
                "file": (
                    "corrupt-backup.zip",
                    (FIXTURES / "corrupt-backup.zip").read_bytes(),
                    "application/zip",
                )
            },
        )
        self.assertEqual(imported.status_code, 409, imported.text)
        self.assertEqual(imported.json()["code"], "backup_corrupt")
        backup_root = self.database_path.parent / "backups" / "catalog"
        backup_root.mkdir(parents=True, exist_ok=True)
        corrupt = backup_root / "corrupt-backup.zip"
        shutil.copyfile(FIXTURES / "corrupt-backup.zip", corrupt)
        checksum = hashlib.sha256(corrupt.read_bytes()).hexdigest()
        from backend.app.database import connect

        with connect() as connection:
            backup_id = int(
                connection.execute(
                    """
                    INSERT INTO backup_catalog(
                        path, kind, checksum, size_bytes, app_version,
                        schema_version, status, created_at
                    ) VALUES (
                        'backups/catalog/corrupt-backup.zip', 'manual', ?, ?,
                        '0.1.0', 5, 'ok', '2026-08-17T00:00:00+00:00'
                    )
                    """,
                    (checksum, corrupt.stat().st_size),
                ).lastrowid
            )
            connection.commit()
            before = connection.execute("SELECT COUNT(*) FROM resources").fetchone()[0]

        response = self.client.post(
            f"/api/backup/{backup_id}/restore", json={"dry_run": False}
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "backup_corrupt")
        with connect() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM resources").fetchone()[0],
                before,
            )

    def test_restore_failure_keeps_current_database_and_assets(self) -> None:
        from backend.app.database import connect
        from backend.app.services.backups import restore_backup

        with connect() as connection:
            from backend.app.services.backups import create_backup

            backup = create_backup(connection)
        current_file = self._stored_file(self.first)
        current_file.write_text("current survives", encoding="utf-8")
        self.client.put(
            f"/api/resources/{self.first['id']}", json={"title": "Current survives"}
        )
        with connect() as connection, patch(
            "backend.app.services.backups._restore_database",
            side_effect=RuntimeError("forced restore interruption"),
        ):
            with self.assertRaises(RuntimeError):
                restore_backup(connection, backup["id"], dry_run=False)
        self.assertEqual(current_file.read_text(encoding="utf-8"), "current survives")
        self.assertEqual(
            self.client.get(f"/api/resources/{self.first['id']}").json()["title"],
            "Current survives",
        )

    def test_failure_after_database_copy_rolls_back_database_and_assets(self) -> None:
        from backend.app.database import connect
        from backend.app.services.backups import create_backup, restore_backup

        with connect() as connection:
            backup = create_backup(connection)
        current_file = self._stored_file(self.first)
        current_file.write_text("post-copy current survives", encoding="utf-8")
        self.client.put(
            f"/api/resources/{self.first['id']}",
            json={"title": "Post-copy current survives"},
        )
        with connect() as connection, patch(
            "backend.app.services.backups._commit_restore_metadata",
            side_effect=RuntimeError("forced post-copy failure"),
        ):
            with self.assertRaises(RuntimeError):
                restore_backup(connection, backup["id"], dry_run=False)
        self.assertEqual(
            current_file.read_text(encoding="utf-8"),
            "post-copy current survives",
        )
        self.assertEqual(
            self.client.get(f"/api/resources/{self.first['id']}").json()["title"],
            "Post-copy current survives",
        )
        self.assertFalse((self.database_path.parent / "backups" / "restore-journal.json").exists())

    def test_finalize_failure_keeps_committed_restore_and_startup_cleans_up(self) -> None:
        from backend.app.database import connect, initialize_database
        from backend.app.services.backups import create_backup, restore_backup

        original_file = self._stored_file(self.first)
        original_bytes = original_file.read_bytes()
        with connect() as connection:
            backup = create_backup(connection)
        original_file.write_text("state to replace", encoding="utf-8")
        self.client.put(
            f"/api/resources/{self.first['id']}", json={"title": "State to replace"}
        )
        with connect() as connection, patch(
            "backend.app.services.backups._finalize_asset_directories",
            side_effect=OSError("forced cleanup failure"),
        ):
            restored = restore_backup(connection, backup["id"], dry_run=False)
        self.assertTrue(restored["restored"])
        self.assertTrue(restored["cleanup_pending"])
        self.assertEqual(original_file.read_bytes(), original_bytes)
        self.assertEqual(
            self.client.get(f"/api/resources/{self.first['id']}").json()["title"],
            "原始资料",
        )
        journal = self.database_path.parent / "backups" / "restore-journal.json"
        self.assertTrue(journal.exists())

        initialize_database()
        self.assertFalse(journal.exists())
        self.assertEqual(original_file.read_bytes(), original_bytes)
        self.assertEqual(
            self.client.get(f"/api/resources/{self.first['id']}").json()["title"],
            "原始资料",
        )

    def test_forced_process_kill_is_rolled_back_on_next_startup(self) -> None:
        from backend.app.database import connect, initialize_database
        from backend.app.services.backups import create_backup

        with connect() as connection:
            backup = create_backup(connection)
        current_file = self._stored_file(self.first)
        current_file.write_text("survives actual process kill", encoding="utf-8")
        self.client.put(
            f"/api/resources/{self.first['id']}",
            json={"title": "Survives actual process kill"},
        )
        signal = self.database_path.parent / "restore-reached-database.signal"
        child_code = r"""
import sys
import time
from pathlib import Path

import backend.app.database as database

database.DATABASE_PATH = Path(sys.argv[1])
from backend.app.services import backups

signal = Path(sys.argv[2])
def pause_before_database_copy(connection, snapshot_path):
    signal.write_text("ready", encoding="utf-8")
    while True:
        time.sleep(1)

backups._restore_database = pause_before_database_copy
with database.connect() as connection:
    backups.restore_backup(connection, int(sys.argv[3]), dry_run=False)
"""
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                child_code,
                str(self.database_path),
                str(signal),
                str(backup["id"]),
            ],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 15
        while not signal.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not signal.exists():
            stdout, stderr = process.communicate(timeout=5)
            self.fail(
                f"restore child did not reach interruption point; "
                f"exit={process.returncode}, stdout={stdout}, stderr={stderr}"
            )
        process.kill()
        process.wait(timeout=10)
        self.assertNotEqual(process.returncode, 0)
        journal = self.database_path.parent / "backups" / "restore-journal.json"
        self.assertTrue(journal.exists())
        self.assertGreaterEqual(
            len(list((self.database_path.parent / "backups" / "catalog").glob("*.zip"))),
            2,
        )

        initialize_database()
        self.assertFalse(journal.exists())
        self.assertEqual(
            current_file.read_text(encoding="utf-8"),
            "survives actual process kill",
        )
        self.assertEqual(
            self.client.get(f"/api/resources/{self.first['id']}").json()["title"],
            "Survives actual process kill",
        )
        with connect() as connection:
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
