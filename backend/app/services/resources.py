from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .. import database as database_module
from ..database import new_trash_batch
from .learning import record_learning_event


MAX_RESOURCE_BYTES = 20 * 1024 * 1024
RESOURCE_STATUSES = {"inbox", "active", "archived", "needs_review"}


class ResourceError(ValueError):
    pass


class DuplicateResourceError(ResourceError):
    def __init__(self, existing_id: int) -> None:
        super().__init__("该资源已经导入")
        self.existing_id = existing_id


class ResourceNotFoundError(ResourceError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _storage_root() -> Path:
    database_path = Path(database_module.DATABASE_PATH)
    root = database_path.parent / "resources"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _decode_text(raw: bytes) -> tuple[str, str]:
    if b"\x00" in raw:
        raise ResourceError("文件包含二进制空字节，无法作为文本解析")
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        controls = sum(
            1 for character in text if ord(character) < 32 and character not in "\r\n\t"
        )
        if controls > max(2, len(text) // 100):
            raise ResourceError("文件包含过多控制字符，可能不是有效文本")
        return text.replace("\r\n", "\n").replace("\r", "\n"), encoding
    raise ResourceError("仅支持 UTF-8 或 GBK/GB18030 文本")


def _plain_segments(text: str) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", text):
        content = block.strip()
        if content:
            segments.append({"kind": "paragraph", "heading_level": 0, "content": content})
    return segments


def _markdown_segments(text: str) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    paragraph: list[str] = []
    fenced: list[str] = []
    in_fence = False

    def flush_paragraph() -> None:
        content = "\n".join(paragraph).strip()
        if content:
            segments.append({"kind": "paragraph", "heading_level": 0, "content": content})
        paragraph.clear()

    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            flush_paragraph()
            fenced.append(line)
            in_fence = not in_fence
            if not in_fence:
                segments.append(
                    {"kind": "paragraph", "heading_level": 0, "content": "\n".join(fenced)}
                )
                fenced.clear()
            continue
        if in_fence:
            fenced.append(line)
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            flush_paragraph()
            segments.append(
                {
                    "kind": "heading",
                    "heading_level": len(heading.group(1)),
                    "content": heading.group(2),
                }
            )
        elif not line.strip():
            flush_paragraph()
        else:
            paragraph.append(line)
    if fenced:
        paragraph.extend(fenced)
    flush_paragraph()
    return segments


def _resource_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload.pop("stored_path", None)
    progress_updated = payload.pop("progress_updated_at", None)
    progress = None
    if progress_updated is not None:
        progress = {
            "last_segment_id": payload.pop("last_segment_id", None),
            "scroll_ratio": payload.pop("scroll_ratio", 0),
            "total_reading_ms": payload.pop("total_reading_ms", 0),
            "opened_count": payload.pop("opened_count", 0),
            "last_opened_at": payload.pop("last_opened_at", None),
            "updated_at": progress_updated,
        }
    else:
        for key in (
            "last_segment_id",
            "scroll_ratio",
            "total_reading_ms",
            "opened_count",
            "last_opened_at",
        ):
            payload.pop(key, None)
    payload["progress"] = progress
    return payload


RESOURCE_SELECT = """
SELECT r.*, p.last_segment_id, p.scroll_ratio, p.total_reading_ms,
       p.opened_count, p.last_opened_at, p.updated_at AS progress_updated_at
FROM resources AS r
LEFT JOIN resource_progress AS p ON p.resource_id = r.id
"""


def create_resource(
    connection: sqlite3.Connection,
    *,
    raw: bytes,
    filename: str,
    title: str = "",
    resource_type: str = "article",
    source: str = "",
    source_url: str = "",
    author: str = "",
    language: str = "en",
    requested_format: str = "",
) -> dict[str, Any]:
    if not raw:
        raise ResourceError("资源内容不能为空")
    if len(raw) > MAX_RESOURCE_BYTES:
        raise ResourceError("资源超过 20 MB 的 V1 导入上限")
    safe_filename = Path(filename or "resource.txt").name
    suffix = Path(safe_filename).suffix.lower()
    resource_format = requested_format.lower().strip() or suffix.lstrip(".")
    if resource_format not in {"txt", "md"}:
        raise ResourceError("V1 仅支持 TXT 和 Markdown 资源")
    checksum = hashlib.sha256(raw).hexdigest()
    duplicate = connection.execute(
        "SELECT id FROM resources WHERE checksum = ? AND deleted_at IS NULL",
        (checksum,),
    ).fetchone()
    if duplicate:
        raise DuplicateResourceError(int(duplicate["id"]))

    parse_error = ""
    encoding = ""
    segments: list[dict[str, Any]] = []
    try:
        text, encoding = _decode_text(raw)
        segments = (
            _markdown_segments(text) if resource_format == "md" else _plain_segments(text)
        )
    except ResourceError as error:
        parse_error = str(error)

    resource_uuid = str(uuid4())
    extension = ".md" if resource_format == "md" else ".txt"
    folder = _storage_root() / resource_uuid
    folder.mkdir(parents=False, exist_ok=False)
    original_path = folder / f"original{extension}"
    try:
        original_path.write_bytes(raw)
        now = _utc_now()
        cursor = connection.execute(
            """
            INSERT INTO resources(
                uuid, title, type, language, format, status, source, source_url,
                author, checksum, original_filename, stored_path, media_type,
                size_bytes, segment_count, parser_name, parser_version,
                parse_error, imported_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                resource_uuid,
                (title.strip() or Path(safe_filename).stem or "未命名资源")[:300],
                resource_type.strip() or "article",
                language.strip() or "en",
                resource_format,
                "needs_review" if parse_error else "inbox",
                source.strip(),
                source_url.strip(),
                author.strip(),
                checksum,
                safe_filename,
                original_path.relative_to(Path(database_module.DATABASE_PATH).parent).as_posix(),
                "text/markdown" if resource_format == "md" else "text/plain",
                len(raw),
                len(segments),
                f"builtin-{resource_format}:{encoding}" if encoding else "builtin-text",
                parse_error,
                now,
                now,
                now,
            ),
        )
        resource_id = int(cursor.lastrowid)
        for sequence, segment in enumerate(segments, start=1):
            connection.execute(
                """
                INSERT INTO resource_segments(
                    resource_id, sequence, kind, heading_level, content, metadata
                ) VALUES (?, ?, ?, ?, ?, '{}')
                """,
                (
                    resource_id,
                    sequence,
                    segment["kind"],
                    segment["heading_level"],
                    segment["content"],
                ),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        original_path.unlink(missing_ok=True)
        try:
            folder.rmdir()
        except OSError:
            pass
        raise
    return get_resource(connection, resource_id)


def list_resources(
    connection: sqlite3.Connection,
    *,
    status: str = "",
    resource_type: str = "",
    query: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    conditions = ["r.deleted_at IS NULL"]
    params: list[Any] = []
    if status:
        if status not in RESOURCE_STATUSES:
            raise ResourceError("无效的资源状态")
        conditions.append("r.status = ?")
        params.append(status)
    if resource_type:
        conditions.append("r.type = ?")
        params.append(resource_type)
    if query.strip():
        conditions.append("r.title LIKE ? ESCAPE '\\'")
        escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{escaped}%")
    where = " AND ".join(conditions)
    total = int(
        connection.execute(
            f"SELECT COUNT(*) FROM resources AS r WHERE {where}", params
        ).fetchone()[0]
    )
    rows = connection.execute(
        f"{RESOURCE_SELECT} WHERE {where} ORDER BY r.updated_at DESC, r.id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return {
        "items": [_resource_payload(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_resource(connection: sqlite3.Connection, resource_id: int) -> dict[str, Any]:
    row = connection.execute(
        f"{RESOURCE_SELECT} WHERE r.id = ? AND r.deleted_at IS NULL",
        (resource_id,),
    ).fetchone()
    if row is None:
        raise ResourceNotFoundError("资源不存在或已在回收站")
    return _resource_payload(row)


def update_resource(
    connection: sqlite3.Connection,
    resource_id: int,
    changes: dict[str, Any],
) -> dict[str, Any]:
    get_resource(connection, resource_id)
    allowed = {"title", "status", "source", "author", "language"}
    values = {key: value for key, value in changes.items() if key in allowed and value is not None}
    if "status" in values and values["status"] not in RESOURCE_STATUSES:
        raise ResourceError("无效的资源状态")
    if "title" in values and not str(values["title"]).strip():
        raise ResourceError("资源标题不能为空")
    if values:
        values["updated_at"] = _utc_now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        connection.execute(
            f"UPDATE resources SET {assignments} WHERE id = ?",
            [*values.values(), resource_id],
        )
        connection.commit()
    return get_resource(connection, resource_id)


def list_segments(
    connection: sqlite3.Connection,
    resource_id: int,
    *,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    get_resource(connection, resource_id)
    total = int(
        connection.execute(
            "SELECT COUNT(*) FROM resource_segments WHERE resource_id = ?",
            (resource_id,),
        ).fetchone()[0]
    )
    rows = connection.execute(
        """
        SELECT id, resource_id, sequence, kind, heading_level, content, metadata
        FROM resource_segments WHERE resource_id = ?
        ORDER BY sequence LIMIT ? OFFSET ?
        """,
        (resource_id, limit, offset),
    ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def save_progress(
    connection: sqlite3.Connection,
    resource_id: int,
    *,
    last_segment_id: int | None,
    scroll_ratio: float,
    reading_ms_delta: int,
) -> dict[str, Any]:
    get_resource(connection, resource_id)
    if last_segment_id is not None:
        segment = connection.execute(
            "SELECT 1 FROM resource_segments WHERE id = ? AND resource_id = ?",
            (last_segment_id, resource_id),
        ).fetchone()
        if segment is None:
            raise ResourceError("阅读位置不属于该资源")
    now = _utc_now()
    connection.execute(
        """
        INSERT INTO resource_progress(
            resource_id, last_segment_id, scroll_ratio, total_reading_ms,
            opened_count, last_opened_at, updated_at
        ) VALUES (?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(resource_id) DO UPDATE SET
            last_segment_id = COALESCE(
                excluded.last_segment_id,
                resource_progress.last_segment_id
            ),
            scroll_ratio = excluded.scroll_ratio,
            total_reading_ms = resource_progress.total_reading_ms + excluded.total_reading_ms,
            last_opened_at = excluded.last_opened_at,
            updated_at = excluded.updated_at
        """,
        (resource_id, last_segment_id, scroll_ratio, reading_ms_delta, now, now),
    )
    if reading_ms_delta:
        record_learning_event(
            connection,
            verb="read",
            object_type="resource",
            object_id=resource_id,
            result={
                "last_segment_id": last_segment_id,
                "scroll_ratio": scroll_ratio,
            },
            duration_ms=reading_ms_delta,
            occurred_at=now,
        )
    connection.commit()
    return get_resource(connection, resource_id)["progress"]


def trash_resource(connection: sqlite3.Connection, resource_id: int) -> None:
    resource = get_resource(connection, resource_id)
    batch_id, purge_after = new_trash_batch()
    now = _utc_now()
    connection.execute(
        "UPDATE resources SET deleted_at = ?, updated_at = ? WHERE id = ?",
        (now, now, resource_id),
    )
    connection.execute(
        """
        INSERT INTO trash_entries(
            deletion_batch_id, resource_type, resource_id, resource_name,
            metadata, deleted_at, purge_after
        ) VALUES (?, 'resource', ?, ?, ?, ?, ?)
        """,
        (
            batch_id,
            resource_id,
            resource["title"],
            json.dumps({"previous_status": resource["status"]}, ensure_ascii=False),
            now,
            purge_after,
        ),
    )
    connection.commit()


def _marked_snippet(value: str, start_marker: str = "\x01", end_marker: str = "\x02") -> str:
    return html.escape(value).replace(start_marker, "<mark>").replace(end_marker, "</mark>")


def _like_snippet(content: str, query: str, radius: int = 80) -> str:
    position = content.casefold().find(query.casefold())
    if position < 0:
        return html.escape(content[: radius * 2])
    start = max(0, position - radius)
    end = min(len(content), position + len(query) + radius)
    prefix = "…" if start else ""
    suffix = "…" if end < len(content) else ""
    before = html.escape(content[start:position])
    match = html.escape(content[position : position + len(query)])
    after = html.escape(content[position + len(query) : end])
    return f"{prefix}{before}<mark>{match}</mark>{after}{suffix}"


def search_resources(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    needle = query.strip()
    if not needle:
        raise ResourceError("搜索词不能为空")
    if len(needle) >= 3:
        fts_query = f'"{needle.replace(chr(34), chr(34) * 2)}"'
        base = """
        FROM resource_segments_fts AS f
        JOIN resource_segments AS s ON s.id = f.rowid
        JOIN resources AS r ON r.id = s.resource_id
        WHERE resource_segments_fts MATCH ? AND r.deleted_at IS NULL
        """
        total = int(connection.execute(f"SELECT COUNT(*) {base}", (fts_query,)).fetchone()[0])
        rows = connection.execute(
            f"""
            SELECT r.id AS resource_id, r.title AS resource_title,
                   s.id AS segment_id, s.sequence,
                   snippet(resource_segments_fts, 0, char(1), char(2), '…', 24) AS snippet
            {base}
            ORDER BY rank LIMIT ? OFFSET ?
            """,
            (fts_query, limit, offset),
        ).fetchall()
        items = [
            {**dict(row), "snippet": _marked_snippet(str(row["snippet"] or ""))}
            for row in rows
        ]
    else:
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        base = """
        FROM resource_segments AS s
        JOIN resources AS r ON r.id = s.resource_id
        WHERE s.content LIKE ? ESCAPE '\\' AND r.deleted_at IS NULL
        """
        total = int(connection.execute(f"SELECT COUNT(*) {base}", (like,)).fetchone()[0])
        rows = connection.execute(
            f"""
            SELECT r.id AS resource_id, r.title AS resource_title,
                   s.id AS segment_id, s.sequence, s.content
            {base}
            ORDER BY r.updated_at DESC, s.sequence LIMIT ? OFFSET ?
            """,
            (like, limit, offset),
        ).fetchall()
        items = [
            {
                "resource_id": row["resource_id"],
                "resource_title": row["resource_title"],
                "segment_id": row["segment_id"],
                "sequence": row["sequence"],
                "snippet": _like_snippet(str(row["content"]), needle),
            }
            for row in rows
        ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def rebuild_search(connection: sqlite3.Connection) -> int:
    connection.execute(
        "INSERT INTO resource_segments_fts(resource_segments_fts) VALUES ('rebuild')"
    )
    connection.commit()
    return int(connection.execute("SELECT COUNT(*) FROM resource_segments").fetchone()[0])
