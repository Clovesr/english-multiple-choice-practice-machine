from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient


class ReviewCenterApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.database_path = Path(self.temp.name) / "review-test.db"
        self.database_patch = patch(
            "backend.app.database.DATABASE_PATH", self.database_path
        )
        self.database_patch.start()
        from backend.app.database import connect, initialize_database
        from backend.app.main import app

        initialize_database()
        with connect() as connection:
            paper_id = int(
                connection.execute(
                    """
                    INSERT INTO papers(profile_id, year, title, status)
                    VALUES (1, 2026, '复习中心测试卷', 'published')
                    """
                ).lastrowid
            )
            self.unit_id = int(
                connection.execute(
                    """
                    INSERT INTO units(paper_id, unit_type, title, sequence, passage)
                    VALUES (?, 'reading', '阅读一', 1, 'A short passage.')
                    """,
                    (paper_id,),
                ).lastrowid
            )
            self.question_id = int(
                connection.execute(
                    """
                    INSERT INTO questions(
                        unit_id, number, stem, question_type, answer, score, sequence
                    ) VALUES (?, 1, 'What is correct?', 'single_choice', 'A', 2, 1)
                    """,
                    (self.unit_id,),
                ).lastrowid
            )
            connection.executemany(
                """
                INSERT INTO options(
                    question_id, stable_key, original_label, content, sequence
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (self.question_id, "A", "A", "Correct", 1),
                    (self.question_id, "B", "B", "Wrong", 2),
                ),
            )
            connection.commit()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.database_patch.stop()
        self.temp.cleanup()

    def _submit_wrong(self, *, timestamp: str) -> None:
        created = self.client.post(
            "/api/practice/sessions",
            json={
                "mode": "unit",
                "unit_ids": [self.unit_id],
                "shuffle_options": False,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        session = created.json()
        saved = self.client.put(
            f"/api/practice/sessions/{session['id']}/answers/{self.question_id}",
            json={"answer": "B", "option_order": ["A", "B"]},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        with patch("backend.app.services.review.utc_now", return_value=timestamp):
            submitted = self.client.post(
                f"/api/practice/sessions/{session['id']}/submit"
            )
        self.assertEqual(submitted.status_code, 200, submitted.text)

    def test_wrong_answer_creates_one_review_item_and_grading_is_idempotent(self) -> None:
        self._submit_wrong(timestamp="2026-08-17T00:00:00+00:00")
        queue = self.client.get("/api/review/queue")
        self.assertEqual(queue.status_code, 200, queue.text)
        payload = queue.json()
        self.assertEqual(payload["counts"]["due"], 1)
        self.assertEqual(len(payload["items"]), 1)
        card = payload["items"][0]
        self.assertEqual(card["item_type"], "wrong_question")
        self.assertEqual(card["payload"]["question_id"], self.question_id)
        self.assertEqual(card["payload"]["original_wrong_answer"], "B")
        self.assertEqual(card["payload"]["correct_answer"], "A")
        self.assertEqual(
            [option["stable_key"] for option in card["payload"]["options"]],
            ["A", "B"],
        )
        wrong_rows = self.client.get("/api/wrong")
        self.assertEqual(wrong_rows.status_code, 200, wrong_rows.text)
        self.assertEqual(len(wrong_rows.json()), 1)
        self.assertEqual(wrong_rows.json()[0]["question_id"], self.question_id)
        self.assertEqual(wrong_rows.json()[0]["wrong_count"], 1)

        self._submit_wrong(timestamp="2026-08-18T00:00:00+00:00")
        from backend.app.database import connect

        with connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM review_items
                WHERE item_type = 'wrong_question' AND ref_id = ?
                """,
                (self.question_id,),
            ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["lapses"], 1)
            self.assertEqual(rows[0]["due_at"], "2026-08-18T00:00:00+00:00")
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM learning_events WHERE verb = 'answer'"
                ).fetchone()[0],
                2,
            )
        repeated_wrong_rows = self.client.get("/api/wrong").json()
        self.assertEqual(len(repeated_wrong_rows), 1)
        self.assertEqual(repeated_wrong_rows[0]["question_id"], self.question_id)
        self.assertEqual(repeated_wrong_rows[0]["wrong_count"], 2)

        attempt_id = str(uuid4())
        body = {"attempt_id": attempt_id, "rating": 3, "duration_ms": 4200}
        graded = self.client.post(
            f"/api/review/items/{card['review_item_id']}/grade", json=body
        )
        self.assertEqual(graded.status_code, 200, graded.text)
        repeated = self.client.post(
            f"/api/review/items/{card['review_item_id']}/grade", json=body
        )
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json(), graded.json())
        with connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM review_logs WHERE attempt_id = ?",
                    (attempt_id,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM learning_events
                    WHERE verb = 'review' AND object_type = 'wrong_question'
                    """
                ).fetchone()[0],
                1,
            )

        stats = self.client.get("/api/review/stats")
        self.assertEqual(stats.status_code, 200, stats.text)
        self.assertEqual(stats.json()["retention_30d"], 1.0)
        vocabulary_overview = self.client.get("/api/study/overview")
        self.assertEqual(vocabulary_overview.status_code, 200, vocabulary_overview.text)
        self.assertEqual(vocabulary_overview.json()["today"]["new_done"], 0)
        self.assertEqual(vocabulary_overview.json()["today"]["reviews_done"], 0)
        self.assertIsNone(vocabulary_overview.json()["retention_7d"])
        self.assertEqual(vocabulary_overview.json()["streak_days"], 0)

    def test_review_item_suspend_and_type_validation(self) -> None:
        self._submit_wrong(timestamp="2026-08-17T00:00:00+00:00")
        item_id = self.client.get("/api/review/queue").json()["items"][0][
            "review_item_id"
        ]
        suspended = self.client.post(f"/api/review/items/{item_id}/suspend")
        self.assertEqual(suspended.status_code, 200, suspended.text)
        self.assertEqual(self.client.get("/api/review/queue").json()["items"], [])
        restored = self.client.post(f"/api/review/items/{item_id}/unsuspend")
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(len(self.client.get("/api/review/queue").json()["items"]), 1)
        invalid = self.client.get(
            "/api/review/queue", params={"types": "vocabulary_card"}
        )
        self.assertEqual(invalid.status_code, 400, invalid.text)
        self.assertEqual(invalid.json()["code"], "invalid_review_type")


if __name__ == "__main__":
    unittest.main()
