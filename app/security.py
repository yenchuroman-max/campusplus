"""
Модуль безопасности платформы мониторинга успеваемости.

Включает:
  - Хеширование паролей (PBKDF2-SHA256 + обратная совместимость с sha256)
  - Валидация паролей и логинов (email-валидатор оставлен для совместимости)
  - CSRF-токены для форм
  - Rate-limiter для логина
  - Санитизация пользовательских строк
"""
from __future__ import annotations

import hashlib
import hmac
import html
import re
import secrets
import time
from collections import defaultdict
from threading import Lock

# ═══════════════════════════════════════════════════════════════
#  Хеширование паролей
# ═══════════════════════════════════════════════════════════════

# PBKDF2 параметры — 260 000 итераций, рекомендовано OWASP 2024+
_PBKDF2_ITERATIONS = 260_000
_PBKDF2_HASH_NAME = "sha256"
_PBKDF2_DK_LEN = 32

# Префикс для PBKDF2-хешей — чтобы отличить от старых sha256
_PBKDF2_PREFIX = "pbkdf2$"


def hash_password(password: str, salt: str) -> str:
    """
    Хеширует пароль с помощью PBKDF2-HMAC-SHA256.

    Формат результата: ``pbkdf2$<hex-digest>``

    Обратная совместимость: :func:`verify_password` распознаёт оба формата.
    """
    dk = hashlib.pbkdf2_hmac(
        _PBKDF2_HASH_NAME,
        password.encode("utf-8"),
        salt.encode("utf-8"),
        _PBKDF2_ITERATIONS,
        dklen=_PBKDF2_DK_LEN,
    )
    return _PBKDF2_PREFIX + dk.hex()


def _hash_password_legacy(password: str, salt: str) -> str:
    """Старый алгоритм (SHA-256 без KDF) — только для проверки существующих хешей."""
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def verify_password(password: str, salt: str, stored_hash: str) -> bool:
    """
    Проверяет пароль по хешу.

    Поддерживает оба формата:
    - ``pbkdf2$...`` — новый PBKDF2
    - Без префикса  — legacy SHA-256 (для старых пользователей)

    Сравнение через ``hmac.compare_digest`` (timing-safe).
    """
    if stored_hash.startswith(_PBKDF2_PREFIX):
        computed = hash_password(password, salt)
    else:
        computed = _hash_password_legacy(password, salt)
    return hmac.compare_digest(computed, stored_hash)


def needs_rehash(stored_hash: str) -> bool:
    """Возвращает True если хеш в старом формате и нужна миграция."""
    return not stored_hash.startswith(_PBKDF2_PREFIX)


def new_salt() -> str:
    """32 символа hex (128 бит энтропии)."""
    return secrets.token_hex(16)


# ═══════════════════════════════════════════════════════════════
#  Валидация
# ═══════════════════════════════════════════════════════════════

_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)

_PASSWORD_MIN_LENGTH = 6
_PASSWORD_MAX_LENGTH = 128
_LOGIN_MIN_LENGTH = 3
_LOGIN_MAX_LENGTH = 80


def validate_email(email: str) -> str | None:
    """
    Нормализует и проверяет email.
    Возвращает нормализованный email или None при ошибке.
    """
    cleaned = (email or "").strip().lower()
    if not cleaned or len(cleaned) > 254:
        return None
    if not _EMAIL_RE.match(cleaned):
        return None
    return cleaned


def validate_login(login: str) -> str | None:
    """
    Проверяет логин для входа.

    Логин:
    - обязателен;
    - 3..80 символов;
    - без пробельных символов;
    - хранится в lower-case для case-insensitive входа.
    """
    cleaned = (login or "").strip().lower()
    if not cleaned:
        return None
    if len(cleaned) < _LOGIN_MIN_LENGTH or len(cleaned) > _LOGIN_MAX_LENGTH:
        return None
    if any(ch.isspace() for ch in cleaned):
        return None
    return cleaned


