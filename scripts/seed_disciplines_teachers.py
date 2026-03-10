from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db import connect, init_db, insert_ignore
from app.security import hash_password, new_salt

DISCIPLINES = [
    "Информационная безопасность и защита информации",
    "Системы искусственного интеллекта",
    "Web - программирование",
    "Администрирование информационных систем",
    "Геоинформационные системы и технологии",
    "Компьютерные сети",
    "Разработка мобильных приложений",
    "DevOps и CI/CD",
    "Тестирование программного обеспечения",
    "Облачные вычисления",
    "Анализ данных и BI",
    "Программная инженерия",
]

TEACHERS = [
    ("Мельников Артём Сергеевич", "discipline.teacher1@example.com", "Teacher123!"),
    ("Орлова Виктория Павловна", "discipline.teacher2@example.com", "Teacher123!"),
    ("Громов Никита Андреевич", "discipline.teacher3@example.com", "Teacher123!"),
    ("Васильева Дарья Игоревна", "discipline.teacher4@example.com", "Teacher123!"),
    ("Тимофеев Максим Олегович", "discipline.teacher5@example.com", "Teacher123!"),
]


def _password_hash(password: str) -> tuple[str, str]:
    salt = new_salt()
    return hash_password(password, salt), salt


def seed() -> dict[str, int]:
    init_db()
    conn = connect()
    cur = conn.cursor()

    for name in DISCIPLINES:
        insert_ignore(cur, "disciplines", ("name",), (name,), conflict_columns=("name",))

    cur.execute("SELECT id, name FROM disciplines")
    discipline_map = {row["name"]: int(row["id"]) for row in cur.fetchall()}

    created = 0
    updated = 0

    for full_name, email, password in TEACHERS:
        cur.execute("SELECT id FROM users WHERE email = ?", (email.lower(),))
        existing = cur.fetchone()
        if existing:
            cur.execute(
                "UPDATE users SET role = 'teacher', full_name = ? WHERE id = ?",
                (full_name, int(existing["id"])),
            )
            updated += 1
            continue

        pwd_hash, salt = _password_hash(password)
        cur.execute(
            """
            INSERT INTO users (role, full_name, email, password_hash, salt)
            VALUES ('teacher', ?, ?, ?, ?)
            """,
            (full_name, email.lower(), pwd_hash, salt),
        )
        created += 1

    cur.execute("SELECT id FROM users WHERE role = 'teacher' ORDER BY id")
    teacher_ids = [int(row["id"]) for row in cur.fetchall()]
    discipline_names = sorted(discipline_map.keys())

    assigned_links = 0
    for idx, teacher_id in enumerate(teacher_ids):
        target_count = 3 + (idx % 2)
        start = (idx * 2) % len(discipline_names)
        picked_names = []
        for shift in range(target_count):
            picked_names.append(discipline_names[(start + shift) % len(discipline_names)])

        for name in picked_names:
            did = int(discipline_map[name])
            insert_ignore(
                cur,
                "teacher_disciplines",
                ("teacher_id", "discipline_id"),
                (teacher_id, did),
                conflict_columns=("teacher_id", "discipline_id"),
            )
            assigned_links += 1

        cur.execute(
            "SELECT MIN(discipline_id) AS primary_discipline FROM teacher_disciplines WHERE teacher_id = ?",
            (teacher_id,),
        )
        primary = cur.fetchone()
        primary_id = int(primary["primary_discipline"]) if primary and primary["primary_discipline"] else None
        if primary_id:
            cur.execute("UPDATE users SET discipline_id = ? WHERE id = ?", (primary_id, teacher_id))

    conn.commit()
    conn.close()

    return {"created": created, "updated": updated, "teacher_links": assigned_links, "disciplines": len(discipline_map)}


if __name__ == "__main__":
    result = seed()
    print("Discipline teachers seeded:")
    print(f"- created: {result['created']}")
    print(f"- updated: {result['updated']}")
    print(f"- disciplines total: {result['disciplines']}")
    print(f"- teacher-discipline links touched: {result['teacher_links']}")
    print("- default password for new teachers: Teacher123!")
