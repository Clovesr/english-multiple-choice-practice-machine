from __future__ import annotations

import sqlite3

from .runner import Migration


STATEMENTS = (
    """
    CREATE TABLE courses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        stage TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active',
        sequence INTEGER NOT NULL DEFAULT 0,
        deleted_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE lessons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid TEXT NOT NULL UNIQUE,
        course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        sequence INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'draft',
        deleted_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE skills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid TEXT NOT NULL UNIQUE,
        parent_id INTEGER REFERENCES skills(id) ON DELETE SET NULL,
        name TEXT NOT NULL,
        skill_type TEXT NOT NULL DEFAULT 'grammar',
        stage TEXT NOT NULL DEFAULT '',
        difficulty INTEGER NOT NULL DEFAULT 3,
        status TEXT NOT NULL DEFAULT 'active',
        deleted_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE skill_dependencies (
        skill_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
        prerequisite_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
        PRIMARY KEY (skill_id, prerequisite_id)
    )
    """,
    """
    CREATE TABLE lesson_skills (
        lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
        skill_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
        PRIMARY KEY (lesson_id, skill_id)
    )
    """,
    """
    CREATE TABLE question_skills (
        question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
        skill_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
        source TEXT NOT NULL DEFAULT 'manual',
        PRIMARY KEY (question_id, skill_id)
    )
    """,
)


FINGERPRINT = "\n-- statement --\n".join(
    " ".join(statement.split()) for statement in STATEMENTS
)


def apply(connection: sqlite3.Connection) -> None:
    for statement in STATEMENTS:
        connection.execute(statement)


MIGRATION = Migration(
    version=3,
    name="courses_and_skills",
    fingerprint=FINGERPRINT,
    apply=apply,
)
