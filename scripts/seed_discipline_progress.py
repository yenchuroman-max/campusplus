from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db import connect, init_db, insert_ignore
from app.security import hash_password, new_salt
from scripts.seed_disciplines_teachers import seed as seed_teachers

random.seed(20260219)

LECTURE_TOPICS = {
    "Информационная безопасность и защита информации": [
        "Модель угроз и оценка рисков",
        "Криптографические основы и PKI",
        "Защита веб-приложений и контроль доступа",
        "Управление инцидентами ИБ",
    ],
    "Системы искусственного интеллекта": [
        "Основы машинного обучения",
        "Нейронные сети и регуляризация",
        "Оценка качества моделей и переобучение",
        "Этика и интерпретируемость моделей",
    ],
    "Web - программирование": [
        "HTTP, REST и архитектура клиент-сервер",
        "Frontend, формы и валидация",
        "Безопасность веб-приложений",
        "Производительность и кеширование",
    ],
    "Администрирование информационных систем": [
        "Мониторинг и журналы системы",
        "Резервное копирование и отказоустойчивость",
        "Управление доступом и ролями",
        "Сценарии автоматизации инфраструктуры",
    ],
    "Геоинформационные системы и технологии": [
        "Координатные системы и проекции",
        "Пространственные запросы и слои",
        "Анализ геоданных и визуализация",
        "Геостатистический анализ",
    ],
    "Компьютерные сети": [
        "Модель OSI и TCP/IP",
        "Маршрутизация и VLAN",
        "Диагностика сетевых проблем",
        "Сетевой мониторинг и безопасность",
    ],
    "Разработка мобильных приложений": [
        "Жизненный цикл мобильного приложения",
        "Работа с сетью и кэшом на клиенте",
        "Архитектура MVVM/MVI",
        "Публикация и поддержка релизов",
    ],
    "DevOps и CI/CD": [
        "Пайплайны сборки и тестирования",
        "Контейнеризация и оркестрация",
        "Наблюдаемость сервисов",
        "Стратегии деплоя",
    ],
    "Тестирование программного обеспечения": [
        "Типы тестирования",
        "Пирамида тестов",
        "Тест-дизайн и эквивалентные классы",
        "Автоматизация регрессионных проверок",
    ],
    "Облачные вычисления": [
        "IaaS/PaaS/SaaS модели",
        "Сетевые сервисы в облаке",
        "Управление расходами и масштабирование",
        "Резервирование и отказоустойчивость",
    ],
    "Анализ данных и BI": [
        "ETL и подготовка данных",
        "Метрики и KPI",
        "Визуализация и дашборды",
        "Качество и консистентность данных",
    ],
    "Программная инженерия": [
        "Жизненный цикл разработки ПО",
        "Архитектурные паттерны",
        "Технический долг и рефакторинг",
        "Управление требованиями",
    ],
}

GROUPS_BY_DISCIPLINE = {
    "Информационная безопасность и защита информации": ["ИБ-41", "ИБ-42"],
    "Системы искусственного интеллекта": ["ИИ-41", "ИИ-42"],
    "Web - программирование": ["WEB-41", "WEB-42"],
    "Администрирование информационных систем": ["АИС-41", "АИС-42"],
    "Геоинформационные системы и технологии": ["ГИС-41", "ГИС-42"],
    "Компьютерные сети": ["СЕТ-41", "СЕТ-42"],
    "Разработка мобильных приложений": ["МОБ-41", "МОБ-42"],
    "DevOps и CI/CD": ["ДЕВ-41", "ДЕВ-42"],
    "Тестирование программного обеспечения": ["QA-41", "QA-42"],
    "Облачные вычисления": ["CLOUD-41", "CLOUD-42"],
    "Анализ данных и BI": ["BI-41", "BI-42"],
    "Программная инженерия": ["SE-41", "SE-42"],
}


def _password_hash(password: str) -> tuple[str, str]:
    salt = new_salt()
    return hash_password(password, salt), salt


