"""
Полные функциональные тесты платформы мониторинга успеваемости.

Покрывают:
  - DB init
  - Регистрация / логин / логаут
  - Студенческий флоу (тесты, аналитика, рост)
  - Преподавательский флоу (лекции, генерация, аналитика, дисциплины)
  - Админский флоу (пользователи, группы, дисциплины)
  - AI модуль (утилиты, fallback генерация, нормализация)
  - Безопасность
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

# ── Путь к проекту ──────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── Фикстура: изолированная БД для каждого теста ────────────────
@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Создаёт временную SQLite БД, подменяет актуальный sqlite path в app.db."""
    db_file = tmp_path / "test.db"
    monkeypatch.setattr("app.db.SQLITE_DB_PATH", db_file)
    from app.db import init_db
    init_db()
    yield db_file


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Сбрасывает глобальный rate-limiter между тестами."""
    from app.security import login_limiter
    login_limiter._store.clear()
    yield
    login_limiter._store.clear()


@pytest.fixture()
def db(isolated_db):
    """Возвращает sqlite3 connection к тестовой БД."""
    conn = sqlite3.connect(isolated_db)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture()
def client(isolated_db):
    """FastAPI TestClient с изолированной БД."""
    # Переимпортируем main — он использует app.db.connect() → нашу tmp БД
    from fastapi.testclient import TestClient
    import main as main_module
    # Вызываем init_db() ещё раз (main.startup уже не вызывается в TestClient)
    from app.db import init_db
    init_db()
    with TestClient(main_module.app, raise_server_exceptions=False) as tc:
        yield tc


# ── Утилиты для тестов ──────────────────────────────────────────
def _register(c, role="student", email="test@test.ru", login=None, password="pass123",
              full_name="Тест Тестов", group=""):
    login_value = (login or email or "").strip()
    data = {
        "role": role,
        "full_name": full_name,
        "login": login_value,
        "password": password,
        "student_group": group,
    }
    return c.post("/register", data=data, follow_redirects=False)


def _login(c, email="test@test.ru", login=None, password="pass123"):
    login_value = (login or email or "").strip()
    return c.post("/login", data={"login": login_value, "password": password}, follow_redirects=False)


def _extract_temporary_password(text: str) -> str:
    match = re.search(r"Временный пароль:\s*([A-Za-z0-9!_-]+)", text)
    assert match is not None, text
    return match.group(1)


def _create_teacher(c, db, email="teacher@test.ru", name="Иванов И.И.", password="pass123"):
    _insert_user(db, role="teacher", login=email, password=password, full_name=name)
    return _login(c, email=email)


def _create_student(c, email="student@test.ru", name="Петров П.П.", group="БИ-41"):
    _register(c, role="student", email=email, full_name=name, group=group)
    return _login(c, email=email)


def _create_admin(db):
    """Создаёт admin-а напрямую в БД."""
    from app.security import hash_password, new_salt
    salt = new_salt()
    pw_hash = hash_password("admin123", salt)
    cur = db.cursor()
    cur.execute(
        "INSERT INTO users (role, full_name, email, password_hash, salt, student_group) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("admin", "Admin", "admin@test.ru", pw_hash, salt, ""),
    )
    db.commit()
    return cur.lastrowid


def _login_admin(c):
    return c.post("/login", data={"login": "admin@test.ru", "password": "admin123"}, follow_redirects=False)


def _create_group(db, name="БИ-41", teacher_id=None):
    cur = db.cursor()
    cur.execute("INSERT OR IGNORE INTO groups (name, teacher_id) VALUES (?, ?)", (name, teacher_id))
    if teacher_id:
        cur.execute("INSERT OR IGNORE INTO group_teachers (group_name, teacher_id) VALUES (?, ?)", (name, teacher_id))
    db.commit()


def _get_first_discipline_id(db) -> int:
    cur = db.cursor()
    cur.execute("SELECT id FROM disciplines ORDER BY id LIMIT 1")
    row = cur.fetchone()
    assert row is not None
    return int(row[0])


def _link_teacher_discipline_group(db, teacher_id: int, group_name: str = "", discipline_id: int | None = None):
    cur = db.cursor()
    resolved_discipline_id = int(discipline_id or _get_first_discipline_id(db))
    normalized_group = (group_name or "").strip()
    cur.execute(
        "INSERT OR IGNORE INTO teacher_disciplines (teacher_id, discipline_id) VALUES (?, ?)",
        (teacher_id, resolved_discipline_id),
    )
    cur.execute(
        "INSERT OR IGNORE INTO teaching_assignments (teacher_id, discipline_id, group_name) VALUES (?, ?, ?)",
        (teacher_id, resolved_discipline_id, normalized_group),
    )
    db.commit()
    return resolved_discipline_id


def _insert_user(
    db,
    role: str,
    login: str,
    password: str = "pass123",
    full_name: str = "Тест Пользователь",
    group: str = "",
    assigned_teacher_id: int | None = None,
):
    from app.security import hash_password, new_salt

    salt = new_salt()
    pw_hash = hash_password(password, salt)
    cur = db.cursor()
    cur.execute(
        """
        INSERT INTO users (role, full_name, email, password_hash, salt, student_group, assigned_teacher_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (role, full_name, login.lower(), pw_hash, salt, group, assigned_teacher_id),
    )
    db.commit()
    return cur.lastrowid


def _insert_lecture(db, teacher_id, title="Тестовая лекция", body="x" * 100, discipline_id=None):
    resolved_discipline_id = int(discipline_id or _get_first_discipline_id(db))
    cur = db.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO teacher_disciplines (teacher_id, discipline_id) VALUES (?, ?)",
        (teacher_id, resolved_discipline_id),
    )
    cur.execute(
        "INSERT INTO lectures (teacher_id, title, body, created_at, discipline_id) VALUES (?, ?, ?, ?, ?)",
        (teacher_id, title, body, datetime.utcnow().isoformat(), resolved_discipline_id),
    )
    db.commit()
    return cur.lastrowid


def _insert_test(db, lecture_id, status="published", title="Тест 1"):
    cur = db.cursor()
    cur.execute(
        "INSERT INTO tests (lecture_id, title, status, created_at) VALUES (?, ?, ?, ?)",
        (lecture_id, title, status, datetime.utcnow().isoformat()),
    )
    db.commit()
    return cur.lastrowid


def _insert_question(db, test_id, text="Вопрос?", correct_index=0):
    options = json.dumps(["Верно", "Неверно А", "Неверно Б", "Неверно В"], ensure_ascii=False)
    cur = db.cursor()
    cur.execute(
        "INSERT INTO questions (test_id, text, options_json, correct_index) VALUES (?, ?, ?, ?)",
        (test_id, text, options, correct_index),
    )
    db.commit()
    return cur.lastrowid


# ═══════════════════════════════════════════════════════════════
# 1. БАЗА ДАННЫХ
# ═══════════════════════════════════════════════════════════════

class TestDatabase:
    def test_init_creates_tables(self, db):
        cur = db.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = {row[0] for row in cur.fetchall()}
        expected = {"users", "lectures", "tests", "questions", "attempts", "answers",
                    "groups", "disciplines", "teacher_disciplines", "teaching_assignments", "audit"}
        assert expected.issubset(tables), f"Не хватает таблиц: {expected - tables}"

    def test_default_disciplines_seeded(self, db):
        cur = db.cursor()
        cur.execute("SELECT COUNT(*) FROM disciplines")
        count = cur.fetchone()[0]
        assert count >= 5, f"Ожидали ≥5 дисциплин, получили {count}"

    def test_users_columns(self, db):
        cur = db.cursor()
        cur.execute("PRAGMA table_info(users)")
        cols = {row[1] for row in cur.fetchall()}
        assert "last_login" in cols
        assert "assigned_teacher_id" in cols
        assert "student_group" in cols
        assert "discipline_id" in cols
        assert "must_change_password" in cols
        assert "session_version" in cols


# ═══════════════════════════════════════════════════════════════
# 2. РЕГИСТРАЦИЯ / ЛОГИН / ЛОГАУТ
# ═══════════════════════════════════════════════════════════════

