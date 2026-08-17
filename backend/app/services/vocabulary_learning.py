from __future__ import annotations

import sqlite3
from typing import Any
from uuid import uuid4

from .dictionary import DictionaryUnavailableError, lookup_dictionary
from .learning import record_learning_event
from .vocabulary import _serialize_entry, validate_term, vocabulary_key
from .vocabulary_cards import generate_cards_for_entry, utc_now


def _meaning_summary(dictionary_entry: dict[str, Any]) -> tuple[str, str]:
    senses = dictionary_entry.get("pos_senses") or []
    chinese = [
        str(sense.get("gloss_zh") or "").strip()
        for sense in senses
        if isinstance(sense, dict)
    ]
    english = [
        str(sense.get("gloss_en") or "").strip()
        for sense in senses
        if isinstance(sense, dict)
    ]
    chinese = list(dict.fromkeys(value for value in chinese if value))
    english = list(dict.fromkeys(value for value in english if value))
    return "；".join(chinese[:4]), "；".join(english[:2])


def _part_of_speech(dictionary_entry: dict[str, Any]) -> str:
    values = [
        str(sense.get("pos") or "").strip()
        for sense in dictionary_entry.get("pos_senses") or []
        if isinstance(sense, dict)
    ]
    return "/".join(dict.fromkeys(value for value in values if value))


def _insert_dictionary_facts(
    connection: sqlite3.Connection,
    entry_id: int,
    dictionary_entry: dict[str, Any],
    *,
    timestamp: str,
) -> None:
    dictionary_key = str(dictionary_entry["dictionary_key"])
    for sequence, sense in enumerate(dictionary_entry.get("pos_senses") or [], start=1):
        if not isinstance(sense, dict):
            continue
        connection.execute(
            """
            INSERT INTO vocabulary_senses(
                uuid, entry_id, pos, gloss_zh, gloss_en, sequence,
                source, source_ref, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'ecdict', ?, ?, ?)
            ON CONFLICT(entry_id, source, source_ref, sequence) DO UPDATE SET
                pos = excluded.pos,
                gloss_zh = excluded.gloss_zh,
                gloss_en = excluded.gloss_en,
                updated_at = excluded.updated_at
            """,
            (
                str(uuid4()),
                entry_id,
                str(sense.get("pos") or "")[:80],
                str(sense.get("gloss_zh") or "")[:2000],
                str(sense.get("gloss_en") or "")[:2000],
                sequence,
                dictionary_key,
                timestamp,
                timestamp,
            ),
        )
    for form in dictionary_entry.get("forms") or []:
        if not isinstance(form, dict) or not str(form.get("text") or "").strip():
            continue
        connection.execute(
            """
            INSERT OR IGNORE INTO vocabulary_forms(
                entry_id, form_type, form_text, source
            ) VALUES (?, ?, ?, 'ecdict')
            """,
            (
                entry_id,
                str(form.get("kind") or "variant")[:80],
                str(form["text"]).strip()[:200],
            ),
        )
    for relation in dictionary_entry.get("relations") or []:
        if not isinstance(relation, dict):
            continue
        related_term = str(relation.get("term") or relation.get("related_term") or "").strip()
        relation_type = str(relation.get("type") or relation.get("relation_type") or "").strip()
        if not related_term or not relation_type:
            continue
        connection.execute(
            """
            INSERT OR IGNORE INTO vocabulary_relations(
                entry_id, relation_type, related_term, note, source
            ) VALUES (?, ?, ?, ?, 'ecdict')
            """,
            (
                entry_id,
                relation_type[:40],
                related_term[:200],
                str(relation.get("note") or "")[:1000],
            ),
        )