def _question_bank(topic: str) -> list[dict]:
    return [
        {
            "text": f"Какой подход наиболее корректен в теме «{topic}»?",
            "options": [
                "Пошаговый анализ и проверка гипотез",
                "Случайные действия без метрик",
                "Игнорирование требований",
                "Отсутствие контроля результатов",
            ],
            "correct_index": 0,
        },
        {
            "text": "Что повышает качество освоения материала?",
            "options": [
                "Практика и анализ ошибок",
                "Изучение только определения",
                "Пропуск обратной связи",
                "Отказ от повторения",
            ],
            "correct_index": 0,
        },
        {
            "text": "Какой результат считается признаком хорошего усвоения?",
            "options": [
                "Стабильный рост точности ответов",
                "Отсутствие динамики",
                "Снижение интереса к предмету",
                "Случайное угадывание",
            ],
            "correct_index": 0,
        },
        {
            "text": "Что делать после допущенной ошибки в тесте?",
            "options": [
                "Разобрать причину и повторить тему",
                "Игнорировать ошибку",
                "Сразу удалить попытку",
                "Отключить аналитику",
            ],
            "correct_index": 0,
        },
        {
            "text": "Как поддерживать высокий результат группы?",
            "options": [
                "Регулярно смотреть метрики и корректировать план",
                "Работать без структуры",
                "Не проводить промежуточные проверки",
                "Отменить тестирование",
            ],
            "correct_index": 0,
        },
    ]


def _ensure_students(cur, teacher_id: int, group_names: list[str]) -> list[int]:
    for group_name in group_names:
        insert_ignore(
            cur,
            "groups",
            ("name", "teacher_id"),
            (group_name, teacher_id),
            conflict_columns=("name",),
        )

    cur.execute(
        "SELECT id FROM users WHERE role = 'student' AND assigned_teacher_id = ? ORDER BY id",
        (teacher_id,),
    )
    existing = [int(row["id"]) for row in cur.fetchall()]
    if len(existing) >= 12:
        return existing

    need = 12 - len(existing)
    for idx in range(need):
        group_name = group_names[idx % len(group_names)]
        student_seq = len(existing) + idx + 1
        email = f"d{teacher_id}.student{student_seq:02d}@example.com"
        cur.execute("SELECT id FROM users WHERE email = ?", (email,))
        if cur.fetchone():
            continue
        full_name = f"Студент {teacher_id}-{student_seq:02d}"
        pwd_hash, salt = _password_hash("Student123!")
        cur.execute(
            """
            INSERT INTO users (role, full_name, email, password_hash, salt, assigned_teacher_id, student_group)
            VALUES ('student', ?, ?, ?, ?, ?, ?)
            """,
            (full_name, email, pwd_hash, salt, teacher_id, group_name),
        )

    cur.execute(
        "SELECT id FROM users WHERE role = 'student' AND assigned_teacher_id = ? ORDER BY id",
        (teacher_id,),
    )
    return [int(row["id"]) for row in cur.fetchall()]


def _ensure_tests_for_teacher(cur, teacher_id: int, discipline_id: int, discipline_name: str) -> list[dict]:
    cur.execute(
        """
        SELECT tests.id, tests.title
        FROM tests
        JOIN lectures ON lectures.id = tests.lecture_id
        WHERE lectures.teacher_id = ? AND lectures.discipline_id = ?
        ORDER BY tests.id
        """,
        (teacher_id, discipline_id),
    )
    existing_tests = [dict(row) for row in cur.fetchall()]
    if len(existing_tests) >= 4:
        return existing_tests

    topics = LECTURE_TOPICS.get(discipline_name, [discipline_name])
    now = datetime.now(timezone.utc)

    for idx, topic in enumerate(topics):
        created_at = (now - timedelta(days=idx * 2)).isoformat()
        lecture_title = f"{discipline_name}: модуль {idx + 1}"
        body = (
            f"Лекция по дисциплине «{discipline_name}». "
            f"Тема: {topic}. "
            "Содержит базовые понятия, типовые ошибки и практические задания для закрепления."
        )
        cur.execute(
            "INSERT INTO lectures (teacher_id, title, body, created_at, discipline_id) VALUES (?, ?, ?, ?, ?)",
            (teacher_id, lecture_title, body, created_at, discipline_id),
        )
        lecture_id = int(cur.lastrowid)

        test_title = f"Тест {idx + 1}: {discipline_name} — {topic}"
        cur.execute(
            "INSERT INTO tests (lecture_id, title, status, created_at) VALUES (?, ?, 'published', ?)",
            (lecture_id, test_title, created_at),
        )
        test_id = int(cur.lastrowid)

        for q in _question_bank(topic):
            cur.execute(
                "INSERT INTO questions (test_id, text, options_json, correct_index) VALUES (?, ?, ?, ?)",
                (test_id, q["text"], json.dumps(q["options"], ensure_ascii=False), q["correct_index"]),
            )

    cur.execute(
        """
        SELECT tests.id, tests.title
        FROM tests
        JOIN lectures ON lectures.id = tests.lecture_id
        WHERE lectures.teacher_id = ? AND lectures.discipline_id = ?
        ORDER BY tests.id
        """,
        (teacher_id, discipline_id),
    )
    return [dict(row) for row in cur.fetchall()]


