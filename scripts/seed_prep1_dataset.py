from __future__ import annotations

import json
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import connect
from app.security import hash_password, new_salt

TEACHER_LOGIN = "prep1"
TEACHER_PASSWORD = "123123"
TEACHER_NAME = "Тестовый Преподаватель"
STUDENT_PASSWORD = "123123"
STUDENT_TOTAL = 50

GROUP_NAMES = [
    "БИ-41.2",
    "БИ-42.1",
    "БИ-42.2",
    "БИ-42.3",
    "БИ-43.1",
    "БИ-43.2",
    "БИ-43.3",
    "БИ-44.1",
    "БИ-44.2",
    "БИ-44.3",
    "БИ-45.1",
    "БИ-45.2",
    "БИ-45.3",
    "БИ-46.1",
    "БИ-46.2",
]

MALE_LAST_NAMES = [
    "Иванов",
    "Петров",
    "Смирнов",
    "Кузнецов",
    "Попов",
    "Волков",
    "Соколов",
    "Михайлов",
    "Новиков",
    "Фёдоров",
    "Орлов",
    "Григорьев",
    "Белов",
    "Никитин",
    "Егоров",
]

FEMALE_LAST_NAMES = [
    "Иванова",
    "Петрова",
    "Смирнова",
    "Кузнецова",
    "Попова",
    "Волкова",
    "Соколова",
    "Михайлова",
    "Новикова",
    "Фёдорова",
    "Орлова",
    "Григорьева",
    "Белова",
    "Никитина",
    "Егорова",
]

MALE_FIRST_NAMES = [
    "Алексей",
    "Дмитрий",
    "Илья",
    "Кирилл",
    "Максим",
    "Никита",
    "Роман",
    "Тимофей",
    "Артём",
    "Егор",
    "Владислав",
    "Андрей",
]

FEMALE_FIRST_NAMES = [
    "Анна",
    "Вероника",
    "Дарья",
    "Елена",
    "Ксения",
    "Мария",
    "Полина",
    "София",
    "Ульяна",
    "Виктория",
    "Алиса",
    "Валерия",
]

MALE_PATRONYMICS = [
    "Андреевич",
    "Викторович",
    "Дмитриевич",
    "Игоревич",
    "Олегович",
    "Павлович",
    "Романович",
    "Сергеевич",
    "Юрьевич",
]

FEMALE_PATRONYMICS = [
    "Андреевна",
    "Викторовна",
    "Дмитриевна",
    "Игоревна",
    "Олеговна",
    "Павловна",
    "Романовна",
    "Сергеевна",
    "Юрьевна",
]


@dataclass(frozen=True)
class DisciplineSpec:
    name: str
    lectures: list[str]
    concepts: list[tuple[str, str]]


