from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient


class TasksAndReportsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.database_path = Path(self.temp.name) / "tasks-reports.db"
        self.database_patch = patch(
            "backend.app.database.DATABASE_PATH", self.database_path
        )
        self.database_patch.start()
        from backend.app.database import connect, initialize_database
        from backend.app.main import app

        initialize_database()
        timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with connect() as connection:
            paper_id = int(
                connection.execute(
                    """
                    INSERT INTO papers(profile_id, year, title, status)
                    VALUES (1, 2026, '任务测试卷', 'published')
                    """
                ).lastrowid
            )
            unit_id = int(
                connection.execute(
                    """
                    INSERT INTO units(paper_id, unit_type, title, sequence)
                    VALUES (?, 'reading', '阅读', 1)
                    """,
                    (paper_id,),
                ).lastrowid
            )
            self.question_id = int(
                connection.execute(
                    """
                    INSERT INTO questions(unit_id, number, stem, answer, score, sequence)
                    VALUES (?, 1, 'Question', 'A', 2, 1)
                    """,
                    (unit_id,),
                ).lastrowid
            )
            connection.execute(
                """
                INSERT INTO wrong_stats(
                    question_id, attempt_count, wrong_count, recent_results,
                    consecutive_correct, last_wrong_at, last_attempt_at
                ) VALUES (?, 1, 1, '[false]', 0, ?, ?)
                """,
                (self.question_id, timestamp, timestamp),
            )
            connection.execute(
                """
                INSERT INTO review_items(
                    uuid, item_type, ref_id, due_at, scheduler_version,
                    created_at, updated_at
                ) VALUES (?, 'wrong_question', ?, ?, 'test-v1', ?, ?)
                """,
                (str(uuid4()), self.question_id, timestamp, timestamp, timestamp),
            )
            resource_id = int(
                connection.execute(
                    """
                    INSERT INTO resources(
                        uuid, title, type, format, checksum, imported_at,
                        created_at, updated_at
                    ) VALUES (?, 'Daily Article', 'article', 'txt', ?, ?, ?, ?)
                    """,
                    (str(uuid4()), str(uuid4()), timestamp, timestamp, timestamp),
                ).lastrowid
            )
            connection.execute(
                """
                INSERT INTO resource_progress(
                    resource_id, scroll_ratio, opened_count,
                    last_opened_at, updated_at
                ) VALUES (?, 0.4, 1, ?, ?)
                """,
                (resource_id, timestamp, timestamp),
            )
            connection.commit()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.database_patch.stop()
        self.temp.cleanup()

    def test_today_tasks_are_idempotent_carried_and_completable(self) -> None:
        from backend.app.database import connect
        from backend.app.services.tasks import generate_daily_tasks

        today = datetime.now().astimezone().date()
        with connect() as connection:
            yesterday = generate_daily_tasks(
                connection, for_date=today - timedelta(days=1)
            )
            self.assertEqual(len(yesterday["items"]), 3)

        first = self.client.get("/api/tasks/today")
        self.assertEqual(first.status_code, 200, first.text)
        first_payload = first.json()
        self.assertEqual(first_payload["date"], today.isoformat())
        self.assertEqual(
            {item["task_type"] for item in first_payload["items"]},
            {"review_due", "continue_reading", "redo_wrong"},
        )
        self.assertTrue(
            all(item["status"] == "carried" for item in first_payload["items"])
        )
        repeated = self.client.post("/api/tasks/generate")
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(
            [item["id"] for item in repeated.json()["items"]],
            [item["id"] for item in first_payload["items"]],
        )

        task_id = first_payload["items"][0]["id"]
        completed = self.client.put(f"/api/tasks/{task_id}/complete")
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(completed.json()["status"], "done")
        after = self.client.get("/api/tasks/today").json()
        self.assertEqual(
            next(item for item in after["items"] if item["id"] == task_id)["status"],
            "done",
        )

        with connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM daily_tasks WHERE task_date = ?",
                    (today.isoformat(),),
                ).fetchone()[0],
                3,
            )

    def test_daily_report_recomputes_from_learning_events(self) -> None:
        from backend.app.database import connect
        from backend.app.services.learning import record_learning_event

        timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with connect() as connection:
            record_learning_event(
                connection,
                verb="read",
                object_type="resource",
                object_id=1,
                duration_ms=30_000,
                occurred_at=timestamp,
            )
            record_learning_event(
                connection,
                verb="answer",
                object_type="question",
                object_id=self.question_id,
                result={"correct": True},
                occurred_at=timestamp,
            )
            wrong_event_id = record_learning_event(
                connection,
                verb="answer",
                object_type="question",
                object_id=self.question_id,
                result={"correct": False},
                occurred_at=timestamp,
            )
            record_learning_event(
                connection,
                verb="review",
                object_type="vocabulary_card",
                object_id=1,
                result={"correct": True, "new_word": True, "rating": 3},
                duration_ms=5_000,
                occurred_at=timestamp,
            )
            record_learning_event(
                connection,
                verb="review",
                object_type="vocabulary_card",
                object_id=2,
                result={
                    "correct": False,
                    "new_word": True,
                    "rating": 1,
                    "schedule_applied": False,
                },
                duration_ms=2_000,
                occurred_at=timestamp,
            )
            connection.commit()

        date_value = datetime.now().astimezone().date().isoformat()
        response = self.client.get(
            "/api/reports/daily", params={"date": date_value}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "study_ms": 37_000,
                "reviews_done": 1,
                "review_accuracy": 1.0,
                "new_words": 1,
                "questions_answered": 2,
                "question_accuracy": 0.5,
            },
        )

        with connect() as connection:
            connection.execute(
                "DELETE FROM learning_events WHERE id = ?", (wrong_event_id,)
            )
            connection.commit()
        recomputed = self.client.get(
            "/api/reports/daily", params={"date": date_value}
        )
        self.assertEqual(recomputed.status_code, 200, recomputed.text)
        self.assertEqual(recomputed.json()["questions_answered"], 1)
        self.assertEqual(recomputed.json()["question_accuracy"], 1.0)
        with connect() as connection:
            cached = connection.execute(
                "SELECT data FROM metrics_daily WHERE metric_date = ?", (date_value,)
            ).fetchone()[0]
            self.assertEqual(json.loads(cached), recomputed.json())


if __name__ == "__main__":
    unittest.main()
