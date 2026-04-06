"""
Seed rich analytics data: create 5 tests across 10 days,
10 students take each test with gradually improving scores
so the trend chart shows a nice upward curve.
"""
import json
import random
import sqlite3
import sys
from datetime import datetime, timedelta

sys.path.insert(0, ".")
from app.security import hash_password, new_salt

DB = "app/app.db"
GROUP_NAME = "БИ-41"
DISCIPLINE_ID = 443
TEACHER_ID = 2  # teacher1@example.com
PASSWORD = "Student123!"

# 10 students – gender-consistent Russian names
STUDENTS = [
    ("Кузнецов Алексей Дмитриевич",    "kuzn@demo.ru"),
    ("Морозова Анна Сергеевна",         "moroz@demo.ru"),
    ("Соколов Даниил Игоревич",         "sokol@demo.ru"),
    ("Павлова Екатерина Андреевна",     "pavl@demo.ru"),
    ("Волков Максим Олегович",          "volk@demo.ru"),
    ("Новикова Полина Владимировна",    "novik@demo.ru"),
    ("Козлов Артём Николаевич",         "kozl@demo.ru"),
    ("Фёдорова Мария Александровна",    "fedor@demo.ru"),
    ("Лебедев Никита Романович",        "lebed@demo.ru"),
    ("Смирнова Дарья Петровна",         "smir@demo.ru"),
]

# 5 test themes with 5 questions each
TEST_THEMES = [
    ("Основы сетевых моделей OSI", [
        ("Сколько уровней в модели OSI?", ["5","6","7","8"], 2),
        ("Какой уровень отвечает за маршрутизацию?", ["Канальный","Сетевой","Транспортный","Физический"], 1),
        ("Протокол HTTP работает на уровне...", ["Сетевом","Транспортном","Прикладном","Сеансовом"], 2),
        ("Что делает коммутатор?", ["Маршрутизирует пакеты","Коммутирует кадры","Шифрует данные","Сжимает трафик"], 1),
        ("TCP обеспечивает...", ["Быструю передачу","Надёжную доставку","Шифрование","Маршрутизацию"], 1),
    ]),
    ("IP-адресация и подсети", [
        ("Сколько бит в IPv4-адресе?", ["16","24","32","64"], 2),
        ("Маска /24 означает...", ["24 бита сети","24 бита хоста","24 подсети","24 маршрутизатора"], 0),
        ("Адрес 192.168.1.0/24 — это...", ["Хост","Сеть","Широковещательный","Маска"], 1),
        ("Сколько хостов в сети /28?", ["14","16","30","32"], 0),
        ("Какой адрес является частным?", ["8.8.8.8","192.168.1.1","1.1.1.1","200.100.50.1"], 1),
    ]),
    ("Протоколы маршрутизации", [
        ("OSPF — это протокол...", ["Дистанционно-векторный","Состояния канала","Статической маршрутизации","Шифрования"], 1),
        ("RIP использует метрику...", ["Пропускная способность","Задержка","Число хопов","Надёжность"], 2),
        ("BGP применяется для...", ["Локальных сетей","Междоменной маршрутизации","Шифрования","DHCP"], 1),
        ("Административная дистанция OSPF?", ["90","110","120","170"], 1),
        ("Что такое AS?", ["Автономная система","Антивирусная защита","Адресное пространство","Активный сервер"], 0),
    ]),
    ("Безопасность сетей", [
        ("Что такое firewall?", ["Маршрутизатор","Межсетевой экран","Коммутатор","DNS-сервер"], 1),
        ("VPN обеспечивает...", ["Скорость","Шифрованный туннель","DNS-разрешение","Балансировку"], 1),
        ("WPA3 используется для...", ["Маршрутизации","Защиты Wi-Fi","Шифрования email","DNS"], 1),
        ("Что делает IDS?", ["Блокирует атаки","Обнаруживает вторжения","Маршрутизирует","Шифрует"], 1),
        ("SSL/TLS работает на уровне...", ["Сетевом","Транспортном","Представления","Прикладном"], 2),
    ]),
    ("Итоговый тест по курсу", [
        ("Какой протокол используется для email?", ["FTP","SMTP","HTTP","DNS"], 1),
        ("DNS преобразует...", ["IP в MAC","Домен в IP","MAC в IP","IP в домен"], 1),
        ("Какой порт у HTTPS?", ["80","443","21","25"], 1),
        ("NAT выполняет...", ["Шифрование","Трансляцию адресов","Маршрутизацию","Фильтрацию"], 1),
        ("DHCP назначает...", ["IP-адрес автоматически","Маршрут","DNS-имя","Маску вручную"], 0),
    ]),
]

