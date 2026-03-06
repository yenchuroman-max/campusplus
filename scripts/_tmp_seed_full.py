"""
Seed: тесты по 11 лекциям Линуса Торвальдса, группа Должники-1, 10 студентов, попытки.
"""
import sys, os, json, random
from datetime import datetime, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db import connect, init_db
from app.security import hash_password, new_salt
from app.ai import generate_questions

init_db()
conn = connect()
cur = conn.cursor()

TEACHER_ID = 2

# ── 1. Обновляем email и пароль преподавателя ──
salt = new_salt()
pw_hash = hash_password("123123", salt)
cur.execute(
    "UPDATE users SET email = ?, password_hash = ?, salt = ? WHERE id = ?",
    ("zakroitedolgpj@sg.ga", pw_hash, salt, TEACHER_ID),
)
print("Преподаватель обновлён: zakroitedolgpj@sg.ga / 123123")

# ── 2. Создаём группу Должники-1 ──
cur.execute("INSERT OR IGNORE INTO groups (name, teacher_id) VALUES (?, ?)", ("Должники-1", TEACHER_ID))
conn.commit()
cur.execute("SELECT id FROM groups WHERE name = 'Должники-1'")
group_row = cur.fetchone()
GROUP_NAME = "Должники-1"
print(f"Группа '{GROUP_NAME}' готова")

# ── 3. Создаём студентов ──
TOP_STUDENTS = [
    ("Енчу Роман", "enchu@student.local"),
    ("Каржаспаев Рахимжан", "karzhaspayev@student.local"),
    ("Сейтхожин Рамис", "seitkhozhin@student.local"),
]

MEME_STUDENTS = [
    ("Джеффри Эпштейн", "epstein@student.local"),
    ("Такер Карлсон", "carlson@student.local"),
    ("Илон Маск-мл.", "musk.jr@student.local"),
    ("Чак Норрис", "norris@student.local"),
    ("Хаябуса Кен", "hayabusa@student.local"),
    ("Боб Росс", "ross@student.local"),
    ("Флориан Киркоров", "kirkorov@student.local"),
]

def create_student(full_name, email):
    cur.execute("SELECT id FROM users WHERE email = ?", (email,))
    row = cur.fetchone()
    if row:
        sid = row["id"]
        cur.execute("UPDATE users SET student_group = ? WHERE id = ?", (GROUP_NAME, sid))
        return sid
    s = new_salt()
    ph = hash_password("Student123!", s)
    cur.execute(
        "INSERT INTO users (role, full_name, email, password_hash, salt, student_group, assigned_teacher_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("student", full_name, email, ph, s, GROUP_NAME, TEACHER_ID),
    )
    return cur.lastrowid

top_ids = []
for name, email in TOP_STUDENTS:
    sid = create_student(name, email)
    top_ids.append(sid)
    print(f"  Студент (топ): {name} id={sid}")

meme_ids = []
for name, email in MEME_STUDENTS:
    sid = create_student(name, email)
    meme_ids.append(sid)
    print(f"  Студент (мем): {name} id={sid}")

conn.commit()

# ── 4. Получаем лекции ──
cur.execute("SELECT id, title, body, discipline_id FROM lectures WHERE teacher_id = ? ORDER BY id", (TEACHER_ID,))
lectures = [dict(r) for r in cur.fetchall()]
print(f"\nЛекций: {len(lectures)}")

# ── 5. Генерируем тесты (по 10 вопросов) для лекций без тестов ──
test_map = {}  # lecture_id -> test_id

