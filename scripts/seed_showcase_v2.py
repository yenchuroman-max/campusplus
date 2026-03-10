"""
Сид-скрипт: 5 преподов (топовые учёные), 5 студентов, 5 дисциплин, лекции + тесты.
Запуск: python scripts/seed_showcase_v2.py --force
"""
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

from app.db import connect, init_db, insert_ignore
from app.security import hash_password, new_salt


# ── Helpers ───────────────────────────────────────────────────────────────────

def _insert_user(cur, role, full_name, email, password, group=None, discipline_id=None):
    salt = new_salt()
    pw_hash = hash_password(password, salt)
    inserted = insert_ignore(
        cur,
        "users",
        ("role", "full_name", "email", "password_hash", "salt", "student_group", "discipline_id"),
        (role, full_name, email.lower(), pw_hash, salt, group, discipline_id),
        conflict_columns=("email",),
    )
    if inserted and cur.lastrowid:
        return cur.lastrowid
    cur.execute("SELECT id FROM users WHERE email = ?", (email.lower(),))
    return cur.fetchone()[0]


def _clear_all(cur):
    for tbl in ("answers", "attempts", "questions", "tests", "lectures",
                 "teacher_disciplines", "groups", "users"):
        cur.execute(f"DELETE FROM {tbl}")
    try:
        cur.execute("DELETE FROM sqlite_sequence")
    except Exception:
        pass


# ── Lecture content (реальные тексты уровня 2-3 курса) ─────────────────────