# Score progression per test round (students improve over time)
# Each row: [student_0_correct, student_1_correct, ..., student_9_correct] out of 5
SCORE_MATRIX = [
    # Test 1 (day 1-2): low start
    [2, 1, 3, 2, 1, 2, 1, 3, 2, 1],
    # Test 2 (day 3-4): slight improvement
    [3, 2, 3, 3, 2, 3, 2, 3, 3, 2],
    # Test 3 (day 5-6): mid-range
    [4, 3, 4, 3, 3, 3, 3, 4, 3, 3],
    # Test 4 (day 7-8): good
    [4, 4, 5, 4, 3, 4, 4, 4, 4, 3],
    # Test 5 (day 9-10): strong finish
    [5, 5, 5, 4, 4, 5, 4, 5, 5, 4],
]


def run():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # 1. Ensure group & teaching assignment
    if not cur.execute("SELECT id FROM groups WHERE name=?", (GROUP_NAME,)).fetchone():
        cur.execute("INSERT INTO groups (name, teacher_id) VALUES (?, ?)", (GROUP_NAME, TEACHER_ID))
    ta = cur.execute(
        "SELECT * FROM teaching_assignments WHERE teacher_id=? AND discipline_id=? AND group_name=?",
        (TEACHER_ID, DISCIPLINE_ID, GROUP_NAME),
    ).fetchone()
    if not ta:
        cur.execute(
            "INSERT INTO teaching_assignments (teacher_id, discipline_id, group_name) VALUES (?, ?, ?)",
            (TEACHER_ID, DISCIPLINE_ID, GROUP_NAME),
        )

    # 2. Create/get students
    student_ids = []
    for full_name, email in STUDENTS:
        ex = cur.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if ex:
            student_ids.append(ex["id"])
        else:
            salt = new_salt()
            pw_hash = hash_password(PASSWORD, salt)
            cur.execute(
                "INSERT INTO users (email, password_hash, salt, role, full_name, student_group) "
                "VALUES (?, ?, ?, 'student', ?, ?)",
                (email, pw_hash, salt, full_name, GROUP_NAME),
            )
            student_ids.append(cur.lastrowid)
            print(f"  Created student {full_name}")

    # 3. Delete ALL old attempts for these students (clean slate)
    for sid in student_ids:
        old = cur.execute("SELECT id FROM attempts WHERE student_id=?", (sid,)).fetchall()
        for oa in old:
            cur.execute("DELETE FROM answers WHERE attempt_id=?", (oa["id"],))
            cur.execute("DELETE FROM attempts WHERE id=?", (oa["id"],))

    # 4. Create tests, questions, and attempts
    now = datetime.now()
    base_date = now - timedelta(days=12)

    for t_idx, (theme_title, questions_data) in enumerate(TEST_THEMES):
        # Reuse existing lecture (pick first available)
        lectures = cur.execute(
            "SELECT id FROM lectures WHERE teacher_id=? AND discipline_id=? ORDER BY id",
            (TEACHER_ID, DISCIPLINE_ID),
        ).fetchall()
        lecture_id = lectures[min(t_idx, len(lectures) - 1)]["id"]

        # Create test
        test_day = base_date + timedelta(days=t_idx * 2)
        cur.execute(
            "INSERT INTO tests (lecture_id, title, status, created_at) VALUES (?, ?, 'published', ?)",
            (lecture_id, f"Тест: {theme_title}", test_day.isoformat()),
        )
        test_id = cur.lastrowid
        print(f"\nTest {t_idx+1}: '{theme_title}' (id={test_id})")

        # Create questions
        q_ids = []
        for q_text, options, correct_idx in questions_data:
            cur.execute(
                "INSERT INTO questions (test_id, text, options_json, correct_index) VALUES (?, ?, ?, ?)",
                (test_id, q_text, json.dumps(options, ensure_ascii=False), correct_idx),
            )
            q_ids.append((cur.lastrowid, correct_idx))

        # Each student takes this test
        scores_row = SCORE_MATRIX[t_idx]
        for s_idx, sid in enumerate(student_ids):
            target_correct = scores_row[s_idx]
            score = round(target_correct / 5 * 100, 1)

            # Randomize attempt time within the 2-day window
            attempt_time = test_day + timedelta(
                hours=random.randint(2, 40),
                minutes=random.randint(0, 59),
            )

            cur.execute(
                "INSERT INTO attempts (test_id, student_id, score, taken_at) VALUES (?, ?, ?, ?)",
                (test_id, sid, score, attempt_time.isoformat()),
            )
            attempt_id = cur.lastrowid

            # Create answers
            correct_positions = random.sample(range(5), min(target_correct, 5))
            for j, (qid, correct_idx) in enumerate(q_ids):
                is_correct = 1 if j in correct_positions else 0
                if is_correct:
                    selected = correct_idx
                else:
                    wrong = [x for x in range(4) if x != correct_idx]
                    selected = random.choice(wrong)
                cur.execute(
                    "INSERT INTO answers (attempt_id, question_id, selected_index, is_correct) VALUES (?, ?, ?, ?)",
                    (attempt_id, qid, selected, is_correct),
                )

            print(f"  {STUDENTS[s_idx][0]}: {score}% ({target_correct}/5)")

    con.commit()
    con.close()
    print("\n✅ Done! 5 tests × 10 students = 50 attempts seeded across 10 days.")


if __name__ == "__main__":
    run()