for lec in lectures:
    # Проверяем, есть ли уже тест для этой лекции
    cur.execute("SELECT id FROM tests WHERE lecture_id = ? LIMIT 1", (lec["id"],))
    existing = cur.fetchone()
    if existing:
        test_map[lec["id"]] = existing["id"]
        print(f"  Лекция '{lec['title'][:50]}' — тест уже есть (id={existing['id']})")
        continue

    # Генерируем вопросы через AI
    discipline_name = None
    if lec["discipline_id"]:
        cur.execute("SELECT name FROM disciplines WHERE id = ?", (lec["discipline_id"],))
        dr = cur.fetchone()
        if dr:
            discipline_name = dr["name"]

    print(f"  Генерация теста для '{lec['title'][:50]}'...", end=" ", flush=True)
    questions = generate_questions(lec["body"], count=10, difficulty="medium", discipline_name=discipline_name)

    if not questions:
        print("⚠ AI не вернул вопросы, пропуск")
        continue

    title = f"Тест: {lec['title']}"
    cur.execute(
        "INSERT INTO tests (lecture_id, title, status, created_at) VALUES (?, ?, ?, ?)",
        (lec["id"], title, "published", datetime.utcnow().isoformat()),
    )
    test_id = cur.lastrowid
    test_map[lec["id"]] = test_id

    for q in questions:
        cur.execute(
            "INSERT INTO questions (test_id, text, options_json, correct_index) VALUES (?, ?, ?, ?)",
            (test_id, q["text"], json.dumps(q["options"], ensure_ascii=False), q["correct_index"]),
        )
    conn.commit()
    print(f"✓ {len(questions)} вопросов, test_id={test_id}")

print(f"\nТестов готово: {len(test_map)}")

# ── 6. Студенты проходят все тесты ──
random.seed(42)

def take_test(student_id, test_id, min_score, max_score):
    """Студент проходит тест с результатом в диапазоне [min_score, max_score]%."""
    cur.execute("SELECT id, correct_index, options_json FROM questions WHERE test_id = ? ORDER BY id", (test_id,))
    questions = [dict(q) for q in cur.fetchall()]
    if not questions:
        return

    # Проверяем, не проходил ли уже
    cur.execute("SELECT id FROM attempts WHERE test_id = ? AND student_id = ?", (test_id, student_id))
    if cur.fetchone():
        return

    total = len(questions)
    target_pct = random.uniform(min_score, max_score) / 100.0
    target_correct = max(1, round(total * target_pct))

    # Выбираем, какие вопросы ответить правильно
    correct_indices = set(random.sample(range(total), min(target_correct, total)))

    correct_count = 0
    taken_at = (datetime.utcnow() - timedelta(days=random.randint(0, 14), hours=random.randint(0, 12))).isoformat()

    cur.execute(
        "INSERT INTO attempts (test_id, student_id, score, taken_at) VALUES (?, ?, ?, ?)",
        (test_id, student_id, 0, taken_at),
    )
    attempt_id = cur.lastrowid

    for i, q in enumerate(questions):
        n_opts = len(json.loads(q["options_json"]))
        if i in correct_indices:
            selected = q["correct_index"]
            is_correct = 1
            correct_count += 1
        else:
            # Выбираем случайный неправильный
            wrong = [j for j in range(n_opts) if j != q["correct_index"]]
            selected = random.choice(wrong) if wrong else 0
            is_correct = 0

        cur.execute(
            "INSERT INTO answers (attempt_id, question_id, selected_index, is_correct) VALUES (?, ?, ?, ?)",
            (attempt_id, q["id"], selected, is_correct),
        )

    score = round(correct_count / total * 100, 1) if total else 0
    cur.execute("UPDATE attempts SET score = ? WHERE id = ?", (score, attempt_id))
    return score

print("\nСтуденты проходят тесты...")
for test_lec_id, test_id in test_map.items():
    lec_title = next((l["title"] for l in lectures if l["id"] == test_lec_id), "?")
    # Топ-студенты: 90-100%
    for sid in top_ids:
        score = take_test(sid, test_id, 90, 100)
        if score is not None:
            cur.execute("SELECT full_name FROM users WHERE id = ?", (sid,))
            name = cur.fetchone()["full_name"]

    # Мем-студенты: 20-65%
    for sid in meme_ids:
        score = take_test(sid, test_id, 20, 65)
        if score is not None:
            cur.execute("SELECT full_name FROM users WHERE id = ?", (sid,))
            name = cur.fetchone()["full_name"]

    conn.commit()
    print(f"  '{lec_title[:50]}' — все прошли")

conn.commit()
conn.close()
print("\n✅ Всё готово!")