def validate_password(password: str) -> tuple[bool, str]:
    """
    Проверяет пароль на минимальные требования.
    Возвращает (ok, error_message).
    """
    if not password:
        return False, "Пароль не может быть пустым."
    if len(password) < _PASSWORD_MIN_LENGTH:
        return False, f"Пароль должен содержать минимум {_PASSWORD_MIN_LENGTH} символов."
    if len(password) > _PASSWORD_MAX_LENGTH:
        return False, f"Пароль не может быть длиннее {_PASSWORD_MAX_LENGTH} символов."
    if password.isdigit():
        return False, "Пароль не может состоять только из цифр."
    if password.isalpha():
        return False, "Пароль должен содержать хотя бы одну цифру или спецсимвол."
    return True, ""


# ═══════════════════════════════════════════════════════════════
#  CSRF-защита
# ═══════════════════════════════════════════════════════════════

_CSRF_TOKEN_BYTES = 32
CSRF_FIELD_NAME = "csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"


def generate_csrf_token(session: dict) -> str:
    """Генерирует или возвращает существующий CSRF-токен из сессии."""
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_hex(_CSRF_TOKEN_BYTES)
        session["_csrf_token"] = token
    return token


def verify_csrf_token(session: dict, token: str | None) -> bool:
    """Проверяет CSRF-токен из формы/заголовка."""
    expected = session.get("_csrf_token")
    if not expected or not token:
        return False
    return hmac.compare_digest(expected, token)


# ═══════════════════════════════════════════════════════════════
#  Rate-limiter (in-memory, per-IP)
# ═══════════════════════════════════════════════════════════════

class RateLimiter:
    """
    Простой rate-limiter для защиты от брутфорса.

    Хранит в памяти список ключ → [timestamps].
    Не зависит от Redis — подходит для одного процесса.
    """

    def __init__(self, max_attempts: int = 5, window_seconds: int = 300):
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._store: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def is_blocked(self, key: str) -> bool:
        """Возвращает True если лимит превышен."""
        now = time.monotonic()
        with self._lock:
            attempts = self._store[key]
            # очистка старых записей
            self._store[key] = [t for t in attempts if now - t < self.window]
            return len(self._store[key]) >= self.max_attempts

    def record(self, key: str) -> None:
        """Фиксирует неудачную попытку."""
        now = time.monotonic()
        with self._lock:
            self._store[key] = [t for t in self._store[key] if now - t < self.window]
            self._store[key].append(now)

    def reset(self, key: str) -> None:
        """Сбрасывает счётчик (после успешного логина)."""
        with self._lock:
            self._store.pop(key, None)

    def remaining_seconds(self, key: str) -> int:
        """Сколько секунд до первого протухания (примерно)."""
        now = time.monotonic()
        with self._lock:
            attempts = self._store.get(key, [])
            if not attempts:
                return 0
            oldest = min(attempts)
            remaining = self.window - (now - oldest)
            return max(0, int(remaining))


# Глобальный экземпляр: 5 попыток за 5 минут
login_limiter = RateLimiter(max_attempts=5, window_seconds=300)


# ═══════════════════════════════════════════════════════════════
#  Санитизация строк
# ═══════════════════════════════════════════════════════════════

def sanitize_string(value: str, max_length: int = 200) -> str:
    """
    Очищает пользовательский ввод:
    - strip
    - html-escape тегов
    - ограничение длины
    """
    cleaned = (value or "").strip()
    cleaned = html.escape(cleaned, quote=True)
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length]
    return cleaned


def sanitize_full_name(name: str) -> str:
    """Очищает ФИО: убирает лишние пробелы, экранирует html."""
    cleaned = " ".join((name or "").split())
    cleaned = html.escape(cleaned, quote=True)
    if len(cleaned) > 150:
        cleaned = cleaned[:150]
    return cleaned