LECTURES = {
    "Операционные системы": [
        (
            "Архитектура ОС и управление процессами",
            """Операционная система (ОС) — это комплекс программ, обеспечивающий управление аппаратными ресурсами компьютера и предоставляющий интерфейс для пользователя и приложений.

Основные функции ОС:
1. Управление процессами — создание, планирование, синхронизация и завершение процессов.
2. Управление памятью — распределение оперативной памяти между процессами, виртуальная память, страничная организация.
3. Управление файловой системой — создание, удаление, чтение и запись файлов, разграничение доступа.
4. Управление устройствами ввода-вывода — драйверы, буферизация, контроллеры прерываний.
5. Обеспечение безопасности — аутентификация, авторизация, аудит.

Процесс — это экземпляр выполняемой программы, включающий код, данные, стек и контекст выполнения. Каждый процесс имеет уникальный идентификатор PID. Планировщик процессов (scheduler) определяет порядок выполнения процессов на CPU.

Основные алгоритмы планирования:
- FCFS (First Come First Served) — обслуживание в порядке очереди.
- SJF (Shortest Job First) — первым выполняется самый короткий процесс.
- Round Robin — каждому процессу выделяется квант времени по кругу.
- Priority Scheduling — процессы выполняются по приоритету.

Многозадачность бывает кооперативной (процесс сам отдаёт управление) и вытесняющей (ОС принудительно переключает процессы). Современные ОС (Linux, Windows, macOS) используют вытесняющую многозадачность.

Межпроцессное взаимодействие (IPC) включает: каналы (pipes), сокеты, разделяемую память, семафоры и сообщения. Для синхронизации используются мьютексы и семафоры, предотвращающие состояния гонки (race conditions).

Виртуальная память позволяет процессам использовать больше памяти, чем физически доступно. Страничная организация разбивает адресное пространство на страницы (обычно 4 КБ), которые отображаются на физические фреймы через таблицу страниц (page table). При отсутствии страницы в RAM генерируется page fault, и ОС подгружает её с диска (swap).

Файловые системы: ext4 (Linux), NTFS (Windows), APFS (macOS). Каждая файловая система определяет структуру хранения метаданных (inodes, MFT) и данных на диске.""",
        ),
    ],

    "Базы данных и SQL": [
        (
            "Реляционная модель и язык SQL",
            """Реляционная модель данных была предложена Эдгаром Коддом в 1970 году. Данные представляются в виде отношений (таблиц), где каждая строка — кортеж, а столбец — атрибут.

Ключевые понятия:
- Первичный ключ (PRIMARY KEY) — уникальный идентификатор строки.
- Внешний ключ (FOREIGN KEY) — ссылка на первичный ключ другой таблицы.
- Нормализация — процесс устранения избыточности данных. Нормальные формы: 1НФ (атомарность), 2НФ (полная функциональная зависимость), 3НФ (отсутствие транзитивных зависимостей), НФБК (Бойса-Кодда).

Язык SQL (Structured Query Language) состоит из подмножеств:
- DDL (Data Definition Language): CREATE TABLE, ALTER TABLE, DROP TABLE.
- DML (Data Manipulation Language): SELECT, INSERT, UPDATE, DELETE.
- DCL (Data Control Language): GRANT, REVOKE.
- TCL (Transaction Control Language): BEGIN, COMMIT, ROLLBACK.

Примеры SQL-запросов:

SELECT s.name, COUNT(e.id) AS exam_count
FROM students s
JOIN exams e ON s.id = e.student_id
WHERE e.score >= 60
GROUP BY s.name
HAVING COUNT(e.id) > 2
ORDER BY exam_count DESC;

Индексы ускоряют поиск данных. B-Tree индекс — основной тип, поддерживающий диапазонные запросы. Hash-индекс эффективен для точного совпадения. Покрывающий индекс (covering index) содержит все необходимые столбцы и позволяет избежать обращения к таблице.

Транзакции обеспечивают свойства ACID:
- Atomicity (атомарность) — транзакция выполняется целиком или не выполняется вообще.
- Consistency (согласованность) — БД переходит из одного корректного состояния в другое.
- Isolation (изолированность) — параллельные транзакции не влияют друг на друга.
- Durability (долговечность) — после фиксации данные сохраняются даже при сбое.

Уровни изоляции: Read Uncommitted, Read Committed, Repeatable Read, Serializable. Чем выше уровень, тем меньше аномалий (грязное чтение, неповторяемое чтение, фантомы), но ниже производительность.""",
        ),
    ],

    "Компьютерные сети": [
        (
            "Модель OSI и стек TCP/IP",
            """Компьютерная сеть — совокупность узлов, объединённых каналами связи для обмена данными. Для стандартизации взаимодействия используется эталонная модель OSI (Open Systems Interconnection), состоящая из 7 уровней:

1. Физический — передача битов через среду (кабель, радио). Стандарты: Ethernet (IEEE 802.3), Wi-Fi (IEEE 802.11).
2. Канальный — формирование кадров (frames), MAC-адресация, обнаружение ошибок (CRC). Протоколы: Ethernet, PPP.
3. Сетевой — маршрутизация пакетов между сетями. Протокол IP (IPv4, IPv6). Маршрутизаторы работают на этом уровне.
4. Транспортный — надёжная доставка данных между приложениями. TCP (с установлением соединения, гарантия доставки) и UDP (без соединения, минимальные задержки).
5. Сеансовый — управление сеансами связи.
6. Представления — кодирование, шифрование, сжатие данных.
7. Прикладной — протоколы взаимодействия приложений: HTTP, FTP, SMTP, DNS.

Стек TCP/IP объединяет уровни OSI в 4 слоя: канальный, сетевой (IP), транспортный (TCP/UDP), прикладной (HTTP, DNS).

IP-адресация: IPv4 — 32-битный адрес (например, 192.168.1.1), IPv6 — 128-битный. Подсети определяются маской (255.255.255.0 = /24). CIDR-нотация позволяет гибко разбивать адресное пространство.

DNS (Domain Name System) преобразует доменные имена в IP-адреса. Иерархическая система: корневые серверы → TLD-серверы (.com, .ru) → авторитетные серверы.

TCP использует трёхстороннее рукопожатие (SYN → SYN-ACK → ACK) для установления соединения. Механизмы надёжности: нумерация сегментов, подтверждения (ACK), повторная передача, управление потоком (скользящее окно).

NAT (Network Address Translation) позволяет нескольким устройствам в локальной сети использовать один внешний IP. Виды: статический NAT, динамический NAT, PAT (Port Address Translation).

Безопасность сетей: межсетевые экраны (firewalls), VPN (IPSec, WireGuard), TLS/SSL для шифрования трафика.""",
        ),
    ],

    "Алгоритмы и структуры данных": [
        (
            "Сортировки, графы и динамическое программирование",
            """Алгоритм — конечная упорядоченная последовательность действий для решения задачи. Эффективность оценивается через асимптотическую сложность (нотация O-большое).

Основные классы сложности:
- O(1) — константное время (доступ по индексу в массиве).
- O(log n) — логарифмическое (бинарный поиск).
- O(n) — линейное (проход по массиву).
- O(n log n) — линейно-логарифмическое (сортировка слиянием, быстрая сортировка в среднем).
- O(n²) — квадратичное (пузырьковая сортировка, вставками).
- O(2^n) — экспоненциальное (полный перебор).

Сортировки:
- Пузырьковая (Bubble Sort): O(n²). Поочерёдно сравниваются соседние элементы.
- Быстрая (Quick Sort): O(n log n) в среднем, O(n²) в худшем. Разделяй и властвуй с выбором опорного элемента (pivot).
- Сортировка слиянием (Merge Sort): O(n log n) гарантированно. Рекурсивно делим массив пополам, сортируем и сливаем.
- Пирамидальная (Heap Sort): O(n log n). Построение max-heap и последовательное извлечение максимума.

Структуры данных:
- Массив: O(1) доступ по индексу, O(n) вставка/удаление.
- Связный список: O(1) вставка/удаление, O(n) доступ.
- Стек (LIFO): push/pop за O(1). Используется в рекурсии, обратной польской нотации.
- Очередь (FIFO): enqueue/dequeue за O(1). Используется в BFS.
- Хеш-таблица: O(1) в среднем для поиска/вставки/удаления. Коллизии решаются цепочками или открытой адресацией.
- Двоичное дерево поиска (BST): O(log n) в среднем. AVL и красно-чёрные деревья гарантируют баланс.

Графы:
- Граф G = (V, E), где V — вершины, E — рёбра.
- BFS (поиск в ширину): обходит по уровням, находит кратчайший путь в невзвешенном графе. Сложность O(V + E).
- DFS (поиск в глубину): идёт вглубь, используется для топологической сортировки и поиска компонент связности.
- Алгоритм Дейкстры: кратчайший путь от одной вершины во взвешенном графе с неотрицательными весами. O((V + E) log V) с приоритетной очередью.

Динамическое программирование (DP) — метод решения задач с оптимальной подструктурой и перекрывающимися подзадачами. Классические задачи: рюкзак, LCS (longest common subsequence), числа Фибоначчи.""",
        ),
    ],

    "Веб-разработка": [
        (
            "Backend-разработка: HTTP, REST API, аутентификация",
            """Веб-приложение — клиент-серверная система, где клиент (браузер) взаимодействует с сервером через протокол HTTP.

HTTP (HyperText Transfer Protocol) — протокол прикладного уровня. Основные методы:
- GET — получение ресурса.
- POST — создание ресурса.
- PUT — полное обновление.
- PATCH — частичное обновление.
- DELETE — удаление.

Коды ответов:
- 2xx — успех (200 OK, 201 Created).
- 3xx — перенаправления (301 Moved Permanently, 302 Found).
- 4xx — ошибка клиента (400 Bad Request, 401 Unauthorized, 403 Forbidden, 404 Not Found).
- 5xx — ошибка сервера (500 Internal Server Error, 502 Bad Gateway).

REST (Representational State Transfer) — архитектурный стиль API:
1. Каждый ресурс имеет уникальный URL (например, /api/users/42).
2. Операции CRUD сопоставляются с HTTP-методами.
3. Stateless — сервер не хранит состояние клиента между запросами.
4. Ответ в формате JSON или XML.

Аутентификация — подтверждение личности пользователя:
- Сессии (cookies) — сервер создаёт сессию, клиент отправляет session_id в cookie.
- JWT (JSON Web Token) — токен с payload, подписанный секретным ключом. Формат: header.payload.signature. Не требует хранения на сервере.
- OAuth 2.0 — делегированная авторизация через сторонний провайдер (Google, GitHub).

Авторизация — определение прав доступа (RBAC — Role-Based Access Control).

Фреймворки для backend:
- Python: FastAPI (async, автодокументация Swagger), Django (ORM, admin-панель), Flask (минималистичный).
- JavaScript: Express.js (Node.js), Nest.js (TypeScript).
- Go: Gin, Fiber.

Безопасность:
- XSS (Cross-Site Scripting) — внедрение скриптов. Защита: экранирование вывода, Content-Security-Policy.
- CSRF (Cross-Site Request Forgery) — подделка запросов. Защита: CSRF-токены.
- SQL-инъекции — внедрение SQL-кода. Защита: параметризованные запросы.
- Хеширование паролей: bcrypt, PBKDF2, Argon2. Никогда не хранить пароли в открытом виде.""",
        ),
    ],
}


