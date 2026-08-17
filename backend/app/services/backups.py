from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from .. import database as database_module
from ..migrations import MIGRATIONS
from .learning import record_learning_event, utc_now


APP_VERSION = "0.1.0"
BACKUP_FORMAT_VERSION = 1
ASSET_DIRECTORIES = ("resources", "question_banks", "uploads")
COUNT_TABLES = (
    "resources",
    "resource_segments",
    "vocabulary_entries",
    "vocabulary_cards",
    "review_items",
    "review_logs",
    "practice_sessions",
    "practice_answers",
    "wrong_stats",
    "learning_events",
    "daily_tasks",
)


class BackupError(ValueError):
    pass


class BackupNotFoundError(BackupError):
    pass


class BackupCorruptError(BackupError):
    pass


def _data_root() -> Path:
    return Path(database_module.DATABASE_PATH).resolve().parent


def _backup_root() -> Path:
    root = _data_root() / "backups" / "catalog"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()
    return int(row[0]) if row else 0


def _object_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in COUNT_TABLES
    }


def _catalog_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "path": str(row["path"]),
        "kind": str(row["kind"]),
        "checksum": str(row["checksum"]),
        "size_bytes": int(row["size_bytes"]),
        "app_version": str(row["app_version"]),
        "schema_version": int(row["schema_version"]),
        "status": str(row["status"]),
        "created_at": str(row["created_at"]),
    }


def _catalog_row(connection: sqlite3.Connection, backup_id: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM backup_catalog WHERE id = ?", (backup_id,)
    ).fetchone()
    if row is None:
        raise BackupNotFoundError("备份记录不存在")
    return row