DISCIPLINE_SPECS: list[DisciplineSpec] = [
    DisciplineSpec(
        name="Программирование на Python",
        lectures=[
            "Синтаксис, типы данных и управляющие конструкции",
            "Функции, модули и обработка исключений",
            "ООП, файловая система и виртуальные окружения",
        ],
        concepts=[
            ("list", "упорядоченная изменяемая коллекция значений"),
            ("dict", "ассоциативная коллекция вида ключ-значение"),
            ("set", "коллекция уникальных элементов"),
            ("tuple", "неизменяемая упорядоченная последовательность"),
            ("try/except", "конструкция перехвата и обработки ошибок"),
            ("venv", "изолированное окружение зависимостей проекта"),
            ("class", "шаблон для создания объектов и описания поведения"),
            ("pip", "менеджер установки пакетов Python"),
            ("for", "цикл для перебора элементов коллекции"),
            ("with", "контекстный менеджер для безопасной работы с ресурсами"),
        ],
    ),
    DisciplineSpec(
        name="Базы данных и SQL",
        lectures=[
            "Реляционная модель, таблицы и ключи",
            "Запросы SELECT, JOIN и группировка",
            "Индексы, транзакции и целостность данных",
        ],
        concepts=[
            ("PRIMARY KEY", "уникальный идентификатор записи таблицы"),
            ("FOREIGN KEY", "ссылка на запись в связанной таблице"),
            ("INNER JOIN", "объединение строк с совпадающими ключами"),
            ("LEFT JOIN", "объединение с сохранением всех строк левой таблицы"),
            ("GROUP BY", "агрегация строк по выбранным полям"),
            ("HAVING", "фильтрация после группировки и агрегации"),
            ("INDEX", "структура для ускорения поиска и сортировки"),
            ("TRANSACTION", "набор операций с подтверждением или откатом"),
            ("UNIQUE", "ограничение уникальности значения в столбце"),
            ("NORMALIZATION", "снижение избыточности структуры данных"),
        ],
    ),
    DisciplineSpec(
        name="Компьютерные сети",
        lectures=[
            "Модель OSI и базовые сетевые протоколы",
            "IP-адресация, маршрутизация и подсети",
            "Диагностика сети и контроль доступа",
        ],
        concepts=[
            ("TCP", "протокол с подтверждением доставки и контролем порядка"),
            ("UDP", "протокол без установления соединения"),
            ("IP-адрес", "логический адрес узла в сети"),
            ("маска подсети", "определение сетевой и хостовой части адреса"),
            ("маршрутизатор", "устройство пересылки пакетов между сетями"),
            ("DNS", "служба преобразования доменных имен в IP-адреса"),
            ("NAT", "преобразование адресов между внутренней и внешней сетью"),
            ("VLAN", "логическое разделение сегментов в коммутируемой сети"),
            ("ping", "утилита проверки доступности узла"),
            ("traceroute", "утилита отображения маршрута до узла"),
        ],
    ),
    DisciplineSpec(
        name="Операционные системы Linux",
        lectures=[
            "Пользователи, права доступа и файловая система",
            "Процессы, службы и мониторинг состояния системы",
            "Сетевые сервисы, shell-скрипты и автоматизация",
        ],
        concepts=[
            ("chmod", "изменение прав доступа к файлам и каталогам"),
            ("chown", "смена владельца и группы файлов"),
            ("systemd", "подсистема управления службами и запуском"),
            ("journalctl", "просмотр журналов systemd"),
            ("bash", "командная оболочка для интерактивной работы"),
            ("cron", "планировщик периодического выполнения задач"),
            ("top", "мониторинг процессов в реальном времени"),
            ("ssh", "безопасный удаленный доступ к серверу"),
            ("grep", "поиск по шаблону в текстовых данных"),
            ("tar", "архивация и упаковка файлов"),
        ],
    ),
    DisciplineSpec(
        name="Информационная безопасность",
        lectures=[
            "Модель угроз, уязвимости и политика доступа",
            "Криптография, хэширование и управление ключами",
            "Аудит, журналирование и реагирование на инциденты",
        ],
        concepts=[
            ("конфиденциальность", "доступ к данным только для уполномоченных лиц"),
            ("целостность", "защита данных от несанкционированного изменения"),
            ("доступность", "обеспечение своевременного доступа к сервисам"),
            ("хэш-функция", "одностороннее преобразование данных в фиксированную строку"),
            ("двухфакторная аутентификация", "подтверждение личности двумя независимыми факторами"),
            ("роль пользователя", "набор разрешений в рамках модели RBAC"),
            ("логирование", "сбор и хранение событий безопасности"),
            ("шифрование", "преобразование данных для защиты содержимого"),
            ("резервное копирование", "создание копий данных для восстановления"),
            ("принцип минимальных привилегий", "выдача только необходимого набора прав"),
        ],
    ),
    DisciplineSpec(
        name="Веб-разработка",
        lectures=[
            "HTTP, REST и клиент-серверное взаимодействие",
            "Frontend: структура, стили и клиентские сценарии",
            "Backend: API, хранение данных и аутентификация",
        ],
        concepts=[
            ("HTTP GET", "запрос получения данных без изменения состояния"),
            ("HTTP POST", "запрос создания данных на сервере"),
            ("JSON", "формат обмена структурированными данными"),
            ("cookie", "механизм хранения состояния сессии на клиенте"),
            ("JWT", "токен с утверждениями для аутентификации"),
            ("CORS", "политика доступа между разными источниками"),
            ("CSRF", "атака подделки межсайтового запроса"),
            ("шаблон MVC", "разделение модели, представления и контроллера"),
            ("валидация", "проверка корректности входных данных"),
            ("ORM", "слой отображения объектов приложения на таблицы БД"),
        ],
    ),
    DisciplineSpec(
        name="Алгоритмы и структуры данных",
        lectures=[
            "Асимптотическая сложность и оценка эффективности",
            "Линейные структуры: массив, стек, очередь, список",
            "Деревья, графы и алгоритмы поиска пути",
        ],
        concepts=[
            ("Big O", "оценка роста времени выполнения по размеру входа"),
            ("стек", "структура LIFO с операциями push и pop"),
            ("очередь", "структура FIFO для последовательной обработки"),
            ("двусвязный список", "узлы с ссылками на предыдущий и следующий элемент"),
            ("бинарное дерево поиска", "дерево с упорядочиванием ключей по поддеревьям"),
            ("хэш-таблица", "структура для быстрого поиска по ключу"),
            ("граф", "множество вершин и ребер между ними"),
            ("поиск в ширину", "обход графа по уровням соседства"),
            ("поиск в глубину", "обход графа с погружением по ветвям"),
            ("динамическое программирование", "метод оптимизации через сохранение подзадач"),
        ],
    ),
]


