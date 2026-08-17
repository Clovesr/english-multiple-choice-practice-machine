from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient


class SkillsAndMasteryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.database_path = Path(self.temp.name) / "skills-test.db"
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
                    VALUES (1, 2026, '知识点测试卷', 'published')
                    """
                ).lastrowid
            )
            self.unit_id = int(
                connection.execute(
                    """
                    INSERT INTO units(paper_id, unit_type, title, sequence, passage)
                    VALUES (?, 'reading', '阅读', 1, 'Passage')
                    """,
                    (paper_id,),
                ).lastrowid
            )
            self.question_ids = []
            for number in range(1, 6):
                question_id = int(
                    connection.execute(
                        """
                        INSERT INTO questions(
                            unit_id, number, stem, question_type, answer, score, sequence
                        ) VALUES (?, ?, ?, 'single_choice', 'A', 1, ?)
                        """,
                        (self.unit_id, number, f"Question {number}", number),
                    ).lastrowid
                )
                self.question_ids.append(question_id)
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
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.database_patch.stop()
        self.temp.cleanup()

    def test_five_linked_questions_update_simple_mastery(self) -> None:
        created = self.client.post(
            "/api/skills",
            json={
                "name": "主旨理解",
                "skill_type": "reading",
                "stage": "postgraduate",
                "difficulty": 3,
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        skill_id = created.json()["id"]
        for question_id in self.question_ids:
            linked = self.client.post(
                f"/api/questions/{question_id}/skills",
                json={"skill_ids": [skill_id, skill_id]},
            )
            self.assertEqual(linked.status_code, 200, linked.text)
            self.assertEqual([item["id"] for item in linked.json()["skills"]], [skill_id])

        session = self.client.post(
            "/api/practice/sessions",
            json={"mode": "unit", "unit_ids": [self.unit_id], "shuffle_options": False},
        ).json()
        for index, question_id in enumerate(self.question_ids):
            answer = "A" if index < 3 else "B"
            saved = self.client.put(
                f"/api/practice/sessions/{session['id']}/answers/{question_id}",
                json={"answer": answer, "option_order": ["A", "B"]},
            )
            self.assertEqual(saved.status_code, 200, saved.text)
        submitted = self.client.post(f"/api/practice/sessions/{session['id']}/submit")
        self.assertEqual(submitted.status_code, 200, submitted.text)

        items = self.client.get(
            "/api/skills", params={"type": "reading", "stage": "postgraduate"}
        ).json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["evidence_count"], 5)
        self.assertAlmostEqual(items[0]["mastery_score"], 0.6)

        # Re-submitting the same already-graded session is idempotent evidence-wise.
        repeated = self.client.post(f"/api/practice/sessions/{session['id']}/submit")
        self.assertEqual(repeated.status_code, 200, repeated.text)
        item = self.client.get("/api/skills").json()["items"][0]
        self.assertEqual(item["evidence_count"], 5)

    def test_deleting_course_does_not_delete_skills_or_mastery(self) -> None:
        skill = self.client.post(
            "/api/skills",
            json={"name": "长难句", "skill_type": "grammar"},
        ).json()
        self.client.post(
            f"/api/questions/{self.question_ids[0]}/skills",
            json={"skill_ids": [skill["id"]]},
        )
        from backend.app.database import connect
        from backend.app.services.skills import update_mastery_for_question
        from backend.app.services.learning import utc_now

        with connect() as connection:
            now = utc_now()
            course_id = int(
                connection.execute(
                    """
                    INSERT INTO courses(uuid, title, created_at, updated_at)
                    VALUES ('course-test', '课程', ?, ?)
                    """,
                    (now, now),
                ).lastrowid
            )
            update_mastery_for_question(connection, self.question_ids[0], correct=True)
            connection.execute("DELETE FROM courses WHERE id = ?", (course_id,))
            connection.commit()
            self.assertIsNotNone(
                connection.execute("SELECT id FROM skills WHERE id = ?", (skill["id"],)).fetchone()
            )
            state = connection.execute(
                "SELECT * FROM mastery_states WHERE skill_id = ?", (skill["id"],)
            ).fetchone()
            self.assertEqual(state["evidence_count"], 1)
            self.assertEqual(state["mastery_score"], 1.0)


if __name__ == "__main__":
    unittest.main()
