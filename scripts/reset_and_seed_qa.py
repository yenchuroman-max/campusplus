from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.ai import generate_questions
from app.db import DB_PATH, connect, init_db
from app.security import hash_password, new_salt

random.seed(20260219)

DISCIPLINES = [
    "Информационная безопасность",
    "Системы искусственного интеллекта",
    "Web-программирование",
    "Администрирование ИС",
    "Геоинформационные системы",
    "Компьютерные сети",
    "Разработка мобильных приложений",
    "DevOps и CI/CD",
    "Тестирование ПО",
    "Облачные вычисления",
]


def _password_pair(password: str) -> tuple[str, str]:
    salt = new_salt()
    return hash_password(password, salt), salt


def _drop_db() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()


def _create_user(
    cur,
    role: str,
    full_name: str,
    email: str,
    password: str,
    assigned_teacher_id: int | None = None,
    student_group: str = "",
    discipline_id: int | None = None,
) -> int:
    pwd_hash, salt = _password_pair(password)
    cur.execute(
        """
        INSERT INTO users (
            role, full_name, email, password_hash, salt, assigned_teacher_id, student_group, discipline_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            role,
            full_name,
            email.lower().strip(),
            pwd_hash,
            salt,
            assigned_teacher_id,
            student_group,
            discipline_id,
        ),
    )
    return int(cur.lastrowid)


def _create_test_with_ai_questions(cur, teacher_id: int, discipline_id: int, discipline_name: str, idx: int) -> int:
    created_at = (datetime.now(timezone.utc) - timedelta(days=idx)).isoformat()
    lecture_title = f"{discipline_name}: модуль 1"
    lecture_body = (
        f"Лекция по дисциплине '{discipline_name}'. "
        "Разбор основных понятий, типовых ошибок, практических кейсов и критериев оценки знаний. "
        "Материал используется для итогового теста и аналитики прогресса."
    )

    cur.execute(
        "INSERT INTO lectures (teacher_id, title, body, created_at, discipline_id) VALUES (?, ?, ?, ?, ?)",
        (teacher_id, lecture_title, lecture_body, created_at, discipline_id),
    )
    lecture_id = int(cur.lastrowid)

    cur.execute(
        "INSERT INTO tests (lecture_id, title, status, created_at) VALUES (?, ?, 'published', ?)",
        (lecture_id, f"ИИ тест: {discipline_name}", created_at),
    )
    test_id = int(cur.lastrowid)

    generated = generate_questions(lecture_body, count=10, difficulty="medium")

    if not generated:
        generated = [
            {
                "text": f"Что является ключевым принципом дисциплины '{discipline_name}'?",
                "options": [
                    "Системный анализ и проверка гипотез",
                    "Игнорирование метрик",
                    "Случайные решения",
                    "Отказ от практики",
                ],
                "correct_index": 0,
            }
            for _ in range(10)
        ]

    for item in generated[:10]:
        options = item.get("options") or []
        if len(options) < 4:
            options = (options + ["Вариант A", "Вариант B", "Вариант C", "Вариант D"])[:4]
        correct_index = int(item.get("correct_index", 0))
        if correct_index < 0 or correct_index > 3:
            correct_index = 0

        cur.execute(
            "INSERT INTO questions (test_id, text, options_json, correct_index) VALUES (?, ?, ?, ?)",
            (
                test_id,
                str(item.get("text") or f"Вопрос по теме {discipline_name}"),
                json.dumps(options, ensure_ascii=False),
                correct_index,
            ),
        )

    return test_id


def _seed_attempt(cur, test_id: int, student_id: int, skill: float, days_shift: int) -> None:
    cur.execute("SELECT id, correct_index FROM questions WHERE test_id = ? ORDER BY id", (test_id,))
    questions = [dict(row) for row in cur.fetchall()]
    if not questions:
        return

    correct_count = 0
    payload: list[tuple[int, int, int]] = []

    for q in questions:
        correct_idx = int(q["correct_index"])
        if random.random() <= skill:
            selected = correct_idx
        else:
            wrong = [0, 1, 2, 3]
            if correct_idx in wrong:
                wrong.remove(correct_idx)
            selected = random.choice(wrong)
        is_correct = int(selected == correct_idx)
        correct_count += is_correct
        payload.append((int(q["id"]), selected, is_correct))

    score = round((correct_count / max(1, len(questions))) * 100, 2)
    taken_at = (datetime.now(timezone.utc) - timedelta(days=days_shift, hours=random.randint(0, 23))).isoformat()

    cur.execute(
        "INSERT INTO attempts (test_id, student_id, score, taken_at) VALUES (?, ?, ?, ?)",
        (test_id, student_id, score, taken_at),
    )
    attempt_id = int(cur.lastrowid)

    for question_id, selected_idx, is_correct in payload:
        cur.execute(
            "INSERT INTO answers (attempt_id, question_id, selected_index, is_correct) VALUES (?, ?, ?, ?)",
            (attempt_id, question_id, selected_idx, is_correct),
        )


def run() -> None:
    # Разрешаем fallback-генерацию, чтобы вопросы создавались даже без внешних API-ключей
    import os

    os.environ["AI_ALLOW_FALLBACK"] = "true"

    _drop_db()
    init_db()

    conn = connect()
    cur = conn.cursor()

    cur.execute("DELETE FROM teacher_disciplines")
    cur.execute("DELETE FROM disciplines")

    discipline_ids: list[tuple[int, str]] = []
    for name in DISCIPLINES:
        cur.execute("INSERT INTO disciplines (name) VALUES (?)", (name,))
        discipline_ids.append((int(cur.lastrowid), name))

    admin_id = _create_user(cur, "admin", "QA Admin", "admin@qa.qa", "123123")

    teacher_ids: list[tuple[int, str, str, int, str]] = []
    for idx, (discipline_id, discipline_name) in enumerate(discipline_ids, start=1):
        email = f"teacher{idx:02d}@qa.qa"
        full_name = f"Преподаватель {idx:02d}"
        teacher_id = _create_user(
            cur,
            "teacher",
            full_name,
            email,
            "123123",
            discipline_id=discipline_id,
        )
        teacher_ids.append((teacher_id, full_name, email, discipline_id, discipline_name))
        cur.execute(
            "INSERT INTO teacher_disciplines (teacher_id, discipline_id) VALUES (?, ?)",
            (teacher_id, discipline_id),
        )

        group_name = f"QA-{idx:02d}"
        cur.execute("INSERT INTO groups (name, teacher_id) VALUES (?, ?)", (group_name, teacher_id))

    students: list[tuple[int, str]] = []
    for idx in range(1, 101):
        teacher_pick = teacher_ids[(idx - 1) % len(teacher_ids)]
        assigned_teacher_id = teacher_pick[0]
        group_name = f"QA-{((idx - 1) % len(teacher_ids)) + 1:02d}"
        email = f"student{idx:03d}@qa.qa"
        full_name = f"Студент {idx:03d}"

        student_id = _create_user(
            cur,
            "student",
            full_name,
            email,
            "123123",
            assigned_teacher_id=assigned_teacher_id,
            student_group=group_name,
        )
        students.append((student_id, email))

    tests: list[tuple[int, int, str]] = []
    for idx, teacher in enumerate(teacher_ids, start=1):
        teacher_id, _full_name, _email, discipline_id, discipline_name = teacher
        test_id = _create_test_with_ai_questions(cur, teacher_id, discipline_id, discipline_name, idx)
        tests.append((test_id, discipline_id, discipline_name))

    # Каждый студент проходит по одному тесту на каждую из 10 дисциплин
    for student_idx, (student_id, _email) in enumerate(students, start=1):
        base_skill = 0.25 + ((student_idx % 10) * 0.07)
        for test_idx, (test_id, _discipline_id, _discipline_name) in enumerate(tests, start=1):
            skill = max(0.15, min(0.95, base_skill + ((test_idx % 3) - 1) * 0.08))
            _seed_attempt(cur, test_id, student_id, skill, days_shift=(student_idx + test_idx) % 14)

    conn.commit()

    cur.execute("SELECT COUNT(*) AS c FROM users WHERE role='admin'")
    admins = int(cur.fetchone()["c"])
    cur.execute("SELECT COUNT(*) AS c FROM users WHERE role='teacher'")
    teachers = int(cur.fetchone()["c"])
    cur.execute("SELECT COUNT(*) AS c FROM users WHERE role='student'")
    students_count = int(cur.fetchone()["c"])
    cur.execute("SELECT COUNT(*) AS c FROM disciplines")
    disciplines_count = int(cur.fetchone()["c"])
    cur.execute("SELECT COUNT(*) AS c FROM tests")
    tests_count = int(cur.fetchone()["c"])
    cur.execute("SELECT COUNT(*) AS c FROM attempts")
    attempts_count = int(cur.fetchone()["c"])

    cur.execute("SELECT id, title FROM tests ORDER BY id LIMIT 1")
    sample_test = cur.fetchone()

    conn.close()

    print("QA reset completed")
    print(f"admin: {admins}, teachers: {teachers}, students: {students_count}")
    print(f"disciplines: {disciplines_count}, tests: {tests_count}, attempts: {attempts_count}")
    print(f"master admin: admin@qa.qa / 123123 (id={admin_id})")
    if sample_test:
        print(f"sample test: id={sample_test['id']} title={sample_test['title']}")

    print("\nTeachers:")
    for _teacher_id, full_name, email, _discipline_id, discipline_name in teacher_ids:
        print(f"- {full_name} | {email} | 123123 | дисциплина: {discipline_name}")


if __name__ == "__main__":
    run()