def choose_unique_full_name(used: set[str], rng: random.Random) -> str:
    for _ in range(400):
        female = rng.random() < 0.5
        if female:
            full_name = f"{rng.choice(FEMALE_LAST_NAMES)} {rng.choice(FEMALE_FIRST_NAMES)} {rng.choice(FEMALE_PATRONYMICS)}"
        else:
            full_name = f"{rng.choice(MALE_LAST_NAMES)} {rng.choice(MALE_FIRST_NAMES)} {rng.choice(MALE_PATRONYMICS)}"
        if full_name not in used:
            used.add(full_name)
            return full_name
    suffix = len(used) + 1
    fallback = f"Студент Тестовый {suffix}"
    used.add(fallback)
    return fallback


def ensure_teacher(cur, login: str, password: str, full_name: str) -> int:
    cur.execute("SELECT id FROM users WHERE lower(email) = lower(?)", (login,))
    row = cur.fetchone()
    salt = new_salt()
    pw_hash = hash_password(password, salt)
    if row:
        teacher_id = int(row["id"])
        cur.execute(
            """
            UPDATE users
            SET role='teacher', full_name=?, password_hash=?, salt=?, student_group='', assigned_teacher_id=NULL
            WHERE id=?
            """,
            (full_name, pw_hash, salt, teacher_id),
        )
        return teacher_id

    cur.execute(
        """
        INSERT INTO users (role, full_name, email, password_hash, salt, student_group)
        VALUES ('teacher', ?, ?, ?, ?, '')
        """,
        (full_name, login, pw_hash, salt),
    )
    return int(cur.lastrowid)


def ensure_groups(cur, teacher_id: int, groups: list[str]) -> None:
    for group_name in groups:
        cur.execute("SELECT id FROM groups WHERE name = ?", (group_name,))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE groups SET teacher_id = ? WHERE id = ?", (teacher_id, int(row["id"])))
        else:
            cur.execute("INSERT INTO groups (name, teacher_id) VALUES (?, ?)", (group_name, teacher_id))


def ensure_disciplines_for_teacher(cur, teacher_id: int) -> dict[str, int]:
    discipline_ids: dict[str, int] = {}
    for spec in DISCIPLINE_SPECS:
        cur.execute("SELECT id FROM disciplines WHERE lower(name) = lower(?)", (spec.name,))
        row = cur.fetchone()
        if row:
            discipline_id = int(row["id"])
        else:
            cur.execute("INSERT INTO disciplines (name) VALUES (?)", (spec.name,))
            discipline_id = int(cur.lastrowid)
        discipline_ids[spec.name] = discipline_id

        cur.execute(
            "SELECT 1 FROM teacher_disciplines WHERE teacher_id = ? AND discipline_id = ?",
            (teacher_id, discipline_id),
        )
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO teacher_disciplines (teacher_id, discipline_id) VALUES (?, ?)",
                (teacher_id, discipline_id),
            )
    return discipline_ids


def cleanup_teacher_content(cur, teacher_id: int) -> None:
    cur.execute("SELECT id FROM lectures WHERE teacher_id = ?", (teacher_id,))
    lecture_ids = [int(row["id"]) for row in cur.fetchall()]
    if not lecture_ids:
        return

    lecture_placeholders = ",".join("?" for _ in lecture_ids)
    cur.execute(f"SELECT id FROM tests WHERE lecture_id IN ({lecture_placeholders})", tuple(lecture_ids))
    test_ids = [int(row["id"]) for row in cur.fetchall()]

    if test_ids:
        test_placeholders = ",".join("?" for _ in test_ids)
        cur.execute(f"SELECT id FROM attempts WHERE test_id IN ({test_placeholders})", tuple(test_ids))
        attempt_ids = [int(row["id"]) for row in cur.fetchall()]
        if attempt_ids:
            attempt_placeholders = ",".join("?" for _ in attempt_ids)
            cur.execute(f"DELETE FROM answers WHERE attempt_id IN ({attempt_placeholders})", tuple(attempt_ids))
            cur.execute(f"DELETE FROM attempts WHERE id IN ({attempt_placeholders})", tuple(attempt_ids))
        cur.execute(f"DELETE FROM questions WHERE test_id IN ({test_placeholders})", tuple(test_ids))
        cur.execute(f"DELETE FROM tests WHERE id IN ({test_placeholders})", tuple(test_ids))

    cur.execute(f"DELETE FROM lectures WHERE id IN ({lecture_placeholders})", tuple(lecture_ids))


