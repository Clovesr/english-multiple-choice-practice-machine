from __future__ import annotations

import argparse
import csv
import hashlib
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path


SOURCE_COMMIT = "bc015ed2e24a7abef49fc6dbbb7fe32c1dadaf8b"
SOURCE_SHA256 = "1a6947e04785db63613a92e14903cdae7954f7e84860b10e68e5c7cbb3f9c3cf"
TARGET_TAGS = ("cet4", "cet6", "ky")
FORM_TYPES = {
    "p": "past",
    "d": "past_participle",
    "i": "present_participle",
    "3": "third_person",
    "s": "plural",
    "r": "comparative",
    "t": "superlative",
    "0": "lemma_variant",
    "1": "derived_form",
}


SCHEMA = """
PRAGMA page_size = 4096;
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;
PRAGMA foreign_keys = ON;

CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE dictionary_entries (
    id INTEGER PRIMARY KEY,
    dictionary_key TEXT NOT NULL UNIQUE,
    lemma TEXT NOT NULL,
    phonetic TEXT NOT NULL DEFAULT '',
    pos TEXT NOT NULL DEFAULT '',
    translation TEXT NOT NULL DEFAULT '',
    definition TEXT NOT NULL DEFAULT '',
    collins INTEGER NOT NULL DEFAULT 0,
    oxford INTEGER NOT NULL DEFAULT 0,
    bnc_rank INTEGER NOT NULL DEFAULT 0,
    frequency_rank INTEGER NOT NULL DEFAULT 0,
    exchange TEXT NOT NULL DEFAULT ''
);

CREATE TABLE dictionary_tags (
    entry_id INTEGER NOT NULL REFERENCES dictionary_entries(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (entry_id, tag)
);
CREATE INDEX idx_dictionary_tags_tag ON dictionary_tags(tag, entry_id);

CREATE TABLE dictionary_forms (
    normalized_form TEXT NOT NULL,
    entry_id INTEGER NOT NULL REFERENCES dictionary_entries(id) ON DELETE CASCADE,
    form_type TEXT NOT NULL,
    form_text TEXT NOT NULL,
    PRIMARY KEY (normalized_form, entry_id, form_type, form_text)
);
CREATE INDEX idx_dictionary_forms_lookup ON dictionary_forms(normalized_form, entry_id);
"""


@dataclass(frozen=True, slots=True)
class Entry:
    dictionary_key: str
    lemma: str
    phonetic: str
    pos: str
    translation: str
    definition: str
    collins: int
    oxford: int
    bnc_rank: int
    frequency_rank: int
    exchange: str
    tags: tuple[str, ...]

    @property
    def quality(self) -> tuple[int, int, int, int]:
        return (
            int(bool(self.translation)) + int(bool(self.definition)),
            int(bool(self.phonetic)) + int(bool(self.pos)),
            len(self.tags),
            int(self.frequency_rank > 0),
        )


def normalize_term(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.strip().casefold().split())


def clean_text(value: str) -> str:
    return str(value or "").replace("\\r", "").replace("\\n", "\n").strip()


def integer(value: str) -> int:
    try:
        return max(0, int(value or 0))
    except ValueError:
        return 0


def source_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_entries(path: Path) -> list[Entry]:
    csv.field_size_limit(16 * 1024 * 1024)
    selected: dict[str, Entry] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        required = {"word", "phonetic", "definition", "translation", "tag", "exchange"}
        if reader.fieldnames is None or not required <= set(reader.fieldnames):
            raise ValueError("ECDICT CSV 缺少必需字段")
        for row in reader:
            tags = tuple(
                tag
                for tag in TARGET_TAGS
                if tag in {value.casefold() for value in str(row.get("tag") or "").split()}
            )
            if not tags:
                continue
            lemma = clean_text(str(row.get("word") or ""))
            key = normalize_term(lemma)
            if not key:
                continue
            bnc_rank = integer(str(row.get("bnc") or ""))
            frq_rank = integer(str(row.get("frq") or ""))
            entry = Entry(
                dictionary_key=key,
                lemma=lemma,
                phonetic=clean_text(str(row.get("phonetic") or "")),
                pos=clean_text(str(row.get("pos") or "")),
                translation=clean_text(str(row.get("translation") or "")),
                definition=clean_text(str(row.get("definition") or "")),
                collins=integer(str(row.get("collins") or "")),
                oxford=integer(str(row.get("oxford") or "")),
                bnc_rank=bnc_rank,
                frequency_rank=bnc_rank or frq_rank,
                exchange=clean_text(str(row.get("exchange") or "")),
                tags=tags,
            )
            current = selected.get(key)
            if current is None or entry.quality > current.quality:
                selected[key] = entry
            elif current is not None and set(tags) - set(current.tags):
                selected[key] = Entry(
                    **{
                        **{
                            field: getattr(current, field)
                            for field in Entry.__dataclass_fields__
                            if field != "tags"
                        },
                        "tags": tuple(tag for tag in TARGET_TAGS if tag in {*current.tags, *tags}),
                    }
                )
    return [selected[key] for key in sorted(selected)]


