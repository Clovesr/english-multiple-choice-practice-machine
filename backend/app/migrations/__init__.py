from __future__ import annotations

from .runner import (
    Migration,
    MigrationApplyError,
    MigrationChecksumError,
    MigrationError,
    MigrationVersionError,
    backup_database,
    pending_migrations,
    run_pending_migrations,
)
from .v0001_resources import MIGRATION as RESOURCES_0001
from .v0002_vocabulary_fsrs import MIGRATION as VOCABULARY_FSRS_0002


MIGRATIONS = (RESOURCES_0001, VOCABULARY_FSRS_0002)


__all__ = [
    "MIGRATIONS",
    "Migration",
    "MigrationApplyError",
    "MigrationChecksumError",
    "MigrationError",
    "MigrationVersionError",
    "backup_database",
    "pending_migrations",
    "run_pending_migrations",
]