def build_lecture_body(spec: DisciplineSpec, lecture_title: str) -> str:
    lines = [
        f"Дисциплина: {spec.name}",
        f"Тема лекции: {lecture_title}",
        "",
        "Ключевые тезисы:",
    ]
    for term, definition in spec.concepts:
        lines.append(f"- {term}: {definition}.")
    lines.append("")
    lines.append("Практическая часть: анализ кейсов, типовых ошибок и корректных решений.")
    return "\n".join(lines)


def create_tests_for_teacher(cur, teacher_id: int, discipline_ids: dict[str, int], rng: random.Random) -> list[dict[str, Any]]:
    created_tests: list[dict[str, Any]] = []
    now = datetime.utcnow()

    for spec in DISCIPLINE_SPECS:
        discipline_id = discipline_ids[spec.name]
        for lecture_index, lecture_title in enumerate(spec.lectures, start=1):
            created_at = (now - timedelta(days=rng.randint(8, 45), hours=rng.randint(0, 20))).isoformat()
            body = build_lecture_body(spec, lecture_title)
            cur.execute(
                """
                INSERT INTO lectures (teacher_id, title, body, created_at, discipline_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (teacher_id, lecture_title, body, created_at, discipline_id),
            )
            lecture_id = int(cur.lastrowid)

            test_title = f"Тест по теме: {lecture_title}"
            cur.execute(
                """
                INSERT INTO tests (lecture_id, title, status, created_at)
                VALUES (?, ?, 'published', ?)
                """,
                (lecture_id, test_title, created_at),
            )
            test_id = int(cur.lastrowid)

            concept_pool = spec.concepts
            for question_index in range(10):
                concept_idx = (question_index + lecture_index) % len(concept_pool)
                correct_term, definition = concept_pool[concept_idx]

                wrong_terms: list[str] = []
                for step in range(1, len(concept_pool) + 1):
                    candidate_term = concept_pool[(concept_idx + step) % len(concept_pool)][0]
                    if candidate_term == correct_term or candidate_term in wrong_terms:
                        continue
                    wrong_terms.append(candidate_term)
                    if len(wrong_terms) >= 3:
                        break

                options = [correct_term, *wrong_terms[:3]]
                rng.shuffle(options)
                correct_index = options.index(correct_term)
                question_text = f"Какой термин в дисциплине «{spec.name}» соответствует формулировке: {definition}?"

                cur.execute(
                    """
                    INSERT INTO questions (test_id, text, options_json, correct_index)
                    VALUES (?, ?, ?, ?)
                    """,
                    (test_id, question_text, json.dumps(options, ensure_ascii=False), correct_index),
                )

            created_tests.append(
                {
                    "id": test_id,
                    "title": test_title,
                    "discipline": spec.name,
                    "discipline_id": discipline_id,
                }
            )

    return created_tests


def upsert_students_for_teacher(cur, teacher_id: int, groups: list[str], rng: random.Random) -> list[dict[str, Any]]:
    used_names: set[str] = set()

    # Keep a pool of already assigned demo students, fix their names if needed.
    cur.execute(
        """
        SELECT id, email, full_name
        FROM users
        WHERE role = 'student'
          AND (
            assigned_teacher_id = ?
            OR lower(email) LIKE 'prep1_stud_%'
            OR lower(email) LIKE 'bi_student_%'
            OR lower(email) LIKE 'nogroup_%'
          )
        ORDER BY id
        """,
        (teacher_id,),
    )
    existing_demo = [dict(row) for row in cur.fetchall()]

    for row in existing_demo:
        if row.get("full_name"):
            used_names.add(str(row["full_name"]).strip())

    seeded_students: list[dict[str, Any]] = []

    for idx in range(1, STUDENT_TOTAL + 1):
        login = f"prep1_stud_{idx:03d}"
        group_name = rng.choice(groups)
        full_name = choose_unique_full_name(used_names, rng)
        salt = new_salt()
        pw_hash = hash_password(STUDENT_PASSWORD, salt)

        cur.execute("SELECT id FROM users WHERE lower(email) = lower(?)", (login,))
        row = cur.fetchone()
        if row:
            student_id = int(row["id"])
            cur.execute(
                """
                UPDATE users
                SET role='student', full_name=?, password_hash=?, salt=?, student_group=?, assigned_teacher_id=?
                WHERE id=?
                """,
                (full_name, pw_hash, salt, group_name, teacher_id, student_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO users (role, full_name, email, password_hash, salt, student_group, assigned_teacher_id)
                VALUES ('student', ?, ?, ?, ?, ?, ?)
                """,
                (full_name, login, pw_hash, salt, group_name, teacher_id),
            )
            student_id = int(cur.lastrowid)

        seeded_students.append({"id": student_id, "email": login, "full_name": full_name, "group": group_name})

    # Normalize any previously attached demo students to valid full names and valid groups.
    for row in existing_demo:
        email = str(row["email"]).strip().lower()
        if email.startswith("prep1_stud_"):
            continue
        student_id = int(row["id"])
        full_name = choose_unique_full_name(used_names, rng)
        group_name = rng.choice(groups)
        cur.execute(
            """
            UPDATE users
            SET role='student', full_name=?, student_group=?, assigned_teacher_id=?
            WHERE id=?
            """,
            (full_name, group_name, teacher_id, student_id),
        )

    # Keep groups linked to prep1 for every seeded student.
    student_group_map: dict[str, int] = {}
    for student in seeded_students:
        student_group_map[student["group"]] = student_group_map.get(student["group"], 0) + 1

    return seeded_students


