"""
Seed 10 students in group БИ-41, assign to discipline 443,
then have them take the CCNA test (id=9) with varying scores.
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
TEST_ID = 9     # CCNA test

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

# Target scores (% correct out of 5 questions): varied range
TARGET_CORRECT = [5, 4, 4, 3, 3, 3, 2, 2, 1, 0]  # 100%, 80%, 80%, 60%, ...

PASSWORD = "Student123!"


def run():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # 1. Ensure group exists
    existing = cur.execute("SELECT id FROM groups WHERE name=?", (GROUP_NAME,)).fetchone()
    if not existing:
        cur.execute("INSERT INTO groups (name, teacher_id) VALUES (?, ?)", (GROUP_NAME, TEACHER_ID))
        print(f"Created group {GROUP_NAME}")
    else:
        print(f"Group {GROUP_NAME} already exists (id={existing['id']})")

    # 2. Ensure teaching assignment
    ta = cur.execute(
        "SELECT * FROM teaching_assignments WHERE teacher_id=? AND discipline_id=? AND group_name=?",
        (TEACHER_ID, DISCIPLINE_ID, GROUP_NAME),
    ).fetchone()
    if not ta:
        cur.execute(
            "INSERT INTO teaching_assignments (teacher_id, discipline_id, group_name) VALUES (?, ?, ?)",
            (TEACHER_ID, DISCIPLINE_ID, GROUP_NAME),
        )
        print(f"Assigned teacher {TEACHER_ID} → discipline {DISCIPLINE_ID} → group {GROUP_NAME}")

    # 3. Get questions for the test
    questions = cur.execute(
        "SELECT id, correct_index FROM questions WHERE test_id=? ORDER BY id", (TEST_ID,)
    ).fetchall()
    if not questions:
        print(f"ERROR: No questions found for test {TEST_ID}")
        return
    q_count = len(questions)
    print(f"Test {TEST_ID} has {q_count} questions")

    # 4. Create students and attempts
    now = datetime.now()
    for i, (full_name, email) in enumerate(STUDENTS):
        # Check if student already exists
        ex = cur.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if ex:
            student_id = ex["id"]
            print(f"  Student {email} already exists (id={student_id})")
        else:
            salt = new_salt()
            pw_hash = hash_password(PASSWORD, salt)
            cur.execute(
                "INSERT INTO users (email, password_hash, salt, role, full_name, student_group) "
                "VALUES (?, ?, ?, 'student', ?, ?)",
                (email, pw_hash, salt, full_name, GROUP_NAME),
            )
            student_id = cur.lastrowid
            print(f"  Created student {full_name} (id={student_id})")

        # Delete old attempts for this student+test
        old_attempts = cur.execute(
            "SELECT id FROM attempts WHERE student_id=? AND test_id=?", (student_id, TEST_ID)
        ).fetchall()
        for oa in old_attempts:
            cur.execute("DELETE FROM answers WHERE attempt_id=?", (oa["id"],))
            cur.execute("DELETE FROM attempts WHERE id=?", (oa["id"],))

        # Create attempt with target score
        target = TARGET_CORRECT[i]
        score = round(target / q_count * 100, 1)
        taken_at = (now - timedelta(hours=random.randint(1, 48), minutes=random.randint(0, 59))).isoformat()

        cur.execute(
            "INSERT INTO attempts (test_id, student_id, score, taken_at) VALUES (?, ?, ?, ?)",
            (TEST_ID, student_id, score, taken_at),
        )
        attempt_id = cur.lastrowid

        # Create individual answers
        correct_ids = random.sample(range(q_count), min(target, q_count))
        for j, q in enumerate(questions):
            is_correct = 1 if j in correct_ids else 0
            if is_correct:
                selected = q["correct_index"]
            else:
                wrong_options = [x for x in range(4) if x != q["correct_index"]]
                selected = random.choice(wrong_options)

            cur.execute(
                "INSERT INTO answers (attempt_id, question_id, selected_index, is_correct) VALUES (?, ?, ?, ?)",
                (attempt_id, q["id"], selected, is_correct),
            )

        print(f"    → Attempt: {score}% ({target}/{q_count} correct)")

    con.commit()
    con.close()
    print("\nDone! 10 students seeded with test attempts.")


if __name__ == "__main__":
    run()
