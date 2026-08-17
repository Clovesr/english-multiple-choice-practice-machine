from __future__ import annotations

import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

MAX_RESPONSE_SECONDS = 1.0
ENTRY_COUNT = 10_000


class RealScalePerformanceRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.database_path = Path(self.temp.name) / "real-scale.db"
        self.database_patch = patch(
            "backend.app.database.DATABASE_PATH", self.database_path
        )
        self.database_patch.start()
        from backend.app.database import connect, initialize_database
        from backend.app.services.dictionary import dictionary_summary
        from backend.app.services.wordbooks import BUILTIN_WORDBOOKS

        initialize_database()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        now_iso = now.isoformat()
        due_iso = (now - timedelta(days=1)).isoformat()
        last_review_iso = (now - timedelta(days=8)).isoformat()
        with connect() as connection:
            connection.executemany(
                """
                INSERT INTO vocabulary_entries(
                    uuid, term, normalized_term, lemma, common_meaning,
                    translation_status, enrichment_status, encounter_count,
                    study_status, created_at, updated_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, 'ready', 'ready', 0,
                          'learning', ?, ?, ?)
                """,
                (
                    (
                        f"perf-entry-{index}",
                        f"term-{index:05d}",
                        f"term-{index:05d}",
                        f"term-{index:05d}",
                        f"释义 {index}",
                        now_iso,
                        now_iso,
                        now_iso,
                    )
                    for index in range(1, ENTRY_COUNT + 1)
                ),
            )
            connection.executemany(
                """
                INSERT INTO vocabulary_cards(
                    uuid, entry_id, card_type, variant_key, generation_source,
                    prompt_data, answer_data, created_at, updated_at
                ) VALUES (?, ?, 'forward', 'primary', 'performance-fixture', ?, ?, ?, ?)
                """,
                (
                    (
                        f"perf-card-{index}",
                        index,
                        json.dumps({"text": f"term-{index:05d}"}),
                        json.dumps(
                            {
                                "text": f"释义 {index}",
                                "accept": [f"释义 {index}"],
                                "distractors": [],
                            },
                            ensure_ascii=False,
                        ),
                        now_iso,
                        now_iso,
                    )
                    for index in range(1, ENTRY_COUNT + 1)
                ),
            )
            connection.executemany(
                """
                INSERT INTO review_items(
                    uuid, item_type, ref_id, state, due_at, last_review_at,
                    stability, difficulty, reps, scheduler_version,
                    created_at, updated_at
                ) VALUES (?, 'vocabulary_card', ?, ?, ?, ?, ?, ?, ?, 'perf-v1', ?, ?)
                """,
                (
                    (
                        f"perf-review-{index}",
                        index,
                        "review" if index <= ENTRY_COUNT // 2 else "new",
                        due_iso if index <= ENTRY_COUNT // 2 else now_iso,
                        last_review_iso if index <= ENTRY_COUNT // 2 else None,
                        7.0 if index <= ENTRY_COUNT // 2 else 0.0,
                        5.0 if index <= ENTRY_COUNT // 2 else 0.0,
                        1 if index <= ENTRY_COUNT // 2 else 0,
                        now_iso,
                        now_iso,
                    )
                    for index in range(1, ENTRY_COUNT + 1)
                ),
            )

            summary = dictionary_summary()
            checksum = str(summary["metadata"].get("source_sha256") or "")
            for tag, name in BUILTIN_WORDBOOKS.items():
                wordbook_id = int(
                    connection.execute(
                        """
                        INSERT INTO wordbooks(
                            uuid, name, kind, source_tag, checksum, status,
                            created_at, updated_at
                        ) VALUES (?, ?, 'builtin', ?, ?, 'active', ?, ?)
                        """,
                        (f"perf-{tag}", name, tag, checksum, now_iso, now_iso),
                    ).lastrowid
                )
                count = int(summary["counts"][tag])
                connection.executemany(
                    """
                    INSERT INTO wordbook_entries(
                        wordbook_id, entry_id, sequence, frequency_rank, added_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        (wordbook_id, index, index, index, now_iso)
                        for index in range(1, count + 1)
                    ),
                )

            self.wordbook_id = int(
                connection.execute(
                    """
                    INSERT INTO wordbooks(
                        uuid, name, kind, checksum, status, created_at, updated_at
                    ) VALUES ('perf-imported', '真实规模词书', 'imported',
                              'perf', 'active', ?, ?)
                    """,
                    (now_iso, now_iso),
                ).lastrowid
            )
            connection.executemany(
                """
                INSERT INTO wordbook_entries(
                    wordbook_id, entry_id, sequence, frequency_rank, added_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (self.wordbook_id, index, index, index, now_iso)
                    for index in range(1, ENTRY_COUNT + 1)
                ),
            )
            connection.execute(
                """
                INSERT INTO study_plans(
                    uuid, wordbook_id, mode, daily_new, new_order,
                    active, created_at, updated_at
                ) VALUES ('perf-plan', ?, 'normal', 20, 'frequency', 1, ?, ?)
                """,
                (self.wordbook_id, now_iso, now_iso),
            )
            connection.commit()

    def tearDown(self) -> None:
        self.database_patch.stop()
        self.temp.cleanup()

    def _timed_call(self, function, *args, **kwargs) -> tuple[float, dict]:
        started = time.perf_counter()
        payload = function(*args, **kwargs)
        elapsed = time.perf_counter() - started
        return elapsed, payload

    def test_wordbooks_session_overview_and_similar_guard_stay_responsive(self) -> None:
        from backend.app.database import connect
        from backend.app.services.study import create_or_resume_session, get_overview
        from backend.app.services.vocabulary import local_similar_matches
        from backend.app.services.wordbooks import list_wordbooks

        with connect() as connection:
            with patch(
                "backend.app.services.wordbooks.bundled_entries",
                side_effect=AssertionError("幂等词书列表不应再次物化 5.7 万词典条目"),
            ):
                elapsed, payload = self._timed_call(list_wordbooks, connection)
            self.assertLess(elapsed, MAX_RESPONSE_SECONDS)
            imported = next(
                item for item in payload["items"] if item["id"] == self.wordbook_id
            )
            self.assertEqual(imported["total"], ENTRY_COUNT)
            self.assertEqual(imported["state_counts"]["learning"], ENTRY_COUNT // 2)
            self.assertEqual(imported["state_counts"]["new"], ENTRY_COUNT // 2)

            elapsed, session = self._timed_call(
                create_or_resume_session, connection, limit=20
            )
            self.assertLess(elapsed, MAX_RESPONSE_SECONDS)
            self.assertEqual(len(session["cards"]), 20)
            self.assertTrue(
                all(card["state"] == "review" for card in session["cards"])
            )

            elapsed, overview = self._timed_call(get_overview, connection)
            self.assertLess(elapsed, MAX_RESPONSE_SECONDS)
            self.assertGreaterEqual(overview["overdue_total"], ENTRY_COUNT // 2)

            started = time.perf_counter()
            matches = local_similar_matches(connection, [1, 2, 3])
            elapsed = time.perf_counter() - started
        self.assertEqual(matches, {})
        self.assertLess(elapsed, 0.25)


if __name__ == "__main__":
    unittest.main()