def cleanup_attempts_for_students(cur, student_ids: list[int]) -> None:
    if not student_ids:
        return
    placeholders = ",".join("?" for _ in student_ids)
    cur.execute(f"SELECT id FROM attempts WHERE student_id IN ({placeholders})", tuple(student_ids))
    attempt_ids = [int(row["id"]) for row in cur.fetchall()]
    if not attempt_ids:
        return

    attempt_placeholders = ",".join("?" for _ in attempt_ids)
    cur.execute(f"DELETE FROM answers WHERE attempt_id IN ({attempt_placeholders})", tuple(attempt_ids))
    cur.execute(f"DELETE FROM attempts WHERE id IN ({attempt_placeholders})", tuple(attempt_ids))


def seed_attempts(cur, tests: list[dict[str, Any]], students: list[dict[str, Any]], rng: random.Random) -> dict[str, Any]:
    test_ids = [int(item["id"]) for item in tests]
    if not test_ids or not students:
        return {"attempts": 0, "passed": 0, "failed": 0}

    questions_by_test: dict[int, list[dict[str, int]]] = {}
    placeholders = ",".join("?" for _ in test_ids)
    cur.execute(
        f"SELECT id, test_id, correct_index FROM questions WHERE test_id IN ({placeholders}) ORDER BY test_id, id",
        tuple(test_ids),
    )
    for row in cur.fetchall():
        test_id = int(row["test_id"])
        questions_by_test.setdefault(test_id, []).append(
            {"id": int(row["id"]), "correct_index": int(row["correct_index"])}
        )

    attempts_created = 0
    passed_count = 0
    failed_count = 0

    now = datetime.utcnow()

    for idx, student in enumerate(students):
        student_id = int(student["id"])
        if idx < 16:
            take_count = 8
            accuracy_range = (0.78, 1.0)
        elif idx < 32:
            take_count = 6
            accuracy_range = (0.55, 0.82)
        elif idx < 44:
            take_count = 5
            accuracy_range = (0.25, 0.58)
        else:
            take_count = 2
            accuracy_range = (0.4, 0.72)

        chosen_tests = rng.sample(test_ids, min(take_count, len(test_ids)))
        for test_id in chosen_tests:
            questions = questions_by_test.get(test_id, [])
            if not questions:
                continue

            accuracy = rng.uniform(*accuracy_range)
            correct_needed = max(0, min(len(questions), round(len(questions) * accuracy)))
            correct_question_ids = {q["id"] for q in rng.sample(questions, correct_needed)}

            correct_answers = 0
            answers_payload: list[tuple[int, int, int]] = []
            for q in questions:
                question_id = int(q["id"])
                correct_index = int(q["correct_index"])
                if question_id in correct_question_ids:
                    selected_index = correct_index
                    is_correct = 1
                    correct_answers += 1
                else:
                    wrong_options = [0, 1, 2, 3]
                    if correct_index in wrong_options:
                        wrong_options.remove(correct_index)
                    selected_index = rng.choice(wrong_options) if wrong_options else (correct_index + 1) % 4
                    is_correct = 0
                answers_payload.append((question_id, selected_index, is_correct))

            score = round((correct_answers / len(questions)) * 100, 2)
            taken_at = (now - timedelta(days=rng.randint(0, 40), hours=rng.randint(0, 23), minutes=rng.randint(0, 59))).isoformat()

            cur.execute(
                "INSERT INTO attempts (test_id, student_id, score, taken_at) VALUES (?, ?, ?, ?)",
                (test_id, student_id, score, taken_at),
            )
            attempt_id = int(cur.lastrowid)

            for question_id, selected_index, is_correct in answers_payload:
                cur.execute(
                    """
                    INSERT INTO answers (attempt_id, question_id, selected_index, is_correct)
                    VALUES (?, ?, ?, ?)
                    """,
                    (attempt_id, question_id, selected_index, is_correct),
                )

            attempts_created += 1
            if score >= 60.0:
                passed_count += 1
            else:
                failed_count += 1

    return {"attempts": attempts_created, "passed": passed_count, "failed": failed_count}


