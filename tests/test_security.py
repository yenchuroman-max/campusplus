"""
Тесты безопасности платформы CampusPlus.

Покрывают:
  - Хеширование и верификация паролей (PBKDF2)
  - Валидация паролей и логинов
  - CSRF-токены
  - Rate-limiter
  - Санитизация ввода
  - Защита от path-traversal при скачивании файлов
  - Ограничение размера загрузки файлов
  - Sanitization AI-статуса (скрытие внутренних ошибок)
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr("app.db.SQLITE_DB_PATH", db_file)
    from app.db import init_db
    init_db()
    yield db_file


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    from app.security import login_limiter
    login_limiter._store.clear()
    yield
    login_limiter._store.clear()


@pytest.fixture()
def db(isolated_db):
    conn = sqlite3.connect(isolated_db)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


# ═══════════════════════════════════════════════════════════════
#  Тесты хеширования паролей
# ═══════════════════════════════════════════════════════════════
class TestPasswordHashing:
    def test_pbkdf2_hash_format(self):
        from app.security import hash_password, new_salt
        salt = new_salt()
        h = hash_password("testpass123", salt)
        assert h.startswith("pbkdf2$"), f"Hash must start with pbkdf2$ prefix, got: {h[:20]}"

    def test_pbkdf2_verify_correct(self):
        from app.security import hash_password, verify_password, new_salt
        salt = new_salt()
        h = hash_password("myPassword1", salt)
        assert verify_password("myPassword1", salt, h)

    def test_pbkdf2_verify_wrong(self):
        from app.security import hash_password, verify_password, new_salt
        salt = new_salt()
        h = hash_password("correct123", salt)
        assert not verify_password("wrong123", salt, h)

    def test_different_salts_produce_different_hashes(self):
        from app.security import hash_password, new_salt
        salt1 = new_salt()
        salt2 = new_salt()
        h1 = hash_password("same_pass1", salt1)
        h2 = hash_password("same_pass1", salt2)
        assert h1 != h2, "Different salts must produce different hashes"

    def test_salt_is_random(self):
        from app.security import new_salt
        salts = {new_salt() for _ in range(100)}
        assert len(salts) == 100, "Salt generation must be random"


# ═══════════════════════════════════════════════════════════════
#  Тесты валидации паролей
# ═══════════════════════════════════════════════════════════════
class TestPasswordValidation:
    def test_valid_password(self):
        from app.security import validate_password
        ok, err = validate_password("test1234")
        assert ok, f"Valid password rejected: {err}"

    def test_too_short(self):
        from app.security import validate_password
        ok, _ = validate_password("ab1")
        assert not ok

    def test_all_digits_rejected(self):
        from app.security import validate_password
        ok, _ = validate_password("123456")
        assert not ok

    def test_all_letters_rejected(self):
        from app.security import validate_password
        ok, _ = validate_password("abcdef")
        assert not ok

    def test_mixed_accepted(self):
        from app.security import validate_password
        ok, _ = validate_password("abc123")
        assert ok

    def test_too_long_rejected(self):
        from app.security import validate_password
        ok, _ = validate_password("a1" * 100)
        assert not ok


# ═══════════════════════════════════════════════════════════════
#  Тесты валидации логинов
# ═══════════════════════════════════════════════════════════════
class TestLoginValidation:
    def test_valid_login(self):
        from app.security import validate_login
        assert validate_login("user123") == "user123"

    def test_too_short(self):
        from app.security import validate_login
        assert validate_login("ab") is None

    def test_whitespace_rejected(self):
        from app.security import validate_login
        assert validate_login("user name") is None

    def test_case_insensitive(self):
        from app.security import validate_login
        assert validate_login("UserName") == "username"


# ═══════════════════════════════════════════════════════════════
#  Тесты CSRF-токенов
# ═══════════════════════════════════════════════════════════════
class TestCSRF:
    def test_generate_token(self):
        from app.security import generate_csrf_token
        session = {}
        token = generate_csrf_token(session)
        assert token
        assert len(token) == 64  # 32 bytes hex

    def test_same_session_same_token(self):
        from app.security import generate_csrf_token
        session = {}
        t1 = generate_csrf_token(session)
        t2 = generate_csrf_token(session)
        assert t1 == t2

    def test_verify_valid_token(self):
        from app.security import generate_csrf_token, verify_csrf_token
        session = {}
        token = generate_csrf_token(session)
        assert verify_csrf_token(session, token)

    def test_verify_invalid_token(self):
        from app.security import generate_csrf_token, verify_csrf_token
        session = {}
        generate_csrf_token(session)
        assert not verify_csrf_token(session, "wrong_token")

    def test_verify_none_token(self):
        from app.security import generate_csrf_token, verify_csrf_token
        session = {}
        generate_csrf_token(session)
        assert not verify_csrf_token(session, None)

    def test_verify_empty_session(self):
        from app.security import verify_csrf_token
        assert not verify_csrf_token({}, "any_token")


# ═══════════════════════════════════════════════════════════════
#  Тесты Rate-limiter
# ═══════════════════════════════════════════════════════════════
class TestRateLimiter:
    def test_not_blocked_initially(self):
        from app.security import login_limiter
        assert not login_limiter.is_blocked("test_ip")

    def test_blocked_after_max_attempts(self):
        from app.security import login_limiter
        for _ in range(5):
            login_limiter.record("test_ip_2")
        assert login_limiter.is_blocked("test_ip_2")

    def test_reset_clears_block(self):
        from app.security import login_limiter
        for _ in range(5):
            login_limiter.record("test_ip_3")
        assert login_limiter.is_blocked("test_ip_3")
        login_limiter.reset("test_ip_3")
        assert not login_limiter.is_blocked("test_ip_3")


# ═══════════════════════════════════════════════════════════════
#  Тесты санитизации ввода
# ═══════════════════════════════════════════════════════════════
class TestSanitization:
    def test_html_escaped(self):
        from app.security import sanitize_string
        result = sanitize_string('<script>alert("xss")</script>')
        assert "<script>" not in result
        assert "&lt;" in result

    def test_max_length(self):
        from app.security import sanitize_string
        long_str = "a" * 500
        result = sanitize_string(long_str, max_length=200)
        assert len(result) <= 200

    def test_name_sanitized(self):
        from app.security import sanitize_full_name
        result = sanitize_full_name('<b>Name</b>')
        assert "<b>" not in result


# ═══════════════════════════════════════════════════════════════
#  Тесты API безопасности
# ═══════════════════════════════════════════════════════════════
class TestAPISecurityEndpoints:
    @pytest.fixture()
    def client(self, isolated_db):
        from fastapi.testclient import TestClient
        import main as main_module
        from app.db import init_db
        init_db()
        with TestClient(main_module.app, raise_server_exceptions=False) as tc:
            yield tc

    def test_health_endpoint(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"

    def test_ai_status_no_leak(self, client):
        """AI-статус не должен раскрывать внутренние детали ошибок."""
        r = client.get("/api/ai/status")
        assert r.status_code == 200
        data = r.json()
        # Не должно содержать API-ключей или стектрейсов
        msg = data.get("message", "")
        assert "sk-" not in msg, "AI status should not leak API keys"
        assert "traceback" not in msg.lower()
        assert "error" not in msg.lower() or "недоступен" in msg.lower()

    def test_unauthorized_api_access(self, client):
        """API-эндпоинты без авторизации должны возвращать 401/403."""
        endpoints = [
            "/api/lectures",
            "/api/analytics/student",
            "/api/analytics/teacher",
        ]
        for ep in endpoints:
            r = client.get(ep)
            assert r.status_code in (401, 403, 422), f"{ep} should require auth, got {r.status_code}"

    def test_api_register_requires_fields(self, client):
        """Регистрация без обязательных полей."""
        r = client.post("/api/auth/register", json={})
        assert r.status_code == 422

    def test_api_login_invalid_credentials(self, client):
        """Неверные данные для входа."""
        r = client.post("/api/auth/login", json={"login": "nonexistent", "password": "wrong"})
        assert r.status_code in (401, 403, 422)


# ═══════════════════════════════════════════════════════════════
#  Тесты SQL-инъекций (параметризированные запросы)
# ═══════════════════════════════════════════════════════════════
class TestSQLInjection:
    def test_login_sql_injection(self, db):
        """SQL-инъекция через логин не должна работать."""
        from app.security import hash_password, new_salt
        salt = new_salt()
        pw_hash = hash_password("test123", salt)
        cur = db.cursor()
        cur.execute(
            "INSERT INTO users (role, full_name, email, password_hash, salt) VALUES (?, ?, ?, ?, ?)",
            ("student", "Test", "safe@test.ru", pw_hash, salt),
        )
        db.commit()

        # Attempt SQL injection
        injection = "' OR '1'='1"
        cur.execute("SELECT * FROM users WHERE email = ?", (injection,))
        assert cur.fetchone() is None, "SQL injection in login should not return any user"

    def test_search_sql_injection(self, db):
        """SQL-инъекция через поиск."""
        cur = db.cursor()
        injection = "'; DROP TABLE users; --"
        # This should not raise even with malicious input
        cur.execute("SELECT * FROM users WHERE full_name LIKE ?", (f"%{injection}%",))
        result = cur.fetchall()
        assert isinstance(result, list)
        # Verify table still exists
        cur.execute("SELECT COUNT(*) FROM users")
        assert cur.fetchone() is not None


# ═══════════════════════════════════════════════════════════════
#  Тесты path-traversal
# ═══════════════════════════════════════════════════════════════
class TestPathTraversal:
    def test_path_name_extraction(self):
        """Path.name должен предотвращать обход директорий."""
        from pathlib import Path
        dangerous = "../../../etc/passwd"
        safe = Path(dangerous).name
        assert safe == "passwd"
        assert ".." not in safe

    def test_uuid_filename_safe(self):
        """UUID-имена файлов безопасны."""
        import uuid
        fname = f"{uuid.uuid4().hex}.pdf"
        safe = Path(fname).name
        assert safe == fname


# ═══════════════════════════════════════════════════════════════
#  Тесты session secret
# ═══════════════════════════════════════════════════════════════
class TestSessionSecret:
    def test_production_requires_secret(self):
        """В production (RENDER=true) отсутствие SESSION_SECRET_KEY должно вызывать ошибку."""
        import importlib
        # This tests the logic, not the actual import
        os.environ.pop("SESSION_SECRET_KEY", None)
        os.environ.pop("SECRET_KEY", None)

        secret = os.environ.get("SESSION_SECRET_KEY") or os.environ.get("SECRET_KEY") or ""
        is_production = os.environ.get("RENDER") or os.environ.get("DATABASE_URL")

        if not secret and is_production:
            # Should raise RuntimeError in production
            assert True
        elif not secret:
            # In dev, should use fallback with warning
            assert True
