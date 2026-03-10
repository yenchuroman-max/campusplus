from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db import connect, init_db
from app.security import hash_password, new_salt


def _insert_user(cur, role: str, full_name: str, email: str, password: str) -> int:
    salt = new_salt()
    password_hash = hash_password(password, salt)
    cur.execute(
        "INSERT INTO users (role, full_name, email, password_hash, salt) VALUES (?, ?, ?, ?, ?)",
        (role, full_name, email.lower(), password_hash, salt),
    )
    return cur.lastrowid


def _build_questions(topic: str, concepts: list[str]) -> list[dict]:
    questions = []
    for i in range(5):
        correct = concepts[i % len(concepts)]
        distractors = [c for c in concepts if c != correct]
        random.shuffle(distractors)
        options = [correct] + distractors[:3]
        random.shuffle(options)
        questions.append(
            {
                "text": f"Что относится к теме '{topic}'?",
                "options": options,
                "correct_index": options.index(correct),
            }
        )
    return questions


def _clear_all(cur) -> None:
    cur.execute("DELETE FROM answers")
    cur.execute("DELETE FROM attempts")
    cur.execute("DELETE FROM questions")
    cur.execute("DELETE FROM tests")
    cur.execute("DELETE FROM lectures")
    cur.execute("DELETE FROM users")
    try:
        cur.execute("DELETE FROM sqlite_sequence")
    except Exception:
        pass


def _seed(force: bool = False) -> dict:
    random.seed(42)
    init_db()
    conn = connect()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS cnt FROM users")
    existing = cur.fetchone()["cnt"]
    if existing and not force:
        raise RuntimeError(
            "В базе уже есть данные. Запустите с флагом --force для полной пересборки демо-данных."
        )
    if existing and force:
        _clear_all(cur)

    admin_id = _insert_user(cur, "admin", "Администратор Системы", "admin@example.com", "Admin123!")

    teacher_ids = [
        _insert_user(cur, "teacher", "Иван Петров", "teacher1@example.com", "Teacher123!"),
        _insert_user(cur, "teacher", "Елена Смирнова", "teacher2@example.com", "Teacher123!"),
    ]

    student_ids = [
        _insert_user(cur, "student", "Алина Иванова", "student1@example.com", "Student123!"),
        _insert_user(cur, "student", "Дмитрий Козлов", "student2@example.com", "Student123!"),
        _insert_user(cur, "student", "Мария Соколова", "student3@example.com", "Student123!"),
        _insert_user(cur, "student", "Артем Волков", "student4@example.com", "Student123!"),
        _insert_user(cur, "student", "Ольга Павлова", "student5@example.com", "Student123!"),
        _insert_user(cur, "student", "Никита Лебедев", "student6@example.com", "Student123!"),
    ]

    lecture_specs = [
        (
            teacher_ids[0],
            "Введение в алгоритмы",
            (
                "Алгоритм - это конечная последовательность шагов для решения задачи. "
                "Разбираем сложность, нотацию O-большое и примеры сортировок."
            ),
            ["алгоритм", "сложность", "O(n log n)", "сортировка", "поиск"],
        ),
        (
            teacher_ids[0],
            "Основы баз данных",
            (
                "Реляционная модель хранит данные в таблицах. "
                "Изучаем первичные ключи, связи, индексы и SQL-запросы."
            ),
            ["таблица", "индекс", "SELECT", "PRIMARY KEY", "JOIN"],
        ),
        (
            teacher_ids[1],
            "Python для начинающих",
            (
                "Переменные, типы данных, функции и циклы позволяют писать читаемый код. "
                "Также рассматриваем модули и обработку исключений."
            ),
            ["функция", "цикл", "модуль", "исключение", "список"],
        ),
        (
            teacher_ids[1],
            "Компьютерные сети",
            (
                "Пакетная передача данных строится на протоколах TCP/IP. "
                "Разбираем модель OSI, маршрутизацию и адресацию."
            ),
            ["TCP", "IP", "маршрутизация", "OSI", "адрес"],
        ),
    ]

    now = datetime.utcnow()
    test_ids: list[int] = []
    stats = {"lectures": 0, "tests": 0, "questions": 0, "attempts": 0, "answers": 0}

    for teacher_id, title, body, concepts in lecture_specs:
        created_at = (now - timedelta(days=random.randint(20, 90))).isoformat()
        cur.execute(
            "INSERT INTO lectures (teacher_id, title, body, created_at) VALUES (?, ?, ?, ?)",
            (teacher_id, title, body, created_at),
        )
        lecture_id = cur.lastrowid
        stats["lectures"] += 1

        cur.execute(
            "INSERT INTO tests (lecture_id, title, status, created_at) VALUES (?, ?, 'published', ?)",
            (lecture_id, f"Тест по теме: {title}", created_at),
        )
        test_id = cur.lastrowid
        test_ids.append(test_id)
        stats["tests"] += 1

        questions = _build_questions(title, concepts)
        for q in questions:
            cur.execute(
                "INSERT INTO questions (test_id, text, options_json, correct_index) VALUES (?, ?, ?, ?)",
                (test_id, q["text"], json.dumps(q["options"], ensure_ascii=False), q["correct_index"]),
            )
            stats["questions"] += 1

    for test_id in test_ids:
        cur.execute("SELECT id, correct_index FROM questions WHERE test_id = ? ORDER BY id", (test_id,))
        questions = [dict(r) for r in cur.fetchall()]
        for student_id in student_ids:
            attempts_count = random.randint(1, 3)
            skill = random.uniform(0.45, 0.92)
            for n in range(attempts_count):
                taken_at = (now - timedelta(days=random.randint(0, 30), hours=n)).isoformat()
                correct_answers = 0
                selected_payload = []
                for q in questions:
                    if random.random() < skill:
                        selected = q["correct_index"]
                    else:
                        wrong = [0, 1, 2, 3]
                        if q["correct_index"] in wrong:
                            wrong.remove(q["correct_index"])
                        selected = random.choice(wrong)
                    is_correct = int(selected == q["correct_index"])
                    if is_correct:
                        correct_answers += 1
                    selected_payload.append((q["id"], selected, is_correct))

                score = round((correct_answers / max(1, len(questions))) * 100, 2)
                cur.execute(
                    "INSERT INTO attempts (test_id, student_id, score, taken_at) VALUES (?, ?, ?, ?)",
                    (test_id, student_id, score, taken_at),
                )
                attempt_id = cur.lastrowid
                stats["attempts"] += 1

                for question_id, selected, is_correct in selected_payload:
                    cur.execute(
                        "INSERT INTO answers (attempt_id, question_id, selected_index, is_correct) VALUES (?, ?, ?, ?)",
                        (attempt_id, question_id, selected, is_correct),
                    )
                    stats["answers"] += 1

    conn.commit()
    conn.close()

    stats["users"] = 1 + len(teacher_ids) + len(student_ids)
    stats["admin_id"] = admin_id
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed demo data for the project.")
    parser.add_argument("--force", action="store_true", help="Очистить текущие данные и заполнить заново.")
    args = parser.parse_args()

    result = _seed(force=args.force)
    print("Demo data created:")
    for key in ("users", "lectures", "tests", "questions", "attempts", "answers"):
        print(f"- {key}: {result[key]}")
    print("\nLogins:")
    print("- admin@example.com / Admin123!")
    print("- teacher1@example.com / Teacher123!")
    print("- teacher2@example.com / Teacher123!")
    print("- student1@example.com..student6@example.com / Student123!")
