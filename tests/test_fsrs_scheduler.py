from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from backend.app.services.fsrs_scheduler import (
    SCHEDULER_VERSION,
    schedule_review,
)


class FsrsSchedulerTests(unittest.TestCase):
    def _new_item(self, now: datetime) -> dict:
        return {
            "ref_id": 7,
            "state": "new",
            "step": 0,
            "due_at": now.isoformat(),
            "last_review_at": None,
            "stability": 0,
            "difficulty": 0,
            "reps": 0,
            "lapses": 0,
        }

    def test_new_card_advances_through_learning_into_review(self) -> None:
        now = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)
        item = self._new_item(now)

        first = schedule_review(item, rating=3, reviewed_at=now, duration_ms=2100)
        self.assertEqual(first["state"], "learning")
        self.assertEqual(first["step"], 1)
        self.assertEqual(
            datetime.fromisoformat(first["due_at"]),
            now + timedelta(minutes=10),
        )
        self.assertGreater(first["stability"], 0)
        self.assertGreater(first["difficulty"], 0)
        self.assertEqual(first["scheduler_version"], SCHEDULER_VERSION)

        second_item = {**item, **first}
        second_review_at = datetime.fromisoformat(first["due_at"])
        second = schedule_review(
            second_item,
            rating=3,
            reviewed_at=second_review_at,
            duration_ms=1800,
        )
        self.assertEqual(second["state"], "review")
        self.assertEqual(second["step"], 0)
        self.assertGreater(datetime.fromisoformat(second["due_at"]), second_review_at)
        self.assertEqual(second["reps"], 2)
        self.assertEqual(second["lapses"], 0)

    def test_again_on_review_card_enters_relearning_and_counts_lapse(self) -> None:
        now = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)
        item = {
            "ref_id": 8,
            "state": "review",
            "step": 0,
            "due_at": now.isoformat(),
            "last_review_at": (now - timedelta(days=7)).isoformat(),
            "stability": 7,
            "difficulty": 5,
            "reps": 3,
            "lapses": 1,
        }
        result = schedule_review(item, rating=1, reviewed_at=now)
        self.assertEqual(result["state"], "relearning")
        self.assertEqual(result["lapses"], 2)
        self.assertEqual(
            datetime.fromisoformat(result["due_at"]),
            now + timedelta(minutes=10),
        )

    def test_rejects_naive_review_time(self) -> None:
        now = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "时区"):
            schedule_review(
                self._new_item(now),
                rating=3,
                reviewed_at=datetime(2026, 8, 17, 12),
            )


if __name__ == "__main__":
    unittest.main()
