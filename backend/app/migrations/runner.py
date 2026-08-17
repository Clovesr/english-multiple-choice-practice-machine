from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


HISTORY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    checksum TEXT NOT NULL DEFAULT ''
)
"""


class MigrationError(RuntimeError):
    """Base class for versioned migration failures."""


class MigrationChecksumError(MigrationError):
    """An applied migration no longer matches the shipped definition."""


class MigrationVersionError(MigrationError):
    """The database was migrated by a newer or unknown application version."""


class MigrationApplyError(MigrationError):
    """A pending migration failed and its transaction was rolled back."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    fingerprint: str
    apply: Callable[[sqlite3.Connection], None]

    @property
    def checksum(self) -> str:
        payload = f"{self.version:04d}:{self.name}\n{self.fingerprint}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _history_exists(connection: sqlite3.Connection) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
    )


def _applied_migrations(connection: sqlite3.Connection) -> dict[int, str]:
    if not _history_exists(connection):
        return {}
    return {
        int(row[0]): str(row[1])
        for row in connection.execute(
            "SELECT version, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
    }


def _validated_migrations(migrations: Sequence[Migration]) -> tuple[Migration, ...]:
    ordered = tuple(sorted(migrations, key=lambda migration: migration.version))
    versions = [migration.version for migration in ordered]
    if any(version <= 0 for version in versions):
        raise MigrationVersionError("迁移编号必须是正整数")
    if len(versions) != len(set(versions)):
        raise MigrationVersionError("检测到重复的迁移编号")
    if versions and versions != list(range(1, versions[-1] + 1)):
        raise MigrationVersionError("迁移编号必须从 0001 开始连续递增")
    return ordered


def pending_migrations(
    connection: sqlite3.Connection,
    migrations: Sequence[Migration],
) -> tuple[Migration, ...]:
    ordered = _validated_migrations(migrations)
    by_version = {migration.version: migration for migration in ordered}
    applied = _applied_migrations(connection)
    unknown = sorted(set(applied) - set(by_version))
    if unknown:
        formatted = ", ".join(f"{version:04d}" for version in unknown)
        raise MigrationVersionError(
            f"数据库包含当前程序不认识的迁移版本：{formatted}；请使用更新版本的程序"
        )
    for version, stored_checksum in applied.items():
        expected = by_version[version].checksum
        if stored_checksum != expected:
            raise MigrationChecksumError(
                f"迁移 {version:04d} 校验值不一致；为保护数据，已停止启动"
            )
    return tuple(migration for migration in ordered if migration.version not in applied)


def _database_file(database_path: Path | str) -> Path | None:
    if str(database_path) == ":memory:":
        return None
    path = Path(database_path)
    if not path.exists() or path.stat().st_size == 0:
        return None
    return path


def backup_database(
    connection: sqlite3.Connection,
    database_path: Path | str,
    *,
    from_version: int,
    to_version: int,
) -> Path | None:
    """Create and verify a consistent SQLite snapshot before migration."""

    source_path = _database_file(database_path)
    if source_path is None:
        return None
    backup_dir = source_path.parent / "backups" / "pre-migration"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination_path = backup_dir / (
        f"{source_path.stem}-{timestamp}-v{from_version:04d}-to-v{to_version:04d}"
        f"-{uuid4().hex[:8]}.sqlite3"
    )
    destination = sqlite3.connect(destination_path)
    try:
        connection.backup(destination)
        result = destination.execute("PRAGMA quick_check").fetchone()
        if not result or str(result[0]).lower() != "ok":
            raise MigrationError(f"迁移前备份校验失败：{result}")
    except Exception:
        destination.close()
        destination_path.unlink(missing_ok=True)
        raise
    else:
        destination.close()
    return destination_path


def _current_version(connection: sqlite3.Connection) -> int:
    if not _history_exists(connection):
        return 0
    row = connection.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()
    return int(row[0]) if row else 0


def _enable_wal(connection: sqlite3.Connection, database_path: Path | str) -> None:
    if str(database_path) == ":memory:":
        return
    row = connection.execute("PRAGMA journal_mode=WAL").fetchone()
    if not row or str(row[0]).lower() != "wal":
        raise MigrationError(f"无法启用 SQLite WAL 模式：{row}")


def run_pending_migrations(
    connection: sqlite3.Connection,
    database_path: Path | str,
    migrations: Sequence[Migration],
    *,
    create_backup: bool = True,
) -> tuple[int, ...]:
    """Apply pending migrations one-by-one, each in its own transaction."""

    pending = pending_migrations(connection, migrations)
    if not pending:
        return ()
    connection.commit()
    if create_backup:
        backup_database(
            connection,
            database_path,
            from_version=_current_version(connection),
            to_version=pending[-1].version,
        )
    _enable_wal(connection, database_path)
    connection.execute(HISTORY_TABLE_SQL)
    connection.commit()
    applied: list[int] = []
    for migration in pending:
        try:
            connection.execute("BEGIN IMMEDIATE")
            migration.apply(connection)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, name, applied_at, checksum)
                VALUES (?, ?, ?, ?)
                """,
                (
                    migration.version,
                    migration.name,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    migration.checksum,
                ),
            )
            connection.commit()
        except Exception as error:
            connection.rollback()
            raise MigrationApplyError(
                f"迁移 {migration.version:04d}（{migration.name}）失败，已回滚"
            ) from error
        applied.append(migration.version)
    return tuple(applied)
