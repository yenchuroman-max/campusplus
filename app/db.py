import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "app.db"

DEFAULT_DISCIPLINES = [
    "Информационная безопасность и защита информации",
    "Системы искусственного интеллекта",
    "Web - программирование",
    "Администрирование информационных систем",
    "Геоинформационные системы и технологии",
]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
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
        CREATE TABLE IF NOT EXISTS teacher_disciplines (
            teacher_id INTEGER NOT NULL,
            discipline_id INTEGER NOT NULL,
            PRIMARY KEY (teacher_id, discipline_id),
            FOREIGN KEY (teacher_id) REFERENCES users(id),
            FOREIGN KEY (discipline_id) REFERENCES disciplines(id)
        )
        """
    )
    for discipline_name in DEFAULT_DISCIPLINES:
        cur.execute("INSERT OR IGNORE INTO disciplines (name) VALUES (?)", (discipline_name,))

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL
        )
        """
    )
    # Ensure optional columns exist (for migrations)
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
    # audit table for admin actions (anti-fraud logging)
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
    # backfill distinct groups from users into groups catalog
    cur.execute("SELECT DISTINCT student_group FROM users WHERE student_group IS NOT NULL AND TRIM(student_group) <> ''")
    for row in cur.fetchall():
        cur.execute("INSERT OR IGNORE INTO groups (name) VALUES (?)", (row[0].strip(),))

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

    # backfill many-to-many mapping from legacy users.discipline_id
    cur.execute(
        """
        INSERT OR IGNORE INTO teacher_disciplines (teacher_id, discipline_id)
        SELECT id, discipline_id
        FROM users
        WHERE role = 'teacher' AND discipline_id IS NOT NULL AND discipline_id > 0
        """
    )

    # ensure every teacher has at least one mapped discipline
    if fallback_discipline_id:
        cur.execute("SELECT id FROM users WHERE role = 'teacher'")
        for row in cur.fetchall():
            teacher_id = int(row[0])
            cur.execute("SELECT 1 FROM teacher_disciplines WHERE teacher_id = ? LIMIT 1", (teacher_id,))
            if not cur.fetchone():
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
    conn.commit()
    conn.close()