def upsert_dictionary_entry(
    connection: sqlite3.Connection,
    dictionary_entry: dict[str, Any],
    *,
    source_kind: str,
) -> tuple[int, bool]:
    timestamp = utc_now()
    dictionary_key = str(dictionary_entry["dictionary_key"])
    lemma = str(dictionary_entry["lemma"] or dictionary_key).strip()
    row = connection.execute(
        """
        SELECT id FROM vocabulary_entries
        WHERE dictionary_key = ? OR normalized_term = ?
        ORDER BY CASE WHEN dictionary_key = ? THEN 0 ELSE 1 END, id
        LIMIT 1
        """,
        (dictionary_key, dictionary_key, dictionary_key),
    ).fetchone()
    common_meaning, english_meaning = _meaning_summary(dictionary_entry)
    fallback_meaning = common_meaning or english_meaning
    phonetic_uk = str(dictionary_entry.get("phonetic_uk") or "")[:120]
    phonetic_us = str(dictionary_entry.get("phonetic_us") or "")[:120]
    part_of_speech = _part_of_speech(dictionary_entry)[:80]
    if row is None:
        cursor = connection.execute(
            """
            INSERT INTO vocabulary_entries(
                term, normalized_term, lemma, phonetic, part_of_speech,
                contextual_meaning, common_meaning, translation_status,
                encounter_count, study_status, uuid, dictionary_key, phonetic_uk, phonetic_us,
                source_kind, enrichment_status,
                created_at, updated_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, '', ?, 'ready', ?, 'learning', ?, ?, ?, ?, ?,
                      'ready', ?, ?, ?)
            """,
            (
                lemma,
                dictionary_key,
                lemma,
                phonetic_uk or phonetic_us,
                part_of_speech,
                fallback_meaning,
                1 if source_kind == "user" else 0,
                str(uuid4()),
                dictionary_key,
                phonetic_uk,
                phonetic_us,
                source_kind,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        entry_id = int(cursor.lastrowid)
        created = True
    else:
        entry_id = int(row["id"])
        connection.execute(
            """
            UPDATE vocabulary_entries
            SET uuid = COALESCE(uuid, ?),
                dictionary_key = CASE WHEN dictionary_key = '' THEN ? ELSE dictionary_key END,
                lemma = CASE WHEN lemma = '' THEN ? ELSE lemma END,
                phonetic = CASE WHEN phonetic = '' THEN ? ELSE phonetic END,
                phonetic_uk = CASE WHEN phonetic_uk = '' THEN ? ELSE phonetic_uk END,
                phonetic_us = CASE WHEN phonetic_us = '' THEN ? ELSE phonetic_us END,
                part_of_speech = CASE WHEN part_of_speech = '' THEN ? ELSE part_of_speech END,
                common_meaning = CASE WHEN common_meaning = '' THEN ? ELSE common_meaning END,
                translation_status = CASE
                    WHEN translation_status IN ('pending', 'queued', 'failed') THEN 'ready'
                    ELSE translation_status END,
                translation_error = CASE
                    WHEN translation_status IN ('pending', 'queued', 'failed') THEN ''
                    ELSE translation_error END,
                enrichment_status = 'ready',
                updated_at = ?
            WHERE id = ?
            """,
            (
                str(uuid4()),
                dictionary_key,
                lemma,
                phonetic_uk or phonetic_us,
                phonetic_uk,
                phonetic_us,
                part_of_speech,
                fallback_meaning,
                timestamp,
                entry_id,
            ),
        )
        created = False
    _insert_dictionary_facts(
        connection,
        entry_id,
        dictionary_entry,
        timestamp=timestamp,
    )
    return entry_id, created


def _minimal_entry(
    connection: sqlite3.Connection,
    term: str,
    *,
    meaning: str = "",
    source_kind: str = "user",
    encountered: bool = True,
) -> tuple[int, bool]:
    normalized = vocabulary_key(term)
    row = connection.execute(
        "SELECT id FROM vocabulary_entries WHERE normalized_term = ?",
        (normalized,),
    ).fetchone()
    if row is not None:
        return int(row["id"]), False
    timestamp = utc_now()
    cursor = connection.execute(
        """
        INSERT INTO vocabulary_entries(
            term, normalized_term, lemma, common_meaning,
            translation_status, encounter_count, study_status, uuid, source_kind,
            enrichment_status, created_at, updated_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'learning', ?, ?, ?, ?, ?, ?)
        """,
        (
            term,
            normalized,
            term,
            meaning,
            "ready" if meaning else "pending",
            1 if encountered else 0,
            str(uuid4()),
            source_kind,
            "needs_enrichment",
            timestamp,
            timestamp,
            timestamp,
        ),
    )
    entry_id = int(cursor.lastrowid)
    if meaning:
        connection.execute(
            """
            INSERT INTO vocabulary_senses(
                uuid, entry_id, gloss_zh, sequence, source, source_ref,
                created_at, updated_at
            ) VALUES (?, ?, ?, 1, 'user', 'import', ?, ?)
            """,
            (str(uuid4()), entry_id, meaning, timestamp, timestamp),
        )
    return entry_id, True


def collect_from_selection(
    connection: sqlite3.Connection,
    data: dict[str, Any],
) -> dict[str, Any]:
    surface_term = validate_term(str(data["term"]))
    resource_id = int(data["resource_id"])
    segment_id = int(data["segment_id"])
    segment = connection.execute(
        """
        SELECT id FROM resource_segments
        WHERE id = ? AND resource_id = ?
        """,
        (segment_id, resource_id),
    ).fetchone()
    if segment is None:
        raise LookupError("资源片段不存在或不属于该资源")

    try:
        dictionary_result = lookup_dictionary(surface_term)
    except DictionaryUnavailableError:
        dictionary_result = {"found": False, "entry": None}
    enriched = bool(dictionary_result.get("found") and dictionary_result.get("entry"))
    try:
        if enriched:
            entry_id, created = upsert_dictionary_entry(
                connection,
                dictionary_result["entry"],
                source_kind="user",
            )
        else:
            entry_id, created = _minimal_entry(connection, surface_term)

        if not created:
            connection.execute(
                """
                UPDATE vocabulary_entries
                SET encounter_count = encounter_count + 1,
                    study_status = CASE
                        WHEN study_status IN ('known', 'mastered', 'ignored', 'paused')
                        THEN study_status ELSE 'learning' END,
                    last_seen_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (utc_now(), utc_now(), entry_id),
            )
        occurrence = connection.execute(
            """
            INSERT INTO vocabulary_occurrences(
                entry_id, surface_form, context_sentence, context_before,
                context_after, resource_id, segment_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                surface_term,
                str(data.get("context_sentence") or "")[:1500],
                str(data.get("context_before") or "")[:1000],
                str(data.get("context_after") or "")[:1000],
                resource_id,
                segment_id,
            ),
        )
        cards = generate_cards_for_entry(
            connection,
            entry_id,
            generation_source="selection_dictionary" if enriched else "selection_context",
        )
        record_learning_event(
            connection,
            verb="collect_word",
            object_type="vocabulary_entry",
            object_id=entry_id,
            result={
                "created": created,
                "enriched": enriched,
                "resource_id": resource_id,
                "segment_id": segment_id,
            },
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {
        "entry": _serialize_entry(connection, entry_id),
        "occurrence_id": int(occurrence.lastrowid),
        "cards": cards,
        "enriched_from_dictionary": enriched,
        "merged": not created,
    }


def create_or_match_imported_entry(
    connection: sqlite3.Connection,
    term: str,
    *,
    meaning: str = "",
) -> tuple[int, bool, bool]:
    validated = validate_term(term)
    try:
        result = lookup_dictionary(validated)
    except DictionaryUnavailableError:
        result = {"found": False, "entry": None}
    if result.get("found") and result.get("entry"):
        entry_id, created = upsert_dictionary_entry(
            connection,
            result["entry"],
            source_kind="imported",
        )
        return entry_id, created, True
    entry_id, created = _minimal_entry(
        connection,
        validated,
        meaning=meaning.strip(),
        source_kind="imported",
        encountered=False,
    )
    return entry_id, created, False