def exchange_forms(exchange: str) -> list[tuple[str, str, str]]:
    forms: set[tuple[str, str, str]] = set()
    for item in exchange.split("/"):
        code, separator, raw_values = item.partition(":")
        if not separator or code not in FORM_TYPES:
            continue
        for value in raw_values.split(","):
            form_text = clean_text(value)
            normalized = normalize_term(form_text)
            if normalized:
                forms.add((normalized, FORM_TYPES[code], form_text))
    return sorted(forms)


def build_bundle(
    source: Path,
    destination: Path,
    *,
    expected_sha256: str = "",
) -> dict[str, int | str]:
    if destination.suffix.casefold() != ".dbpkg":
        raise ValueError("输出文件必须使用 .dbpkg 后缀")
    checksum = source_checksum(source)
    if expected_sha256 and checksum != expected_sha256:
        raise ValueError(
            f"ECDICT 源文件 SHA-256 不一致：期望 {expected_sha256}，实际 {checksum}"
        )
    entries = read_entries(source)
    if not entries:
        raise ValueError("没有找到 CET4/CET6/考研标签词条")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    connection = sqlite3.connect(destination)
    try:
        connection.executescript(SCHEMA)
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            (
                ("format", "ecdict-essential-v1"),
                ("source_repository", "https://github.com/skywind3000/ECDICT"),
                ("source_commit", SOURCE_COMMIT),
                ("source_sha256", checksum),
                ("license", "MIT"),
                ("tags", "cet4,cet6,ky"),
            ),
        )
        tag_counts = {tag: 0 for tag in TARGET_TAGS}
        form_count = 0
        for entry_id, entry in enumerate(entries, start=1):
            connection.execute(
                """
                INSERT INTO dictionary_entries(
                    id, dictionary_key, lemma, phonetic, pos, translation,
                    definition, collins, oxford, bnc_rank, frequency_rank, exchange
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    entry.dictionary_key,
                    entry.lemma,
                    entry.phonetic,
                    entry.pos,
                    entry.translation,
                    entry.definition,
                    entry.collins,
                    entry.oxford,
                    entry.bnc_rank,
                    entry.frequency_rank,
                    entry.exchange,
                ),
            )
            for tag in entry.tags:
                connection.execute(
                    "INSERT INTO dictionary_tags(entry_id, tag) VALUES (?, ?)",
                    (entry_id, tag),
                )
                tag_counts[tag] += 1
            forms = exchange_forms(entry.exchange)
            connection.executemany(
                """
                INSERT OR IGNORE INTO dictionary_forms(
                    normalized_form, entry_id, form_type, form_text
                ) VALUES (?, ?, ?, ?)
                """,
                ((normalized, entry_id, kind, text) for normalized, kind, text in forms),
            )
            form_count += len(forms)
        connection.execute("ANALYZE")
        connection.commit()
        connection.execute("VACUUM")
        check = connection.execute("PRAGMA quick_check").fetchone()
        if not check or str(check[0]).lower() != "ok":
            raise RuntimeError(f"词典资产 quick_check 失败：{check}")
    except Exception:
        connection.close()
        destination.unlink(missing_ok=True)
        raise
    else:
        connection.close()
    return {
        "entries": len(entries),
        "forms": form_count,
        **tag_counts,
        "source_sha256": checksum,
        "bundle_sha256": source_checksum(destination),
        "size_bytes": destination.stat().st_size,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="构建随包分发的 ECDICT 精简词典")
    parser.add_argument("source", type=Path, help="固定版本的 ecdict.csv")
    parser.add_argument("output", type=Path, help="输出 SQLite 资产路径")
    arguments = parser.parse_args()
    result = build_bundle(
        arguments.source,
        arguments.output,
        expected_sha256=SOURCE_SHA256,
    )
    for key, value in result.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
