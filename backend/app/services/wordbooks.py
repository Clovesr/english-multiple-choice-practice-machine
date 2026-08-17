from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from typing import Any, Iterable
from uuid import NAMESPACE_URL, uuid4, uuid5

from .dictionary import DictionaryUnavailableError, bundled_entries, dictionary_summary
from .vocabulary_cards import generate_cards_for_entry, utc_now
from .vocabulary_learning import create_or_match_imported_entry, upsert_dictionary_entry


BUILTIN_WORDBOOKS = {
    "cet4": "CET4 核心词书",
    "cet6": "CET6 核心词书",
    "ky": "考研英语核心词书",
}


def _plan_payload(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "uuid": row["uuid"],
        "wordbook_id": int(row["wordbook_id"]),
        "mode": row["mode"],
        "daily_new": int(row["daily_new"]),
        "new_order": row["new_order"],
        "exam_date": row["exam_date"],
        "active": bool(row["active"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _state_case(alias: str = "e") -> str:
    return f"""
        CASE
            WHEN {alias}.study_status IN ('known', 'mastered') THEN 'known'
            WHEN {alias}.study_status = 'ignored' THEN 'ignored'
            WHEN {alias}.study_status = 'paused' THEN 'paused'
            WHEN EXISTS (
                SELECT 1
                FROM vocabulary_cards AS vc
                JOIN review_items AS ri
                  ON ri.item_type = 'vocabulary_card' AND ri.ref_id = vc.id
                WHERE vc.entry_id = {alias}.id AND ri.reps > 0
            ) THEN 'learning'
            ELSE 'new'
        END
    """


def wordbook_payload(
    connection: sqlite3.Connection,
    wordbook_id: int,
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM wordbooks WHERE id = ?", (wordbook_id,)
    ).fetchone()
    if row is None:
        raise LookupError("词书不存在")
    state_case = _state_case()
    counts = connection.execute(
        f"""
        SELECT state, COUNT(*) AS total
        FROM (
            SELECT {state_case} AS state
            FROM wordbook_entries AS we
            JOIN vocabulary_entries AS e ON e.id = we.entry_id
            WHERE we.wordbook_id = ? AND e.deleted_at IS NULL
        )
        GROUP BY state
        """,
        (wordbook_id,),
    ).fetchall()
    state_counts = {key: 0 for key in ("new", "learning", "known", "ignored", "paused")}
    state_counts.update({str(item["state"]): int(item["total"]) for item in counts})
    plan = connection.execute(
        """
        SELECT * FROM study_plans
        WHERE wordbook_id = ? AND active = 1
        ORDER BY id DESC LIMIT 1
        """,
        (wordbook_id,),
    ).fetchone()
    return {
        "id": int(row["id"]),
        "uuid": row["uuid"],
        "name": row["name"],
        "kind": row["kind"],
        "source_tag": row["source_tag"],
        "total": sum(state_counts.values()),
        "state_counts": state_counts,
        "active_plan": _plan_payload(plan),
        "source_name": row["source_name"],
        "source_version": row["source_version"],
        "license": row["license"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def install_bundled_wordbooks(connection: sqlite3.Connection) -> dict[str, Any]:
    """Idempotently materialize the three bundled dictionary tags in user data."""

    try:
        summary = dictionary_summary()
    except DictionaryUnavailableError:
        return {"available": False, "installed": 0, "entries": 0}
    metadata = summary["metadata"]
    checksum = str(metadata.get("source_sha256") or "")
    source_version = str(metadata.get("source_commit") or "")
    installed = 0
    entry_ids: dict[str, int] = {}
    try:
        all_entries = bundled_entries()
        entries_by_tag = {
            tag: [entry for entry in all_entries if tag in entry["tags"]]
            for tag in BUILTIN_WORDBOOKS
        }
        for tag, name in BUILTIN_WORDBOOKS.items():
            expected = int(summary["counts"].get(tag, 0))
            wordbook = connection.execute(
                """
                SELECT * FROM wordbooks
                WHERE kind = 'builtin' AND source_tag = ?
                ORDER BY id LIMIT 1
                """,
                (tag,),
            ).fetchone()
            if wordbook is not None:
                actual = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM wordbook_entries WHERE wordbook_id = ?",
                        (wordbook["id"],),
                    ).fetchone()[0]
                )
                if wordbook["checksum"] == checksum and actual == expected:
                    continue
            timestamp = utc_now()
            if wordbook is None:
                cursor = connection.execute(
                    """
                    INSERT INTO wordbooks(
                        uuid, name, kind, source_tag, source_name, source_version,
                        license, checksum, status, created_at, updated_at
                    ) VALUES (?, ?, 'builtin', ?, 'ECDICT', ?, 'MIT', ?, 'active', ?, ?)
                    """,
                    (
                        str(uuid5(NAMESPACE_URL, f"wenqu:ecdict:{source_version}:{tag}")),
                        name,
                        tag,
                        source_version,
                        checksum,
                        timestamp,
                        timestamp,
                    ),
                )
                wordbook_id = int(cursor.lastrowid)
            else:
                wordbook_id = int(wordbook["id"])
                connection.execute(
                    """
                    UPDATE wordbooks
                    SET name = ?, source_version = ?, license = 'MIT', checksum = ?,
                        status = 'active', updated_at = ?
                    WHERE id = ?
                    """,
                    (name, source_version, checksum, timestamp, wordbook_id),
                )
                connection.execute(
                    "DELETE FROM wordbook_entries WHERE wordbook_id = ?",
                    (wordbook_id,),
                )
            for sequence, dictionary_entry in enumerate(entries_by_tag[tag], start=1):
                key = str(dictionary_entry["dictionary_key"])
                entry_id = entry_ids.get(key)
                if entry_id is None:
                    entry_id, _ = upsert_dictionary_entry(
                        connection,
                        dictionary_entry,
                        source_kind="builtin_wordbook",
                    )
                    entry_ids[key] = entry_id
                connection.execute(
                    """
                    INSERT OR IGNORE INTO wordbook_entries(
                        wordbook_id, entry_id, sequence, frequency_rank, added_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        wordbook_id,
                        entry_id,
                        sequence,
                        int(dictionary_entry.get("frequency_rank") or 0) or None,
                        timestamp,
                    ),
                )
            installed += 1
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {
        "available": True,
        "installed": installed,
        "entries": len(entry_ids),
        "source_version": source_version,
        "source_sha256": checksum,
    }


def list_wordbooks(connection: sqlite3.Connection) -> dict[str, Any]:
    install_bundled_wordbooks(connection)
    ids = [
        int(row["id"])
        for row in connection.execute(
            "SELECT id FROM wordbooks WHERE status = 'active' ORDER BY kind, id"
        ).fetchall()
    ]
    return {"items": [wordbook_payload(connection, wordbook_id) for wordbook_id in ids]}


def list_wordbook_entries(
    connection: sqlite3.Connection,
    wordbook_id: int,
    *,
    offset: int,
    limit: int,
    state: str = "",
) -> dict[str, Any]:
    if connection.execute(
        "SELECT 1 FROM wordbooks WHERE id = ?", (wordbook_id,)
    ).fetchone() is None:
        raise LookupError("词书不存在")
    state_case = _state_case()
    condition = ""
    parameters: list[Any] = [wordbook_id]
    if state:
        if state not in {"new", "learning", "known", "ignored", "paused"}:
            raise ValueError("不支持的词条状态")
        condition = f"AND ({state_case}) = ?"
        parameters.append(state)
    total = int(
        connection.execute(
            f"""
            SELECT COUNT(*)
            FROM wordbook_entries AS we
            JOIN vocabulary_entries AS e ON e.id = we.entry_id
            WHERE we.wordbook_id = ? AND e.deleted_at IS NULL {condition}
            """,
            parameters,
        ).fetchone()[0]
    )
    rows = connection.execute(
        f"""
        SELECT e.id, e.uuid, e.term, e.lemma, e.phonetic_uk, e.phonetic_us,
               e.part_of_speech, e.common_meaning, e.enrichment_status,
               we.sequence, we.frequency_rank, {state_case} AS state
        FROM wordbook_entries AS we
        JOIN vocabulary_entries AS e ON e.id = we.entry_id
        WHERE we.wordbook_id = ? AND e.deleted_at IS NULL {condition}
        ORDER BY we.sequence, e.id
        LIMIT ? OFFSET ?
        """,
        (*parameters, limit, offset),
    ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def activate_wordbook_plan(
    connection: sqlite3.Connection,
    wordbook_id: int,
    *,
    daily_new: int,
    new_order: str,
) -> dict[str, Any]:
    if new_order not in {"frequency", "sequence", "random"}:
        raise ValueError("new_order 必须是 frequency、sequence 或 random")
    if not 1 <= daily_new <= 500:
        raise ValueError("daily_new 必须在 1 到 500 之间")
    install_bundled_wordbooks(connection)
    if connection.execute(
        "SELECT 1 FROM wordbooks WHERE id = ? AND status = 'active'", (wordbook_id,)
    ).fetchone() is None:
        raise LookupError("词书不存在")
    wordbook_kind = str(
        connection.execute(
            "SELECT kind FROM wordbooks WHERE id = ?", (wordbook_id,)
        ).fetchone()["kind"]
    )
    timestamp = utc_now()
    try:
        connection.execute("UPDATE study_plans SET active = 0, updated_at = ? WHERE active = 1", (timestamp,))
        plan = connection.execute(
            """
            SELECT id FROM study_plans
            WHERE wordbook_id = ? AND mode = 'normal'
            ORDER BY id DESC LIMIT 1
            """,
            (wordbook_id,),
        ).fetchone()
        if plan is None:
            cursor = connection.execute(
                """
                INSERT INTO study_plans(
                    uuid, wordbook_id, mode, daily_new, new_order, active,
                    created_at, updated_at
                ) VALUES (?, ?, 'normal', ?, ?, 1, ?, ?)
                """,
                (str(uuid4()), wordbook_id, daily_new, new_order, timestamp, timestamp),
            )
            plan_id = int(cursor.lastrowid)
        else:
            plan_id = int(plan["id"])
            connection.execute(
                """
                UPDATE study_plans
                SET daily_new = ?, new_order = ?, exam_date = NULL,
                    active = 1, updated_at = ?
                WHERE id = ?
                """,
                (daily_new, new_order, timestamp, plan_id),
            )
        entry_ids = [
            int(row["entry_id"])
            for row in connection.execute(
                """
                SELECT entry_id FROM wordbook_entries
                WHERE wordbook_id = ? ORDER BY sequence
                """,
                (wordbook_id,),
            ).fetchall()
        ]
        generated = 0
        for entry_id in entry_ids:
            generated += len(
                generate_cards_for_entry(
                    connection,
                    entry_id,
                    generation_source=(
                        "builtin_wordbook"
                        if wordbook_kind == "builtin"
                        else "imported_wordbook"
                    ),
                )
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    plan_row = connection.execute("SELECT * FROM study_plans WHERE id = ?", (plan_id,)).fetchone()
    return {
        "wordbook": wordbook_payload(connection, wordbook_id),
        "plan": _plan_payload(plan_row),
        "generated_cards": generated,
    }


def deactivate_wordbook_plan(
    connection: sqlite3.Connection,
    wordbook_id: int,
) -> dict[str, Any]:
    if connection.execute(
        "SELECT 1 FROM wordbooks WHERE id = ?", (wordbook_id,)
    ).fetchone() is None:
        raise LookupError("词书不存在")
    connection.execute(
        "UPDATE study_plans SET active = 0, updated_at = ? WHERE wordbook_id = ? AND active = 1",
        (utc_now(), wordbook_id),
    )
    connection.commit()
    return wordbook_payload(connection, wordbook_id)


def parse_wordbook_text(raw: bytes, filename: str) -> list[tuple[str, str]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gb18030")
        except UnicodeDecodeError as error:
            raise ValueError("词表文件必须是 UTF-8 或 GBK/GB18030 编码") from error
    rows: list[tuple[str, str]] = []
    if filename.casefold().endswith(".csv"):
        iterator: Iterable[list[str]] = csv.reader(io.StringIO(text))
    else:
        iterator = (line.split("\t", 1) for line in text.splitlines())
    for index, values in enumerate(iterator):
        if not values:
            continue
        term = str(values[0]).strip()
        meaning = str(values[1]).strip() if len(values) > 1 else ""
        if index == 0 and term.casefold() in {"word", "term", "单词", "词汇"}:
            continue
        if term:
            rows.append((term, meaning))
    return rows


def import_wordbook(
    connection: sqlite3.Connection,
    *,
    name: str,
    terms: list[tuple[str, str]],
) -> dict[str, Any]:
    cleaned_name = " ".join(name.strip().split())
    if not cleaned_name:
        raise ValueError("词书名称不能为空")
    if not terms:
        raise ValueError("词表中没有可导入的词")
    timestamp = utc_now()
    digest = hashlib.sha256(
        json.dumps(terms, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    try:
        cursor = connection.execute(
            """
            INSERT INTO wordbooks(
                uuid, name, kind, source_name, source_version, license,
                checksum, status, created_at, updated_at
            ) VALUES (?, ?, 'imported', 'user_import', '1', '', ?, 'active', ?, ?)
            """,
            (str(uuid4()), cleaned_name[:200], digest, timestamp, timestamp),
        )
        wordbook_id = int(cursor.lastrowid)
        matched = 0
        unmatched: list[dict[str, str]] = []
        seen_entries: set[int] = set()
        for sequence, (term, meaning) in enumerate(terms, start=1):
            entry_id, _, found = create_or_match_imported_entry(
                connection,
                term,
                meaning=meaning,
            )
            if entry_id in seen_entries:
                continue
            seen_entries.add(entry_id)
            connection.execute(
                """
                INSERT INTO wordbook_entries(
                    wordbook_id, entry_id, sequence, added_at
                ) VALUES (?, ?, ?, ?)
                """,
                (wordbook_id, entry_id, sequence, timestamp),
            )
            cards = generate_cards_for_entry(
                connection,
                entry_id,
                generation_source="imported_wordbook",
            )
            if found:
                matched += 1
            else:
                unmatched.append(
                    {
                        "term": term,
                        "status": "needs_enrichment",
                    }
                )
                if not meaning and cards:
                    raise RuntimeError("数据不足的未匹配词不应生成空白卡片")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {
        "wordbook": wordbook_payload(connection, wordbook_id),
        "matched": matched,
        "unmatched": unmatched,
    }


def update_entry_state(
    connection: sqlite3.Connection,
    entry_id: int,
    study_status: str,
) -> dict[str, Any]:
    if study_status not in {"known", "learning", "ignored", "paused", "focus"}:
        raise ValueError("不支持的学习状态")
    row = connection.execute(
        "SELECT id FROM vocabulary_entries WHERE id = ? AND deleted_at IS NULL",
        (entry_id,),
    ).fetchone()
    if row is None:
        raise LookupError("单词不存在")
    connection.execute(
        "UPDATE vocabulary_entries SET study_status = ?, updated_at = ? WHERE id = ?",
        (study_status, utc_now(), entry_id),
    )
    connection.commit()
    return {"id": entry_id, "study_status": study_status}
