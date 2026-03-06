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


def _insert_user(
    cur,
    role: str,
    full_name: str,
    email: str,
    password: str,
    assigned_teacher_id: int | None = None,
    student_group: str = "",
) -> int:
    salt = new_salt()
    password_hash = hash_password(password, salt)
    cur.execute(
        """
        INSERT INTO users (role, full_name, email, password_hash, salt, assigned_teacher_id, student_group)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (role, full_name, email.lower(), password_hash, salt, assigned_teacher_id, student_group),
    )
    return int(cur.lastrowid)


def _clear_all(cur) -> None:
    cur.execute("DELETE FROM answers")
    cur.execute("DELETE FROM attempts")
    cur.execute("DELETE FROM questions")
    cur.execute("DELETE FROM tests")
    cur.execute("DELETE FROM lectures")
    cur.execute("DELETE FROM groups")
    cur.execute("DELETE FROM audit")
    cur.execute("DELETE FROM users")
    cur.execute("DELETE FROM sqlite_sequence")


def _question_bank(lecture_title: str) -> list[dict]:
    return [
        {
            "text": f"Какой тезис лучше всего отражает тему «{lecture_title}»?",
            "options": [
                "Системный подход и последовательные шаги решения",
                "Случайный подбор без критериев",
                "Игнорирование входных данных",
                "Отсутствие проверки результата",
            ],
            "correct_index": 0,
        },
        {
            "text": "Что является корректной практикой в учебном анализе результатов?",
            "options": [
                "Фиксировать метрики и сравнивать динамику",
                "Оценивать только по одному случаю",
                "Не учитывать ошибки",
                "Не хранить историю попыток",
            ],
            "correct_index": 0,
        },
        {
            "text": "Что повышает качество подготовки к тесту?",
            "options": [
                "Повторение материала и практика на вопросах",
                "Пропуск разбора сложных тем",
                "Полный отказ от обратной связи",
                "Сокрытие результатов",
            ],
            "correct_index": 0,
        },
        {
            "text": "Какой подход улучшает итоговый результат группы?",
            "options": [
                "Регулярный анализ ошибок и коррекция плана",
                "Отсутствие структуры занятий",
                "Одинаковый темп без адаптации",
                "Отмена контрольных точек",
            ],
            "correct_index": 0,
        },
        {
            "text": "Что важно после прохождения теста?",
            "options": [
                "Разобрать неверные ответы и закрепить тему",
                "Сразу забыть материал",
                "Удалить историю попыток",
                "Игнорировать рекомендации",
            ],
            "correct_index": 0,
        },
    ]


def _build_student_tiers(student_ids: list[int]) -> dict[int, str]:
    tiers: dict[int, str] = {}
    for idx, student_id in enumerate(student_ids):
        if idx < 3:
            tiers[student_id] = "perfect"
        elif idx < 7:
            tiers[student_id] = "mixed"
        else:
            tiers[student_id] = "weak"
    return tiers


def _tier_skill(tier: str, test_idx: int) -> float:
    if tier == "perfect":
        return 1.0
    if tier == "mixed":
        base = 0.50 + (0.02 * test_idx)
        return max(0.45, min(0.55, base))
    base = 0.24 + (0.01 * test_idx)
    return max(0.18, min(0.35, base))


def seed_showcase(force: bool = False) -> dict:
    random.seed(20260219)
    init_db()
    conn = connect()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS cnt FROM users")
    existing = int(cur.fetchone()["cnt"])
    if existing and not force:
        raise RuntimeError("База не пустая. Запустите с --force для полной пересборки данных.")
    if existing and force:
        _clear_all(cur)

    stats = {
        "groups": 0,
        "teachers": 0,
        "students": 0,
        "lectures": 0,
        "tests": 0,
        "questions": 0,
        "attempts": 0,
        "answers": 0,
        "tier_perfect": 0,
        "tier_mixed": 0,
        "tier_weak": 0,
    }

    _insert_user(cur, "admin", "Администратор Системы", "admin@example.com", "Admin123!")

    teacher_specs = [
        ("Соколов Андрей Викторович", "teacher1@example.com", "Тест-1"),
        ("Миронова Елена Павловна", "teacher2@example.com", "Тест-2"),
        ("Кравцов Илья Романович", "teacher3@example.com", "Тест-3"),
    ]

    teacher_password = "Teacher123!"
    teacher_ids: list[int] = []
    teacher_by_group: dict[str, int] = {}

    for full_name, email, group_name in teacher_specs:
        teacher_id = _insert_user(cur, "teacher", full_name, email, teacher_password)
        teacher_ids.append(teacher_id)
        teacher_by_group[group_name] = teacher_id
        stats["teachers"] += 1

    for group_name in ("Тест-1", "Тест-2", "Тест-3"):
        cur.execute(
            "INSERT INTO groups (name, teacher_id) VALUES (?, ?)",
            (group_name, teacher_by_group[group_name]),
        )
        stats["groups"] += 1

    last_names = [
        "Иванов", "Петров", "Смирнов", "Кузнецов", "Попов", "Волков", "Соколов", "Лебедев", "Козлов", "Новиков",
        "Морозов", "Егоров", "Павлов", "Семёнов", "Голубев", "Виноградов", "Богданов", "Воробьёв", "Фёдоров", "Михайлов",
        "Беляев", "Тарасов", "Баранов", "Фролов", "Антонов", "Данилов", "Николаев", "Жуков", "Комаров", "Орлов",
    ]
    first_names = [
        "Алексей", "Дмитрий", "Максим", "Иван", "Никита", "Артём", "Кирилл", "Егор", "Павел", "Роман",
        "Алина", "Мария", "София", "Полина", "Екатерина", "Виктория", "Анна", "Дарья", "Ольга", "Наталья",
        "Ирина", "Татьяна", "Вероника", "Ксения", "Ульяна", "Яна", "Ева", "Кристина", "Агата", "Людмила",
    ]
    patronymics = [
        "Андреевич", "Сергеевич", "Павлович", "Игоревич", "Викторович", "Романович", "Олегович", "Денисович", "Максимович", "Алексеевич",
        "Андреевна", "Сергеевна", "Павловна", "Игоревна", "Викторовна", "Романовна", "Олеговна", "Денисовна", "Максимовна", "Алексеевна",
    ]

    students_by_teacher: dict[int, list[int]] = {teacher_id: [] for teacher_id in teacher_ids}
    group_names = ["Тест-1", "Тест-2", "Тест-3"]

    for idx in range(30):
        group_name = group_names[idx // 10]
        teacher_id = teacher_by_group[group_name]
        full_name = f"{last_names[idx]} {first_names[idx]} {patronymics[idx % len(patronymics)]}"
        email = f"student{idx + 1:02d}@example.com"
        student_id = _insert_user(
            cur,
            "student",
            full_name,
            email,
            "Student123!",
            assigned_teacher_id=teacher_id,
            student_group=group_name,
        )
        students_by_teacher[teacher_id].append(student_id)
        stats["students"] += 1

    lecture_specs = [
        (
            "Алгоритмы и структуры данных",
            "Big-O, массивы, списки, стек/очередь, базовые сортировки и поиск.",
        ),
        (
            "Базы данных и SQL",
            "Реляционные таблицы, ключи, JOIN, агрегаты, нормализация и транзакции.",
        ),
        (
            "Компьютерные сети и Web",
            "OSI/TCP-IP, HTTP, DNS, клиент-серверная модель и базовая безопасность.",
        ),
    ]

    now = datetime.utcnow()
    student_tiers_by_teacher: dict[int, dict[int, str]] = {
        teacher_id: _build_student_tiers(students_by_teacher[teacher_id]) for teacher_id in teacher_ids
    }
    for teacher_tiers in student_tiers_by_teacher.values():
        for tier in teacher_tiers.values():
            if tier == "perfect":
                stats["tier_perfect"] += 1
            elif tier == "mixed":
                stats["tier_mixed"] += 1
            else:
                stats["tier_weak"] += 1

    for teacher_idx, teacher_id in enumerate(teacher_ids):
        for test_idx, lecture_spec in enumerate(lecture_specs):
            lecture_title, lecture_topic_text = lecture_spec
            created_at = (now - timedelta(days=14 - test_idx * 3 - teacher_idx)).isoformat()
            full_lecture_title = f"{lecture_title} — модуль {test_idx + 1}"
            lecture_body = (
                f"Лекция «{full_lecture_title}».\n"
                f"{lecture_topic_text} "
                "Разбор ключевых понятий, примеров и типичных ошибок. "
                "Материал используется для итогового тестирования группы."
            )

            cur.execute(
                "INSERT INTO lectures (teacher_id, title, body, created_at) VALUES (?, ?, ?, ?)",
                (teacher_id, full_lecture_title, lecture_body, created_at),
            )
            lecture_id = int(cur.lastrowid)
            stats["lectures"] += 1

            cur.execute(
                "INSERT INTO tests (lecture_id, title, status, created_at) VALUES (?, ?, 'published', ?)",
                (lecture_id, f"Тест {test_idx + 1}: {full_lecture_title}", created_at),
            )
            test_id = int(cur.lastrowid)
            stats["tests"] += 1

            questions = _question_bank(full_lecture_title)
            question_rows: list[dict] = []
            for q in questions:
                cur.execute(
                    "INSERT INTO questions (test_id, text, options_json, correct_index) VALUES (?, ?, ?, ?)",
                    (test_id, q["text"], json.dumps(q["options"], ensure_ascii=False), int(q["correct_index"])),
                )
                question_rows.append({"id": int(cur.lastrowid), "correct_index": int(q["correct_index"])})
                stats["questions"] += 1

            for student_id in students_by_teacher[teacher_id]:
                tier = student_tiers_by_teacher[teacher_id][student_id]
                skill = _tier_skill(tier, test_idx)
                correct_count = 0
                answers_payload: list[tuple[int, int, int]] = []

                for question in question_rows:
                    if random.random() <= skill:
                        selected = question["correct_index"]
                    else:
                        wrong = [0, 1, 2, 3]
                        wrong.remove(question["correct_index"])
                        selected = random.choice(wrong)
                    is_correct = int(selected == question["correct_index"])
                    correct_count += is_correct
                    answers_payload.append((question["id"], selected, is_correct))

                score = round((correct_count / len(question_rows)) * 100, 2)
                taken_at = (now - timedelta(days=random.randint(0, 10), hours=random.randint(0, 23))).isoformat()

                cur.execute(
                    "INSERT INTO attempts (test_id, student_id, score, taken_at) VALUES (?, ?, ?, ?)",
                    (test_id, student_id, score, taken_at),
                )
                attempt_id = int(cur.lastrowid)
                stats["attempts"] += 1

                for question_id, selected, is_correct in answers_payload:
                    cur.execute(
                        "INSERT INTO answers (attempt_id, question_id, selected_index, is_correct) VALUES (?, ?, ?, ?)",
                        (attempt_id, question_id, selected, is_correct),
                    )
                    stats["answers"] += 1

    conn.commit()
    conn.close()

    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed showcase data: 3 teachers, 30 students, 9 tests.")
    parser.add_argument("--force", action="store_true", help="Очистить текущие данные и заполнить заново.")
    args = parser.parse_args()

    result = seed_showcase(force=args.force)
    print("Showcase data created:")
    print(f"- groups: {result['groups']}")
    print(f"- teachers: {result['teachers']}")
    print(f"- students: {result['students']}")
    print(f"- lectures: {result['lectures']}")
    print(f"- tests: {result['tests']}")
    print(f"- questions: {result['questions']}")
    print(f"- attempts: {result['attempts']}")
    print(f"- answers: {result['answers']}")
    print(f"- tier perfect: {result['tier_perfect']}")
    print(f"- tier mixed: {result['tier_mixed']}")
    print(f"- tier weak: {result['tier_weak']}")
    print("\nTeachers:")
    print("- teacher1@example.com / Teacher123!")
    print("- teacher2@example.com / Teacher123!")
    print("- teacher3@example.com / Teacher123!")