"""Inspect DB state for seeding."""
import sqlite3
con = sqlite3.connect("app/app.db")
con.row_factory = sqlite3.Row
cur = con.cursor()

print("=== TESTS ===")
for r in cur.execute("SELECT t.id, t.title, t.status, l.discipline_id, l.teacher_id FROM tests t JOIN lectures l ON t.lecture_id=l.id"):
    print(dict(r))

print("\n=== DISCIPLINES ===")
for r in cur.execute("SELECT * FROM disciplines"):
    print(dict(r))

print("\n=== GROUPS ===")
for r in cur.execute("SELECT * FROM groups"):
    print(dict(r))

print("\n=== TEACHING_ASSIGNMENTS ===")
for r in cur.execute("SELECT * FROM teaching_assignments"):
    print(dict(r))

print("\n=== QUESTIONS for newest test ===")
last = cur.execute("SELECT id FROM tests ORDER BY id DESC LIMIT 1").fetchone()
if last:
    for r in cur.execute("SELECT id, text, correct_index FROM questions WHERE test_id=?", (last["id"],)):
        print(dict(r))

print("\n=== TEACHERS ===")
for r in cur.execute("SELECT id, email, full_name FROM users WHERE role='teacher'"):
    print(dict(r))

con.close()
