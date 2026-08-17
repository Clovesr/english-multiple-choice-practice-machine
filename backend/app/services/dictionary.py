from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..config import BUNDLED_DICTIONARY_PATH


POS_PREFIX = re.compile(r"^([a-z]+(?:\.[a-z]+)*\.)\s*(.+)$", re.IGNORECASE)


class DictionaryUnavailableError(RuntimeError):
    pass


def normalize_term(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.strip().casefold().split())


@contextmanager
def dictionary_connection(
    path: Path = BUNDLED_DICTIONARY_PATH,
) -> Iterator[sqlite3.Connection]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise DictionaryUnavailableError(f"离线词典资源不存在：{resolved.name}")
    connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    try:
        yield connection
    finally:
        connection.close()


def _senses(row: sqlite3.Row) -> list[dict[str, str]]:
    translation = str(row["translation"] or "").strip()
    definition = str(row["definition"] or "").strip()
    default_pos = str(row["pos"] or "").strip()
    senses: list[dict[str, str]] = []
    for line in translation.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        match = POS_PREFIX.match(cleaned)
        if match:
            pos = match.group(1)
            gloss_zh = match.group(2).strip()
        else:
            pos = default_pos
            gloss_zh = cleaned
        senses.append(
            {
                "pos": pos,
                "gloss_zh": gloss_zh,
                "gloss_en": definition if not senses else "",
            }
        )
    if not senses and definition:
        senses.append(
            {
                "pos": default_pos,
                "gloss_zh": "",
                "gloss_en": definition,
            }
        )
    return senses


def _entry_payload(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    matched_form: str = "",
    forms: list[dict[str, str]] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    entry_id = int(row["id"])
    if forms is None:
        forms = [
            {
                "kind": item["form_type"],
                "text": item["form_text"],
            }
            for item in connection.execute(
                """
                SELECT form_type, form_text FROM dictionary_forms
                WHERE entry_id = ? ORDER BY form_type, form_text
                """,
                (entry_id,),
            ).fetchall()
        ]
    if tags is None:
        tags = [
            str(item["tag"])
            for item in connection.execute(
                "SELECT tag FROM dictionary_tags WHERE entry_id = ? ORDER BY tag",
                (entry_id,),
            ).fetchall()
        ]
    return {
        "dictionary_key": row["dictionary_key"],
        "lemma": row["lemma"],
        "phonetic_uk": row["phonetic"],
        "phonetic_us": "",
        "pos_senses": _senses(row),
        "forms": forms,
        "relations": [],
        "tags": tags,
        "frequency_rank": int(row["frequency_rank"] or 0),
        "matched_form": matched_form,
    }


def lookup_dictionary(
    term: str,
    *,
    path: Path = BUNDLED_DICTIONARY_PATH,
) -> dict[str, Any]:
    normalized = normalize_term(term)
    if not normalized:
        return {"found": False, "entry": None}
    with dictionary_connection(path) as connection:
        row = connection.execute(
            "SELECT * FROM dictionary_entries WHERE dictionary_key = ?",
            (normalized,),
        ).fetchone()
        matched_form = ""
        if row is None:
            row = connection.execute(
                """
                SELECT e.*
                FROM dictionary_forms AS f
                JOIN dictionary_entries AS e ON e.id = f.entry_id
                WHERE f.normalized_form = ?
                ORDER BY CASE WHEN e.frequency_rank > 0 THEN 0 ELSE 1 END,
                         e.frequency_rank, e.id
                LIMIT 1
                """,
                (normalized,),
            ).fetchone()
            matched_form = term.strip() if row is not None else ""
        if row is None:
            return {"found": False, "entry": None}
        return {
            "found": True,
            "entry": _entry_payload(connection, row, matched_form=matched_form),
        }


def tagged_entries(
    tag: str,
    *,
    path: Path = BUNDLED_DICTIONARY_PATH,
) -> list[dict[str, Any]]:
    if tag not in {"cet4", "cet6", "ky"}:
        raise ValueError("不支持的内置词书标签")
    return [entry for entry in bundled_entries(path=path) if tag in entry["tags"]]


def bundled_entries(
    *,
    path: Path = BUNDLED_DICTIONARY_PATH,
) -> list[dict[str, Any]]:
    """Read the release subset once, avoiding per-entry queries during installation."""

    with dictionary_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT e.*
            FROM dictionary_entries AS e
            JOIN dictionary_tags AS t ON t.entry_id = e.id
            ORDER BY CASE WHEN e.frequency_rank > 0 THEN 0 ELSE 1 END,
                     e.frequency_rank, e.lemma COLLATE NOCASE
            """
        ).fetchall()
        forms_by_entry: dict[int, list[dict[str, str]]] = {}
        for item in connection.execute(
            """
            SELECT entry_id, form_type, form_text
            FROM dictionary_forms ORDER BY entry_id, form_type, form_text
            """
        ).fetchall():
            forms_by_entry.setdefault(int(item["entry_id"]), []).append(
                {"kind": item["form_type"], "text": item["form_text"]}
            )
        tags_by_entry: dict[int, list[str]] = {}
        for item in connection.execute(
            "SELECT entry_id, tag FROM dictionary_tags ORDER BY entry_id, tag"
        ).fetchall():
            tags_by_entry.setdefault(int(item["entry_id"]), []).append(str(item["tag"]))
        return [
            _entry_payload(
                connection,
                row,
                forms=forms_by_entry.get(int(row["id"]), []),
                tags=tags_by_entry.get(int(row["id"]), []),
            )
            for row in rows
        ]


def dictionary_summary(
    *,
    path: Path = BUNDLED_DICTIONARY_PATH,
) -> dict[str, Any]:
    with dictionary_connection(path) as connection:
        metadata = {
            str(row["key"]): str(row["value"])
            for row in connection.execute("SELECT key, value FROM metadata")
        }
        counts = {
            str(row["tag"]): int(row["total"])
            for row in connection.execute(
                """
                SELECT tag, COUNT(*) AS total
                FROM dictionary_tags GROUP BY tag ORDER BY tag
                """
            )
        }
        return {"metadata": metadata, "counts": counts}