def _seed_attempts(cur, test_id: int, student_ids: list[int], shift_days: int = 0) -> int:
    cur.execute("SELECT id, correct_index FROM questions WHERE test_id = ? ORDER BY id", (test_id,))
    questions = [dict(row) for row in cur.fetchall()]
    if not questions:
        return 0

    created = 0
    for idx, student_id in enumerate(student_ids):
        cur.execute(
            "SELECT id FROM attempts WHERE test_id = ? AND student_id = ? LIMIT 1",
            (test_id, student_id),
        )
        if cur.fetchone():
            continue

        if idx % 3 == 0:
            skill = 1.0
        elif idx % 3 == 1:
            skill = 0.55
        else:
            skill = 0.3

        correct_count = 0
        payload: list[tuple[int, int, int]] = []
        for q in questions:
            if random.random() <= skill:
                selected = int(q["correct_index"])
            else:
                options = [0, 1, 2, 3]
                if int(q["correct_index"]) in options:
                    options.remove(int(q["correct_index"]))
                selected = random.choice(options)
            is_correct = int(selected == int(q["correct_index"]))
            correct_count += is_correct
            payload.append((int(q["id"]), selected, is_correct))

        score = round((correct_count / len(questions)) * 100, 2)
        taken_at = (
            datetime.now(timezone.utc)
            - timedelta(days=shift_days + (idx % 7), hours=random.randint(0, 20))
        ).isoformat()

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
        created += 1

    return created


def seed_progress() -> dict[str, int]:
    init_db()
    seed_teachers()

    conn = connect()
    cur = conn.cursor()

    cur.execute("SELECT id AS teacher_id, full_name FROM users WHERE role = 'teacher' ORDER BY id")
    teachers = [dict(row) for row in cur.fetchall()]

    stats = {
        "teachers": len(teachers),
        "discipline_links": 0,
        "tests_total": 0,
        "attempts_created": 0,
        "students_linked": 0,
    }

    for teacher in teachers:
        teacher_id = int(teacher["teacher_id"])
        cur.execute(
            """
            SELECT d.id AS discipline_id, d.name AS discipline_name
            FROM teacher_disciplines td
            JOIN disciplines d ON d.id = td.discipline_id
            WHERE td.teacher_id = ?
            ORDER BY d.name
            """,
            (teacher_id,),
        )
        teacher_disciplines = [dict(row) for row in cur.fetchall()]
        stats["discipline_links"] += len(teacher_disciplines)
        if not teacher_disciplines:
            continue

        all_groups: list[str] = []
        for item in teacher_disciplines:
            discipline_name = str(item["discipline_name"])
            base_groups = GROUPS_BY_DISCIPLINE.get(discipline_name, ["Группа-1", "Группа-2"])
            for group_name in base_groups:
                all_groups.append(f"T{teacher_id}-{group_name}")
        unique_groups = sorted(set(all_groups))

        student_ids = _ensure_students(cur, teacher_id, unique_groups)
        stats["students_linked"] += len(student_ids)

        for discipline_idx, item in enumerate(teacher_disciplines):
            discipline_id = int(item["discipline_id"])
            discipline_name = str(item["discipline_name"])

            tests = _ensure_tests_for_teacher(cur, teacher_id, discipline_id, discipline_name)
            stats["tests_total"] += len(tests)

            for offset, test in enumerate(tests):
                stats["attempts_created"] += _seed_attempts(
                    cur,
                    int(test["id"]),
                    student_ids,
                    shift_days=(discipline_idx * 5) + (offset * 2),
                )

    conn.commit()
    conn.close()
    return stats


if __name__ == "__main__":
    result = seed_progress()
    print("Discipline progress seeded:")
    print(f"- teachers processed: {result['teachers']}")
    print(f"- teacher-discipline links: {result['discipline_links']}")
    print(f"- tests found/created: {result['tests_total']}")
    print(f"- students linked total: {result['students_linked']}")
    print(f"- attempts created: {result['attempts_created']}")