def _resolve_catalog_path(raw: str) -> Path:
    relative = Path(raw)
    if relative.is_absolute():
        raise BackupCorruptError("备份目录记录不是可迁移的相对路径")
    root = _backup_root().resolve()
    candidate = (_data_root() / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise BackupCorruptError("备份文件路径越过了备份目录边界") from error
    return candidate


def _archive_name(path: Path) -> str:
    return PurePosixPath("data", *path.relative_to(_data_root()).parts).as_posix()


def _write_snapshot(connection: sqlite3.Connection, destination: Path) -> None:
    connection.commit()
    snapshot = sqlite3.connect(destination)
    try:
        connection.backup(snapshot)
        result = snapshot.execute("PRAGMA quick_check").fetchone()
        if not result or str(result[0]).lower() != "ok":
            raise BackupError(f"数据库快照校验失败：{result}")
        violations = snapshot.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise BackupError(f"数据库快照存在外键错误：{violations[:3]}")
    finally:
        snapshot.close()


def _asset_files() -> list[Path]:
    data_root = _data_root()
    files: list[Path] = []
    for directory_name in ASSET_DIRECTORIES:
        root = (data_root / directory_name).resolve()
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            resolved = path.resolve()
            try:
                resolved.relative_to(root)
            except ValueError as error:
                raise BackupError(f"用户数据文件越过目录边界：{path}") from error
            files.append(resolved)
    return files


def create_backup(
    connection: sqlite3.Connection,
    *,
    kind: str = "manual",
    reason: str = "manual",
) -> dict[str, Any]:
    if kind not in {"manual", "pre_migration", "daily"}:
        raise BackupError("不支持的备份类型")
    root = _backup_root()
    created_at = utc_now()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"backup-{stamp}-{uuid4().hex[:10]}.zip"
    destination = root / filename
    partial = root / f".{filename}.partial"
    try:
        with tempfile.TemporaryDirectory(prefix="build-", dir=root) as temp_name:
            temp = Path(temp_name)
            database_snapshot = temp / "database.sqlite3"
            _write_snapshot(connection, database_snapshot)
            files = _asset_files()
            file_manifest = [
                {
                    "path": _archive_name(path),
                    "checksum": _sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
                for path in files
            ]
            manifest = {
                "format_version": BACKUP_FORMAT_VERSION,
                "created_at": created_at,
                "app_version": APP_VERSION,
                "schema_version": _schema_version(connection),
                "reason": reason,
                "database": {
                    "path": "database.sqlite3",
                    "checksum": _sha256_file(database_snapshot),
                    "size_bytes": database_snapshot.stat().st_size,
                },
                "files": file_manifest,
                "object_counts": _object_counts(connection),
            }
            with zipfile.ZipFile(
                partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps(
                        manifest,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8"),
                )
                archive.write(database_snapshot, "database.sqlite3")
                for path in files:
                    archive.write(path, _archive_name(path))
            os.replace(partial, destination)
        checksum = _sha256_file(destination)
        relative_path = destination.relative_to(_data_root()).as_posix()
        cursor = connection.execute(
            """
            INSERT INTO backup_catalog(
                path, kind, checksum, size_bytes, app_version,
                schema_version, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'ok', ?)
            """,
            (
                relative_path,
                kind,
                checksum,
                destination.stat().st_size,
                APP_VERSION,
                int(manifest["schema_version"]),
                created_at,
            ),
        )
        backup_id = int(cursor.lastrowid)
        record_learning_event(
            connection,
            verb="backup",
            object_type="backup",
            object_id=backup_id,
            result={"kind": kind, "reason": reason},
            occurred_at=created_at,
        )
        connection.commit()
        return _catalog_payload(_catalog_row(connection, backup_id))
    except Exception:
        connection.rollback()
        partial.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise


def list_backups(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT * FROM backup_catalog ORDER BY created_at DESC, id DESC"
    ).fetchall()
    return {"items": [_catalog_payload(row) for row in rows]}


def _safe_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members: dict[str, zipfile.ZipInfo] = {}
    total_size = 0
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts or name.startswith("/"):
            raise BackupCorruptError(f"备份包含不安全路径：{info.filename}")
        if info.is_dir():
            continue
        if name in members:
            raise BackupCorruptError(f"备份包含重复文件：{name}")
        total_size += info.file_size
        if total_size > 4 * 1024 * 1024 * 1024:
            raise BackupCorruptError("备份解压后超过 4 GB 安全上限")
        members[name] = info
    return members


def _copy_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    destination: Path,
) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with archive.open(info) as source, destination.open("wb") as target:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            target.write(chunk)
    return digest.hexdigest()


def _read_manifest(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
) -> dict[str, Any]:
    manifest_info = members.get("manifest.json")
    if manifest_info is None or manifest_info.file_size > 2 * 1024 * 1024:
        raise BackupCorruptError("备份缺少有效 manifest.json")
    try:
        manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as error:
        raise BackupCorruptError("备份清单无法解析") from error
    if not isinstance(manifest, dict) or manifest.get("format_version") != 1:
        raise BackupCorruptError("备份格式版本不受支持")
    if not isinstance(manifest.get("database"), dict):
        raise BackupCorruptError("备份清单缺少数据库信息")
    if not isinstance(manifest.get("files"), list):
        raise BackupCorruptError("备份清单缺少用户文件列表")
    if not isinstance(manifest.get("object_counts"), dict):
        raise BackupCorruptError("备份清单缺少对象计数")
    return manifest


def _inspect_package(
    path: Path,
    *,
    expected_checksum: str,
    extract_to: Path | None = None,
) -> dict[str, Any]:
    if not path.is_file():
        raise BackupCorruptError("备份文件不存在")
    if _sha256_file(path) != expected_checksum:
        raise BackupCorruptError("备份包 checksum 不一致")
    if not zipfile.is_zipfile(path):
        raise BackupCorruptError("备份包不是有效 ZIP 文件")
    work_context = (
        tempfile.TemporaryDirectory(prefix="verify-", dir=_backup_root())
        if extract_to is None
        else None
    )
    work = Path(work_context.name) if work_context is not None else extract_to
    assert work is not None
    try:
        with zipfile.ZipFile(path) as archive:
            members = _safe_members(archive)
            manifest = _read_manifest(archive, members)
            database_info = manifest["database"]
            database_name = str(database_info.get("path") or "")
            if database_name != "database.sqlite3" or database_name not in members:
                raise BackupCorruptError("备份缺少数据库快照")
            database_path = work / "database.sqlite3"
            actual_database_checksum = _copy_member(
                archive, members[database_name], database_path
            )
            if actual_database_checksum != str(database_info.get("checksum") or ""):
                raise BackupCorruptError("数据库快照 checksum 不一致")

            declared_files: set[str] = set()
            for raw_entry in manifest["files"]:
                if not isinstance(raw_entry, dict):
                    raise BackupCorruptError("用户文件清单格式错误")
                name = str(raw_entry.get("path") or "").replace("\\", "/")
                pure = PurePosixPath(name)
                if (
                    pure.is_absolute()
                    or ".." in pure.parts
                    or len(pure.parts) < 2
                    or pure.parts[0] != "data"
                    or pure.parts[1] not in ASSET_DIRECTORIES
                ):
                    raise BackupCorruptError(f"用户文件路径不安全：{name}")
                info = members.get(name)
                if info is None or name in declared_files:
                    raise BackupCorruptError(f"用户文件缺失或重复：{name}")
                declared_files.add(name)
                extracted = work / Path(*pure.parts)
                checksum = _copy_member(archive, info, extracted)
                if checksum != str(raw_entry.get("checksum") or ""):
                    raise BackupCorruptError(f"用户文件 checksum 不一致：{name}")
            allowed = {"manifest.json", "database.sqlite3", *declared_files}
            unexpected = set(members) - allowed
            if unexpected:
                raise BackupCorruptError(
                    f"备份包含未登记文件：{sorted(unexpected)[0]}"
                )

        snapshot = sqlite3.connect(database_path)
        try:
            quick = snapshot.execute("PRAGMA quick_check").fetchone()
            if not quick or str(quick[0]).lower() != "ok":
                raise BackupCorruptError(f"数据库快照已损坏：{quick}")
            violations = snapshot.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise BackupCorruptError("数据库快照存在外键错误")
            version_row = snapshot.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()
            database_version = int(version_row[0]) if version_row else 0
        except sqlite3.DatabaseError as error:
            raise BackupCorruptError("数据库快照无法打开") from error
        finally:
            snapshot.close()
        if database_version != int(manifest.get("schema_version", -1)):
            raise BackupCorruptError("清单与数据库 schema 版本不一致")
        if database_version > MIGRATIONS[-1].version:
            raise BackupCorruptError("备份来自更高版本应用，当前版本不能恢复")
        return manifest
    finally:
        if work_context is not None:
            work_context.cleanup()


def verify_backup(
    connection: sqlite3.Connection,
    backup_id: int,
) -> dict[str, Any]:
    row = _catalog_row(connection, backup_id)
    try:
        path = _resolve_catalog_path(str(row["path"]))
        manifest = _inspect_package(
            path, expected_checksum=str(row["checksum"])
        )
    except BackupCorruptError:
        connection.execute(
            "UPDATE backup_catalog SET status = 'corrupt' WHERE id = ?",
            (backup_id,),
        )
        connection.commit()
        raise
    connection.execute(
        "UPDATE backup_catalog SET status = 'verified' WHERE id = ?", (backup_id,)
    )
    connection.commit()
    return {
        "verified": True,
        "backup": _catalog_payload(_catalog_row(connection, backup_id)),
        "object_counts": {
            key: int(value) for key, value in manifest["object_counts"].items()
        },
    }


def _insert_catalog_if_missing(
    connection: sqlite3.Connection,
    backup: dict[str, Any],
) -> int:
    row = connection.execute(
        "SELECT id FROM backup_catalog WHERE path = ? AND checksum = ?",
        (backup["path"], backup["checksum"]),
    ).fetchone()
    if row is not None:
        return int(row["id"])
    return int(
        connection.execute(
            """
            INSERT INTO backup_catalog(
                path, kind, checksum, size_bytes, app_version,
                schema_version, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                backup["path"],
                backup["kind"],
                backup["checksum"],
                backup["size_bytes"],
                backup["app_version"],
                backup["schema_version"],
                backup["status"],
                backup["created_at"],
            ),
        ).lastrowid
    )


def _swap_asset_directories(staged_data: Path) -> list[tuple[Path, Path]]:
    data_root = _data_root()
    token = uuid4().hex[:10]
    swapped: list[tuple[Path, Path]] = []
    try:
        for name in ASSET_DIRECTORIES:
            current = data_root / name
            staged = staged_data / name
            staged.mkdir(parents=True, exist_ok=True)
            old = data_root / f".restore-old-{token}-{name}"
            if current.exists():
                os.replace(current, old)
            os.replace(staged, current)
            swapped.append((current, old))
        return swapped
    except Exception:
        _rollback_asset_directories(swapped)
        raise


def _rollback_asset_directories(swapped: list[tuple[Path, Path]]) -> None:
    for current, old in reversed(swapped):
        if current.exists():
            shutil.rmtree(current)
        if old.exists():
            os.replace(old, current)


def _finalize_asset_directories(swapped: list[tuple[Path, Path]]) -> None:
    for _, old in swapped:
        if old.exists():
            shutil.rmtree(old)


def _restore_database(connection: sqlite3.Connection, snapshot_path: Path) -> None:
    connection.commit()
    source = sqlite3.connect(snapshot_path)
    try:
        source.backup(connection)
    finally:
        source.close()
    quick = connection.execute("PRAGMA quick_check").fetchone()
    if not quick or str(quick[0]).lower() != "ok":
        raise BackupError(f"恢复后的数据库校验失败：{quick}")
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise BackupError("恢复后的数据库存在外键错误")


def restore_backup(
    connection: sqlite3.Connection,
    backup_id: int,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    row = _catalog_row(connection, backup_id)
    original_backup = _catalog_payload(row)
    try:
        path = _resolve_catalog_path(str(row["path"]))
        manifest = _inspect_package(path, expected_checksum=str(row["checksum"]))
    except BackupCorruptError:
        connection.execute(
            "UPDATE backup_catalog SET status = 'corrupt' WHERE id = ?",
            (backup_id,),
        )
        connection.commit()
        raise
    backup_counts = {
        key: int(value) for key, value in manifest["object_counts"].items()
    }
    current_counts = _object_counts(connection)
    comparison = {
        key: {
            "current": current_counts.get(key, 0),
            "backup": backup_counts.get(key, 0),
            "delta": backup_counts.get(key, 0) - current_counts.get(key, 0),
        }
        for key in sorted(set(current_counts) | set(backup_counts))
    }
    if dry_run:
        return {
            "dry_run": True,
            "can_restore": int(manifest["schema_version"]) == _schema_version(connection),
            "schema_version": {
                "current": _schema_version(connection),
                "backup": int(manifest["schema_version"]),
            },
            "object_counts": comparison,
        }
    if int(manifest["schema_version"]) != _schema_version(connection):
        raise BackupError("V1 仅允许恢复与当前 schema 版本一致的备份")

    pre_restore = create_backup(
        connection,
        kind="manual",
        reason=f"pre_restore:{backup_id}",
    )
    swapped: list[tuple[Path, Path]] = []
    try:
        with tempfile.TemporaryDirectory(
            prefix="restore-", dir=_backup_root()
        ) as temp_name:
            staging = Path(temp_name)
            _inspect_package(
                path,
                expected_checksum=str(row["checksum"]),
                extract_to=staging,
            )
            swapped = _swap_asset_directories(staging / "data")
            try:
                _restore_database(connection, staging / "database.sqlite3")
            except Exception:
                _rollback_asset_directories(swapped)
                swapped = []
                raise
        _finalize_asset_directories(swapped)
        swapped = []
        restored_backup_id = _insert_catalog_if_missing(
            connection,
            {**original_backup, "status": "verified"},
        )
        pre_restore_id = _insert_catalog_if_missing(connection, pre_restore)
        record_learning_event(
            connection,
            verb="restore",
            object_type="backup",
            object_id=restored_backup_id,
            result={
                "pre_restore_backup_id": pre_restore_id,
                "source_backup_id": restored_backup_id,
            },
        )
        connection.commit()
        return {
            "dry_run": False,
            "restored": True,
            "backup_id": restored_backup_id,
            "pre_restore_backup_id": pre_restore_id,
            "object_counts": comparison,
        }
    except Exception:
        if swapped:
            _rollback_asset_directories(swapped)
        raise
