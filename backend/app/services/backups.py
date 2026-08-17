from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from .. import database as database_module
from ..migrations import MIGRATIONS, MigrationError, pending_migrations
from .learning import record_learning_event, utc_now


APP_VERSION = "0.1.0"
BACKUP_FORMAT_VERSION = 1
RESTORE_JOURNAL_VERSION = 1
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


def _restore_journal_path() -> Path:
    root = _data_root() / "backups"
    root.mkdir(parents=True, exist_ok=True)
    return root / "restore-journal.json"


def _restore_lock_path() -> Path:
    return _restore_journal_path().with_name("restore.lock")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _acquire_restore_lock() -> None:
    path = _restore_lock_path()
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise BackupError("已有恢复任务正在执行；若上次被中断，请重启应用自动回滚") from error
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _clear_restore_state() -> None:
    # 先删 journal；若恰在两次 unlink 之间断电，启动时可安全清理孤立 lock。
    _restore_journal_path().unlink(missing_ok=True)
    _restore_lock_path().unlink(missing_ok=True)


def _load_restore_journal() -> dict[str, Any]:
    path = _restore_journal_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BackupError("恢复日志损坏；为避免覆盖数据，应用拒绝自动恢复") from error
    if not isinstance(payload, dict) or payload.get("version") != RESTORE_JOURNAL_VERSION:
        raise BackupError("恢复日志版本不受支持；为避免覆盖数据，应用拒绝自动恢复")
    token = str(payload.get("token") or "")
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise BackupError("恢复日志包含无效事务标识")
    rollback = payload.get("rollback_backup")
    assets = payload.get("assets")
    if not isinstance(rollback, dict) or not isinstance(assets, list):
        raise BackupError("恢复日志缺少回滚信息")
    names = [str(item.get("name") or "") for item in assets if isinstance(item, dict)]
    if names != list(ASSET_DIRECTORIES):
        raise BackupError("恢复日志的用户文件目录清单无效")
    return payload


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
    _discover_backup_packages(connection)
    rows = connection.execute(
        "SELECT * FROM backup_catalog ORDER BY created_at DESC, id DESC"
    ).fetchall()
    return {"items": [_catalog_payload(row) for row in rows]}


