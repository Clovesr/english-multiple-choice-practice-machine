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
from .v0003_courses_skills import MIGRATION as COURSES_SKILLS_0003
from .v0004_tasks_events_mastery import MIGRATION as TASKS_EVENTS_MASTERY_0004
from .v0005_backup_catalog import MIGRATION as BACKUP_CATALOG_0005


MIGRATIONS = (
    RESOURCES_0001,
    VOCABULARY_FSRS_0002,
    COURSES_SKILLS_0003,
    TASKS_EVENTS_MASTERY_0004,
    BACKUP_CATALOG_0005,
)


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
