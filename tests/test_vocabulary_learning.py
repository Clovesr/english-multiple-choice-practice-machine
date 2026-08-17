from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient


class VocabularyLearningApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.database_path = Path(self.temp.name) / "vocabulary-learning.db"
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

    def _resource_segment(self, title: str, content: str) -> tuple[int, int]:
        from backend.app.database import connect
        from backend.app.services.vocabulary_cards import utc_now

        timestamp = utc_now()
        with connect() as connection:
            resource_id = int(
                connection.execute(
                    """
                    INSERT INTO resources(
                        uuid, title, type, format, checksum, segment_count,
                        imported_at, created_at, updated_at
                    ) VALUES (?, ?, 'article', 'txt', ?, 1, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        title,
                        str(uuid4()),
                        timestamp,
                        timestamp,
                        timestamp,
                    ),
                ).lastrowid
            )
            segment_id = int(
                connection.execute(
                    """
                    INSERT INTO resource_segments(resource_id, sequence, content)
                    VALUES (?, 1, ?)
                    """,
                    (resource_id, content),
                ).lastrowid
            )
            connection.commit()
        connection.close()
        return resource_id, segment_id

    def test_selection_enriches_merges_occurrences_and_generates_cards(self) -> None:
        first_resource, first_segment = self._resource_segment(
            "Article One", "Her ability surprised everyone."
        )
        response = self.client.post(
            "/api/vocabulary/from-selection",
            json={
                "term": "ability",
                "context_sentence": "Her ability surprised everyone.",
                "resource_id": first_resource,
                "segment_id": first_segment,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        first = response.json()
        self.assertTrue(first["enriched_from_dictionary"])
        self.assertFalse(first["merged"])
        self.assertEqual(first["entry"]["lemma"], "ability")
        self.assertTrue(first["entry"]["common_meaning"])
        self.assertEqual(
            {card["card_type"] for card in first["cards"]},
            {"forward", "reverse", "listening", "spelling", "cloze"},
        )
        occurrence = first["entry"]["occurrences"][0]
        self.assertEqual(occurrence["resource_id"], first_resource)
        self.assertEqual(occurrence["segment_id"], first_segment)

        second_resource, second_segment = self._resource_segment(
            "Article Two", "This ability grows with practice."
        )
        repeated = self.client.post(
            "/api/vocabulary/from-selection",
            json={
                "term": "ability",
                "context_sentence": "This ability grows with practice.",
                "resource_id": second_resource,
                "segment_id": second_segment,
            },
        )
        self.assertEqual(repeated.status_code, 201, repeated.text)
        merged = repeated.json()
        self.assertTrue(merged["merged"])
        self.assertEqual(merged["entry"]["id"], first["entry"]["id"])
        self.assertEqual(merged["entry"]["encounter_count"], 2)
        self.assertEqual(len(merged["entry"]["occurrences"]), 2)
        self.assertEqual(
            len([card for card in merged["cards"] if card["card_type"] == "cloze"]),
            2,
        )

        lookup = self.client.get("/api/dictionary/lookup", params={"term": "abilities"})
        self.assertEqual(lookup.status_code, 200)
        self.assertTrue(lookup.json()["found"])
        self.assertEqual(lookup.json()["entry"]["lemma"], "ability")

    def test_vocabulary_defaults_to_collected_scope_and_uses_card_due_state(self) -> None:
        from backend.app.database import connect

        now = datetime.now(timezone.utc).replace(microsecond=0)
        due = (now - timedelta(days=1)).isoformat()
        with connect() as connection:
            entries = (
                ("user-trace", "user", 0, 0, 0),
                ("seeded-unseen", "builtin_wordbook", 0, 0, 0),
                ("seeded-edited", "builtin_wordbook", 0, 1, 0),
                ("seeded-reviewed", "builtin_wordbook", 0, 0, 0),
                ("seeded-frequent", "builtin_wordbook", 0, 0, 1),
            )
            ids: dict[str, int] = {}
            for term, source_kind, encounters, edited, frequent in entries:
                ids[term] = int(
                    connection.execute(
                        """
                        INSERT INTO vocabulary_entries(
                            uuid, term, normalized_term, lemma, common_meaning,
                            translation_status, enrichment_status, encounter_count,
                            study_status, source_kind, user_edited, manually_frequent,
                            created_at, updated_at, last_seen_at
                        ) VALUES (?, ?, ?, ?, ?, 'ready', 'ready', ?, 'learning',
                                  ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid4()),
                            term,
                            term,
                            term,
                            f"{term} meaning",
                            encounters,
                            source_kind,
                            edited,
                            frequent,
                            now.isoformat(),
                            now.isoformat(),
                            now.isoformat(),
                        ),
                    ).lastrowid
                )
            card_id = int(
                connection.execute(
                    """
                    INSERT INTO vocabulary_cards(
                        uuid, entry_id, card_type, variant_key, generation_source,
                        prompt_data, answer_data, created_at, updated_at
                    ) VALUES (?, ?, 'forward', 'primary', 'scope-test', '{}', '{}', ?, ?)
                    """,
                    (
                        str(uuid4()),
                        ids["seeded-reviewed"],
                        now.isoformat(),
                        now.isoformat(),
                    ),
                ).lastrowid
            )
            connection.execute(
                """
                INSERT INTO review_items(
                    uuid, item_type, ref_id, state, due_at, reps,
                    scheduler_version, created_at, updated_at
                ) VALUES (?, 'vocabulary_card', ?, 'review', ?, 1,
                          'scope-test', ?, ?)
                """,
                (str(uuid4()), card_id, due, now.isoformat(), now.isoformat()),
            )
            connection.commit()

        collected = self.client.get("/api/vocabulary")
        self.assertEqual(collected.status_code, 200, collected.text)
        payload = collected.json()
        self.assertEqual(
            {item["term"] for item in payload["items"]},
            {"user-trace", "seeded-edited", "seeded-reviewed", "seeded-frequent"},
        )
        self.assertTrue(all(item["is_collected"] for item in payload["items"]))
        self.assertEqual(payload["counts"]["total"], 5)
        self.assertEqual(payload["counts"]["collected_total"], 4)
        self.assertEqual(payload["counts"]["seeded_total"], 1)
        self.assertEqual(payload["counts"]["visible_total"], 4)
        self.assertEqual(payload["counts"]["review"], 1)
        self.assertEqual(payload["total"], 4)
        self.assertEqual(payload["limit"], 120)
        self.assertEqual(payload["offset"], 0)

        all_entries = self.client.get("/api/vocabulary", params={"scope": "all"})
        self.assertEqual(all_entries.status_code, 200, all_entries.text)
        self.assertEqual(len(all_entries.json()["items"]), 5)
        self.assertEqual(all_entries.json()["counts"]["visible_total"], 5)
        unseen = next(
            item
            for item in all_entries.json()["items"]
            if item["term"] == "seeded-unseen"
        )
        self.assertFalse(unseen["is_collected"])

        paged = self.client.get(
            "/api/vocabulary",
            params={"scope": "all", "limit": 2, "offset": 1},
        )
        self.assertEqual(paged.status_code, 200, paged.text)
        self.assertEqual(len(paged.json()["items"]), 2)
        self.assertEqual(paged.json()["total"], 5)
        self.assertEqual(paged.json()["limit"], 2)
        self.assertEqual(paged.json()["offset"], 1)
        self.assertEqual(paged.json()["counts"]["visible_total"], 5)

        due_entries = self.client.get(
            "/api/vocabulary", params={"status": "review"}
        )
        self.assertEqual(due_entries.status_code, 200, due_entries.text)
        self.assertEqual(
            [item["term"] for item in due_entries.json()["items"]],
            ["seeded-reviewed"],
        )
        home = self.client.get("/api/vocabulary/home", params={"limit": 20})
        self.assertEqual(home.status_code, 200, home.text)
        self.assertNotIn("seeded-unseen", {item["term"] for item in home.json()["items"]})

    def test_builtin_wordbooks_import_report_and_plan(self) -> None:
        response = self.client.get("/api/wordbooks")
        self.assertEqual(response.status_code, 200, response.text)
        builtins = response.json()["items"]
        self.assertEqual(
            {item["source_tag"]: item["total"] for item in builtins},
            {"cet4": 3849, "cet6": 5407, "ky": 4801},
        )
        self.assertTrue(all(item["kind"] == "builtin" for item in builtins))

        imported = self.client.post(
            "/api/wordbooks/import",
            json={
                "name": "My Words",
                "terms": ["ability", "florblexyz"],
            },
        )
        self.assertEqual(imported.status_code, 201, imported.text)
        report = imported.json()
        self.assertEqual(report["matched"], 1)
        self.assertEqual(
            report["unmatched"],
            [{"term": "florblexyz", "status": "needs_enrichment"}],
        )
        wordbook_id = report["wordbook"]["id"]
        plan = self.client.post(
            f"/api/wordbooks/{wordbook_id}/plan",
            json={"daily_new": 7, "new_order": "sequence"},
        )
        self.assertEqual(plan.status_code, 200, plan.text)
        self.assertTrue(plan.json()["plan"]["active"])
        self.assertEqual(plan.json()["plan"]["daily_new"], 7)

        from backend.app.database import connect

        connection = connect()
        try:
            unmatched_id = int(
                connection.execute(
                    "SELECT id FROM vocabulary_entries WHERE normalized_term = 'florblexyz'"
                ).fetchone()[0]
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM vocabulary_cards WHERE entry_id = ?",
                    (unmatched_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