# ── Main seed ─────────────────────────────────────────────────────────────────

TEACHERS = [
    ("Алан Тьюринг",        "turing@university.ru",    "Turing2026!"),
    ("Линус Торвальдс",      "torvalds@university.ru",  "Linux2026!"),
    ("Дональд Кнут",         "knuth@university.ru",     "Knuth2026!"),
    ("Эдсгер Дейкстра",     "dijkstra@university.ru",  "Dijkstra2026!"),
    ("Тим Бернерс-Ли",      "berners@university.ru",   "Web2026!"),
]

STUDENTS = [
    ("Джеффри Эпштейн",     "epstein@student.ru",   "Student2026!", "ИВТ-301"),
    ("Анна Кузнецова",       "kuznetsova@student.ru","Student2026!", "ИВТ-301"),
    ("Максим Попов",         "popov@student.ru",     "Student2026!", "ИВТ-302"),
    ("Екатерина Морозова",   "morozova@student.ru",  "Student2026!", "ИВТ-302"),
    ("Сергей Новиков",       "novikov@student.ru",   "Student2026!", "ИВТ-301"),
]

DISCIPLINE_NAMES = list(LECTURES.keys())

# Маппинг: какой препод ведёт какую дисциплину
TEACHER_DISC_MAP = {
    0: [0, 1],   # Тьюринг  → ОС, Базы данных
    1: [0],      # Торвальдс → ОС
    2: [3],      # Кнут      → Алгоритмы
    3: [2, 3],   # Дейкстра  → Сети, Алгоритмы
    4: [4],      # Бернерс-Ли → Веб
}