def _discover_backup_packages(connection: sqlite3.Connection) -> None:
    """Rebuild catalog rows for portable packages copied into an empty install."""

    root = _backup_root()
    known_paths = {
        str(row["path"])
        for row in connection.execute("SELECT path FROM backup_catalog").fetchall()
    }
    inserted = False
    for package in sorted(root.glob("*.zip")):
        if not package.is_file() or package.is_symlink():
            continue
        relative_path = package.relative_to(_data_root()).as_posix()
        if relative_path in known_paths:
            continue
        size_bytes = package.stat().st_size
        checksum = _sha256_file(package)
        try:
            manifest = _inspect_package(package, expected_checksum=checksum)
        except BackupCorruptError:
            app_version = APP_VERSION
            schema_version = _schema_version(connection)
            status = "corrupt"
            created_at = datetime.fromtimestamp(
                package.stat().st_mtime, timezone.utc
            ).isoformat()
        else:
            app_version = str(manifest["app_version"])
            schema_version = int(manifest["schema_version"])
            status = "verified"
            created_at = str(manifest["created_at"])
        connection.execute(
            """
            INSERT INTO backup_catalog(
                path, kind, checksum, size_bytes, app_version,
                schema_version, status, created_at
            ) VALUES (?, 'manual', ?, ?, ?, ?, ?, ?)
            """,
            (
                relative_path,
                checksum,
                size_bytes,
                app_version,
                schema_version,
                status,
                created_at,
            ),
        )
        known_paths.add(relative_path)
        inserted = True
    if inserted:
        connection.commit()


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
    try:
        schema_version = int(manifest.get("schema_version", -1))
    except (TypeError, ValueError) as error:
        raise BackupCorruptError("备份清单的 schema 版本无效") from error
    if schema_version < 0:
        raise BackupCorruptError("备份清单的 schema 版本无效")
    if not str(manifest.get("created_at") or "").strip():
        raise BackupCorruptError("备份清单缺少创建时间")
    if not str(manifest.get("app_version") or "").strip():
        raise BackupCorruptError("备份清单缺少应用版本")
    if set(manifest["object_counts"]) != set(COUNT_TABLES):
        raise BackupCorruptError("备份清单的对象计数项目不完整")
    for value in manifest["object_counts"].values():
        if not isinstance(value, int) or value < 0:
            raise BackupCorruptError("备份清单的对象计数无效")
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
            try:
                declared_database_size = int(database_info.get("size_bytes", -1))
            except (TypeError, ValueError) as error:
                raise BackupCorruptError("数据库快照大小清单无效") from error
            if members[database_name].file_size != declared_database_size:
                raise BackupCorruptError("数据库快照大小与清单不一致")

            declared_files: set[str] = set()
            for raw_entry in manifest["files"]:
                if not isinstance(raw_entry, dict):
                    raise BackupCorruptError("用户文件清单格式错误")
                name = str(raw_entry.get("path") or "").replace("\\", "/")
                pure = PurePosixPath(name)
                if (
                    pure.is_absolute()
                    or ".." in pure.parts
                    or any(part in {"", "."} or ":" in part for part in pure.parts)
                    or len(pure.parts) < 3
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
                try:
                    declared_size = int(raw_entry.get("size_bytes", -1))
                except (TypeError, ValueError) as error:
                    raise BackupCorruptError(
                        f"用户文件大小清单无效：{name}"
                    ) from error
                if info.file_size != declared_size:
                    raise BackupCorruptError(f"用户文件大小与清单不一致：{name}")
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
            pending_migrations(snapshot, MIGRATIONS)
        except (sqlite3.DatabaseError, MigrationError) as error:
            raise BackupCorruptError("数据库快照结构或迁移历史无效") from error
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


def _asset_old_path(token: str, name: str) -> Path:
    return _data_root() / f".restore-old-{token}-{name}"


def _remove_asset_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def _journal_staging_path(journal: dict[str, Any]) -> Path:
    relative = Path(str(journal.get("staging") or ""))
    if relative.is_absolute():
        raise BackupError("恢复日志的暂存目录无效")
    candidate = (_data_root() / relative).resolve()
    root = _backup_root().resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise BackupError("恢复日志的暂存目录越过备份边界") from error
    return candidate


def _write_restore_journal(journal: dict[str, Any]) -> None:
    _write_json_atomic(_restore_journal_path(), journal)


def _swap_asset_directories(
    staged_data: Path,
    journal: dict[str, Any],
) -> None:
    data_root = _data_root()
    token = str(journal["token"])
    for entry in journal["assets"]:
        name = str(entry["name"])
        current = data_root / name
        staged = staged_data / name
        staged.mkdir(parents=True, exist_ok=True)
        old = _asset_old_path(token, name)
        if old.exists():
            raise BackupError(f"恢复暂存目录冲突：{old.name}")
        journal["active_asset"] = name
        journal["phase"] = "assets_swapping"
        _write_restore_journal(journal)
        if bool(entry["original_exists"]):
            os.replace(current, old)
        os.replace(staged, current)
        entry["swapped"] = True
        journal["active_asset"] = None
        _write_restore_journal(journal)
    journal["phase"] = "assets_swapped"
    _write_restore_journal(journal)


def _rollback_asset_directories(journal: dict[str, Any]) -> None:
    token = str(journal["token"])
    active_asset = str(journal.get("active_asset") or "")
    for entry in reversed(journal["assets"]):
        name = str(entry["name"])
        current = _data_root() / name
        old = _asset_old_path(token, name)
        original_exists = bool(entry["original_exists"])
        swap_may_have_started = bool(entry.get("swapped")) or name == active_asset
        if original_exists:
            # old 的存在是 current 已被移动的权威证据；恢复动作可重复执行。
            if old.exists():
                _remove_asset_path(current)
                os.replace(old, current)
        elif swap_may_have_started:
            _remove_asset_path(current)
        entry["swapped"] = False
    journal["active_asset"] = None


def _finalize_asset_directories(journal: dict[str, Any]) -> None:
    token = str(journal["token"])
    for name in ASSET_DIRECTORIES:
        _remove_asset_path(_asset_old_path(token, name))


def _cleanup_restore_staging(journal: dict[str, Any]) -> None:
    staging = _journal_staging_path(journal)
    if staging.exists():
        shutil.rmtree(staging)


def _copy_database_snapshot(
    connection: sqlite3.Connection,
    snapshot_path: Path,
) -> None:
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


def _restore_database(connection: sqlite3.Connection, snapshot_path: Path) -> None:
    _copy_database_snapshot(connection, snapshot_path)


def _restore_rollback_database(
    connection: sqlite3.Connection,
    journal: dict[str, Any],
) -> None:
    rollback = journal["rollback_backup"]
    path = _resolve_catalog_path(str(rollback.get("path") or ""))
    checksum = str(rollback.get("checksum") or "")
    if not checksum:
        raise BackupError("恢复日志缺少回滚快照 checksum")
    with tempfile.TemporaryDirectory(prefix="rollback-", dir=_backup_root()) as name:
        staging = Path(name)
        _inspect_package(path, expected_checksum=checksum, extract_to=staging)
        connection.rollback()
        _copy_database_snapshot(connection, staging / "database.sqlite3")


def _commit_restore_metadata(
    connection: sqlite3.Connection,
    *,
    source_backup: dict[str, Any],
    pre_restore: dict[str, Any],
) -> tuple[int, int]:
    restored_backup_id = _insert_catalog_if_missing(
        connection,
        {**source_backup, "status": "verified"},
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
    return restored_backup_id, pre_restore_id


def recover_interrupted_restore() -> dict[str, Any]:
    """Finish cleanup or roll back a restore journal before the app opens."""

    journal_path = _restore_journal_path()
    if not journal_path.exists():
        # A crash between acquiring the lock and writing the journal cannot have
        # changed user data, so the orphan lock is safe to discard.
        _restore_lock_path().unlink(missing_ok=True)
        return {"recovered": False}
    journal = _load_restore_journal()
    if str(journal.get("phase")) == "committed":
        try:
            _finalize_asset_directories(journal)
            _cleanup_restore_staging(journal)
            _clear_restore_state()
        except OSError:
            return {"recovered": True, "action": "committed_cleanup_pending"}
        return {"recovered": True, "action": "finished_committed_cleanup"}

    _rollback_asset_directories(journal)
    database_path = Path(database_module.DATABASE_PATH)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        _restore_rollback_database(connection, journal)
    finally:
        connection.close()
    _cleanup_restore_staging(journal)
    _clear_restore_state()
    return {"recovered": True, "action": "rolled_back_interrupted_restore"}


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

    _acquire_restore_lock()
    journal: dict[str, Any] | None = None
    committed = False
    try:
        pre_restore = create_backup(
            connection,
            kind="manual",
            reason=f"pre_restore:{backup_id}",
        )
        token = uuid4().hex
        staging = Path(tempfile.mkdtemp(prefix="restore-", dir=_backup_root()))
        journal = {
            "version": RESTORE_JOURNAL_VERSION,
            "token": token,
            "phase": "prepared",
            "active_asset": None,
            "staging": staging.relative_to(_data_root()).as_posix(),
            "rollback_backup": {
                "path": pre_restore["path"],
                "checksum": pre_restore["checksum"],
            },
            "assets": [
                {
                    "name": name,
                    "original_exists": (_data_root() / name).exists(),
                    "swapped": False,
                }
                for name in ASSET_DIRECTORIES
            ],
        }
        _write_restore_journal(journal)
        _inspect_package(
            path,
            expected_checksum=str(row["checksum"]),
            extract_to=staging,
        )
        journal["phase"] = "package_extracted"
        _write_restore_journal(journal)
        _swap_asset_directories(staging / "data", journal)
        journal["phase"] = "database_restoring"
        _write_restore_journal(journal)
        _restore_database(connection, staging / "database.sqlite3")
        journal["phase"] = "database_restored"
        _write_restore_journal(journal)
        restored_backup_id, pre_restore_id = _commit_restore_metadata(
            connection,
            source_backup=original_backup,
            pre_restore=pre_restore,
        )
        journal["phase"] = "committed"
        _write_restore_journal(journal)
        committed = True
    except Exception as restore_error:
        if journal is None:
            _restore_lock_path().unlink(missing_ok=True)
            raise
        try:
            _rollback_asset_directories(journal)
            _restore_rollback_database(connection, journal)
            _cleanup_restore_staging(journal)
            _clear_restore_state()
        except Exception as rollback_error:
            # 保留 journal/lock，下一次启动会再次执行同一幂等回滚。
            raise BackupError(
                f"恢复失败且自动回滚未完成；请重启应用继续恢复：{rollback_error}"
            ) from restore_error
        raise

    cleanup_pending = False
    assert journal is not None and committed
    try:
        _finalize_asset_directories(journal)
        _cleanup_restore_staging(journal)
        _clear_restore_state()
    except OSError:
        # 数据库和新资产已经共同提交。留下 committed journal，让下次启动
        # 只重试旧目录清理，绝不把一个成功恢复误判为失败后回滚。
        cleanup_pending = True
    return {
        "dry_run": False,
        "restored": True,
        "backup_id": restored_backup_id,
        "pre_restore_backup_id": pre_restore_id,
        "object_counts": comparison,
        "cleanup_pending": cleanup_pending,
    }
