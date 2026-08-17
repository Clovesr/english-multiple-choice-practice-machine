from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .fsrs_scheduler import SCHEDULER_NAME, SCHEDULER_VERSION


CARD_TYPES = (
    "forward",
    "reverse",
    "listening",
    "spelling",
    "cloze",
    "collocation",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_answer(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.strip().split())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _ensure_review_item(
    connection: sqlite3.Connection,
    card_id: int,
    *,
    timestamp: str,
) -> int:
    row = connection.execute(
        """
        SELECT id FROM review_items
        WHERE item_type = 'vocabulary_card' AND ref_id = ?
        """,
        (card_id,),
    ).fetchone()
    if row is not None:
        return int(row["id"])
    cursor = connection.execute(
        """
        INSERT INTO review_items(
            uuid, item_type, ref_id, state, step, due_at,
            stability, difficulty, scheduled_days, elapsed_days, reps, lapses,
            manually_suspended, scheduler, scheduler_version, created_at, updated_at
        ) VALUES (?, 'vocabulary_card', ?, 'new', 0, ?, 0, 0, 0, 0, 0, 0,
                  0, ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            card_id,
            timestamp,
            SCHEDULER_NAME,
            SCHEDULER_VERSION,
            timestamp,
            timestamp,
        ),
    )
    return int(cursor.lastrowid)


def _ensure_card(
    connection: sqlite3.Connection,
    *,
    entry_id: int,
    card_type: str,
    variant_key: str,
    generation_source: str,
    prompt: dict[str, Any],
    answer: dict[str, Any],
    timestamp: str,
) -> dict[str, Any]:
    prompt_data = _json(prompt)
    answer_data = _json(answer)
    row = connection.execute(
        """
        SELECT id, prompt_data, answer_data
        FROM vocabulary_cards
        WHERE entry_id = ? AND card_type = ? AND variant_key = ?
        """,
        (entry_id, card_type, variant_key),
    ).fetchone()
    if row is None:
        cursor = connection.execute(
            """
            INSERT INTO vocabulary_cards(
                uuid, entry_id, card_type, variant_key, generation_source,
                prompt_data, answer_data, content_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                str(uuid4()),
                entry_id,
                card_type,
                variant_key,
                generation_source,
                prompt_data,
                answer_data,
                timestamp,
                timestamp,
            ),
        )
        card_id = int(cursor.lastrowid)
    else:
        card_id = int(row["id"])
        if row["prompt_data"] != prompt_data or row["answer_data"] != answer_data:
            connection.execute(
                """
                UPDATE vocabulary_cards
                SET prompt_data = ?, answer_data = ?, generation_source = ?,
                    content_version = content_version + 1, updated_at = ?
                WHERE id = ?
                """,
                (prompt_data, answer_data, generation_source, timestamp, card_id),
            )
    review_item_id = _ensure_review_item(connection, card_id, timestamp=timestamp)
    return {
        "card_id": card_id,
        "card_type": card_type,
        "review_item_id": review_item_id,
    }


def _entry_facts(
    connection: sqlite3.Connection,
    entry_id: int,
) -> tuple[sqlite3.Row, list[sqlite3.Row], list[sqlite3.Row]]:
    entry = connection.execute(
        "SELECT * FROM vocabulary_entries WHERE id = ?", (entry_id,)
    ).fetchone()
    if entry is None:
        raise LookupError("单词不存在")
    senses = connection.execute(
        """
        SELECT * FROM vocabulary_senses
        WHERE entry_id = ?
        ORDER BY sequence, id
        """,
        (entry_id,),
    ).fetchall()
    forms = connection.execute(
        """
        SELECT form_text FROM vocabulary_forms
        WHERE entry_id = ? ORDER BY id
        """,
        (entry_id,),
    ).fetchall()
    return entry, senses, forms


def _meaning(entry: sqlite3.Row, senses: list[sqlite3.Row]) -> str:
    values = [
        str(sense["gloss_zh"] or sense["gloss_en"] or "").strip()
        for sense in senses
    ]
    values = list(dict.fromkeys(value for value in values if value))
    if values:
        return "；".join(values[:4])
    return str(entry["common_meaning"] or entry["contextual_meaning"] or "").strip()


def _accept_values(lemma: str, forms: list[sqlite3.Row]) -> list[str]:
    values = [lemma]
    values.extend(str(row["form_text"] or "") for row in forms)
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(value.strip().split())
        key = normalize_answer(cleaned)
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _reverse_distractors(
    connection: sqlite3.Connection,
    entry_id: int,
    accept: list[str],
    *,
    limit: int = 3,
) -> list[str]:
    """Return stable, distinct reverse-card choices without inventing content."""

    if limit <= 0:
        return []
    excluded = {normalize_answer(value) for value in accept if value.strip()}
    distractors: list[str] = []

    def add_candidates(rows: list[sqlite3.Row]) -> None:
        for row in rows:
            candidate = str(row["candidate"] or "").strip()
            normalized = normalize_answer(candidate)
            if not candidate or normalized in excluded:
                continue
            excluded.add(normalized)
            distractors.append(candidate)
            if len(distractors) >= limit:
                return

    # Prefer neighbours from the same wordbook so choices stay relevant to the
    # learner's current material. The ordering is deterministic, otherwise a
    # repeated generation pass would bump content_version and churn cards.
    same_wordbook = connection.execute(
        """
        SELECT candidate_entry.id,
               COALESCE(
                   NULLIF(trim(candidate_entry.lemma), ''),
                   NULLIF(trim(candidate_entry.term), '')
               ) AS candidate
        FROM wordbook_entries AS target
        JOIN wordbook_entries AS candidate_membership
          ON candidate_membership.wordbook_id = target.wordbook_id
        JOIN vocabulary_entries AS candidate_entry
          ON candidate_entry.id = candidate_membership.entry_id
        WHERE target.entry_id = ?
          AND candidate_entry.id <> ?
          AND candidate_entry.deleted_at IS NULL
          AND COALESCE(
                NULLIF(trim(candidate_entry.lemma), ''),
                NULLIF(trim(candidate_entry.term), '')
              ) IS NOT NULL
        GROUP BY candidate_entry.id
        ORDER BY
            MIN(COALESCE(NULLIF(candidate_membership.frequency_rank, 0), 2147483647)),
            MIN(candidate_membership.sequence),
            candidate_entry.id
        LIMIT 24
        """,
        (entry_id, entry_id),
    ).fetchall()
    add_candidates(same_wordbook)

    if len(distractors) < limit:
        # A one-off/custom entry may not share a wordbook with enough terms.
        # Fall back to persisted vocabulary facts, never generated fake words.
        global_candidates = connection.execute(
            """
            SELECT id,
                   COALESCE(NULLIF(trim(lemma), ''), NULLIF(trim(term), '')) AS candidate
            FROM vocabulary_entries
            WHERE id <> ?
              AND deleted_at IS NULL
              AND COALESCE(NULLIF(trim(lemma), ''), NULLIF(trim(term), '')) IS NOT NULL
            ORDER BY id
            LIMIT 64
            """,
            (entry_id,),
        ).fetchall()
        add_candidates(global_candidates)

    return distractors


def _cloze_sentence(sentence: str, surface_form: str, lemma: str) -> str:
    for candidate in (surface_form, lemma):
        if not candidate:
            continue
        pattern = re.compile(rf"(?<![A-Za-z]){re.escape(candidate)}(?![A-Za-z])", re.IGNORECASE)
        replaced, count = pattern.subn("_____", sentence, count=1)
        if count:
            return replaced
    return ""


def generate_cards_for_entry(
    connection: sqlite3.Connection,
    entry_id: int,
    *,
    generation_source: str,
) -> list[dict[str, Any]]:
    """Generate only cards supported by persisted facts, without resetting FSRS state."""

    entry, senses, forms = _entry_facts(connection, entry_id)
    lemma = str(entry["lemma"] or entry["term"] or "").strip()
    meaning = _meaning(entry, senses)
    accept = _accept_values(lemma, forms)
    timestamp = utc_now()
    cards: list[dict[str, Any]] = []

    if lemma and meaning:
        reverse_distractors = _reverse_distractors(connection, entry_id, accept)
        cards.append(
            _ensure_card(
                connection,
                entry_id=entry_id,
                card_type="forward",
                variant_key="primary",
                generation_source=generation_source,
                prompt={"text": lemma, "tts_text": lemma},
                answer={"text": meaning, "accept": []},
                timestamp=timestamp,
            )
        )
        cards.append(
            _ensure_card(
                connection,
                entry_id=entry_id,
                card_type="reverse",
                variant_key="primary",
                generation_source=generation_source,
                prompt={"text": meaning},
                answer={
                    "text": lemma,
                    "accept": accept,
                    "distractors": reverse_distractors,
                },
                timestamp=timestamp,
            )
        )
        cards.append(
            _ensure_card(
                connection,
                entry_id=entry_id,
                card_type="listening",
                variant_key="primary",
                generation_source=generation_source,
                prompt={"text": "", "tts_text": lemma},
                answer={"text": lemma, "accept": accept},
                timestamp=timestamp,
            )
        )
        cards.append(
            _ensure_card(
                connection,
                entry_id=entry_id,
                card_type="spelling",
                variant_key="primary",
                generation_source=generation_source,
                prompt={
                    "text": meaning,
                    "tts_text": lemma,
                    "phonetic_uk": str(entry["phonetic_uk"] or entry["phonetic"] or ""),
                },
                answer={"text": lemma, "accept": accept},
                timestamp=timestamp,
            )
        )

    occurrences = connection.execute(
        """
        SELECT o.*, r.title AS resource_title
        FROM vocabulary_occurrences AS o
        LEFT JOIN resources AS r ON r.id = o.resource_id
        WHERE o.entry_id = ? AND trim(o.context_sentence) <> ''
        ORDER BY o.id
        """,
        (entry_id,),
    ).fetchall()
    for occurrence in occurrences:
        sentence = str(occurrence["context_sentence"] or "").strip()
        cloze = _cloze_sentence(
            sentence,
            str(occurrence["surface_form"] or ""),
            lemma,
        )
        if not cloze or not lemma:
            continue
        cards.append(
            _ensure_card(
                connection,
                entry_id=entry_id,
                card_type="cloze",
                variant_key=f"occurrence:{int(occurrence['id'])}",
                generation_source="selection_context",
                prompt={"text": cloze, "cloze_sentence": cloze},
                answer={"text": str(occurrence["surface_form"] or lemma), "accept": accept},
                timestamp=timestamp,
            )
        )

    relations = connection.execute(
        """
        SELECT id, relation_type, related_term, note
        FROM vocabulary_relations
        WHERE entry_id = ? AND relation_type IN ('collocation', 'phrasal_verb')
        ORDER BY id
        """,
        (entry_id,),
    ).fetchall()
    for relation in relations:
        related = str(relation["related_term"] or "").strip()
        if not related:
            continue
        cards.append(
            _ensure_card(
                connection,
                entry_id=entry_id,
                card_type="collocation",
                variant_key=f"relation:{int(relation['id'])}",
                generation_source="explicit_relation",
                prompt={"text": str(relation["note"] or meaning or lemma), "pairs": []},
                answer={"text": related, "accept": [related]},
                timestamp=timestamp,
            )
        )

    if " " in lemma and meaning:
        cards.append(
            _ensure_card(
                connection,
                entry_id=entry_id,
                card_type="collocation",
                variant_key="phrase:primary",
                generation_source=generation_source,
                prompt={"text": meaning, "pairs": []},
                answer={"text": lemma, "accept": [lemma]},
                timestamp=timestamp,
            )
        )
    return cards