def _seed(force: bool = False):
    random.seed(2026)
    init_db()
    conn = connect()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS cnt FROM users")
    existing = cur.fetchone()["cnt"]
    if existing and not force:
        raise RuntimeError("В базе уже есть данные. Запустите с --force для пересборки.")
    if existing and force:
        _clear_all(cur)

    # Дисциплины
    disc_ids = {}
    for name in DISCIPLINE_NAMES:
        insert_ignore(cur, "disciplines", ("name",), (name,), conflict_columns=("name",))
        cur.execute("SELECT id FROM disciplines WHERE name = ?", (name,))
        disc_ids[name] = cur.fetchone()[0]

    # Преподаватели
    teacher_ids = []
    for i, (name, email, pwd) in enumerate(TEACHERS):
        first_disc = TEACHER_DISC_MAP[i][0]
        tid = _insert_user(cur, "teacher", name, email, pwd,
                           discipline_id=disc_ids[DISCIPLINE_NAMES[first_disc]])
        teacher_ids.append(tid)
        for di in TEACHER_DISC_MAP[i]:
            insert_ignore(
                cur,
                "teacher_disciplines",
                ("teacher_id", "discipline_id"),
                (tid, disc_ids[DISCIPLINE_NAMES[di]]),
                conflict_columns=("teacher_id", "discipline_id"),
            )

    # Группы
    for group_name in ("ИВТ-301", "ИВТ-302"):
        insert_ignore(
            cur,
            "groups",
            ("name", "teacher_id"),
            (group_name, teacher_ids[0]),
            conflict_columns=("name",),
        )

    # Студенты
    student_ids = []
    for name, email, pwd, group in STUDENTS:
        sid = _insert_user(cur, "student", name, email, pwd, group=group)
        student_ids.append(sid)

    # Лекции + тесты
    now = datetime.utcnow()
    stats = {"teachers": len(teacher_ids), "students": len(student_ids),
             "disciplines": len(disc_ids), "lectures": 0, "tests": 0, "questions": 0}

    # Привязка дисциплины → основной препод (первый в маппинге)
    disc_teacher = {}
    for ti, discs in TEACHER_DISC_MAP.items():
        for di in discs:
            dname = DISCIPLINE_NAMES[di]
            if dname not in disc_teacher:
                disc_teacher[dname] = teacher_ids[ti]

    for disc_name, lec_list in LECTURES.items():
        d_id = disc_ids[disc_name]
        t_id = disc_teacher[disc_name]
        for title, body in lec_list:
            created_at = (now - timedelta(days=random.randint(5, 60))).isoformat()
            cur.execute(
                "INSERT INTO lectures (teacher_id, title, body, created_at, discipline_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (t_id, title, body, created_at, d_id),
            )
            lecture_id = cur.lastrowid
            stats["lectures"] += 1

            # Тест-заглушка (5 вопросов) — чтобы была структура
            cur.execute(
                "INSERT INTO tests (lecture_id, title, status, created_at) VALUES (?, ?, 'published', ?)",
                (lecture_id, f"Тест: {title}", created_at),
            )
            test_id = cur.lastrowid
            stats["tests"] += 1

            # Генерим 5 простых вопросов из текста лекции
            questions = _generate_questions_from_text(title, body)
            for q in questions:
                cur.execute(
                    "INSERT INTO questions (test_id, text, options_json, correct_index) VALUES (?, ?, ?, ?)",
                    (test_id, q["text"], json.dumps(q["options"], ensure_ascii=False), q["correct_index"]),
                )
                stats["questions"] += 1

    conn.commit()
    conn.close()
    return stats


