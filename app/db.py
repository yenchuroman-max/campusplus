from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

SQLITE_DB_PATH = Path(os.getenv("SQLITE_DB_PATH", str(Path(__file__).resolve().parent / "app.db")))

DEFAULT_DISCIPLINES = [
    "Информационная безопасность и защита информации",
    "Системы искусственного интеллекта",
    "Web - программирование",
    "Администрирование информационных систем",
    "Геоинформационные системы и технологии",
]


def _database_url() -> str:
    return (os.getenv("DATABASE_URL") or "").strip()


def _use_postgres() -> bool:
    url = _database_url().lower()
    return url.startswith("postgres://") or url.startswith("postgresql://")


def _rewrite_placeholders(query: str) -> str:
    # The codebase uses sqlite-style "?" params everywhere.
    # For PostgreSQL drivers we map them to "%s".
    return query.replace("?", "%s")


def insert_ignore(
    cur,
    table: str,
    columns: tuple[str, ...] | list[str],
    values: tuple[Any, ...] | list[Any],
    conflict_columns: tuple[str, ...] | list[str] | None = None,
) -> int:
    """Run an INSERT that ignores duplicate-key conflicts on both SQLite and PostgreSQL."""
    column_list = tuple(columns)
    placeholders = ", ".join("?" for _ in column_list)
    columns_sql = ", ".join(column_list)
    base_sql = f"INSERT INTO {table} ({columns_sql}) VALUES ({placeholders})"

    if _use_postgres():
        if conflict_columns:
            conflict_sql = ", ".join(conflict_columns)
            sql = f"{base_sql} ON CONFLICT ({conflict_sql}) DO NOTHING"
        else:
            sql = f"{base_sql} ON CONFLICT DO NOTHING"
    else:
        sql = f"INSERT OR IGNORE INTO {table} ({columns_sql}) VALUES ({placeholders})"

    cur.execute(sql, tuple(values))
    return int(getattr(cur, "rowcount", 0) or 0)


class DictLikeRow(dict):
    """Row object with both dict and index access (sqlite3.Row-like)."""

    def __init__(self, data: dict[str, Any]):
        super().__init__(data)
        self._values = list(data.values())

    def __getitem__(self, key: Any) -> Any:  # type: ignore[override]
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class PostgresCursorAdapter:
    def __init__(self, raw_cursor):
        self._cur = raw_cursor
        self.lastrowid: int | None = None
        self.rowcount: int = 0

    def execute(self, query: str, params: tuple | list | None = None):
        sql = _rewrite_placeholders(query)
        self._cur.execute(sql, params or ())
        self.lastrowid = None
        self.rowcount = int(getattr(self._cur, "rowcount", 0) or 0)
        if sql.lstrip().lower().startswith("insert") and "returning" not in sql.lower():
            match = re.match(r"\s*insert\s+into\s+([a-zA-Z_][\w]*)", sql, flags=re.IGNORECASE)
            table_name = match.group(1).lower() if match else ""
            auto_id_tables = {
                "disciplines",
                "users",
                "groups",
                "lectures",
                "tests",
                "questions",
                "attempts",
                "answers",
                "audit",
            }
            if table_name not in auto_id_tables or self.rowcount <= 0:
                return self
            try:
                self._cur.execute("SELECT LASTVAL() AS last_id")
                row = self._cur.fetchone()
                if row:
                    self.lastrowid = int(row["last_id"])
            except Exception:
                self.lastrowid = None
        return self

    def executemany(self, query: str, seq_of_params):
        sql = _rewrite_placeholders(query)
        self._cur.executemany(sql, seq_of_params)
        self.lastrowid = None
        self.rowcount = int(getattr(self._cur, "rowcount", 0) or 0)
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        if isinstance(row, dict):
            return DictLikeRow(row)
        if hasattr(row, "keys"):
            return DictLikeRow(dict(row))
        return row

    def fetchall(self):
        rows = self._cur.fetchall()
        result = []
        for row in rows:
            if isinstance(row, dict):
                result.append(DictLikeRow(row))
            elif hasattr(row, "keys"):
                result.append(DictLikeRow(dict(row)))
            else:
                result.append(row)
        return result

    def close(self):
        self._cur.close()


