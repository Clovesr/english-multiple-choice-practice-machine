from __future__ import annotations

import hashlib
import shutil
import sqlite3
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

    def test_corrupt_fixture_is_rejected_without_touching_database(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