def _generate_questions_from_text(title: str, body: str) -> list[dict]:
    """Генерация 5 вопросов на основе содержания лекции (без AI)."""
    questions_bank = {
        "Архитектура ОС и управление процессами": [
            {"text": "Какой алгоритм планирования выделяет каждому процессу квант времени по кругу?",
             "options": ["Round Robin", "FCFS", "SJF", "Priority Scheduling"], "correct_index": 0},
            {"text": "Что генерируется при отсутствии страницы в оперативной памяти?",
             "options": ["Page fault", "Stack overflow", "Segmentation fault", "Deadlock"], "correct_index": 0},
            {"text": "Какова типичная размерность страницы виртуальной памяти?",
             "options": ["4 КБ", "1 МБ", "64 байт", "512 КБ"], "correct_index": 0},
            {"text": "Какой механизм IPC использует общую область памяти для нескольких процессов?",
             "options": ["Разделяемая память", "Pipe", "Сокет", "Сигнал"], "correct_index": 0},
            {"text": "Какую файловую систему использует macOS?",
             "options": ["APFS", "ext4", "NTFS", "FAT32"], "correct_index": 0},
        ],
        "Реляционная модель и язык SQL": [
            {"text": "Кто предложил реляционную модель данных в 1970 году?",
             "options": ["Эдгар Кодд", "Алан Тьюринг", "Дональд Кнут", "Деннис Ритчи"], "correct_index": 0},
            {"text": "Какое свойство ACID гарантирует, что транзакция выполняется целиком?",
             "options": ["Atomicity", "Consistency", "Isolation", "Durability"], "correct_index": 0},
            {"text": "Какой тип индекса наиболее эффективен для диапазонных запросов?",
             "options": ["B-Tree", "Hash", "Bitmap", "Fulltext"], "correct_index": 0},
            {"text": "К какому подмножеству SQL относится оператор SELECT?",
             "options": ["DML", "DDL", "DCL", "TCL"], "correct_index": 0},
            {"text": "Какая нормальная форма устраняет транзитивные зависимости?",
             "options": ["3НФ", "1НФ", "2НФ", "НФБК"], "correct_index": 0},
        ],
        "Модель OSI и стек TCP/IP": [
            {"text": "Сколько уровней в эталонной модели OSI?",
             "options": ["7", "4", "5", "3"], "correct_index": 0},
            {"text": "Какой протокол транспортного уровня гарантирует доставку данных?",
             "options": ["TCP", "UDP", "ICMP", "ARP"], "correct_index": 0},
            {"text": "Что выполняет DNS?",
             "options": ["Преобразует доменные имена в IP-адреса", "Шифрует трафик", "Маршрутизирует пакеты", "Назначает MAC-адреса"], "correct_index": 0},
            {"text": "Какой механизм TCP используется для установления соединения?",
             "options": ["Трёхстороннее рукопожатие", "Двухфазная фиксация", "Polling", "Token Ring"], "correct_index": 0},
            {"text": "Сколько бит в IPv4-адресе?",
             "options": ["32", "64", "128", "16"], "correct_index": 0},
        ],
        "Сортировки, графы и динамическое программирование": [
            {"text": "Какова средняя сложность быстрой сортировки (Quick Sort)?",
             "options": ["O(n log n)", "O(n²)", "O(n)", "O(log n)"], "correct_index": 0},
            {"text": "Какая структура данных работает по принципу LIFO?",
             "options": ["Стек", "Очередь", "Дек", "Список"], "correct_index": 0},
            {"text": "Какой алгоритм находит кратчайший путь в взвешенном графе?",
             "options": ["Алгоритм Дейкстры", "BFS", "DFS", "Прим"], "correct_index": 0},
            {"text": "Какова сложность доступа к элементу хеш-таблицы в среднем?",
             "options": ["O(1)", "O(n)", "O(log n)", "O(n²)"], "correct_index": 0},
            {"text": "Какой метод решает задачи с перекрывающимися подзадачами?",
             "options": ["Динамическое программирование", "Жадный алгоритм", "Бинарный поиск", "Метод ветвей и границ"], "correct_index": 0},
        ],
        "Backend-разработка: HTTP, REST API, аутентификация": [
            {"text": "Какой HTTP-метод используется для получения ресурса?",
             "options": ["GET", "POST", "DELETE", "PATCH"], "correct_index": 0},
            {"text": "Какой код ответа HTTP означает 'Не найдено'?",
             "options": ["404", "403", "500", "301"], "correct_index": 0},
            {"text": "Из каких частей состоит JWT-токен?",
             "options": ["header.payload.signature", "key.value.hash", "user.role.token", "id.secret.data"], "correct_index": 0},
            {"text": "Какая атака подделывает запросы от имени авторизованного пользователя?",
             "options": ["CSRF", "XSS", "SQL-инъекция", "DDoS"], "correct_index": 0},
            {"text": "Какой Python-фреймворк поддерживает async и автодокументацию Swagger?",
             "options": ["FastAPI", "Django", "Flask", "Tornado"], "correct_index": 0},
        ],
    }

    qs = questions_bank.get(title, [])
    if not qs:
        return []
    # Перемешиваем варианты ответов для каждого вопроса
    result = []
    for q in qs:
        opts = q["options"][:]
        correct_text = opts[q["correct_index"]]
        random.shuffle(opts)
        result.append({
            "text": q["text"],
            "options": opts,
            "correct_index": opts.index(correct_text),
        })
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed showcase data: scientists + IT disciplines")
    parser.add_argument("--force", action="store_true", help="Пересоздать все данные с нуля.")
    args = parser.parse_args()

    result = _seed(force=args.force)
    print("=== Demo data created ===")
    for k, v in result.items():
        print(f"  {k}: {v}")

    print("\n=== Логины преподавателей ===")
    for name, email, pwd in TEACHERS:
        print(f"  {name}: {email} / {pwd}")
    print("\n=== Логины студентов ===")
    for name, email, pwd, group in STUDENTS:
        print(f"  {name} ({group}): {email} / {pwd}")
