from __future__ import annotations

import json
import sqlite3
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.database import connect


class StudyApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.database_path = Path(self.temp.name) / "study.db"
        self.database_patch = patch(
            "backend.app.database.DATABASE_PATH", self.database_path
        )
        self.database_patch.start()
        from backend.app.database import initialize_database
        from backend.app.main import app

        initialize_database()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.database_patch.stop()
        self.temp.cleanup()

    def _collect(self, term: str = "ability") -> dict:
        from backend.app.database import connect
        from backend.app.services.vocabulary_cards import utc_now

        timestamp = utc_now()
        connection = connect()
        try:
            resource_id = int(
                connection.execute(
                    """
                    INSERT INTO resources(
                        uuid, title, type, format, checksum, segment_count,
                        imported_at, created_at, updated_at
                    ) VALUES (?, 'Study Article', 'article', 'txt', ?, 1, ?, ?, ?)
                    """,
                    (str(uuid4()), str(uuid4()), timestamp, timestamp, timestamp),
                ).lastrowid
            )
            sentence = f"Her {term} improves with deliberate practice."
            segment_id = int(
                connection.execute(
                    """
                    INSERT INTO resource_segments(resource_id, sequence, content)
                    VALUES (?, 1, ?)
                    """,
                    (resource_id, sentence),
                ).lastrowid
            )
            connection.commit()
        finally:
            connection.close()
        response = self.client.post(
            "/api/vocabulary/from-selection",
            json={
                "term": term,
                "context_sentence": sentence,
                "resource_id": resource_id,
                "segment_id": segment_id,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _create_wordbook(
        self,
        *,
        size: int,
        name: str = "Lazy Pool",
        term_prefix: str = "pool",
    ) -> int:
        from backend.app.services.vocabulary_cards import utc_now

        timestamp = utc_now()
        connection = connect()
        try:
            wordbook_id = int(
                connection.execute(
                    """
                    INSERT INTO wordbooks(
                        uuid, name, kind, source_name, source_version,
                        license, checksum, status, created_at, updated_at
                    ) VALUES (?, ?, 'imported', 'test', '1', '', ?, 'active', ?, ?)
                    """,
                    (str(uuid4()), name, str(uuid4()), timestamp, timestamp),
                ).lastrowid
            )
            for sequence in range(1, size + 1):
                term = f"{term_prefix}-{sequence:03d}"
                entry_id = int(
                    connection.execute(
                        """
                        INSERT INTO vocabulary_entries(
                            uuid, term, normalized_term, lemma, common_meaning,
                            translation_status, enrichment_status, encounter_count,
                            study_status, created_at, updated_at, last_seen_at
                        ) VALUES (?, ?, ?, ?, ?, 'ready', 'ready', 0,
                                  'learning', ?, ?, ?)
                        """,
                        (
                            str(uuid4()),
                            term,
                            term,
                            term,
                            f"释义 {sequence}",
                            timestamp,
                            timestamp,
                            timestamp,
                        ),
                    ).lastrowid
                )
                connection.execute(
                    """
                    INSERT INTO wordbook_entries(
                        wordbook_id, entry_id, sequence, frequency_rank, added_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (wordbook_id, entry_id, sequence, sequence, timestamp),
                )
            connection.commit()
            return wordbook_id
        finally:
            connection.close()

    def test_plan_activation_seeds_bounded_pool_and_session_refills_it(self) -> None:
        wordbook_id = self._create_wordbook(size=80)
        with patch(
            "backend.app.services.wordbooks.install_bundled_wordbooks",
            return_value={"available": True, "installed": 0, "entries": 0},
        ):
            activated = self.client.post(
                f"/api/wordbooks/{wordbook_id}/plan",
                json={"daily_new": 2, "new_order": "sequence"},
            )
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertEqual(activated.json()["generated_cards"], 8)

        connection = connect()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(DISTINCT entry_id) FROM vocabulary_cards"
                ).fetchone()[0],
                2,
            )
            connection.execute(
                """
                UPDATE review_items
                SET state = 'review', due_at = ?, reps = 1
                WHERE item_type = 'vocabulary'
                """,
                ((datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),),
            )
            connection.commit()
        finally:
            connection.close()

        session = self.client.get("/api/study/session", params={"limit": 2})
        self.assertEqual(session.status_code, 200, session.text)
        self.assertEqual(len(session.json()["cards"]), 2)
        self.assertEqual(session.json()["cards"][0]["entry"]["lemma"], "pool-003")
        connection = connect()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(DISTINCT entry_id) FROM vocabulary_cards"
                ).fetchone()[0],
                4,
            )
        finally:
            connection.close()

    def test_import_seeds_bounded_sufficient_cards_and_zero_plan_queues_none(self) -> None:
        imported = self.client.post(
            "/api/wordbooks/import",
            json={
                "name": "Deferred Cards",
                "terms": [
                    {
                        "term": (
                            "deferredword"
                            f"{chr(97 + index // 26)}{chr(97 + index % 26)}"
                        ),
                        "meaning": f"延迟 {index}",
                    }
                    for index in range(55)
                ],
            },
        )
        self.assertEqual(imported.status_code, 201, imported.text)
        wordbook_id = imported.json()["wordbook"]["id"]
        connection = connect()
        try:
            cards = connection.execute(
                """
                SELECT vc.card_type, vc.prompt_data, vc.answer_data
                FROM vocabulary_cards AS vc
                JOIN wordbook_entries AS we ON we.entry_id = vc.entry_id
                WHERE we.wordbook_id = ?
                """,
                (wordbook_id,),
            ).fetchall()
            self.assertTrue(cards)
            self.assertTrue(
                all(row["prompt_data"] and row["answer_data"] for row in cards)
            )
            self.assertEqual(
                {row["card_type"] for row in cards},
                {"forward", "reverse", "listening", "spelling"},
            )
            reverse_answers = [
                json.loads(row["answer_data"])
                for row in cards
                if row["card_type"] == "reverse"
            ]
            self.assertTrue(reverse_answers)
            self.assertTrue(
                all(len(answer["distractors"]) == 3 for answer in reverse_answers)
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(DISTINCT vc.entry_id)
                    FROM vocabulary_cards AS vc
                    JOIN wordbook_entries AS we ON we.entry_id = vc.entry_id
                    WHERE we.wordbook_id = ?
                    """,
                    (wordbook_id,),
                ).fetchone()[0],
                50,
            )
        finally:
            connection.close()
        with patch(
            "backend.app.services.wordbooks.install_bundled_wordbooks",
            return_value={"available": True, "installed": 0, "entries": 0},
        ):
            activated = self.client.post(
                f"/api/wordbooks/{wordbook_id}/plan",
                json={"daily_new": 0, "new_order": "sequence"},
            )
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertEqual(activated.json()["generated_cards"], 0)
        self.assertEqual(self.client.get("/api/study/session").json()["cards"], [])

    def test_session_refresh_grade_idempotency_objective_recheck_and_suspend(self) -> None:
        collected = self._collect()
        entry_id = int(collected["entry"]["id"])
        with connect() as connection:
            connection.execute(
                """
                UPDATE vocabulary_entries
                SET memory_hint = 'able + ity，能力', note = '个人笔记'
                WHERE id = ?
                """,
                (entry_id,),
            )
            connection.commit()
        settings = self.client.put(
            "/api/study/settings",
            json={"daily_new": 10, "daily_review_max": 20},
        )
        self.assertEqual(settings.status_code, 200, settings.text)

        first = self.client.get("/api/study/session", params={"limit": 10})
        self.assertEqual(first.status_code, 200, first.text)
        session = first.json()
        self.assertEqual(len(session["cards"]), 1)
        self.assertTrue(all(card["entry_id"] == entry_id for card in session["cards"]))
        self.assertTrue(
            all(
                card["entry"]["memory_hint"] == "able + ity，能力"
                and card["entry"]["note"] == "个人笔记"
                for card in session["cards"]
            )
        )
        refreshed = self.client.get("/api/study/session", params={"limit": 3}).json()
        self.assertEqual(refreshed["session_id"], session["session_id"])
        self.assertEqual(
            [card["card_id"] for card in refreshed["cards"]],
            [card["card_id"] for card in session["cards"]],
        )

        forward = session["cards"][0]
        self.assertEqual(forward["card_type"], "forward")
        attempt_id = str(uuid4())
        grade = self.client.post(
            f"/api/study/cards/{forward['card_id']}/grade",
            json={
                "attempt_id": attempt_id,
                "rating": 1,
                "duration_ms": 1250,
            },
        )
        self.assertEqual(grade.status_code, 200, grade.text)
        result = grade.json()
        self.assertIsNone(result["auto_correct"])
        self.assertEqual(result["attempt_id"], attempt_id)
        due = datetime.fromisoformat(result["next_due_at"])
        self.assertIsNotNone(due.tzinfo)
        self.assertLess(due - datetime.now(timezone.utc), timedelta(hours=1))

        retry = self.client.post(
            f"/api/study/cards/{forward['card_id']}/grade",
            json={
                "attempt_id": attempt_id,
                "rating": 4,
                "duration_ms": 9999,
            },
        )
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json()["review_log_id"], result["review_log_id"])
        self.assertEqual(retry.json()["final_rating"], 1)

        with connect() as connection:
            spelling_id = int(
                connection.execute(
                    """
                    SELECT id FROM vocabulary_cards
                    WHERE entry_id = ? AND card_type = 'spelling'
                    """,
                    (entry_id,),
                ).fetchone()[0]
            )
        conflict = self.client.post(
            f"/api/study/cards/{spelling_id}/grade",
            json={
                "attempt_id": attempt_id,
                "rating": 3,
                "answer_given": "ability",
                "duration_ms": 200,
            },
        )
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["code"], "study_card_conflict")
        objective = self.client.post(
            f"/api/study/cards/{spelling_id}/grade",
            json={
                "attempt_id": str(uuid4()),
                "rating": 4,
                "answer_given": "wrong answer",
                "duration_ms": 800,
            },
        )
        self.assertEqual(objective.status_code, 200, objective.text)
        self.assertFalse(objective.json()["auto_correct"])
        self.assertEqual(objective.json()["final_rating"], 4)

        with connect() as connection:
            listening_id = int(
                connection.execute(
                    """
                    SELECT id FROM vocabulary_cards
                    WHERE entry_id = ? AND card_type = 'listening'
                    """,
                    (entry_id,),
                ).fetchone()[0]
            )
        suspended = self.client.post(
            f"/api/study/cards/{listening_id}/suspend"
        )
        self.assertEqual(suspended.status_code, 200, suspended.text)
        self.assertTrue(suspended.json()["card_type_suspended"])
        blocked_grade = self.client.post(
            f"/api/study/cards/{listening_id}/grade",
            json={
                "attempt_id": str(uuid4()),
                "rating": 3,
                "answer_given": "ability",
                "duration_ms": 200,
            },
        )
        self.assertEqual(blocked_grade.status_code, 409, blocked_grade.text)

        connection = connect()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM review_logs WHERE attempt_id = ?",
                    (attempt_id,),
                ).fetchone()[0],
                1,
            )
            log = connection.execute(
                "SELECT * FROM review_logs WHERE card_id = ?",
                (spelling_id,),
            ).fetchone()
            self.assertEqual(log["auto_correct"], 0)
            self.assertEqual(log["auto_rating"], 1)
            self.assertEqual(log["final_rating"], 4)
        finally:
            connection.close()

        overview = self.client.get("/api/study/overview")
        self.assertEqual(overview.status_code, 200, overview.text)
        self.assertEqual(overview.json()["today"]["new_done"], 1)
        self.assertEqual(overview.json()["today"]["reviews_done"], 1)
        self.assertEqual(len(overview.json()["forecast_7d"]), 7)

    def test_unknown_api_path_is_json_404_not_spa_html(self) -> None:
        response = self.client.get("/api/study/not-a-real-endpoint")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["content-type"].split(";", 1)[0], "application/json")
        self.assertEqual(response.json()["code"], "api_not_found")
        self.assertEqual(response.json()["details"]["path"], "/api/study/not-a-real-endpoint")

        api_root = self.client.get("/api")
        self.assertEqual(api_root.status_code, 404)
        self.assertEqual(api_root.json()["code"], "api_not_found")

    def test_settings_daily_cap_backlog_and_sprint_round_trip(self) -> None:
        self._collect()
        updated = self.client.put(
            "/api/study/settings",
            json={
                "daily_new": 2,
                "daily_review_max": 5,
                "enabled_card_types": ["spelling"],
                "leech_threshold": 3,
                "backlog_mode": "spread",
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["enabled_card_types"], ["spelling"])
        session = self.client.get("/api/study/session", params={"limit": 20}).json()
        session_id = session["session_id"]
        self.assertEqual(len(session["cards"]), 1)
        self.assertEqual(session["cards"][0]["card_type"], "spelling")

        backlog = self.client.post(
            "/api/study/backlog/plan",
            json={"mode": "suspend_new"},
        )
        self.assertEqual(backlog.status_code, 200, backlog.text)
        self.assertTrue(backlog.json()["applied"])
        self.assertEqual(backlog.json()["settings"]["backlog_mode"], "suspend_new")
        self.assertNotEqual(
            self.client.get("/api/study/session").json()["session_id"],
            session_id,
        )

        imported = self.client.post(
            "/api/wordbooks/import",
            json={"name": "Sprint Words", "terms": ["ability", "abandon"]},
        )
        self.assertEqual(imported.status_code, 201, imported.text)
        wordbook_id = imported.json()["wordbook"]["id"]
        normal = self.client.post(
            f"/api/wordbooks/{wordbook_id}/plan",
            json={"daily_new": 0, "new_order": "sequence"},
        )
        self.assertEqual(normal.status_code, 200, normal.text)
        self.assertEqual(normal.json()["plan"]["daily_new"], 0)
        exam_date = (datetime.now(timezone.utc).date() + timedelta(days=10)).isoformat()
        sprint = self.client.post(
            "/api/study/sprint",
            json={"exam_date": exam_date, "wordbook_id": wordbook_id},
        )
        self.assertEqual(sprint.status_code, 200, sprint.text)
        self.assertTrue(sprint.json()["active"])
        self.assertIn("feasible", sprint.json()["preview"])
        self.assertTrue(self.client.get("/api/study/sprint").json()["active"])
        stopped = self.client.delete("/api/study/sprint")
        self.assertEqual(stopped.status_code, 200, stopped.text)
        self.assertFalse(stopped.json()["active"])
        self.assertTrue(stopped.json()["restored_plan"]["active"])

    def test_due_priority_easy_interval_daily_cap_and_leech_overview(self) -> None:
        collected = self._collect()
        entry_id = int(collected["entry"]["id"])
        settings = self.client.put(
            "/api/study/settings",
            json={"daily_new": 1, "daily_review_max": 1, "leech_threshold": 1},
        )
        self.assertEqual(settings.status_code, 200, settings.text)

        from backend.app.database import connect

        now = datetime.now(timezone.utc).replace(microsecond=0)
        connection = connect()
        try:
            due_card = connection.execute(
                """
                SELECT vc.id, ri.id AS review_item_id
                FROM vocabulary_cards AS vc
                JOIN review_items AS ri
                  ON ri.item_type = 'vocabulary' AND ri.ref_id = vc.entry_id
                WHERE vc.entry_id = ? AND vc.card_type = 'spelling'
                """,
                (entry_id,),
            ).fetchone()
            connection.execute(
                """
                UPDATE review_items
                SET state = 'review', step = 0, due_at = ?, last_review_at = ?,
                    stability = 5, difficulty = 5, reps = 2, lapses = 1
                WHERE id = ?
                """,
                (
                    (now - timedelta(days=2)).isoformat(),
                    (now - timedelta(days=10)).isoformat(),
                    due_card["review_item_id"],
                ),
            )
            connection.commit()
        finally:
            connection.close()

        session = self.client.get("/api/study/session", params={"limit": 10}).json()
        self.assertEqual(len(session["cards"]), 1)
        self.assertEqual(session["cards"][0]["card_id"], due_card["id"])
        self.assertEqual(session["cards"][0]["card_type"], "spelling")
        grade = self.client.post(
            f"/api/study/cards/{due_card['id']}/grade",
            json={
                "attempt_id": str(uuid4()),
                "rating": 4,
                "answer_given": " ability ",
                "duration_ms": 300,
            },
        )
        self.assertEqual(grade.status_code, 200, grade.text)
        self.assertTrue(grade.json()["auto_correct"])
        self.assertGreater(
            datetime.fromisoformat(grade.json()["next_due_at"]) - datetime.now(timezone.utc),
            timedelta(days=1),
        )
        overview = self.client.get("/api/study/overview").json()
        self.assertEqual(overview["today"]["reviews_done"], 1)
        self.assertEqual(overview["today"]["new_done"], 0)
        self.assertEqual(overview["leeches"], 1)

    def test_spelling_variant_entry_state_and_manual_card_pause_are_independent(self) -> None:
        collected = self._collect("colour")
        entry_id = int(collected["entry"]["id"])
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with connect() as connection:
            connection.execute(
                """
                UPDATE review_items
                SET state = 'review', stability = 5, difficulty = 5,
                    reps = 2, due_at = ?
                WHERE item_type = 'vocabulary' AND ref_id = ?
                """,
                (now, entry_id),
            )
            connection.commit()
        session = self.client.get("/api/study/session", params={"limit": 10}).json()
        spelling = session["cards"][0]
        self.assertEqual(spelling["card_type"], "spelling")
        self.assertTrue({"colour", "color"} <= set(spelling["answer"]["accept"]))
        grade = self.client.post(
            f"/api/study/cards/{spelling['card_id']}/grade",
            json={
                "attempt_id": str(uuid4()),
                "rating": 3,
                "answer_given": " COLOR ",
                "duration_ms": 200,
            },
        )
        self.assertEqual(grade.status_code, 200, grade.text)
        self.assertTrue(grade.json()["auto_correct"])

        with connect() as connection:
            cards = connection.execute(
                """
                SELECT id, card_type FROM vocabulary_cards
                WHERE entry_id = ? AND card_type IN ('listening', 'reverse')
                """,
                (entry_id,),
            ).fetchall()
            by_type = {str(row["card_type"]): int(row["id"]) for row in cards}
            connection.execute(
                """
                UPDATE review_items
                SET state = 'learning', stability = 0.5, reps = 1, due_at = ?
                WHERE item_type = 'vocabulary' AND ref_id = ?
                """,
                (now, entry_id),
            )
            connection.execute(
                "UPDATE study_sessions SET status = 'completed', completed_at = ? WHERE status = 'active'",
                (now,),
            )
            connection.commit()
        listening_id = by_type["listening"]
        self.client.post(f"/api/study/cards/{listening_id}/suspend")
        paused = self.client.get("/api/study/session").json()
        self.assertEqual(paused["cards"][0]["card_id"], by_type["reverse"])
        self.client.post(f"/api/study/cards/{listening_id}/unsuspend")
        with connect() as connection:
            connection.execute(
                "UPDATE study_sessions SET status = 'completed', completed_at = ? WHERE status = 'active'",
                (now,),
            )
            connection.commit()
        restored = self.client.get("/api/study/session").json()
        self.assertEqual(restored["cards"][0]["card_id"], listening_id)

        self.client.post(f"/api/study/cards/{listening_id}/suspend")
        ignored = self.client.put(
            f"/api/vocabulary/entries/{entry_id}/state",
            json={"study_status": "ignored"},
        )
        self.assertEqual(ignored.status_code, 200, ignored.text)
        self.assertEqual(self.client.get("/api/study/session").json()["cards"], [])
        learning = self.client.put(
            f"/api/vocabulary/entries/{entry_id}/state",
            json={"study_status": "learning"},
        )
        self.assertEqual(learning.status_code, 200, learning.text)
        resumed = self.client.get("/api/study/session").json()["cards"]
        self.assertTrue(resumed)
        self.assertNotIn(listening_id, [card["card_id"] for card in resumed])

        connection = connect()
        try:
            suspended = connection.execute(
                """
                SELECT manually_suspended
                FROM vocabulary_card_type_settings
                WHERE entry_id = ? AND card_type = 'listening'
                """,
                (entry_id,),
            ).fetchone()[0]
            self.assertEqual(suspended, 1)
        finally:
            connection.close()

    def test_backlog_spread_adds_capacity_and_focus_only_serves_overdue(self) -> None:
        collected = self._collect()
        overdue_entry_id = int(collected["entry"]["id"])
        due_today_entry_id = int(self._collect("colour")["entry"]["id"])
        local_now = datetime.now().astimezone()
        local_start = datetime.combine(
            local_now.date(),
            datetime.min.time(),
            tzinfo=local_now.tzinfo,
        )
        connection = connect()
        try:
            connection.execute(
                """
                UPDATE review_items
                SET state = 'review', due_at = ?, last_review_at = ?,
                    stability = 3, difficulty = 5, reps = 1
                WHERE item_type = 'vocabulary' AND ref_id = ?
                """,
                (
                    (local_start - timedelta(days=2)).astimezone(timezone.utc).isoformat(),
                    (local_start - timedelta(days=5)).astimezone(timezone.utc).isoformat(),
                    overdue_entry_id,
                ),
            )
            connection.execute(
                """
                UPDATE review_items
                SET state = 'review', due_at = ?, last_review_at = ?,
                    stability = 3, difficulty = 5, reps = 1
                WHERE item_type = 'vocabulary' AND ref_id = ?
                """,
                (
                    local_start.astimezone(timezone.utc).isoformat(),
                    (local_start - timedelta(days=3)).astimezone(timezone.utc).isoformat(),
                    due_today_entry_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        self.client.put(
            "/api/study/settings",
            json={
                "daily_new": 0,
                "daily_review_max": 0,
                "enabled_card_types": ["forward", "reverse"],
            },
        )
        spread = self.client.post(
            "/api/study/backlog/plan",
            json={"mode": "spread", "days": 2},
        )
        self.assertEqual(spread.status_code, 200, spread.text)
        self.assertEqual(spread.json()["daily_extra_reviews"], 1)
        spread_cards = self.client.get("/api/study/session", params={"limit": 10}).json()["cards"]
        self.assertEqual([card["entry_id"] for card in spread_cards], [overdue_entry_id])

        connection = connect()
        try:
            connection.execute(
                "UPDATE study_sessions SET status = 'completed', completed_at = ? WHERE status = 'active'",
                (datetime.now(timezone.utc).isoformat(),),
            )
            connection.commit()
        finally:
            connection.close()
        self.client.put("/api/study/settings", json={"daily_review_max": 10})
        focus = self.client.post(
            "/api/study/backlog/plan",
            json={"mode": "focus_overdue"},
        )
        self.assertEqual(focus.status_code, 200, focus.text)
        focus_cards = self.client.get("/api/study/session", params={"limit": 10}).json()["cards"]
        self.assertEqual([card["entry_id"] for card in focus_cards], [overdue_entry_id])

    def test_sprint_directly_generates_cards_for_an_unactivated_wordbook(self) -> None:
        from backend.app.services.dictionary import lookup_dictionary
        from backend.app.services.vocabulary_learning import upsert_dictionary_entry
        from backend.app.services.vocabulary_cards import utc_now

        connection = connect()
        try:
            entry, _ = upsert_dictionary_entry(
                connection,
                lookup_dictionary("ability")["entry"],
                source_kind="builtin_wordbook",
            )
            timestamp = utc_now()
            wordbook_id = int(
                connection.execute(
                    """
                    INSERT INTO wordbooks(
                        uuid, name, kind, source_tag, source_name, source_version,
                        license, checksum, status, created_at, updated_at
                    ) VALUES (?, 'Sprint Only', 'builtin', 'test', 'test', '1',
                              'MIT', 'test', 'active', ?, ?)
                    """,
                    (str(uuid4()), timestamp, timestamp),
                ).lastrowid
            )
            connection.execute(
                """
                INSERT INTO wordbook_entries(
                    wordbook_id, entry_id, sequence, frequency_rank, added_at
                ) VALUES (?, ?, 1, 1, ?)
                """,
                (wordbook_id, entry, timestamp),
            )
            connection.commit()
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM vocabulary_cards WHERE entry_id = ?",
                    (entry,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()
        exam_date = (datetime.now(timezone.utc).date() + timedelta(days=5)).isoformat()
        sprint = self.client.post(
            "/api/study/sprint",
            json={"exam_date": exam_date, "wordbook_id": wordbook_id},
        )
        self.assertEqual(sprint.status_code, 200, sprint.text)
        connection = connect()
        try:
            self.assertGreater(
                connection.execute(
                    "SELECT COUNT(*) FROM vocabulary_cards WHERE entry_id = ?",
                    (entry,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_sprint_exit_restores_previous_plan_from_another_wordbook(self) -> None:
        regular_id = self._create_wordbook(
            size=1,
            name="Regular Plan",
            term_prefix="regular",
        )
        sprint_id = self._create_wordbook(
            size=1,
            name="Sprint Plan",
            term_prefix="sprint",
        )
        with patch(
            "backend.app.services.wordbooks.install_bundled_wordbooks",
            return_value={"available": True, "installed": 0, "entries": 0},
        ):
            regular = self.client.post(
                f"/api/wordbooks/{regular_id}/plan",
                json={"daily_new": 1, "new_order": "sequence"},
            )
        self.assertEqual(regular.status_code, 200, regular.text)
        regular_plan_id = regular.json()["plan"]["id"]

        exam_date = (datetime.now(timezone.utc).date() + timedelta(days=5)).isoformat()
        sprint = self.client.post(
            "/api/study/sprint",
            json={"exam_date": exam_date, "wordbook_id": sprint_id},
        )
        self.assertEqual(sprint.status_code, 200, sprint.text)
        stopped = self.client.delete("/api/study/sprint")
        self.assertEqual(stopped.status_code, 200, stopped.text)
        self.assertEqual(stopped.json()["restored_plan"]["id"], regular_plan_id)
        self.assertEqual(stopped.json()["restored_plan"]["wordbook_id"], regular_id)

    def test_all_six_card_types_share_one_fsrs_grade_core(self) -> None:
        collected = self._collect()
        entry_id = int(collected["entry"]["id"])
        self._create_wordbook(size=3, term_prefix="distractor")
        from backend.app.database import connect
        from backend.app.services.vocabulary_cards import generate_cards_for_entry

        connection = connect()
        try:
            connection.execute(
                """
                INSERT INTO vocabulary_relations(
                    entry_id, relation_type, related_term, note, source
                ) VALUES (?, 'collocation', 'ability to', '常见搭配', 'user')
                """,
                (entry_id,),
            )
            generate_cards_for_entry(
                connection,
                entry_id,
                generation_source="test_explicit_relation",
            )
            connection.commit()
        finally:
            connection.close()

        def surface(state: str, reps: int, stability: float) -> dict:
            timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            with connect() as connection:
                connection.execute(
                    """
                    UPDATE review_items
                    SET state = ?, reps = ?, stability = ?, difficulty = 5, due_at = ?
                    WHERE item_type = 'vocabulary' AND ref_id = ?
                    """,
                    (state, reps, stability, timestamp, entry_id),
                )
                connection.execute(
                    "UPDATE study_sessions SET status = 'completed', completed_at = ? WHERE status = 'active'",
                    (timestamp,),
                )
                connection.commit()
            session = self.client.get("/api/study/session", params={"limit": 20}).json()
            self.assertEqual(len(session["cards"]), 1)
            return session["cards"][0]

        cards = {
            "forward": surface("new", 0, 0),
            "reverse": surface("learning", 2, 0.5),
            "listening": surface("learning", 1, 0.5),
            "spelling": surface("review", 2, 5),
            "cloze": surface("review", 3, 5),
            "collocation": surface("review", 5, 5),
        }
        self.assertEqual({card["card_type"] for card in cards.values()}, set(cards))
        self.assertEqual(len({card["review_item_id"] for card in cards.values()}), 1)
        reverse = cards["reverse"]
        self.assertEqual(len(reverse["answer"]["distractors"]), 3)
        self.assertEqual(len(set(reverse["answer"]["distractors"])), 3)

        answers = {
            "reverse": reverse["answer"]["distractors"][0],
            "listening": "ability",
            "spelling": "ability",
            "cloze": "ability",
            "collocation": "ability to",
        }
        for card_type, card in cards.items():
            body = {
                "attempt_id": str(uuid4()),
                "rating": 3,
                "duration_ms": 100,
            }
            if card_type in answers:
                body["answer_given"] = answers[card_type]
            response = self.client.post(
                f"/api/study/cards/{card['card_id']}/grade",
                json=body,
            )
            self.assertEqual(response.status_code, 200, response.text)
            if card_type == "forward":
                self.assertIsNone(response.json()["auto_correct"])
            elif card_type == "reverse":
                self.assertFalse(response.json()["auto_correct"])
            else:
                self.assertTrue(response.json()["auto_correct"])

        connection = connect()
        try:
            rows = connection.execute(
                """
                SELECT DISTINCT vc.card_type, rl.review_item_id
                FROM review_logs AS rl
                JOIN vocabulary_cards AS vc ON vc.id = rl.card_id
                WHERE vc.entry_id = ?
                """,
                (entry_id,),
            ).fetchall()
            self.assertEqual({row["card_type"] for row in rows}, set(cards))
            self.assertEqual(len({row["review_item_id"] for row in rows}), 1)
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM review_items
                    WHERE item_type = 'vocabulary' AND ref_id = ? AND archived_at IS NULL
                    """,
                    (entry_id,),
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()


class LegacyVocabularyReviewCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.database_path = Path(self.temp.name) / "legacy.db"
        from backend.app.database import SCHEMA

        legacy = sqlite3.connect(self.database_path)
        try:
            legacy.executescript(SCHEMA)
            legacy.execute(
                """
                INSERT INTO vocabulary_entries(
                    term, normalized_term, lemma, common_meaning,
                    translation_status, next_review_at, last_reviewed_at
                ) VALUES (
                    'legacy', 'legacy', 'legacy', '遗留词', 'ready',
                    '2026-08-16T20:00:00', '2026-08-15T20:00:00'
                )
                """
            )
            legacy.commit()
        finally:
            legacy.close()
        self.database_patch = patch(
            "backend.app.database.DATABASE_PATH", self.database_path
        )
        self.database_patch.start()
        from backend.app.database import initialize_database
        from backend.app.main import app

        initialize_database()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.database_patch.stop()
        self.temp.cleanup()

    def test_old_review_endpoint_advances_forward_card_with_fsrs_and_utc_log(self) -> None:
        from backend.app.database import connect

        connection = connect()
        try:
            entry_id = int(
                connection.execute(
                    "SELECT id FROM vocabulary_entries WHERE normalized_term = 'legacy'"
                ).fetchone()[0]
            )
            card_id = int(
                connection.execute(
                    "SELECT id FROM vocabulary_cards WHERE entry_id = ? AND card_type = 'forward'",
                    (entry_id,),
                ).fetchone()[0]
            )
            due_before = connection.execute(
                """
                SELECT due_at FROM review_items
                WHERE item_type = 'vocabulary' AND ref_id = ?
                """,
                (entry_id,),
            ).fetchone()[0]
        finally:
            connection.close()

        response = self.client.post(
            f"/api/vocabulary/{entry_id}/review",
            json={"rating": "hard"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        entry = response.json()
        self.assertEqual(entry["study_status"], "learning")
        self.assertIsNotNone(datetime.fromisoformat(entry["next_review_at"]).tzinfo)
        self.assertIsNotNone(datetime.fromisoformat(entry["last_reviewed_at"]).tzinfo)

        connection = connect()
        try:
            item = connection.execute(
                """
                SELECT * FROM review_items
                WHERE item_type = 'vocabulary' AND ref_id = ?
                """,
                (entry_id,),
            ).fetchone()
            self.assertNotEqual(item["due_at"], due_before)
            # 0002 conservatively seeds a reviewed legacy card with reps=1;
            # this compatibility grade is therefore its second persisted rep.
            self.assertEqual(item["reps"], 2)
            log = connection.execute(
                "SELECT * FROM review_logs WHERE card_id = ?", (card_id,)
            ).fetchone()
            self.assertIsNotNone(log)
            self.assertEqual(log["final_rating"], 2)
            self.assertIsNotNone(datetime.fromisoformat(log["reviewed_at"]).tzinfo)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM vocabulary_reviews WHERE entry_id = ?",
                    (entry_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()


@unittest.skipUnless(
    (Path(__file__).parents[1] / "test-fixtures" / "existing-user-database.sqlite").exists(),
    "旧版仿真库夹具未安装",
)
class LegacyFixtureReviewCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory(ignore_cleanup_errors=True)
        fixture = (
            Path(__file__).parents[1]
            / "test-fixtures"
            / "existing-user-database.sqlite"
        )
        self.database_path = Path(self.temp.name) / "legacy-fixture.db"
        shutil.copy2(fixture, self.database_path)
        self.database_patch = patch(
            "backend.app.database.DATABASE_PATH", self.database_path
        )
        self.database_patch.start()
        from backend.app.database import initialize_database
        from backend.app.main import app

        initialize_database()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.database_patch.stop()
        self.temp.cleanup()

    def test_a6_4_hard_review_updates_forward_fsrs_and_log(self) -> None:
        connection = connect()
        try:
            entry = connection.execute(
                "SELECT id FROM vocabulary_entries WHERE normalized_term = 'resilience'"
            ).fetchone()
            self.assertIsNotNone(entry)
            entry_id = int(entry["id"])
            card = connection.execute(
                """
                SELECT vc.id, ri.due_at
                FROM vocabulary_cards AS vc
                JOIN review_items AS ri
                  ON ri.item_type = 'vocabulary' AND ri.ref_id = vc.entry_id
                WHERE vc.entry_id = ? AND vc.card_type = 'forward'
                """,
                (entry_id,),
            ).fetchone()
            due_before = str(card["due_at"])
            card_id = int(card["id"])
        finally:
            connection.close()

        response = self.client.post(
            f"/api/vocabulary/{entry_id}/review",
            json={"rating": "hard"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNotNone(datetime.fromisoformat(response.json()["next_review_at"]).tzinfo)

        connection = connect()
        try:
            item = connection.execute(
                """
                SELECT due_at FROM review_items
                WHERE item_type = 'vocabulary' AND ref_id = ?
                """,
                (entry_id,),
            ).fetchone()
            self.assertNotEqual(item["due_at"], due_before)
            log = connection.execute(
                "SELECT final_rating, reviewed_at FROM review_logs WHERE card_id = ?",
                (card_id,),
            ).fetchone()
            self.assertEqual(log["final_rating"], 2)
            self.assertIsNotNone(datetime.fromisoformat(log["reviewed_at"]).tzinfo)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