class TestAuth:
    def test_register_page_loads(self, client):
        r = client.get("/register")
        assert r.status_code == 200
        assert "Регистрация" in r.text

    def test_register_student_needs_group(self, client, db):
        _create_group(db, "БИ-41")
        r = _register(client, role="student", email="s1@t.ru", group="")
        assert r.status_code == 200
        assert "группу" in r.text.lower()

    def test_register_student_success(self, client, db):
        _create_group(db, "БИ-41")
        r = _register(client, role="student", email="s1@t.ru", group="БИ-41")
        assert r.status_code == 302
        assert "/dashboard" in r.headers.get("location", "")

    def test_register_teacher_blocked(self, client):
        r = _register(client, role="teacher", email="t1@t.ru")
        assert r.status_code == 200
        assert "только студентам" in r.text.lower()

    def test_register_duplicate_email(self, client, db):
        _create_group(db, "БИ-41")
        _register(client, role="student", email="dup@t.ru", group="БИ-41")
        r = _register(client, role="student", email="dup@t.ru", group="БИ-41")
        assert r.status_code == 200
        assert "логин" in r.text.lower() or "используется" in r.text.lower()

    def test_register_invalid_role(self, client):
        r = _register(client, role="hacker")
        assert r.status_code == 200
        assert "только студентам" in r.text.lower()

    def test_login_wrong_password(self, client, db):
        _insert_user(db, role="teacher", login="t2@t.ru", password="pass123", full_name="Teacher")
        r = _login(client, email="t2@t.ru", password="wrong")
        assert r.status_code == 200
        assert "неверный" in r.text.lower()

    def test_login_nonexistent(self, client):
        r = _login(client, email="noone@t.ru", password="x")
        assert r.status_code == 200
        assert "неверный" in r.text.lower()

    def test_login_teacher_redirects_v2(self, client, db):
        _insert_user(db, role="teacher", login="t3@t.ru", password="pass123", full_name="Teacher V2")
        r = _login(client, email="t3@t.ru")
        assert r.status_code == 302
        assert "/v2/teacher" in r.headers.get("location", "")

    def test_login_student_redirects_dashboard(self, client, db):
        _create_group(db, "БИ-41")
        _register(client, role="student", email="s3@t.ru", group="БИ-41")
        r = _login(client, email="s3@t.ru")
        assert r.status_code == 302
        assert "/dashboard" in r.headers.get("location", "")

    def test_login_with_temporary_password_redirects_to_forced_change(self, client, db):
        user_id = _insert_user(
            db,
            role="student",
            login="reset_student@test.ru",
            password="temp123!",
            full_name="Reset Student",
            group="БИ-41",
        )
        cur = db.cursor()
        cur.execute("UPDATE users SET must_change_password = 1 WHERE id = ?", (user_id,))
        db.commit()

        r = _login(client, email="reset_student@test.ru", password="temp123!")
        assert r.status_code == 302
        assert "/dashboard#profile-settings" == r.headers.get("location", "")

        blocked = client.get("/student/tests", follow_redirects=False)
        assert blocked.status_code == 302
        assert "/dashboard#profile-settings" == blocked.headers.get("location", "")

    def test_logout(self, client, db):
        _insert_user(db, role="teacher", login="t4@t.ru", password="pass123", full_name="Teacher Logout")
        _login(client, email="t4@t.ru")
        r = client.post("/logout", follow_redirects=False)
        assert r.status_code == 302
        assert "/" == r.headers.get("location", "").rstrip("/") or r.headers.get("location", "") == "/"

    def test_relogin_clears_stale_admin_session_flags(self, client, db):
        _create_admin(db)
        _insert_user(db, role="teacher", login="teacher_relogin@test.ru", password="pass123", full_name="Teacher Relogin")

        admin_login = _login_admin(client)
        assert admin_login.status_code == 302

        teacher_login = _login(client, email="teacher_relogin@test.ru", password="pass123")
        assert teacher_login.status_code == 302
        assert "/v2/teacher" in teacher_login.headers.get("location", "")

        dashboard = client.get("/dashboard")
        assert dashboard.status_code == 200
        assert "Teacher Relogin" in dashboard.text

        stale_admin = client.get("/v1/admin/students", follow_redirects=False)
        assert stale_admin.status_code == 302
        assert "/v1/admin" in stale_admin.headers.get("location", "")

    def test_parallel_clients_keep_isolated_sessions(self, isolated_db, db):
        from fastapi.testclient import TestClient
        import main as main_module
        from app.db import init_db

        init_db()
        _create_group(db, "BI-41")
        _insert_user(db, role="student", login="iso_one@test.ru", password="pass123", full_name="Student One", group="BI-41")
        _insert_user(db, role="student", login="iso_two@test.ru", password="pass123", full_name="Student Two", group="BI-41")

        with TestClient(main_module.app, raise_server_exceptions=False) as client_one, TestClient(main_module.app, raise_server_exceptions=False) as client_two:
            assert _login(client_one, email="iso_one@test.ru").status_code == 302
            assert _login(client_two, email="iso_two@test.ru").status_code == 302

            dash_one = client_one.get("/dashboard")
            dash_two = client_two.get("/dashboard")

            assert dash_one.status_code == 200
            assert dash_two.status_code == 200
            assert "Student One" in dash_one.text
            assert "Student Two" not in dash_one.text
            assert "Student Two" in dash_two.text
            assert "Student One" not in dash_two.text

    def test_password_reset_invalidates_existing_student_session(self, isolated_db, db):
        from fastapi.testclient import TestClient
        import main as main_module
        from app.db import init_db

        init_db()
        student_id = _insert_user(
            db,
            role="student",
            login="session_reset_student@test.ru",
            password="pass123",
            full_name="Session Reset Student",
            group="БИ-41",
        )
        _create_admin(db)

        with TestClient(main_module.app, raise_server_exceptions=False) as student_client, TestClient(main_module.app, raise_server_exceptions=False) as admin_client:
            assert _login(student_client, email="session_reset_student@test.ru", password="pass123").status_code == 302
            assert _login_admin(admin_client).status_code == 302

            reset_response = admin_client.post(
                f"/admin/users/{student_id}/reset_password",
                data={"next": "/admin/students"},
                follow_redirects=True,
            )
            assert reset_response.status_code == 200
            assert "Временный пароль:" in reset_response.text

            stale_dashboard = student_client.get("/dashboard", follow_redirects=False)
            assert stale_dashboard.status_code == 302
            assert stale_dashboard.headers.get("location", "") == "/login"

    def test_self_password_change_invalidates_parallel_session(self, isolated_db, db):
        from fastapi.testclient import TestClient
        import main as main_module
        from app.db import init_db

        init_db()
        _insert_user(
            db,
            role="teacher",
            login="parallel_teacher@test.ru",
            password="pass123",
            full_name="Parallel Teacher",
        )

        with TestClient(main_module.app, raise_server_exceptions=False) as primary_client, TestClient(main_module.app, raise_server_exceptions=False) as stale_client:
            assert _login(primary_client, email="parallel_teacher@test.ru", password="pass123").status_code == 302
            assert _login(stale_client, email="parallel_teacher@test.ru", password="pass123").status_code == 302

            change_response = primary_client.post(
                "/dashboard/profile/password",
                data={
                    "current_password": "pass123",
                    "new_password": "newpass123",
                    "new_password_confirm": "newpass123",
                },
                follow_redirects=False,
            )
            assert change_response.status_code == 302
            assert "/dashboard#profile-settings" in change_response.headers.get("location", "")

            fresh_dashboard = primary_client.get("/dashboard")
            assert fresh_dashboard.status_code == 200
            assert "Parallel Teacher" in fresh_dashboard.text

            stale_dashboard = stale_client.get("/dashboard", follow_redirects=False)
            assert stale_dashboard.status_code == 302
            assert stale_dashboard.headers.get("location", "") == "/login"

            relogin = _login(stale_client, email="parallel_teacher@test.ru", password="newpass123")
            assert relogin.status_code == 302
            assert "/v2/teacher" in relogin.headers.get("location", "")

    def test_dynamic_pages_disable_cache_and_use_project_cookie(self, client, db):
        login_page = client.get("/login")
        assert login_page.status_code == 200
        assert "no-store" in login_page.headers.get("cache-control", "").lower()
        assert "cookie" in login_page.headers.get("vary", "").lower()

        _insert_user(db, role="teacher", login="cache_teacher@test.ru", password="pass123", full_name="Cache Teacher")
        login_response = _login(client, email="cache_teacher@test.ru")
        assert login_response.status_code == 302
        assert "campusplus_session=" in login_response.headers.get("set-cookie", "")

        dashboard = client.get("/dashboard")
        assert dashboard.status_code == 200
        assert "no-store" in dashboard.headers.get("cache-control", "").lower()
        assert "cookie" in dashboard.headers.get("vary", "").lower()

    @pytest.mark.parametrize(
        "role, login, password, initial_name, updated_name",
        [
            ("student", "profile_student@test.ru", "pass123", "Student Profile", "Student Profile Updated"),
            ("teacher", "profile_teacher@test.ru", "pass123", "Teacher Profile", "Teacher Profile Updated"),
            ("admin", "admin@test.ru", "admin123", "Admin", "Admin Profile Updated"),
        ],
    )
    def test_dashboard_profile_full_name_update_for_any_role(
        self,
        client,
        db,
        role,
        login,
        password,
        initial_name,
        updated_name,
    ):
        if role == "admin":
            _create_admin(db)
            _login_admin(client)
        else:
            _insert_user(db, role=role, login=login, password=password, full_name=initial_name)
            _login(client, email=login, password=password)

        r = client.post(
            "/dashboard/profile/name",
            data={"full_name": updated_name},
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "/dashboard" in r.headers.get("location", "")

        cur = db.cursor()
        cur.execute("SELECT full_name FROM users WHERE email = ?", (login,))
        row = cur.fetchone()
        assert row is not None
        assert row["full_name"] == updated_name

    def test_dashboard_profile_password_update(self, client, db):
        _insert_user(db, role="teacher", login="pwd_teacher@test.ru", password="pass123", full_name="Password Teacher")
        _login(client, email="pwd_teacher@test.ru", password="pass123")

        r = client.post(
            "/dashboard/profile/password",
            data={
                "current_password": "pass123",
                "new_password": "newpass123",
                "new_password_confirm": "newpass123",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "/dashboard" in r.headers.get("location", "")

        client.post("/logout", follow_redirects=False)
        wrong_old = _login(client, email="pwd_teacher@test.ru", password="pass123")
        assert wrong_old.status_code == 200
        ok_new = _login(client, email="pwd_teacher@test.ru", password="newpass123")
        assert ok_new.status_code == 302

    def test_dashboard_profile_password_update_clears_forced_change_flag(self, client, db):
        user_id = _insert_user(
            db,
            role="teacher",
            login="forced_change@test.ru",
            password="temp123!",
            full_name="Forced Change",
        )
        cur = db.cursor()
        cur.execute("UPDATE users SET must_change_password = 1 WHERE id = ?", (user_id,))
        db.commit()

        assert _login(client, email="forced_change@test.ru", password="temp123!").status_code == 302

        r = client.post(
            "/dashboard/profile/password",
            data={
                "current_password": "temp123!",
                "new_password": "newpass123",
                "new_password_confirm": "newpass123",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302

        cur.execute("SELECT must_change_password FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        assert row is not None
        assert int(row["must_change_password"]) == 0


# ═══════════════════════════════════════════════════════════════
# 3. СТРАНИЦЫ (GET — без авторизации)
# ═══════════════════════════════════════════════════════════════

class TestPublicPages:
    def test_index(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "КампусПлюс" in r.text

    def test_login_page(self, client):
        r = client.get("/login")
        assert r.status_code == 200

    def test_dashboard_requires_auth(self, client):
        r = client.get("/dashboard", follow_redirects=False)
        assert r.status_code in (302, 401)

    def test_student_tests_requires_auth(self, client):
        r = client.get("/student/tests", follow_redirects=False)
        assert r.status_code in (302, 401)

    def test_teacher_lectures_requires_auth(self, client):
        r = client.get("/teacher/lectures", follow_redirects=False)
        assert r.status_code in (302, 401)


# ═══════════════════════════════════════════════════════════════
# 4. УЧИТЕЛЬ: лекции, тесты, аналитика
# ═══════════════════════════════════════════════════════════════

class TestTeacherFlow:
    def _setup_teacher(self, client, db):
        teacher_id = _insert_user(db, role="teacher", login="teach@t.ru", password="pass123", full_name="Учитель")
        _login(client, email="teach@t.ru", password="pass123")
        return teacher_id

    def test_lectures_page(self, client, db):
        self._setup_teacher(client, db)
        r = client.get("/teacher/lectures")
        assert r.status_code == 200

    def test_lectures_grouped_by_discipline(self, client, db):
        teacher_id = self._setup_teacher(client, db)
        cur = db.cursor()
        cur.execute("INSERT INTO disciplines (name) VALUES (?)", ("Algorithms",))
        db.commit()
        discipline_id = cur.lastrowid

        lecture_id = _insert_lecture(db, teacher_id, title="Lecture A", discipline_id=discipline_id)
        _insert_test(db, lecture_id, title="Test A")
        _insert_lecture(db, teacher_id, title="Lecture B", discipline_id=discipline_id)

        r = client.get(f"/teacher/lectures?discipline_id={discipline_id}")
        assert r.status_code == 200
        body = r.text
        assert "Algorithms" in body
        assert "Лекции дисциплины" in body
        assert "Тесты дисциплины" in body
        assert "Lecture A" in body
        assert "Test A" in body

    def test_new_lecture_page(self, client, db):
        self._setup_teacher(client, db)
        r = client.get("/teacher/lectures/new")
        assert r.status_code == 200

    def test_lecture_detail(self, client, db):
        tid = self._setup_teacher(client, db)
        lid = _insert_lecture(db, tid, "Лекция 1")
        r = client.get(f"/teacher/lectures/{lid}")
        assert r.status_code == 200
        assert "Лекция 1" in r.text

    def test_lecture_detail_other_teacher(self, client, db):
        """Преподаватель не видит чужие лекции."""
        tid = self._setup_teacher(client, db)
        lid = _insert_lecture(db, tid + 999, "Чужая")  # fake teacher_id
        r = client.get(f"/teacher/lectures/{lid}", follow_redirects=False)
        assert r.status_code == 302

    def test_delete_lecture(self, client, db):
        tid = self._setup_teacher(client, db)
        lid = _insert_lecture(db, tid)
        r = client.post(f"/teacher/lectures/{lid}/delete", follow_redirects=False)
        assert r.status_code == 302
        cur = db.cursor()
        cur.execute("SELECT id FROM lectures WHERE id = ?", (lid,))
        assert cur.fetchone() is None

    def test_edit_test(self, client, db):
        tid = self._setup_teacher(client, db)
        lid = _insert_lecture(db, tid)
        test_id = _insert_test(db, lid, status="draft")
        qid = _insert_question(db, test_id)
        r = client.get(f"/teacher/tests/{test_id}/edit")
        assert r.status_code == 200
        assert "Вопрос?" in r.text

    def test_publish_test(self, client, db):
        tid = self._setup_teacher(client, db)
        lid = _insert_lecture(db, tid)
        test_id = _insert_test(db, lid, status="draft")
        r = client.post(f"/teacher/tests/{test_id}/publish", follow_redirects=False)
        assert r.status_code == 302
        cur = db.cursor()
        cur.execute("SELECT status FROM tests WHERE id = ?", (test_id,))
        assert cur.fetchone()[0] == "published"

    def test_delete_test(self, client, db):
        tid = self._setup_teacher(client, db)
        lid = _insert_lecture(db, tid)
        test_id = _insert_test(db, lid, status="draft")
        _insert_question(db, test_id)
        r = client.post(f"/teacher/tests/{test_id}/delete", follow_redirects=False)
        assert r.status_code == 302
        cur = db.cursor()
        cur.execute("SELECT id FROM tests WHERE id = ?", (test_id,))
        assert cur.fetchone() is None

    def test_analytics(self, client, db):
        self._setup_teacher(client, db)
        r = client.get("/teacher/analytics")
        assert r.status_code == 200

    def test_qr_code_page(self, client, db):
        tid = self._setup_teacher(client, db)
        lid = _insert_lecture(db, tid)
        test_id = _insert_test(db, lid, status="published")
        r = client.get(f"/teacher/tests/{test_id}/qr")
        assert r.status_code == 200
        assert "qr" in r.text.lower() or "QR" in r.text

    def test_teacher_can_reset_assigned_student_password(self, client, db):
        teacher_id = self._setup_teacher(client, db)
        _link_teacher_discipline_group(db, teacher_id, "БИ-41")
        student_id = _insert_user(
            db,
            role="student",
            login="reset_me@student.ru",
            password="oldpass123",
            full_name="Reset Me",
            group="БИ-41",
            assigned_teacher_id=None,
        )

        response = client.post(
            f"/v2/teacher/students/{student_id}/reset_password",
            data={"next": "/v2/teacher/students"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        temp_password = _extract_temporary_password(response.text)

        client.post("/logout", follow_redirects=False)
        old_login = _login(client, email="reset_me@student.ru", password="oldpass123")
        assert old_login.status_code == 200

        new_login = _login(client, email="reset_me@student.ru", password=temp_password)
        assert new_login.status_code == 302
        assert "/dashboard#profile-settings" == new_login.headers.get("location", "")

    def test_teacher_cannot_reset_foreign_student_password(self, client, db):
        self._setup_teacher(client, db)
        student_id = _insert_user(
            db,
            role="student",
            login="foreign@student.ru",
            password="oldpass123",
            full_name="Foreign Student",
            group="БИ-41",
            assigned_teacher_id=None,
        )

        response = client.post(
            f"/v2/teacher/students/{student_id}/reset_password",
            data={"next": "/v2/teacher/students"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "Нет доступа" in response.text


# ═══════════════════════════════════════════════════════════════
# 5. СТУДЕНТ: тесты, прохождение, аналитика, рост
# ═══════════════════════════════════════════════════════════════

class TestStudentFlow:
    def _setup_student(self, client, db):
        _create_group(db, "БИ-41")
        _register(client, role="student", email="stud@t.ru", group="БИ-41")
        _login(client, email="stud@t.ru")
        cur = db.cursor()
        cur.execute("SELECT id FROM users WHERE email='stud@t.ru'")
        return cur.fetchone()[0]

    def test_student_tests_page(self, client, db):
        self._setup_student(client, db)
        r = client.get("/student/tests")
        assert r.status_code == 200

    def test_take_test(self, client, db):
        sid = self._setup_student(client, db)
        # Создаём учителя + лекцию + тест
        from app.security import hash_password, new_salt
        salt = new_salt()
        cur = db.cursor()
        cur.execute(
            "INSERT INTO users (role, full_name, email, password_hash, salt, student_group) VALUES (?, ?, ?, ?, ?, ?)",
            ("teacher", "T", "tx@t.ru", hash_password("p", salt), salt, ""),
        )
        db.commit()
        tid = cur.lastrowid
        _link_teacher_discipline_group(db, tid, "БИ-41")
        lid = _insert_lecture(db, tid)
        test_id = _insert_test(db, lid, status="published")
        q1 = _insert_question(db, test_id, "В1?", correct_index=0)
        q2 = _insert_question(db, test_id, "В2?", correct_index=2)

        # Проходим тест
        r = client.post(f"/student/tests/{test_id}/take", data={
            f"q_{q1}": "0",
            f"q_{q2}": "2",
        }, follow_redirects=False)
        assert r.status_code == 302
        assert "/student/attempts/" in r.headers.get("location", "")

        # Проверяем запись в attempts
        cur.execute("SELECT score FROM attempts WHERE student_id = ? AND test_id = ?", (sid, test_id))
        row = cur.fetchone()
        assert row is not None
        assert float(row[0]) == 100.0

    def test_take_test_partial_score(self, client, db):
        sid = self._setup_student(client, db)
        from app.security import hash_password, new_salt
        salt = new_salt()
        cur = db.cursor()
        cur.execute(
            "INSERT INTO users (role, full_name, email, password_hash, salt, student_group) VALUES (?, ?, ?, ?, ?, ?)",
            ("teacher", "T2", "t2x@t.ru", hash_password("p", salt), salt, ""),
        )
        db.commit()
        tid = cur.lastrowid
        _link_teacher_discipline_group(db, tid, "БИ-41")
        lid = _insert_lecture(db, tid)
        test_id = _insert_test(db, lid)
        q1 = _insert_question(db, test_id, "В1?", correct_index=0)
        q2 = _insert_question(db, test_id, "В2?", correct_index=1)

        r = client.post(f"/student/tests/{test_id}/take", data={
            f"q_{q1}": "0",  # верно
            f"q_{q2}": "3",  # неверно
        }, follow_redirects=False)
        assert r.status_code == 302

        cur.execute("SELECT score FROM attempts WHERE student_id = ?", (sid,))
        assert float(cur.fetchone()[0]) == 50.0

    def test_cannot_retake_test(self, client, db):
        sid = self._setup_student(client, db)
        from app.security import hash_password, new_salt
        salt = new_salt()
        cur = db.cursor()
        cur.execute(
            "INSERT INTO users (role, full_name, email, password_hash, salt, student_group) VALUES (?, ?, ?, ?, ?, ?)",
            ("teacher", "T3", "t3x@t.ru", hash_password("p", salt), salt, ""),
        )
        db.commit()
        tid = cur.lastrowid
        _link_teacher_discipline_group(db, tid, "БИ-41")
        lid = _insert_lecture(db, tid)
        test_id = _insert_test(db, lid)
        qid = _insert_question(db, test_id)
        # Первая попытка
        client.post(f"/student/tests/{test_id}/take", data={f"q_{qid}": "0"}, follow_redirects=False)
        # Вторая — должна перенаправить
        r = client.post(f"/student/tests/{test_id}/take", data={f"q_{qid}": "0"}, follow_redirects=False)
        assert r.status_code == 302
        cur.execute("SELECT COUNT(*) FROM attempts WHERE student_id = ? AND test_id = ?", (sid, test_id))
        assert cur.fetchone()[0] == 1

    def test_attempt_review(self, client, db):
        sid = self._setup_student(client, db)
        from app.security import hash_password, new_salt
        salt = new_salt()
        cur = db.cursor()
        cur.execute(
            "INSERT INTO users (role, full_name, email, password_hash, salt, student_group) VALUES (?, ?, ?, ?, ?, ?)",
            ("teacher", "T4", "t4x@t.ru", hash_password("p", salt), salt, ""),
        )
        db.commit()
        tid = cur.lastrowid
        _link_teacher_discipline_group(db, tid, "БИ-41")
        lid = _insert_lecture(db, tid)
        test_id = _insert_test(db, lid)
        qid = _insert_question(db, test_id)
        client.post(f"/student/tests/{test_id}/take", data={f"q_{qid}": "0"}, follow_redirects=False)
        cur.execute("SELECT id FROM attempts WHERE student_id = ?", (sid,))
        attempt_id = cur.fetchone()[0]
        r = client.get(f"/student/attempts/{attempt_id}")
        assert r.status_code == 200

    def test_analytics_page(self, client, db):
        self._setup_student(client, db)
        r = client.get("/student/analytics")
        assert r.status_code == 200

    def test_growth_page(self, client, db):
        self._setup_student(client, db)
        r = client.get("/growth")
        assert r.status_code == 200

    def test_dashboard_student(self, client, db):
        self._setup_student(client, db)
        r = client.get("/dashboard")
        assert r.status_code == 200

    def test_student_cannot_open_unassigned_test(self, client, db):
        self._setup_student(client, db)
        teacher_id = _insert_user(db, role="teacher", login="locked_teacher@test.ru", password="pass123", full_name="Locked Teacher")
        _link_teacher_discipline_group(db, teacher_id, "БИ-99")
        lecture_id = _insert_lecture(db, teacher_id, title="Locked Lecture")
        test_id = _insert_test(db, lecture_id, status="published")

        r = client.get(f"/student/tests/{test_id}/entry", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers.get("location", "") == "/student/tests"


# ═══════════════════════════════════════════════════════════════
# 6. АДМИН
# ═══════════════════════════════════════════════════════════════

class TestAdminFlow:
    def _setup_admin(self, client, db):
        _create_admin(db)
        _login_admin(client)

    def test_admin_login(self, client, db):
        _create_admin(db)
        r = _login_admin(client)
        assert r.status_code == 302
        assert "/admin" in r.headers.get("location", "")

    def test_admin_students_page(self, client, db):
        self._setup_admin(client, db)
        r = client.get("/admin/students")
        assert r.status_code == 200

    def test_admin_students_grouped_by_group(self, client, db):
        self._setup_admin(client, db)
        _insert_user(db, role="student", login="grouped_1@test.ru", password="pass123", full_name="Student One", group="BI-41.2")
        _insert_user(db, role="student", login="grouped_2@test.ru", password="pass123", full_name="Student Two", group="BI-41.2")
        _insert_user(db, role="student", login="nogroup@test.ru", password="pass123", full_name="Student Zero", group="")

        r = client.get("/admin/students")
        assert r.status_code == 200
        body = r.text
        assert 'data-group-name="BI-41.2"' in body
        assert body.count('data-group-name="') >= 2
        assert "Student One" in body
        assert "Student Zero" in body

    def test_admin_teachers_page(self, client, db):
        self._setup_admin(client, db)
        r = client.get("/admin/teachers")
        assert r.status_code == 200

    def test_admin_groups_page(self, client, db):
        self._setup_admin(client, db)
        r = client.get("/admin/groups")
        assert r.status_code == 200

    def test_admin_group_supports_multiple_teachers(self, client, db):
        self._setup_admin(client, db)
        teacher_one = _insert_user(db, role="teacher", login="g1@test.ru", password="pass123", full_name="Teacher One")
        teacher_two = _insert_user(db, role="teacher", login="g2@test.ru", password="pass123", full_name="Teacher Two")
        _create_group(db, "БИ-41.1", teacher_one)

        r = client.post("/admin/groups/БИ-41.1/teacher", data={"teacher_id": str(teacher_two)}, follow_redirects=False)
        assert r.status_code == 302

        cur = db.cursor()
        cur.execute(
            "SELECT teacher_id FROM group_teachers WHERE group_name = ? ORDER BY teacher_id",
            ("БИ-41.1",),
        )
        teacher_ids = [int(row[0]) for row in cur.fetchall()]
        assert teacher_one in teacher_ids
        assert teacher_two in teacher_ids

        page = client.get("/admin/groups/БИ-41.1")
        assert page.status_code == 200
        assert "Teacher One" in page.text
        assert "Teacher Two" in page.text

    def test_admin_group_page_shows_teacher_disciplines_for_group(self, client, db):
        self._setup_admin(client, db)
        teacher_one = _insert_user(db, role="teacher", login="g5@test.ru", password="pass123", full_name="Teacher Five")
        teacher_two = _insert_user(db, role="teacher", login="g6@test.ru", password="pass123", full_name="Teacher Six")
        _create_group(db, "БИ-41.3", teacher_one)
        cur = db.cursor()
        cur.execute("INSERT OR IGNORE INTO group_teachers (group_name, teacher_id) VALUES (?, ?)", ("БИ-41.3", teacher_two))
        cur.execute("SELECT id, name FROM disciplines ORDER BY id LIMIT 2")
        discipline_rows = cur.fetchall()
        first_discipline_id = int(discipline_rows[0][0])
        first_discipline_name = discipline_rows[0][1]
        second_discipline_id = int(discipline_rows[1][0])
        second_discipline_name = discipline_rows[1][1]
        _link_teacher_discipline_group(db, teacher_one, "БИ-41.3", first_discipline_id)
        _link_teacher_discipline_group(db, teacher_two, "БИ-41.3", second_discipline_id)

        page = client.get("/admin/groups/БИ-41.3")
        assert page.status_code == 200
        assert "Teacher Five" in page.text
        assert "Teacher Six" in page.text
        assert first_discipline_name in page.text
        assert second_discipline_name in page.text
        assert "Дисциплины этой группы" in page.text

    def test_admin_can_remove_teacher_from_group(self, client, db):
        self._setup_admin(client, db)
        teacher_one = _insert_user(db, role="teacher", login="g3@test.ru", password="pass123", full_name="Teacher Three")
        teacher_two = _insert_user(db, role="teacher", login="g4@test.ru", password="pass123", full_name="Teacher Four")
        _create_group(db, "БИ-41.2", teacher_one)
        cur = db.cursor()
        cur.execute("INSERT OR IGNORE INTO group_teachers (group_name, teacher_id) VALUES (?, ?)", ("БИ-41.2", teacher_two))
        db.commit()

        r = client.post(f"/admin/groups/БИ-41.2/teachers/{teacher_one}/delete", follow_redirects=False)
        assert r.status_code == 302

        cur.execute(
            "SELECT teacher_id FROM group_teachers WHERE group_name = ? ORDER BY teacher_id",
            ("БИ-41.2",),
        )
        teacher_ids = [int(row[0]) for row in cur.fetchall()]
        assert teacher_one not in teacher_ids
        assert teacher_two in teacher_ids

    def test_admin_can_delete_empty_group_and_cleanup_links(self, client, db):
        self._setup_admin(client, db)
        teacher_id = _insert_user(db, role="teacher", login="g7@test.ru", password="pass123", full_name="Teacher Seven")
        _create_group(db, "БИ-99.9", teacher_id)
        cur = db.cursor()
        cur.execute("SELECT id FROM disciplines ORDER BY id LIMIT 1")
        discipline_id = int(cur.fetchone()[0])
        _link_teacher_discipline_group(db, teacher_id, "БИ-99.9", discipline_id)

        r = client.post("/admin/groups/БИ-99.9/delete", follow_redirects=False)
        assert r.status_code == 302

        cur.execute("SELECT 1 FROM groups WHERE name = ?", ("БИ-99.9",))
        assert cur.fetchone() is None
        cur.execute("SELECT 1 FROM group_teachers WHERE group_name = ?", ("БИ-99.9",))
        assert cur.fetchone() is None
        cur.execute("SELECT 1 FROM teaching_assignments WHERE group_name = ?", ("БИ-99.9",))
        assert cur.fetchone() is None

    def test_admin_disciplines_page(self, client, db):
        self._setup_admin(client, db)
        r = client.get("/admin/disciplines")
        assert r.status_code == 200

    def test_admin_create_discipline(self, client, db):
        self._setup_admin(client, db)
        r = client.post("/admin/disciplines/create",
                        data={"discipline_name": "Новая дисциплина"},
                        follow_redirects=False)
        assert r.status_code == 302
        cur = db.cursor()
        cur.execute("SELECT id FROM disciplines WHERE name = 'Новая дисциплина'")
        assert cur.fetchone() is not None

    def test_admin_create_group(self, client, db):
        self._setup_admin(client, db)
        # Сначала нужен преподаватель
        from app.security import hash_password, new_salt
        salt = new_salt()
        cur = db.cursor()
        cur.execute(
            "INSERT INTO users (role, full_name, email, password_hash, salt, student_group) VALUES (?, ?, ?, ?, ?, ?)",
            ("teacher", "Препод", "prep@t.ru", hash_password("p", salt), salt, ""),
        )
        db.commit()
        tid = cur.lastrowid
        r = client.post("/admin/groups/create",
                        data={"group_name": "НоваяГруппа", "teacher_id": str(tid)},
                        follow_redirects=False)
        assert r.status_code == 302
        cur.execute("SELECT id FROM groups WHERE name = 'НоваяГруппа'")
        assert cur.fetchone() is not None

    def test_admin_create_user(self, client, db):
        self._setup_admin(client, db)
        r = client.post("/admin/users/create", data={
            "full_name": "Новый Преподаватель",
            "login": "new_t@t.ru",
            "password": "pass123",
            "role": "teacher",
        }, follow_redirects=False)
        assert r.status_code == 302
        cur = db.cursor()
        cur.execute("SELECT role FROM users WHERE email='new_t@t.ru'")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "teacher"

    def test_admin_edit_user(self, client, db):
        self._setup_admin(client, db)
        from app.security import hash_password, new_salt
        salt = new_salt()
        cur = db.cursor()
        cur.execute(
            "INSERT INTO users (role, full_name, email, password_hash, salt, student_group) VALUES (?, ?, ?, ?, ?, ?)",
            ("student", "Old Name", "edit@t.ru", hash_password("p", salt), salt, ""),
        )
        db.commit()
        uid = cur.lastrowid

        r = client.get(f"/admin/users/{uid}/edit")
        assert r.status_code == 200
        assert "Old Name" in r.text

        r = client.post(f"/admin/users/{uid}/edit", data={
            "full_name": "New Name",
            "login": "edit@t.ru",
            "role": "student",
            "student_group": "БИ-99",
        }, follow_redirects=False)
        assert r.status_code == 302
        cur.execute("SELECT full_name, student_group FROM users WHERE id = ?", (uid,))
        row = cur.fetchone()
        assert row[0] == "New Name"
        assert row[1] == "БИ-99"

    def test_admin_delete_student(self, client, db):
        self._setup_admin(client, db)
        from app.security import hash_password, new_salt
        salt = new_salt()
        cur = db.cursor()
        cur.execute(
            "INSERT INTO users (role, full_name, email, password_hash, salt, student_group) VALUES (?, ?, ?, ?, ?, ?)",
            ("student", "Del", "del@t.ru", hash_password("p", salt), salt, ""),
        )
        db.commit()
        uid = cur.lastrowid

        r = client.post(f"/admin/users/{uid}/delete", follow_redirects=False)
        assert r.status_code == 302
        cur.execute("SELECT id FROM users WHERE id = ?", (uid,))
        assert cur.fetchone() is None

    def test_admin_can_reset_teacher_password(self, client, db):
        self._setup_admin(client, db)
        teacher_id = _insert_user(
            db,
            role="teacher",
            login="reset_teacher@test.ru",
            password="oldpass123",
            full_name="Reset Teacher",
        )

        response = client.post(
            f"/admin/users/{teacher_id}/reset_password",
            data={"next": "/admin/teachers"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        temp_password = _extract_temporary_password(response.text)

        client.post("/logout", follow_redirects=False)
        old_login = _login(client, email="reset_teacher@test.ru", password="oldpass123")
        assert old_login.status_code == 200

        new_login = _login(client, email="reset_teacher@test.ru", password=temp_password)
        assert new_login.status_code == 302
        assert "/dashboard#profile-settings" == new_login.headers.get("location", "")

    def test_admin_cannot_delete_teacher(self, client, db):
        self._setup_admin(client, db)
        from app.security import hash_password, new_salt
        salt = new_salt()
        cur = db.cursor()
        cur.execute(
            "INSERT INTO users (role, full_name, email, password_hash, salt, student_group) VALUES (?, ?, ?, ?, ?, ?)",
            ("teacher", "NoDel", "nodel@t.ru", hash_password("p", salt), salt, ""),
        )
        db.commit()
        uid = cur.lastrowid

        r = client.post(f"/admin/users/{uid}/delete", follow_redirects=False)
        assert r.status_code == 302
        cur.execute("SELECT id FROM users WHERE id = ?", (uid,))
        assert cur.fetchone() is not None  # Не удалён

    def test_admin_discipline_detail(self, client, db):
        self._setup_admin(client, db)
        cur = db.cursor()
        cur.execute("SELECT id FROM disciplines LIMIT 1")
        did = cur.fetchone()[0]
        r = client.get(f"/admin/disciplines/{did}")
        assert r.status_code == 200

    def test_admin_assign_teacher_to_discipline(self, client, db):
        self._setup_admin(client, db)
        from app.security import hash_password, new_salt
        salt = new_salt()
        cur = db.cursor()
        cur.execute(
            "INSERT INTO users (role, full_name, email, password_hash, salt, student_group) VALUES (?, ?, ?, ?, ?, ?)",
            ("teacher", "AssignT", "at@t.ru", hash_password("p", salt), salt, ""),
        )
        db.commit()
        tid = cur.lastrowid
        cur.execute("SELECT id FROM disciplines LIMIT 1")
        did = cur.fetchone()[0]

        r = client.post(f"/admin/disciplines/{did}/assign-teacher",
                        data={"teacher_id": str(tid)},
                        follow_redirects=False)
        assert r.status_code == 302
        cur.execute("SELECT 1 FROM teacher_disciplines WHERE teacher_id = ? AND discipline_id = ?", (tid, did))
        assert cur.fetchone() is not None


# ═══════════════════════════════════════════════════════════════
# 7. V2 УЧИТЕЛЬ
# ═══════════════════════════════════════════════════════════════

class TestV2Teacher:
    def _setup(self, client, db):
        teacher_id = _insert_user(db, role="teacher", login="v2t@t.ru", password="pass123", full_name="V2 Teacher")
        _login(client, email="v2t@t.ru", password="pass123")
        return teacher_id

    def test_disciplines_page(self, client, db):
        self._setup(client, db)
        r = client.get("/v2/teacher/disciplines")
        assert r.status_code == 200

    def test_disciplines_page_shows_group_access_controls(self, client, db):
        teacher_id = self._setup(client, db)
        discipline_id = _link_teacher_discipline_group(db, teacher_id, "BI-42.1")
        db.cursor().execute("INSERT OR IGNORE INTO groups (name) VALUES (?)", ("BI-42.2",))
        db.commit()

        r = client.get("/v2/teacher/disciplines")
        assert r.status_code == 200
        assert "BI-42.1" in r.text
        assert "Открыть доступ группе" in r.text
        assert f'name="discipline_id" value="{discipline_id}"' in r.text

    def test_tests_page(self, client, db):
        self._setup(client, db)
        r = client.get("/v2/teacher/tests")
        assert r.status_code == 200

    def test_groups_page(self, client, db):
        self._setup(client, db)
        r = client.get("/v2/teacher/groups")
        assert r.status_code == 200

    def test_assign_group_to_discipline(self, client, db):
        teacher_id = self._setup(client, db)
        cur = db.cursor()
        cur.execute("SELECT id FROM disciplines LIMIT 1")
        discipline_id = int(cur.fetchone()[0])
        cur.execute(
            "INSERT OR IGNORE INTO teacher_disciplines (teacher_id, discipline_id) VALUES (?, ?)",
            (teacher_id, discipline_id),
        )
        cur.execute("INSERT OR IGNORE INTO groups (name) VALUES (?)", ("BI-77",))
        _insert_user(
            db,
            role="student",
            login="group_bind@test.ru",
            password="pass123",
            full_name="Group Bind Student",
            group="BI-77",
        )
        db.commit()

        r = client.post(
            "/v2/teacher/groups/assign",
            data={"group_name": "BI-77", "discipline_id": str(discipline_id)},
            follow_redirects=False,
        )
        assert r.status_code == 302
        cur.execute(
            """
            SELECT 1 FROM teaching_assignments
            WHERE teacher_id = ? AND discipline_id = ? AND group_name = ?
            """,
            (teacher_id, discipline_id, "BI-77"),
        )
        assert cur.fetchone() is not None

    def test_unassign_group_from_discipline_blocks_attempt_backfill(self, client, db):
        teacher_id = self._setup(client, db)
        discipline_id = _link_teacher_discipline_group(db, teacher_id, "BI-78")
        student_id = _insert_user(
            db,
            role="student",
            login="blocked_backfill@test.ru",
            password="pass123",
            full_name="Blocked Backfill Student",
            group="BI-78",
            assigned_teacher_id=None,
        )
        lecture_id = _insert_lecture(db, teacher_id, title="Blocked Backfill Lecture", discipline_id=discipline_id)
        test_id = _insert_test(db, lecture_id, status="published", title="Blocked Backfill Test")
        cur = db.cursor()
        cur.execute(
            "INSERT INTO attempts (test_id, student_id, score, taken_at) VALUES (?, ?, ?, ?)",
            (test_id, student_id, 83.0, datetime.utcnow().isoformat()),
        )
        db.commit()

        r = client.post(
            "/v2/teacher/groups/unassign",
            data={"group_name": "BI-78", "discipline_id": str(discipline_id)},
            follow_redirects=False,
        )
        assert r.status_code == 302

        client.get("/v2/teacher/disciplines")
        cur.execute(
            """
            SELECT 1 FROM teaching_assignments
            WHERE teacher_id = ? AND discipline_id = ? AND group_name = ?
            """,
            (teacher_id, discipline_id, "BI-78"),
        )
        assert cur.fetchone() is None
        cur.execute(
            """
            SELECT 1 FROM teaching_assignment_blocks
            WHERE teacher_id = ? AND discipline_id = ? AND group_name = ?
            """,
            (teacher_id, discipline_id, "BI-78"),
        )
        assert cur.fetchone() is not None

    def test_reassign_group_to_discipline_clears_manual_block(self, client, db):
        teacher_id = self._setup(client, db)
        discipline_id = _link_teacher_discipline_group(db, teacher_id, "BI-79")

        client.post(
            "/v2/teacher/groups/unassign",
            data={"group_name": "BI-79", "discipline_id": str(discipline_id)},
            follow_redirects=False,
        )
        r = client.post(
            "/v2/teacher/groups/assign",
            data={"group_name": "BI-79", "discipline_id": str(discipline_id)},
            follow_redirects=False,
        )
        assert r.status_code == 302

        cur = db.cursor()
        cur.execute(
            """
            SELECT 1 FROM teaching_assignments
            WHERE teacher_id = ? AND discipline_id = ? AND group_name = ?
            """,
            (teacher_id, discipline_id, "BI-79"),
        )
        assert cur.fetchone() is not None
        cur.execute(
            """
            SELECT 1 FROM teaching_assignment_blocks
            WHERE teacher_id = ? AND discipline_id = ? AND group_name = ?
            """,
            (teacher_id, discipline_id, "BI-79"),
        )
        assert cur.fetchone() is None

    def test_students_page(self, client, db):
        self._setup(client, db)
        r = client.get("/v2/teacher/students")
        assert r.status_code == 200

    def test_students_page_grouped_accordion(self, client, db):
        teacher_id = self._setup(client, db)
        _link_teacher_discipline_group(db, teacher_id, "G-10")
        _link_teacher_discipline_group(db, teacher_id, "G-2")
        _insert_user(
            db,
            role="student",
            login="v2s1@t.ru",
            password="pass123",
            full_name="Борис Борисов",
            group="G-10",
            assigned_teacher_id=None,
        )
        _insert_user(
            db,
            role="student",
            login="v2s2@t.ru",
            password="pass123",
            full_name="Алексей Алексеев",
            group="G-2",
            assigned_teacher_id=None,
        )
        _insert_user(
            db,
            role="student",
            login="v2s3@t.ru",
            password="pass123",
            full_name="Виктор Викторов",
            group="G-2",
            assigned_teacher_id=None,
        )

        r = client.get("/v2/teacher/students")
        assert r.status_code == 200
        body = r.text
        assert 'data-student-group-toggle' in body
        assert 'data-group-name="G-2"' in body
        assert 'data-group-name="G-10"' in body
        assert body.find('data-group-name="G-2"') < body.find('data-group-name="G-10"')
        assert body.find("Алексей Алексеев") < body.find("Виктор Викторов")

    def test_students_page_backfills_attempt_group_assignments(self, client, db):
        teacher_id = self._setup(client, db)
        cur = db.cursor()
        cur.execute("SELECT id FROM disciplines LIMIT 1")
        discipline_id = int(cur.fetchone()[0])
        cur.execute(
            "INSERT OR IGNORE INTO teacher_disciplines (teacher_id, discipline_id) VALUES (?, ?)",
            (teacher_id, discipline_id),
        )
        student_id = _insert_user(
            db,
            role="student",
            login="history_group@test.ru",
            password="pass123",
            full_name="History Group Student",
            group="BI-88",
        )
        lecture_id = _insert_lecture(db, teacher_id, title="History Lecture", discipline_id=discipline_id)
        test_id = _insert_test(db, lecture_id, status="published", title="History Test")
        cur.execute(
            "INSERT INTO attempts (test_id, student_id, score, taken_at) VALUES (?, ?, ?, ?)",
            (test_id, student_id, 91.0, datetime.utcnow().isoformat()),
        )
        db.commit()

        r = client.get("/v2/teacher/students")
        assert r.status_code == 200
        assert "History Group Student" in r.text
        cur.execute(
            """
            SELECT 1 FROM teaching_assignments
            WHERE teacher_id = ? AND discipline_id = ? AND group_name = ?
            """,
            (teacher_id, discipline_id, "BI-88"),
        )
        assert cur.fetchone() is not None

    def test_student_performance_page(self, client, db):
        teacher_id = self._setup(client, db)
        cur = db.cursor()
        cur.execute("SELECT id FROM disciplines LIMIT 1")
        discipline_id = int(cur.fetchone()[0])
        _link_teacher_discipline_group(db, teacher_id, "BI-50", discipline_id)

        student_id = _insert_user(
            db,
            role="student",
            login="perf_stud@t.ru",
            password="pass123",
            full_name="Perf Student",
            group="BI-50",
            assigned_teacher_id=None,
        )
        lecture_id = _insert_lecture(db, teacher_id, title="Perf Lecture", discipline_id=discipline_id)
        test_id = _insert_test(db, lecture_id, status="published", title="Perf Test")
        cur.execute(
            "INSERT INTO attempts (test_id, student_id, score, taken_at) VALUES (?, ?, ?, ?)",
            (test_id, student_id, 88.5, datetime.utcnow().isoformat()),
        )
        db.commit()

        r = client.get(f"/v2/teacher/students/{student_id}/performance")
        assert r.status_code == 200
        assert "Perf Test" in r.text
        assert "Успеваемость студента" in r.text

    def test_analytics_page(self, client, db):
        teacher_id = self._setup(client, db)
        discipline_id = _link_teacher_discipline_group(db, teacher_id, "BI-51")
        student_id = _insert_user(
            db,
            role="student",
            login="analytics_stud@t.ru",
            password="pass123",
            full_name="Analytics Student",
            group="BI-51",
            assigned_teacher_id=None,
        )
        lecture_id = _insert_lecture(db, teacher_id, title="Analytics Lecture", discipline_id=discipline_id)
        test_id = _insert_test(db, lecture_id, status="published", title="Analytics Test")
        cur = db.cursor()
        cur.execute(
            "INSERT INTO attempts (test_id, student_id, score, taken_at) VALUES (?, ?, ?, ?)",
            (test_id, student_id, 72.0, datetime.utcnow().isoformat()),
        )
        db.commit()
        r = client.get("/v2/teacher/analytics")
        assert r.status_code == 200
        assert "Analytics Student" in r.text
        assert "72.0" in r.text

    def test_analytics_page_defaults_to_all_disciplines(self, client, db):
        teacher_id = self._setup(client, db)
        first_discipline_id = _link_teacher_discipline_group(db, teacher_id, "BI-70")
        cur = db.cursor()
        cur.execute("SELECT id FROM disciplines WHERE id != ? ORDER BY id LIMIT 1", (first_discipline_id,))
        second_discipline_id = int(cur.fetchone()[0])
        _link_teacher_discipline_group(db, teacher_id, "BI-71", second_discipline_id)

        student_id = _insert_user(
            db,
            role="student",
            login="analytics_second@t.ru",
            password="pass123",
            full_name="Analytics Second Student",
            group="BI-71",
            assigned_teacher_id=None,
        )
        lecture_id = _insert_lecture(db, teacher_id, title="Second Discipline Lecture", discipline_id=second_discipline_id)
        test_id = _insert_test(db, lecture_id, status="published", title="Second Discipline Test")
        cur.execute(
            "INSERT INTO attempts (test_id, student_id, score, taken_at) VALUES (?, ?, ?, ?)",
            (test_id, student_id, 95.0, datetime.utcnow().isoformat()),
        )
        db.commit()

        r = client.get("/v2/teacher/analytics")
        assert r.status_code == 200
        assert "Все дисциплины" in r.text
        assert "Analytics Second Student" in r.text
        assert "95.0" in r.text

    def test_analytics_backfills_students_from_existing_attempts(self, client, db):
        teacher_id = self._setup(client, db)
        discipline_id = _link_teacher_discipline_group(db, teacher_id, "BI-60")
        lecture_id = _insert_lecture(db, teacher_id, title="Analytics Scope", discipline_id=discipline_id)
        test_id = _insert_test(db, lecture_id, status="published", title="Scoped Test")

        visible_student_id = _insert_user(
            db,
            role="student",
            login="visible_scope@test.ru",
            password="pass123",
            full_name="Visible Student",
            group="BI-60",
        )
        hidden_student_id = _insert_user(
            db,
            role="student",
            login="hidden_scope@test.ru",
            password="pass123",
            full_name="Hidden Student",
            group="BI-61",
        )

        cur = db.cursor()
        cur.execute(
            "INSERT INTO attempts (test_id, student_id, score, taken_at) VALUES (?, ?, ?, ?)",
            (test_id, visible_student_id, 81.0, datetime.utcnow().isoformat()),
        )
        cur.execute(
            "INSERT INTO attempts (test_id, student_id, score, taken_at) VALUES (?, ?, ?, ?)",
            (test_id, hidden_student_id, 49.0, datetime.utcnow().isoformat()),
        )
        db.commit()

        r = client.get("/v2/teacher/analytics")
        assert r.status_code == 200
        assert "Visible Student" in r.text
        assert "Hidden Student" in r.text
        cur.execute(
            """
            SELECT 1 FROM teaching_assignments
            WHERE teacher_id = ? AND discipline_id = ? AND group_name = ?
            """,
            (teacher_id, discipline_id, "BI-61"),
        )
        assert cur.fetchone() is not None

    def test_create_discipline(self, client, db):
        self._setup(client, db)
        r = client.post("/v2/teacher/disciplines/create",
                        data={"discipline_name": "ML и нейросети"},
                        follow_redirects=False)
        assert r.status_code == 302
        cur = db.cursor()
        cur.execute("SELECT id FROM disciplines WHERE name = 'ML и нейросети'")
        assert cur.fetchone() is not None

    def test_attach_discipline(self, client, db):
        tid = self._setup(client, db)
        cur = db.cursor()
        cur.execute("SELECT id FROM disciplines LIMIT 1")
        did = cur.fetchone()[0]
        # Убедимся что не привязана
        cur.execute("DELETE FROM teacher_disciplines WHERE teacher_id = ? AND discipline_id = ?", (tid, did))
        db.commit()

        r = client.post("/v2/teacher/disciplines/attach",
                        data={"discipline_id": str(did)},
                        follow_redirects=False)
        assert r.status_code == 302
        cur.execute("SELECT 1 FROM teacher_disciplines WHERE teacher_id = ? AND discipline_id = ?", (tid, did))
        assert cur.fetchone() is not None

    def test_detach_discipline(self, client, db):
        tid = self._setup(client, db)
        cur = db.cursor()
        cur.execute("SELECT id FROM disciplines LIMIT 1")
        did = cur.fetchone()[0]
        cur.execute("INSERT OR IGNORE INTO teacher_disciplines (teacher_id, discipline_id) VALUES (?, ?)", (tid, did))
        db.commit()

        r = client.post(f"/v2/teacher/disciplines/{did}/detach", follow_redirects=False)
        assert r.status_code == 302
        cur.execute("SELECT 1 FROM teacher_disciplines WHERE teacher_id = ? AND discipline_id = ?", (tid, did))
        assert cur.fetchone() is None


# ═══════════════════════════════════════════════════════════════
# 8. AI МОДУЛЬ: утилиты
# ═══════════════════════════════════════════════════════════════

class TestAIUtils:
    def test_prepare_source_text_basic(self):
        from app.ai import _prepare_source_text
        text = "Это тестовый параграф достаточной длины для проверки фильтрации текста лекции." * 5
        result = _prepare_source_text(text)
        assert len(result) > 0
        assert "http" not in result

    def test_prepare_source_text_removes_urls(self):
        from app.ai import _prepare_source_text
        text = ("Нормальный текст длиной больше тридцати символов для теста. " * 3 +
                "https://example.com/page " +
                "Ещё один длинный параграф с нормальным содержимым для тестирования фильтрации.")
        result = _prepare_source_text(text)
        assert "example.com" not in result

    def test_prepare_source_text_preserves_order(self):
        from app.ai import _prepare_source_text
        t = ("Первый абзац с достаточным количеством текста для прохождения фильтра нормализации.\n\n"
             "Второй абзац тоже содержит достаточно текста чтобы пройти все проверки фильтрации.\n\n"
             "Третий заключительный абзац с хорошим объёмом контента для проверки порядка элементов.")
        result = _prepare_source_text(t)
        if "Первый" in result and "Третий" in result:
            assert result.index("Первый") < result.index("Третий")

    def test_extract_terms(self):
        from app.ai import _extract_terms
        text = "Машинное обучение применяется для классификации данных и прогнозирования результатов"
        terms = _extract_terms(text)
        assert isinstance(terms, list)
        assert len(terms) > 0

    def test_shorten_text(self):
        from app.ai import _shorten_text
        short = _shorten_text("Привет", 100)
        assert short == "Привет"
        long_text = "А" * 200
        result = _shorten_text(long_text, 50)
        assert len(result) <= 51  # +1 для «…»

    def test_text_similarity(self):
        from app.ai import _text_similarity
        s = _text_similarity("машинное обучение нейросети", "машинное обучение нейросети")
        assert s > 0.9
        s2 = _text_similarity("квантовая физика", "машинное обучение")
        assert s2 < 0.3

    def test_extract_json_array(self):
        from app.ai import _extract_json
        data = _extract_json('[{"a":1}]')
        assert isinstance(data, list)
        assert data[0]["a"] == 1

    def test_extract_json_from_markdown(self):
        from app.ai import _extract_json
        data = _extract_json('```json\n[{"x":2}]\n```')
        assert isinstance(data, list)

    def test_normalize_questions(self):
        from app.ai import _normalize_questions
        items = [
            {"text": "Что такое тестовый вопрос для проверки нормализации результатов?",
             "options": ["Верно", "Неверно А", "Неверно Б", "Неверно В"],
             "correct_index": 0},
            {"text": "Второй вопрос о применении методов валидации данных?",
             "options": ["A", "B", "C", "D"],
             "correct_index": 1},
        ]
        result = _normalize_questions(items, 5)
        assert len(result) >= 1
        for q in result:
            assert "text" in q
            assert "options" in q
            assert "correct_index" in q

    def test_normalize_questions_dedup(self):
        from app.ai import _normalize_questions
        items = [
            {"text": "Одинаковый вопрос о проверке дедупликации тестовых данных?",
             "options": ["A", "B", "C", "D"], "correct_index": 0},
            {"text": "Одинаковый вопрос о проверке дедупликации тестовых данных?",
             "options": ["A", "B", "C", "D"], "correct_index": 0},
        ]
        result = _normalize_questions(items, 5)
        assert len(result) == 1

    # дополнительная регрессия на OCR/copyright-артефакты
    def test_prepare_source_text_removes_copyright_lines(self):
        from app.ai import _prepare_source_text
        text = (
            "Полезный учебный материал о сетевой безопасности и сегментации трафика.\n"
            "© Cisco and/or its affiliates, 2016\n"
            "Еще один содержательный абзац по теме маршрутизации и ACL.\n"
        )
        result = _prepare_source_text(text)
        assert "Cisco" not in result
        assert "2016" not in result
        assert "маршрутизации" in result

    def test_normalize_questions_filters_blank_and_artifact_items(self):
        from app.ai import _normalize_questions
        items = [
            {
                "text": "Вставьте пропущенный термин: 2 © Cisco и/или ее ___ компании, 2016",
                "options": ["дочерние", "защищены", "интернет", "компании"],
                "correct_index": 0,
            },
            {
                "text": "Какой протокол используется для безопасного удаленного доступа?",
                "options": ["SSH", "HTTP", "FTP", "Telnet"],
                "correct_index": 0,
            },
        ]
        result = _normalize_questions(items, 5)
        assert len(result) == 1
        assert "SSH" in result[0]["options"]
# 9. AI: Fallback генерация
# ═══════════════════════════════════════════════════════════════

class TestAIFallback:
    def test_fallback_generates_questions(self):
        from app.ai import _generate_fallback
        text = (
            "Информационная безопасность — это совокупность мер по защите информации. "
            "Основные принципы: конфиденциальность, целостность, доступность. "
            "Модель угроз описывает потенциальные источники атак на систему. "
            "Криптографические методы используются для шифрования данных. "
            "Аутентификация подтверждает личность пользователя в системе. "
        ) * 5
        result = _generate_fallback(text, count=5, difficulty="medium")
        assert isinstance(result, list)
        assert len(result) >= 3
        for q in result:
            assert "text" in q
            assert "options" in q
            assert len(q["options"]) == 4
            assert 0 <= q["correct_index"] <= 3

    def test_fallback_different_difficulties(self):
        from app.ai import _generate_fallback
        text = "Алгоритмы машинного обучения. Нейронные сети. Градиентный спуск. " * 20
        for diff in ("easy", "medium", "hard"):
            result = _generate_fallback(text, count=3, difficulty=diff)
            assert len(result) >= 1, f"Fallback провалился для сложности {diff}"


# ═══════════════════════════════════════════════════════════════
# 10. AI: Промпты
# ═══════════════════════════════════════════════════════════════

class TestAIPrompts:
    def test_build_prompt_returns_tuple(self):
        from app.ai import _build_prompt
        result = _build_prompt("text", 5, "medium")
        assert isinstance(result, tuple)
        assert len(result) == 2
        system_msg, user_msg = result
        assert "методист" in system_msg.lower()
        assert "5" in user_msg

    def test_build_prompt_with_discipline(self):
        from app.ai import _build_prompt
        system_msg, user_msg = _build_prompt("text", 3, "easy", discipline_name="Информационная безопасность")
        assert "безопасность" in user_msg.lower()

    def test_build_prompt_with_theses(self):
        from app.ai import _build_prompt
        theses = ["Тезис 1 про безопасность", "Тезис 2 про шифрование"]
        system_msg, user_msg = _build_prompt("text", 3, "medium", theses=theses)
        assert "тезис" in user_msg.lower() or "Тезис" in user_msg

    def test_thesis_prompt_includes_discipline(self):
        from app.ai import _build_thesis_prompt
        prompt = _build_thesis_prompt("Текст лекции", discipline_name="Web-программирование")
        assert "web" in prompt.lower() or "Web" in prompt

    def test_difficulty_guidance(self):
        from app.ai import _difficulty_guidance
        easy = _difficulty_guidance("easy")
        hard = _difficulty_guidance("hard")
        assert easy != hard
        assert "базов" in easy.lower()
        assert "аналит" in hard.lower()

    def test_infer_discipline_guidance(self):
        from app.ai import _infer_discipline_guidance
        name, guidance = _infer_discipline_guidance(
            "криптография шифрование хеширование безопасность",
            discipline_name="Информационная безопасность",
        )
        assert isinstance(name, str)
        assert isinstance(guidance, str)
        assert len(guidance) > 10


# ═══════════════════════════════════════════════════════════════
# 11. AI: generate_questions (мок провайдеров)
# ═══════════════════════════════════════════════════════════════

class TestAIGenerate:
    def test_generate_questions_fallback(self):
        """Без ключей API должен использовать fallback."""
        from app.ai import generate_questions
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "AI_ALLOW_FALLBACK": "true"}, clear=False):
            result = generate_questions(
                "Информационная безопасность включает защиту конфиденциальности и целостности. " * 30,
                count=5,
                difficulty="medium",
            )
        assert isinstance(result, list)
        assert len(result) >= 3

    def test_generate_questions_no_fallback(self):
        """С отключённым fallback — пустой результат."""
        from app.ai import generate_questions
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "AI_ALLOW_FALLBACK": "false"}, clear=False):
            result = generate_questions("Текст " * 100, count=5)
        assert result == []

    def test_generate_questions_exact_requested_count(self):
        from app.ai import generate_questions

        source_text = (
            "Информационная безопасность включает конфиденциальность, целостность и доступность данных. "
            "Система защиты строится из организационных и технических мер. "
            "Контроль доступа должен учитывать роли пользователей и принципы минимальных привилегий. "
            "Регулярный аудит событий помогает выявлять аномалии и снижать риск инцидентов. "
            "Резервное копирование уменьшает потери при сбоях и ошибках эксплуатации. "
        ) * 40

        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "", "GEMINI_API_KEY": "", "AI_ALLOW_FALLBACK": "true"},
            clear=False,
        ):
            result = generate_questions(source_text, count=20, difficulty="medium")

        assert isinstance(result, list)
        assert len(result) == 20
        for item in result:
            assert isinstance(item.get("text"), str) and item["text"].strip()
            assert isinstance(item.get("options"), list) and len(item["options"]) >= 3
            assert 0 <= int(item.get("correct_index", -1)) < len(item["options"])

    def test_diagnose_ai_setup(self):
        from app.ai import diagnose_ai_setup
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            msg = diagnose_ai_setup()
        assert isinstance(msg, str)
        assert len(msg) > 5


# ═══════════════════════════════════════════════════════════════
# 12. БЕЗОПАСНОСТЬ
# ═══════════════════════════════════════════════════════════════

class TestSecurity:
    def test_password_hashing(self):
        from app.security import hash_password, new_salt
        salt = new_salt()
        h1 = hash_password("test", salt)
        h2 = hash_password("test", salt)
        assert h1 == h2
        h3 = hash_password("other", salt)
        assert h1 != h3

    def test_salt_uniqueness(self):
        from app.security import new_salt
        salts = {new_salt() for _ in range(100)}
        assert len(salts) == 100

    def test_session_secret_from_env(self):
        """Проверяем что секрет сессии берётся из env."""
        import main as m
        # Просто проверяем что переменная _session_secret существует
        assert hasattr(m, '_session_secret')

    def test_unauthorized_access(self, client):
        """Неавторизованный пользователь не должен попасть на защищённые страницы."""
        protected = [
            "/dashboard",
            "/teacher/lectures",
            "/teacher/lectures/new",
            "/student/tests",
            "/student/analytics",
            "/growth",
            "/admin/students",
            "/admin/teachers",
            "/admin/groups",
            "/admin/disciplines",
            "/v2/teacher/disciplines",
            "/v2/teacher/tests",
            "/v2/teacher/students",
            "/v2/teacher/analytics",
        ]
        for url in protected:
            r = client.get(url, follow_redirects=False)
            assert r.status_code in (302, 401, 403), f"{url} вернул {r.status_code} без авторизации"

    def test_student_cannot_access_teacher(self, client, db):
        _create_group(db, "БИ-41")
        _register(client, role="student", email="st_sec@t.ru", group="БИ-41")
        _login(client, email="st_sec@t.ru")
        r = client.get("/teacher/lectures", follow_redirects=False)
        assert r.status_code in (302, 401, 403)

    def test_teacher_cannot_access_admin(self, client, db):
        _insert_user(db, role="teacher", login="t_sec@t.ru", password="pass123", full_name="Sec Teacher")
        _login(client, email="t_sec@t.ru", password="pass123")
        r = client.get("/admin/students", follow_redirects=False)
        assert r.status_code in (302, 401, 403)


# ═══════════════════════════════════════════════════════════════
# 13. QR / ENTRY POINTS
# ═══════════════════════════════════════════════════════════════

class TestEntryPoints:
    def test_student_entry_redirect_to_login(self, client, db):
        """Неавторизованный юзер идёт на entry → login."""
        from app.security import hash_password, new_salt
        salt = new_salt()
        cur = db.cursor()
        cur.execute(
            "INSERT INTO users (role, full_name, email, password_hash, salt, student_group) VALUES (?, ?, ?, ?, ?, ?)",
            ("teacher", "T", "entry_t@t.ru", hash_password("p", salt), salt, ""),
        )
        db.commit()
        tid = cur.lastrowid
        lid = _insert_lecture(db, tid)
        test_id = _insert_test(db, lid)
        r = client.get(f"/student/tests/{test_id}/entry", follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers.get("location", "")

    def test_legacy_lecture_redirect(self, client):
        r = client.get("/lecture/new", follow_redirects=False)
        assert r.status_code == 302
        assert "/teacher/lectures/new" in r.headers.get("location", "")


# ═══════════════════════════════════════════════════════════════
# 14. V2 ADMIN
# ═══════════════════════════════════════════════════════════════

class TestV2Admin:
    def test_v2_admin_login_page(self, client):
        r = client.get("/v2/admin")
        assert r.status_code == 200

    def test_v2_admin_login_works(self, client, db):
        _create_admin(db)
        r = client.post("/v2/admin/login",
                        data={"email": "admin@test.ru", "password": "admin123"},
                        follow_redirects=False)
        assert r.status_code == 302
        assert "/admin" in r.headers.get("location", "")

    def test_v2_admin_login_wrong(self, client, db):
        _create_admin(db)
        r = client.post("/v2/admin/login",
                        data={"email": "admin@test.ru", "password": "wrong"},
                        follow_redirects=False)
        assert r.status_code == 200
        assert "неверный" in r.text.lower()

    def test_v2_admin_disciplines_alias(self, client, db):
        _create_admin(db)
        _login_admin(client)
        r = client.get("/v2/admin/disciplines", follow_redirects=False)
        assert r.status_code == 302


# ═══════════════════════════════════════════════════════════════
# 15. РАСШИРЕННАЯ БЕЗОПАСНОСТЬ (PBKDF2, валидация, CSRF, rate-limit, санитизация)
# ═══════════════════════════════════════════════════════════════

class TestSecurityEnhanced:
    """Тесты для нового модуля безопасности."""

    # ── Хеширование паролей ──

    def test_pbkdf2_hash_format(self):
        from app.security import hash_password, new_salt
        salt = new_salt()
        h = hash_password("myPassword1", salt)
        assert h.startswith("pbkdf2$"), "PBKDF2-хеш должен начинаться с 'pbkdf2$'"
        hex_part = h[len("pbkdf2$"):]
        assert len(hex_part) == 64, "Hex-часть должна быть 64 символа (32 байта)"

    def test_pbkdf2_deterministic(self):
        from app.security import hash_password, new_salt
        salt = new_salt()
        h1 = hash_password("test123", salt)
        h2 = hash_password("test123", salt)
        assert h1 == h2

    def test_pbkdf2_different_salts(self):
        from app.security import hash_password, new_salt
        s1 = new_salt()
        s2 = new_salt()
        assert hash_password("same", s1) != hash_password("same", s2)

    # ── verify_password ──

    def test_verify_password_pbkdf2(self):
        from app.security import hash_password, new_salt, verify_password
        salt = new_salt()
        h = hash_password("secret1", salt)
        assert verify_password("secret1", salt, h) is True
        assert verify_password("wrong", salt, h) is False

    def test_verify_password_legacy(self):
        """Проверка обратной совместимости с SHA-256."""
        from app.security import _hash_password_legacy, new_salt, verify_password
        salt = new_salt()
        legacy_hash = _hash_password_legacy("oldPass1", salt)
        assert not legacy_hash.startswith("pbkdf2$")
        assert verify_password("oldPass1", salt, legacy_hash) is True
        assert verify_password("wrong", salt, legacy_hash) is False

    def test_needs_rehash(self):
        from app.security import hash_password, _hash_password_legacy, needs_rehash, new_salt
        salt = new_salt()
        assert needs_rehash(_hash_password_legacy("x", salt)) is True
        assert needs_rehash(hash_password("x", salt)) is False

    # ── validate_email ──

    def test_validate_email_valid(self):
        from app.security import validate_email
        assert validate_email("User@Example.COM") == "user@example.com"
        assert validate_email("  test@mail.ru  ") == "test@mail.ru"
        assert validate_email("a.b+c@sub.domain.org") == "a.b+c@sub.domain.org"

    def test_validate_email_invalid(self):
        from app.security import validate_email
        assert validate_email("") is None
        assert validate_email("nope") is None
        assert validate_email("@no-local.ru") is None
        assert validate_email("no-domain@") is None
        assert validate_email("a" * 300 + "@x.ru") is None

    # ── validate_password ──

    def test_validate_password_ok(self):
        from app.security import validate_password
        ok, msg = validate_password("pass123")
        assert ok is True
        assert msg == ""

    def test_validate_password_too_short(self):
        from app.security import validate_password
        ok, msg = validate_password("ab1")
        assert ok is False
        assert "минимум" in msg.lower()

    def test_validate_password_all_digits(self):
        from app.security import validate_password
        ok, msg = validate_password("123456")
        assert ok is False
        assert "цифр" in msg.lower()

    def test_validate_password_all_alpha(self):
        from app.security import validate_password
        ok, msg = validate_password("abcdef")
        assert ok is False
        assert "цифру" in msg.lower() or "спецсимвол" in msg.lower()

    def test_validate_password_empty(self):
        from app.security import validate_password
        ok, _ = validate_password("")
        assert ok is False

    def test_validate_password_too_long(self):
        from app.security import validate_password
        ok, msg = validate_password("a1" * 100)
        assert ok is False
        assert "длиннее" in msg.lower()

    # ── CSRF ──

    def test_csrf_token_generation(self):
        from app.security import generate_csrf_token, verify_csrf_token
        session = {}
        token = generate_csrf_token(session)
        assert isinstance(token, str)
        assert len(token) == 64  # 32 bytes hex
        # Повторный вызов — тот же токен
        assert generate_csrf_token(session) == token

    def test_csrf_verify_valid(self):
        from app.security import generate_csrf_token, verify_csrf_token
        session = {}
        token = generate_csrf_token(session)
        assert verify_csrf_token(session, token) is True

    def test_csrf_verify_invalid(self):
        from app.security import generate_csrf_token, verify_csrf_token
        session = {}
        generate_csrf_token(session)
        assert verify_csrf_token(session, "badtoken") is False
        assert verify_csrf_token(session, None) is False
        assert verify_csrf_token({}, "anything") is False

    # ── RateLimiter ──

    def test_rate_limiter_blocks_after_max(self):
        from app.security import RateLimiter
        rl = RateLimiter(max_attempts=3, window_seconds=60)
        key = "1.2.3.4"
        assert rl.is_blocked(key) is False
        rl.record(key)
        rl.record(key)
        rl.record(key)
        assert rl.is_blocked(key) is True

    def test_rate_limiter_reset(self):
        from app.security import RateLimiter
        rl = RateLimiter(max_attempts=2, window_seconds=60)
        key = "5.6.7.8"
        rl.record(key)
        rl.record(key)
        assert rl.is_blocked(key) is True
        rl.reset(key)
        assert rl.is_blocked(key) is False

    def test_rate_limiter_remaining_seconds(self):
        from app.security import RateLimiter
        rl = RateLimiter(max_attempts=2, window_seconds=300)
        key = "9.0.0.1"
        rl.record(key)
        remaining = rl.remaining_seconds(key)
        assert 0 < remaining <= 300

    def test_rate_limiter_no_attempts(self):
        from app.security import RateLimiter
        rl = RateLimiter(max_attempts=5, window_seconds=60)
        assert rl.remaining_seconds("clean") == 0
        assert rl.is_blocked("clean") is False

    # ── Санитизация ──

    def test_sanitize_string_basic(self):
        from app.security import sanitize_string
        assert sanitize_string("  hello  ") == "hello"
        assert sanitize_string("<script>alert(1)</script>") == "&lt;script&gt;alert(1)&lt;/script&gt;"

    def test_sanitize_string_max_length(self):
        from app.security import sanitize_string
        long_str = "A" * 500
        result = sanitize_string(long_str, max_length=100)
        assert len(result) == 100

    def test_sanitize_string_none(self):
        from app.security import sanitize_string
        assert sanitize_string(None) == ""

    def test_sanitize_full_name(self):
        from app.security import sanitize_full_name
        assert sanitize_full_name("  Иванов   Иван   Иванович  ") == "Иванов Иван Иванович"
        assert sanitize_full_name("<b>Взлом</b>") == "&lt;b&gt;Взлом&lt;/b&gt;"

    def test_sanitize_full_name_empty(self):
        from app.security import sanitize_full_name
        assert sanitize_full_name("") == ""
        assert sanitize_full_name(None) == ""

    def test_sanitize_full_name_long(self):
        from app.security import sanitize_full_name
        long_name = "А" * 200
        result = sanitize_full_name(long_name)
        assert len(result) <= 150


# ═══════════════════════════════════════════════════════════════
# 16. ИНТЕГРАЦИЯ: валидация при регистрации
# ═══════════════════════════════════════════════════════════════

class TestRegistrationValidation:
    """Тесты валидации при регистрации через HTML-форму."""

    def test_register_bad_login(self, client):
        r = _register(client, login="a b", role="student")
        assert r.status_code == 200
        assert "логин" in r.text.lower() or "некорректный" in r.text.lower()

    def test_register_weak_password_short(self, client, db):
        _create_group(db, "БИ-41")
        r = _register(client, password="ab1", role="student", email="wp@t.ru", group="БИ-41")
        assert r.status_code == 200
        assert "минимум" in r.text.lower() or "символ" in r.text.lower()

    def test_register_weak_password_all_digits(self, client, db):
        _create_group(db, "БИ-41")
        r = _register(client, password="123456", role="student", email="wp2@t.ru", group="БИ-41")
        assert r.status_code == 200
        assert "цифр" in r.text.lower()

    def test_register_weak_password_all_alpha(self, client, db):
        _create_group(db, "БИ-41")
        r = _register(client, password="abcdefg", role="student", email="wp3@t.ru", group="БИ-41")
        assert r.status_code == 200
        assert "цифру" in r.text.lower() or "спецсимвол" in r.text.lower()

    def test_register_xss_name(self, client, db):
        _create_group(db, "БИ-41")
        r = _register(client, full_name="<script>alert(1)</script>", email="xss@t.ru",
                       password="pass123", role="student", group="БИ-41")
        # Должна пройти регистрация, но имя экранировано
        assert r.status_code == 302
        cur = db.cursor()
        cur.execute("SELECT full_name FROM users WHERE email='xss@t.ru'")
        row = cur.fetchone()
        assert row is not None
        assert "<script>" not in row[0]
        assert "&lt;script&gt;" in row[0]


# ═══════════════════════════════════════════════════════════════
# 17. ИНТЕГРАЦИЯ: rate-limit при логине
# ═══════════════════════════════════════════════════════════════

class TestLoginRateLimit:
    """Тесты rate-limiter при логине."""

    def test_login_rate_limit_blocks(self, client, db):
        """После 5 неудачных попыток — блокировка."""
        _insert_user(db, role="teacher", login="rl@t.ru", password="pass123", full_name="RL Teacher")
        for _ in range(5):
            _login(client, email="rl@t.ru", password="wrongpass1")
        r = _login(client, email="rl@t.ru", password="wrongpass1")
        assert r.status_code == 200
        assert "попыток" in r.text.lower() or "подождите" in r.text.lower()

    def test_login_rate_limit_resets_on_success(self, client, db):
        """Успешный логин сбрасывает счётчик."""
        _insert_user(db, role="teacher", login="rl2@t.ru", password="pass123", full_name="RL Teacher 2")
        # 3 неудачных
        for _ in range(3):
            _login(client, email="rl2@t.ru", password="wrongpass1")
        # Успешный
        r = _login(client, email="rl2@t.ru")
        assert r.status_code == 302
        # Ещё 3 неудачных — не заблокированы (счётчик сброшен)
        for _ in range(3):
            r = _login(client, email="rl2@t.ru", password="wrongpass1")
            assert r.status_code == 200
            assert "неверный" in r.text.lower()

    def test_v2_admin_login_rate_limit(self, client, db):
        """Rate-limit работает и для v2 admin login."""
        _create_admin(db)
        for _ in range(5):
            client.post("/v2/admin/login",
                        data={"email": "admin@test.ru", "password": "wrong"},
                        follow_redirects=False)
        r = client.post("/v2/admin/login",
                        data={"email": "admin@test.ru", "password": "wrong"},
                        follow_redirects=False)
        assert r.status_code == 200
        assert "попыток" in r.text.lower() or "подождите" in r.text.lower()

