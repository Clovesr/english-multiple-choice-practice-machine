from __future__ import annotations

from datetime import datetime, timezone
from importlib.metadata import version
from typing import Any, Mapping

from fsrs import Card, Rating, Scheduler, State


SCHEDULER_NAME = "fsrs"
SCHEDULER_VERSION = f"py-fsrs-{version('fsrs')}"
_SCHEDULER = Scheduler()


_DATABASE_TO_FSRS_STATE = {
    "new": State.Learning,
    "learning": State.Learning,
    "review": State.Review,
    "relearning": State.Relearning,
}

_FSRS_TO_DATABASE_STATE = {
    State.Learning: "learning",
    State.Review: "review",
    State.Relearning: "relearning",
}


def _utc_datetime(value: Any, *, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError) as error:
            raise ValueError(f"{field} 不是有效的 ISO-8601 时间") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} 必须包含时区偏移")
    return parsed.astimezone(timezone.utc)


def _optional_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _card_from_item(item: Mapping[str, Any]) -> Card:
    state_name = str(item.get("state") or "new")
    try:
        fsrs_state = _DATABASE_TO_FSRS_STATE[state_name]
    except KeyError as error:
        raise ValueError(f"不支持的 FSRS 状态：{state_name}") from error
    step = int(item.get("step") or 0) if fsrs_state != State.Review else None
    stability = _optional_positive(item.get("stability"))
    difficulty = _optional_positive(item.get("difficulty"))
    if fsrs_state in {State.Review, State.Relearning} and (
        stability is None or difficulty is None
    ):
        raise ValueError("review/relearning 卡缺少有效的 stability 或 difficulty")
    last_review_value = item.get("last_review_at")
    last_review = (
        _utc_datetime(last_review_value, field="last_review_at")
        if last_review_value
        else None
    )
    return Card(
        card_id=int(item["ref_id"]),
        state=fsrs_state,
        step=step,
        stability=stability,
        difficulty=difficulty,
        due=_utc_datetime(item["due_at"], field="due_at"),
        last_review=last_review,
    )


def schedule_review(
    item: Mapping[str, Any],
    *,
    rating: int,
    reviewed_at: datetime,
    duration_ms: int = 0,
) -> dict[str, Any]:
    """Advance one persisted review item using the pinned py-fsrs scheduler."""

    if rating not in {1, 2, 3, 4}:
        raise ValueError("rating 必须是 1、2、3 或 4")
    if duration_ms < 0:
        raise ValueError("duration_ms 不能为负数")
    reviewed_at_utc = _utc_datetime(reviewed_at, field="reviewed_at")
    before = _card_from_item(item)
    after, _ = _SCHEDULER.review_card(
        before,
        Rating(rating),
        review_datetime=reviewed_at_utc,
        review_duration=duration_ms,
    )
    elapsed_days = (
        max(0.0, (reviewed_at_utc - before.last_review).total_seconds() / 86400)
        if before.last_review
        else 0.0
    )
    scheduled_days = max(
        0.0,
        (after.due - reviewed_at_utc).total_seconds() / 86400,
    )
    previous_state = str(item.get("state") or "new")
    return {
        "state": _FSRS_TO_DATABASE_STATE[after.state],
        "step": after.step if after.step is not None else 0,
        "due_at": after.due.isoformat(timespec="seconds"),
        "last_review_at": reviewed_at_utc.isoformat(timespec="seconds"),
        "stability": float(after.stability or 0),
        "difficulty": float(after.difficulty or 0),
        "scheduled_days": scheduled_days,
        "elapsed_days": elapsed_days,
        "reps": int(item.get("reps") or 0) + 1,
        "lapses": int(item.get("lapses") or 0)
        + int(rating == 1 and previous_state == "review"),
        "scheduler": SCHEDULER_NAME,
        "scheduler_version": SCHEDULER_VERSION,
        "updated_at": reviewed_at_utc.isoformat(timespec="seconds"),
    }