def print_summary(cur, teacher_id: int, tests: list[dict[str, Any]], students: list[dict[str, Any]], attempts_stats: dict[str, Any]) -> None:
    cur.execute("SELECT full_name FROM users WHERE id = ?", (teacher_id,))
    teacher_name = cur.fetchone()["full_name"]
    cur.execute("SELECT COUNT(*) AS cnt FROM groups WHERE teacher_id = ?", (teacher_id,))
    groups_count = int(cur.fetchone()["cnt"])
    cur.execute("SELECT COUNT(*) AS cnt FROM teacher_disciplines WHERE teacher_id = ?", (teacher_id,))
    teacher_disciplines_count = int(cur.fetchone()["cnt"])
    cur.execute("SELECT COUNT(*) AS cnt FROM lectures WHERE teacher_id = ?", (teacher_id,))
    lectures_count = int(cur.fetchone()["cnt"])
    cur.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM users
        WHERE role = 'student' AND assigned_teacher_id = ?
        """,
        (teacher_id,),
    )
    linked_students = int(cur.fetchone()["cnt"])

    discipline_breakdown: dict[str, int] = {}
    for item in tests:
        discipline_breakdown[item["discipline"]] = discipline_breakdown.get(item["discipline"], 0) + 1

    print("=== PREP1 DATASET READY ===")
    print(f"Teacher: {teacher_name} ({TEACHER_LOGIN})")
    print(f"Groups attached: {groups_count}")
    print(f"Teacher disciplines: {teacher_disciplines_count}")
    print(f"Lectures created: {lectures_count}")
    print(f"Tests created: {len(tests)}")
    print(f"Seeded students (prep1_stud_*): {len(students)}")
    print(f"Total students linked to prep1: {linked_students}")
    print(
        f"Attempts created: {attempts_stats['attempts']} | passed: {attempts_stats['passed']} | failed: {attempts_stats['failed']}"
    )
    print("Tests per discipline:")
    for discipline_name in sorted(discipline_breakdown.keys()):
        print(f"- {discipline_name}: {discipline_breakdown[discipline_name]}")
    print("Sample student logins:")
    for student in students[:10]:
        print(f"- {student['email']} ({student['full_name']}) [{student['group']}]")


def main() -> None:
    rng = random.Random(20260306)
    conn = connect()
    cur = conn.cursor()

    teacher_id = ensure_teacher(cur, TEACHER_LOGIN, TEACHER_PASSWORD, TEACHER_NAME)
    ensure_groups(cur, teacher_id, GROUP_NAMES)

    discipline_ids = ensure_disciplines_for_teacher(cur, teacher_id)
    cleanup_teacher_content(cur, teacher_id)

    students = upsert_students_for_teacher(cur, teacher_id, GROUP_NAMES, rng)
    cleanup_attempts_for_students(cur, [int(item["id"]) for item in students])

    tests = create_tests_for_teacher(cur, teacher_id, discipline_ids, rng)
    attempts_stats = seed_attempts(cur, tests, students, rng)

    conn.commit()
    print_summary(cur, teacher_id, tests, students, attempts_stats)
    conn.close()


if __name__ == "__main__":
    main()