class PostgresConnectionAdapter:
    def __init__(self, raw_connection):
        self._conn = raw_connection

    def cursor(self):
        return PostgresCursorAdapter(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def connect():
    if _use_postgres():
        import psycopg
        from psycopg.rows import dict_row

        conn = psycopg.connect(_database_url(), row_factory=dict_row)
        return PostgresConnectionAdapter(conn)

    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _backfill_common(cur) -> None:
    # Backfill distinct groups from users into groups catalog
    cur.execute("SELECT DISTINCT student_group FROM users WHERE student_group IS NOT NULL AND TRIM(student_group) <> ''")
    for row in cur.fetchall():
        group_name = (row[0] or "").strip()
        if group_name:
            if _use_postgres():
                cur.execute("INSERT INTO groups (name) VALUES (?) ON CONFLICT (name) DO NOTHING", (group_name,))
            else:
                cur.execute("INSERT OR IGNORE INTO groups (name) VALUES (?)", (group_name,))

    cur.execute("SELECT id FROM disciplines ORDER BY id LIMIT 1")
    fallback_discipline = cur.fetchone()
    fallback_discipline_id = int(fallback_discipline[0]) if fallback_discipline else None

    if fallback_discipline_id:
        cur.execute(
            """
            UPDATE users
            SET discipline_id = ?
            WHERE role = 'teacher' AND (discipline_id IS NULL OR discipline_id = 0)
            """,
            (fallback_discipline_id,),
        )
        cur.execute(
            """
            UPDATE lectures
            SET discipline_id = COALESCE(
                (SELECT u.discipline_id FROM users u WHERE u.id = lectures.teacher_id),
                ?
            )
            WHERE discipline_id IS NULL OR discipline_id = 0
            """,
            (fallback_discipline_id,),
        )

    if _use_postgres():
        cur.execute(
            """
            INSERT INTO teacher_disciplines (teacher_id, discipline_id)
            SELECT id, discipline_id
            FROM users
            WHERE role = 'teacher' AND discipline_id IS NOT NULL AND discipline_id > 0
            ON CONFLICT (teacher_id, discipline_id) DO NOTHING
            """
        )
    else:
        cur.execute(
            """
            INSERT OR IGNORE INTO teacher_disciplines (teacher_id, discipline_id)
            SELECT id, discipline_id
            FROM users
            WHERE role = 'teacher' AND discipline_id IS NOT NULL AND discipline_id > 0
            """
        )

    if fallback_discipline_id:
        cur.execute("SELECT id FROM users WHERE role = 'teacher'")
        for row in cur.fetchall():
            teacher_id = int(row[0])
            cur.execute("SELECT 1 FROM teacher_disciplines WHERE teacher_id = ? LIMIT 1", (teacher_id,))
            if not cur.fetchone():
                if _use_postgres():
                    cur.execute(
                        "INSERT INTO teacher_disciplines (teacher_id, discipline_id) VALUES (?, ?) ON CONFLICT (teacher_id, discipline_id) DO NOTHING",
                        (teacher_id, fallback_discipline_id),
                    )
                else:
                    cur.execute(
                        "INSERT OR IGNORE INTO teacher_disciplines (teacher_id, discipline_id) VALUES (?, ?)",
                        (teacher_id, fallback_discipline_id),
                    )

    if fallback_discipline_id:
        cur.execute(
            """
            UPDATE users
            SET discipline_id = COALESCE(
                (SELECT MIN(td.discipline_id) FROM teacher_disciplines td WHERE td.teacher_id = users.id),
                ?
            )
            WHERE role = 'teacher'
            """,
            (fallback_discipline_id,),
        )

    # Backfill explicit group-teacher links from the legacy single-teacher column.
    cur.execute("SELECT name, teacher_id FROM groups WHERE teacher_id IS NOT NULL")
    for row in cur.fetchall():
        teacher_id = int(row["teacher_id"])
        group_name = (row["name"] or "").strip()
        if not group_name:
            continue
        if _use_postgres():
            cur.execute(
                """
                INSERT INTO group_teachers (group_name, teacher_id)
                VALUES (?, ?)
                ON CONFLICT (group_name, teacher_id) DO NOTHING
                """,
                (group_name, teacher_id),
            )
        else:
            cur.execute(
                """
                INSERT OR IGNORE INTO group_teachers (group_name, teacher_id)
                VALUES (?, ?)
                """,
                (group_name, teacher_id),
            )

    # Legacy users may still reference teachers directly through assigned_teacher_id.
    cur.execute(
        """
        SELECT DISTINCT assigned_teacher_id AS teacher_id, COALESCE(student_group, '') AS group_name
        FROM users
        WHERE role = 'student' AND assigned_teacher_id IS NOT NULL
        """
    )
    for row in cur.fetchall():
        teacher_id = row["teacher_id"]
        if not teacher_id:
            continue
        normalized_group = (row["group_name"] or "").strip()
        if not normalized_group:
            continue
        if _use_postgres():
            cur.execute(
                """
                INSERT INTO group_teachers (group_name, teacher_id)
                VALUES (?, ?)
                ON CONFLICT (group_name, teacher_id) DO NOTHING
                """,
                (normalized_group, int(teacher_id)),
            )
        else:
            cur.execute(
                """
                INSERT OR IGNORE INTO group_teachers (group_name, teacher_id)
                VALUES (?, ?)
                """,
                (normalized_group, int(teacher_id)),
            )

    # Backfill discipline/group/teacher access model from group-teacher links.
    cur.execute("SELECT group_name, teacher_id FROM group_teachers")
    for row in cur.fetchall():
        teacher_id = int(row["teacher_id"])
        group_name = (row["group_name"] or "").strip()
        if not group_name:
            continue
        cur.execute(
            "SELECT discipline_id FROM teacher_disciplines WHERE teacher_id = ?",
            (teacher_id,),
        )
        for discipline_row in cur.fetchall():
            discipline_id = int(discipline_row["discipline_id"])
            if _use_postgres():
                cur.execute(
                    """
                    INSERT INTO teaching_assignments (teacher_id, discipline_id, group_name)
                    VALUES (?, ?, ?)
                    ON CONFLICT (teacher_id, discipline_id, group_name) DO NOTHING
                    """,
                    (teacher_id, discipline_id, group_name),
                )
            else:
                cur.execute(
                    """
                    INSERT OR IGNORE INTO teaching_assignments (teacher_id, discipline_id, group_name)
                    VALUES (?, ?, ?)
                    """,
                    (teacher_id, discipline_id, group_name),
                )


def _init_db_sqlite() -> None:
    conn = connect()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS disciplines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            must_change_password INTEGER NOT NULL DEFAULT 0,
            session_version INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    cur.execute("PRAGMA table_info(users)")
    existing = [r[1] for r in cur.fetchall()]
    if "last_login" not in existing:
        cur.execute("ALTER TABLE users ADD COLUMN last_login TEXT")
    if "assigned_teacher_id" not in existing:
        cur.execute("ALTER TABLE users ADD COLUMN assigned_teacher_id INTEGER")
    if "student_group" not in existing:
        cur.execute("ALTER TABLE users ADD COLUMN student_group TEXT")
    if "discipline_id" not in existing:
        cur.execute("ALTER TABLE users ADD COLUMN discipline_id INTEGER")
    if "must_change_password" not in existing:
        cur.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
    if "session_version" not in existing:
        cur.execute("ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 1")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            teacher_id INTEGER,
            FOREIGN KEY (teacher_id) REFERENCES users(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS group_teachers (
            group_name TEXT NOT NULL,
            teacher_id INTEGER NOT NULL,
            PRIMARY KEY (group_name, teacher_id),
            FOREIGN KEY (teacher_id) REFERENCES users(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS lectures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (teacher_id) REFERENCES users(id)
        )
        """
    )
    cur.execute("PRAGMA table_info(lectures)")
    lecture_cols = [r[1] for r in cur.fetchall()]
    if "discipline_id" not in lecture_cols:
        cur.execute("ALTER TABLE lectures ADD COLUMN discipline_id INTEGER")
    if "original_filename" not in lecture_cols:
        cur.execute("ALTER TABLE lectures ADD COLUMN original_filename TEXT")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lecture_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (lecture_id) REFERENCES lectures(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            options_json TEXT NOT NULL,
            correct_index INTEGER NOT NULL,
            FOREIGN KEY (test_id) REFERENCES tests(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            score REAL NOT NULL,
            taken_at TEXT NOT NULL,
            FOREIGN KEY (test_id) REFERENCES tests(id),
            FOREIGN KEY (student_id) REFERENCES users(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            attempt_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            selected_index INTEGER NOT NULL,
            is_correct INTEGER NOT NULL,
            FOREIGN KEY (attempt_id) REFERENCES attempts(id),
            FOREIGN KEY (question_id) REFERENCES questions(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_email TEXT,
            actor_role TEXT,
            action TEXT NOT NULL,
            target_user_id INTEGER,
            details TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS teacher_disciplines (
            teacher_id INTEGER NOT NULL,
            discipline_id INTEGER NOT NULL,
            PRIMARY KEY (teacher_id, discipline_id),
            FOREIGN KEY (teacher_id) REFERENCES users(id),
            FOREIGN KEY (discipline_id) REFERENCES disciplines(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS teaching_assignments (
            teacher_id INTEGER NOT NULL,
            discipline_id INTEGER NOT NULL,
            group_name TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (teacher_id, discipline_id, group_name),
            FOREIGN KEY (teacher_id) REFERENCES users(id),
            FOREIGN KEY (discipline_id) REFERENCES disciplines(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS teaching_assignment_blocks (
            teacher_id INTEGER NOT NULL,
            discipline_id INTEGER NOT NULL,
            group_name TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (teacher_id, discipline_id, group_name),
            FOREIGN KEY (teacher_id) REFERENCES users(id),
            FOREIGN KEY (discipline_id) REFERENCES disciplines(id)
        )
        """
    )

    for discipline_name in DEFAULT_DISCIPLINES:
        cur.execute("INSERT OR IGNORE INTO disciplines (name) VALUES (?)", (discipline_name,))

    _backfill_common(cur)
    conn.commit()
    conn.close()


def _init_db_postgres() -> None:
    conn = connect()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS disciplines (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY,
            role TEXT NOT NULL,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            must_change_password INTEGER NOT NULL DEFAULT 0,
            session_version INTEGER NOT NULL DEFAULT 1,
            last_login TEXT,
            assigned_teacher_id BIGINT,
            student_group TEXT,
            discipline_id BIGINT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS groups (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            teacher_id BIGINT REFERENCES users(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS group_teachers (
            group_name TEXT NOT NULL,
            teacher_id BIGINT NOT NULL REFERENCES users(id),
            PRIMARY KEY (group_name, teacher_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS lectures (
            id BIGSERIAL PRIMARY KEY,
            teacher_id BIGINT NOT NULL REFERENCES users(id),
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            discipline_id BIGINT,
            original_filename TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tests (
            id BIGSERIAL PRIMARY KEY,
            lecture_id BIGINT NOT NULL REFERENCES lectures(id),
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS questions (
            id BIGSERIAL PRIMARY KEY,
            test_id BIGINT NOT NULL REFERENCES tests(id),
            text TEXT NOT NULL,
            options_json TEXT NOT NULL,
            correct_index INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS attempts (
            id BIGSERIAL PRIMARY KEY,
            test_id BIGINT NOT NULL REFERENCES tests(id),
            student_id BIGINT NOT NULL REFERENCES users(id),
            score DOUBLE PRECISION NOT NULL,
            taken_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS answers (
            id BIGSERIAL PRIMARY KEY,
            attempt_id BIGINT NOT NULL REFERENCES attempts(id),
            question_id BIGINT NOT NULL REFERENCES questions(id),
            selected_index INTEGER NOT NULL,
            is_correct INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS audit (
            id BIGSERIAL PRIMARY KEY,
            actor_email TEXT,
            actor_role TEXT,
            action TEXT NOT NULL,
            target_user_id BIGINT,
            details TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS teacher_disciplines (
            teacher_id BIGINT NOT NULL REFERENCES users(id),
            discipline_id BIGINT NOT NULL REFERENCES disciplines(id),
            PRIMARY KEY (teacher_id, discipline_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS teaching_assignments (
            teacher_id BIGINT NOT NULL REFERENCES users(id),
            discipline_id BIGINT NOT NULL REFERENCES disciplines(id),
            group_name TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (teacher_id, discipline_id, group_name)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS teaching_assignment_blocks (
            teacher_id BIGINT NOT NULL REFERENCES users(id),
            discipline_id BIGINT NOT NULL REFERENCES disciplines(id),
            group_name TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (teacher_id, discipline_id, group_name)
        )
        """
    )

    # Migrations for existing PostgreSQL schema (if deployed before these columns were added)
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login TEXT")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS assigned_teacher_id BIGINT")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS student_group TEXT")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS discipline_id BIGINT")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password INTEGER NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS session_version INTEGER NOT NULL DEFAULT 1")
    cur.execute("ALTER TABLE lectures ADD COLUMN IF NOT EXISTS discipline_id BIGINT")
    cur.execute("ALTER TABLE lectures ADD COLUMN IF NOT EXISTS original_filename TEXT")

    for discipline_name in DEFAULT_DISCIPLINES:
        cur.execute("INSERT INTO disciplines (name) VALUES (?) ON CONFLICT (name) DO NOTHING", (discipline_name,))

    _backfill_common(cur)
    conn.commit()
    conn.close()


def init_db() -> None:
    if _use_postgres():
        _init_db_postgres()
    else:
        _init_db_sqlite()
