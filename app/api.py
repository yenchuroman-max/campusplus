"""
REST JSON API для Swagger (/docs).

Все эндпоинты возвращают JSON, сгруппированы по тегам.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.db import connect
from app.security import (
    hash_password,
    login_limiter,
    needs_rehash,
    new_salt,
    sanitize_full_name,
    validate_login,
    validate_password,
    verify_password,
)

router = APIRouter(prefix="/api", tags=["API"])

# ═══════════════════════════════════════════════════════════════
# Pydantic-модели
# ═══════════════════════════════════════════════════════════════

class MessageOut(BaseModel):
    ok: bool = True
    message: str = ""

class UserOut(BaseModel):
    id: int
    role: str
    full_name: str
    login: str = Field("", description="Логин пользователя")
    email: str = Field("", description="Совместимость: дублирует login")
    student_group: Optional[str] = ""
    assigned_teacher_id: Optional[int] = None
    last_login: Optional[str] = None
    discipline_id: Optional[int] = None

class LoginIn(BaseModel):
    login: Optional[str] = None
    email: Optional[str] = Field(None, description="Legacy alias для login")
    password: str

class RegisterIn(BaseModel):
    role: str = Field("student", description="student")
    full_name: str
    login: Optional[str] = None
    email: Optional[str] = Field(None, description="Legacy alias для login")
    password: str
    student_group: str = ""

class LectureOut(BaseModel):
    id: int
    teacher_id: int
    title: str
    body: str = ""
    discipline_id: Optional[int] = None
    created_at: Optional[str] = None

class LectureCreateIn(BaseModel):
    title: str
    body: str
    discipline_id: Optional[int] = None

class TestOut(BaseModel):
    id: int
    lecture_id: int
    title: str
    status: str
    created_at: Optional[str] = None

class QuestionOut(BaseModel):
    id: int
    test_id: int
    text: str
    options: list[str]
    correct_index: int

class AttemptOut(BaseModel):
    id: int
    test_id: int
    student_id: int
    score: float
    taken_at: Optional[str] = None

class AnswerOut(BaseModel):
    question_id: int
    selected_index: int
    is_correct: bool
    question_text: str = ""
    options: list[str] = []
    correct_index: int = 0

class DisciplineOut(BaseModel):
    id: int
    name: str

class GroupOut(BaseModel):
    name: str
    teacher_id: Optional[int] = None
    teacher_name: Optional[str] = None

class GenerateIn(BaseModel):
    question_count: int = Field(5, ge=1, le=50)
    difficulty: str = Field("medium", description="easy | medium | hard")

class AnalyticsOut(BaseModel):
    avg: float = 0.0
    total: int = 0
    best: float = 0.0
    worst: float = 0.0
    trend: float = 0.0
    last7: float = 0.0
    recent: list[dict[str, Any]] = []

class AISetupOut(BaseModel):
    ok: bool
    message: str

# ═══════════════════════════════════════════════════════════════
# Утилиты
# ═══════════════════════════════════════════════════════════════

def _get_user(request: Request) -> dict | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def _require_user(request: Request) -> dict:
    user = _get_user(request)
    if not user:
        raise HTTPException(401, "Необходима авторизация")
    return user


def _require_role(request: Request, *roles: str) -> dict:
    user = _require_user(request)
    if user["role"] not in roles:
        raise HTTPException(403, "Недостаточно прав")
    return user


def _user_out(row) -> dict:
    d = dict(row) if not isinstance(row, dict) else row
    out = {k: d.get(k) for k in UserOut.__fields__}
    out["login"] = d.get("email", "") or ""
    out["email"] = d.get("email", "") or ""
    return out


# ═══════════════════════════════════════════════════════════════
# Health
# ═══════════════════════════════════════════════════════════════

@router.get("/health", tags=["Система"], summary="Health-check")
def api_health():
    return {"status": "ok", "ts": datetime.utcnow().isoformat()}


# ═══════════════════════════════════════════════════════════════
# Auth
# ═══════════════════════════════════════════════════════════════

@router.post("/auth/login", tags=["Авторизация"], summary="Вход", response_model=UserOut)
def api_login(request: Request, data: LoginIn):
    raw_login = (data.login or data.email or "").strip()
    clean_login = validate_login(raw_login)
    if not clean_login:
        raise HTTPException(422, "Некорректный логин")
    client_ip = request.client.host if request.client else "unknown"
    if login_limiter.is_blocked(client_ip):
        wait = login_limiter.remaining_seconds(client_ip)
        raise HTTPException(429, f"Слишком много попыток. Подождите {wait} сек.")
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email = ?", (clean_login,))
    row = cur.fetchone()
    conn.close()
    if not row or not verify_password(data.password, row["salt"], row["password_hash"]):
        login_limiter.record(client_ip)
        raise HTTPException(401, "Неверный логин или пароль")
    login_limiter.reset(client_ip)
    # auto-rehash legacy SHA-256 → PBKDF2
    if needs_rehash(row["password_hash"]):
        s = new_salt()
        h = hash_password(data.password, s)
        conn = connect()
        cur = conn.cursor()
        cur.execute("UPDATE users SET password_hash = ?, salt = ? WHERE id = ?", (h, s, row["id"]))
        conn.commit()
        conn.close()
    request.session["user_id"] = row["id"]
    request.session["user_email"] = row["email"]
    if row["role"] == "admin":
        request.session["admin_authenticated"] = True
        request.session["admin_email"] = row["email"]
    conn = connect()
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_login = ? WHERE id = ?", (datetime.utcnow().isoformat(), row["id"]))
    conn.commit()
    conn.close()
    return _user_out(row)


@router.post("/auth/register", tags=["Авторизация"], summary="Регистрация", response_model=MessageOut)
def api_register(data: RegisterIn):
    role = (data.role or "student").strip().lower()
    if role != "student":
        raise HTTPException(422, "Самостоятельная регистрация доступна только студентам")
    clean_name = sanitize_full_name(data.full_name)
    if not clean_name:
        raise HTTPException(422, "Укажите ФИО")
    clean_login = validate_login(data.login or data.email or "")
    if not clean_login:
        raise HTTPException(422, "Некорректный логин")
    pw_ok, pw_err = validate_password(data.password)
    if not pw_ok:
        raise HTTPException(422, pw_err)
    group = (data.student_group or "").strip()
    if not group:
        raise HTTPException(422, "Для студента необходимо указать группу")
    salt = new_salt()
    pw = hash_password(data.password, salt)
    conn = connect()
    cur = conn.cursor()
    assigned = None
    if group:
        cur.execute("SELECT teacher_id FROM groups WHERE name = ?", (group,))
        r = cur.fetchone()
        assigned = int(r["teacher_id"]) if r and r["teacher_id"] else None
    try:
        cur.execute(
            "INSERT INTO users (role, full_name, email, password_hash, salt, student_group, assigned_teacher_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("student", clean_name, clean_login, pw, salt, group, assigned),
        )
        conn.commit()
    except Exception:
        conn.close()
        raise HTTPException(409, "Логин уже используется")
    conn.close()
    return {"ok": True, "message": "Пользователь зарегистрирован"}


@router.post("/auth/logout", tags=["Авторизация"], summary="Выход", response_model=MessageOut)
def api_logout(request: Request):
    request.session.clear()
    return {"ok": True, "message": "Сессия завершена"}


@router.get("/auth/me", tags=["Авторизация"], summary="Текущий пользователь", response_model=UserOut)
def api_me(request: Request):
    user = _require_user(request)
    return _user_out(user)


# ═══════════════════════════════════════════════════════════════
# Лекции
# ═══════════════════════════════════════════════════════════════

@router.get("/lectures", tags=["Лекции"], summary="Список лекций", response_model=list[LectureOut])
def api_lectures(request: Request):
    user = _require_role(request, "teacher", "admin")
    conn = connect()
    cur = conn.cursor()
    if user["role"] == "teacher":
        cur.execute("SELECT id, teacher_id, title, body, discipline_id, created_at FROM lectures WHERE teacher_id = ? ORDER BY id DESC", (user["id"],))
    else:
        cur.execute("SELECT id, teacher_id, title, body, discipline_id, created_at FROM lectures ORDER BY id DESC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


@router.get("/lectures/{lecture_id}", tags=["Лекции"], summary="Детали лекции", response_model=LectureOut)
def api_lecture_detail(request: Request, lecture_id: int):
    user = _require_role(request, "teacher", "admin")
    conn = connect()
    cur = conn.cursor()
    if user["role"] == "teacher":
        cur.execute("SELECT * FROM lectures WHERE id = ? AND teacher_id = ?", (lecture_id, user["id"]))
    else:
        cur.execute("SELECT * FROM lectures WHERE id = ?", (lecture_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Лекция не найдена")
    return dict(row)


@router.post("/lectures", tags=["Лекции"], summary="Создать лекцию", response_model=LectureOut)
def api_create_lecture(request: Request, data: LectureCreateIn):
    user = _require_role(request, "teacher", "admin")
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO lectures (teacher_id, title, body, discipline_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (user["id"], data.title.strip(), data.body.strip(), data.discipline_id, datetime.utcnow().isoformat()),
    )
    lid = cur.lastrowid
    conn.commit()
    cur.execute("SELECT * FROM lectures WHERE id = ?", (lid,))
    row = dict(cur.fetchone())
    conn.close()
    return row


@router.delete("/lectures/{lecture_id}", tags=["Лекции"], summary="Удалить лекцию", response_model=MessageOut)
def api_delete_lecture(request: Request, lecture_id: int):
    user = _require_role(request, "teacher", "admin")
    conn = connect()
    cur = conn.cursor()
    if user["role"] == "teacher":
        cur.execute("SELECT id FROM lectures WHERE id = ? AND teacher_id = ?", (lecture_id, user["id"]))
    else:
        cur.execute("SELECT id FROM lectures WHERE id = ?", (lecture_id,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, "Лекция не найдена")
    cur.execute("DELETE FROM questions WHERE test_id IN (SELECT id FROM tests WHERE lecture_id = ?)", (lecture_id,))
    cur.execute("DELETE FROM tests WHERE lecture_id = ?", (lecture_id,))
    cur.execute("DELETE FROM lectures WHERE id = ?", (lecture_id,))
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Лекция удалена"}


# ═══════════════════════════════════════════════════════════════
# Тесты
# ═══════════════════════════════════════════════════════════════

@router.get("/tests", tags=["Тесты"], summary="Список тестов", response_model=list[TestOut])
def api_tests(request: Request, status: str = Query(None, description="draft | published")):
    user = _require_user(request)
    conn = connect()
    cur = conn.cursor()
    if user["role"] == "student":
        sql = "SELECT t.* FROM tests t WHERE t.status = 'published' ORDER BY t.id DESC"
        cur.execute(sql)
    elif user["role"] == "teacher":
        cur.execute(
            "SELECT t.* FROM tests t JOIN lectures l ON l.id = t.lecture_id WHERE l.teacher_id = ? ORDER BY t.id DESC",
            (user["id"],),
        )
    else:
        cur.execute("SELECT * FROM tests ORDER BY id DESC")
    rows = [dict(r) for r in cur.fetchall()]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    conn.close()
    return rows


@router.get("/tests/{test_id}", tags=["Тесты"], summary="Детали теста")
def api_test_detail(request: Request, test_id: int):
    user = _require_user(request)
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tests WHERE id = ?", (test_id,))
    test = cur.fetchone()
    if not test:
        conn.close()
        raise HTTPException(404, "Тест не найден")
    cur.execute("SELECT * FROM questions WHERE test_id = ? ORDER BY id", (test_id,))
    questions = []
    for q in cur.fetchall():
        qd = dict(q)
        qd["options"] = json.loads(qd.pop("options_json", "[]"))
        questions.append(qd)
    conn.close()
    return {"test": dict(test), "questions": questions}


@router.post("/tests/{test_id}/publish", tags=["Тесты"], summary="Опубликовать тест", response_model=MessageOut)
def api_publish_test(request: Request, test_id: int):
    user = _require_role(request, "teacher", "admin")
    conn = connect()
    cur = conn.cursor()
    cur.execute("UPDATE tests SET status = 'published' WHERE id = ?", (test_id,))
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Тест опубликован"}


@router.delete("/tests/{test_id}", tags=["Тесты"], summary="Удалить тест", response_model=MessageOut)
def api_delete_test(request: Request, test_id: int):
    user = _require_role(request, "teacher", "admin")
    conn = connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM answers WHERE question_id IN (SELECT id FROM questions WHERE test_id = ?)", (test_id,))
    cur.execute("DELETE FROM questions WHERE test_id = ?", (test_id,))
    cur.execute("DELETE FROM attempts WHERE test_id = ?", (test_id,))
    cur.execute("DELETE FROM tests WHERE id = ?", (test_id,))
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Тест удалён"}


# ═══════════════════════════════════════════════════════════════
# Прохождение теста
# ═══════════════════════════════════════════════════════════════

@router.post("/tests/{test_id}/attempt", tags=["Прохождение"], summary="Пройти тест", response_model=AttemptOut)
def api_take_test(request: Request, test_id: int, answers: dict[str, int]):
    """
    Body: `{"<question_id>": <selected_index>, ...}`
    """
    user = _require_role(request, "student")
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tests WHERE id = ? AND status = 'published'", (test_id,))
    test = cur.fetchone()
    if not test:
        conn.close()
        raise HTTPException(404, "Тест не найден или не опубликован")
    cur.execute("SELECT id FROM attempts WHERE test_id = ? AND student_id = ?", (test_id, user["id"]))
    if cur.fetchone():
        conn.close()
        raise HTTPException(409, "Тест уже пройден")
    cur.execute("SELECT * FROM questions WHERE test_id = ? ORDER BY id", (test_id,))
    questions = [dict(r) for r in cur.fetchall()]
    correct = 0
    for q in questions:
        sel = answers.get(str(q["id"]), -1)
        if sel == q["correct_index"]:
            correct += 1
    score = round(100 * correct / max(1, len(questions)), 2)
    cur.execute(
        "INSERT INTO attempts (test_id, student_id, score, taken_at) VALUES (?, ?, ?, ?)",
        (test_id, user["id"], score, datetime.utcnow().isoformat()),
    )
    attempt_id = cur.lastrowid
    for q in questions:
        sel = answers.get(str(q["id"]), -1)
        is_ok = 1 if sel == q["correct_index"] else 0
        cur.execute(
            "INSERT INTO answers (attempt_id, question_id, selected_index, is_correct) VALUES (?, ?, ?, ?)",
            (attempt_id, q["id"], sel, is_ok),
        )
    conn.commit()
    cur.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,))
    row = dict(cur.fetchone())
    conn.close()
    return row


@router.get("/attempts/{attempt_id}", tags=["Прохождение"], summary="Результат попытки")
def api_attempt_detail(request: Request, attempt_id: int):
    user = _require_user(request)
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,))
    attempt = cur.fetchone()
    if not attempt:
        conn.close()
        raise HTTPException(404, "Попытка не найдена")
    if user["role"] == "student" and attempt["student_id"] != user["id"]:
        conn.close()
        raise HTTPException(403, "Нет доступа к чужой попытке")
    cur.execute(
        """
        SELECT a.question_id, a.selected_index, a.is_correct,
               q.text AS question_text, q.options_json, q.correct_index
        FROM answers a
        JOIN questions q ON q.id = a.question_id
        WHERE a.attempt_id = ?
        ORDER BY a.question_id
        """,
        (attempt_id,),
    )
    details = []
    for r in cur.fetchall():
        details.append({
            "question_id": r["question_id"],
            "question_text": r["question_text"],
            "options": json.loads(r["options_json"]),
            "correct_index": r["correct_index"],
            "selected_index": r["selected_index"],
            "is_correct": bool(r["is_correct"]),
        })
    conn.close()
    return {"attempt": dict(attempt), "answers": details}


# ═══════════════════════════════════════════════════════════════
# AI-генерация
# ═══════════════════════════════════════════════════════════════

@router.post("/lectures/{lecture_id}/generate", tags=["AI"], summary="Сгенерировать тест по лекции")
def api_generate(request: Request, lecture_id: int, data: GenerateIn):
    user = _require_role(request, "teacher", "admin")
    conn = connect()
    cur = conn.cursor()
    if user["role"] == "teacher":
        cur.execute("SELECT * FROM lectures WHERE id = ? AND teacher_id = ?", (lecture_id, user["id"]))
    else:
        cur.execute("SELECT * FROM lectures WHERE id = ?", (lecture_id,))
    lecture = cur.fetchone()
    if not lecture:
        conn.close()
        raise HTTPException(404, "Лекция не найдена")

    from app.ai import diagnose_ai_setup, generate_questions

    discipline_name = None
    did = lecture["discipline_id"] if "discipline_id" in lecture.keys() else None
    if did:
        cur.execute("SELECT name FROM disciplines WHERE id = ?", (did,))
        dr = cur.fetchone()
        if dr:
            discipline_name = dr["name"]

    count = max(1, min(data.question_count, 50))
    difficulty = data.difficulty if data.difficulty in ("easy", "medium", "hard") else "medium"

    questions = generate_questions(lecture["body"], count=count, difficulty=difficulty, discipline_name=discipline_name)
    if not questions:
        conn.close()
        details = diagnose_ai_setup()
        raise HTTPException(422, f"AI не вернул вопросы. {details}")

    title = f'Тест по теме: {lecture["title"]} ({difficulty}, {count} вопр.)'
    cur.execute(
        "INSERT INTO tests (lecture_id, title, status, created_at) VALUES (?, ?, ?, ?)",
        (lecture_id, title, "draft", datetime.utcnow().isoformat()),
    )
    test_id = cur.lastrowid
    for q in questions:
        cur.execute(
            "INSERT INTO questions (test_id, text, options_json, correct_index) VALUES (?, ?, ?, ?)",
            (test_id, q["text"], json.dumps(q["options"], ensure_ascii=False), q["correct_index"]),
        )
    conn.commit()
    conn.close()
    return {"ok": True, "test_id": test_id, "questions_count": len(questions)}


@router.get("/ai/status", tags=["AI"], summary="Статус AI-провайдеров", response_model=AISetupOut)
def api_ai_status():
    from app.ai import diagnose_ai_setup
    msg = diagnose_ai_setup()
    ok = "настроен" in msg.lower() or "ok" in msg.lower() or "ключ" in msg.lower()
    return {"ok": ok, "message": msg}


# ═══════════════════════════════════════════════════════════════
# Аналитика (студент)
# ═══════════════════════════════════════════════════════════════

@router.get("/analytics/student", tags=["Аналитика"], summary="Аналитика студента", response_model=AnalyticsOut)
def api_student_analytics(request: Request):
    user = _require_role(request, "student")
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.id AS attempt_id, a.score, a.taken_at,
               t.id AS test_id, t.title AS test_title
        FROM attempts a JOIN tests t ON t.id = a.test_id
        WHERE a.student_id = ?
        ORDER BY a.taken_at DESC
        """,
        (user["id"],),
    )
    rows = cur.fetchall()
    conn.close()
    scores = [r["score"] for r in rows]
    avg = round(sum(scores) / len(scores), 2) if scores else 0.0
    trend = round(scores[0] - scores[1], 2) if len(scores) >= 2 else 0.0
    recent = [
        {"score": r["score"], "taken_at": r["taken_at"], "test_title": r["test_title"], "attempt_id": r["attempt_id"]}
        for r in rows[:5]
    ]
    return {
        "avg": avg,
        "total": len(scores),
        "best": max(scores) if scores else 0.0,
        "worst": min(scores) if scores else 0.0,
        "trend": trend,
        "last7": 0.0,
        "recent": recent,
    }


# ═══════════════════════════════════════════════════════════════
# Аналитика (преподаватель)
# ═══════════════════════════════════════════════════════════════

@router.get("/analytics/teacher", tags=["Аналитика"], summary="Аналитика преподавателя")
def api_teacher_analytics(request: Request):
    user = _require_role(request, "teacher", "admin")
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.score, a.taken_at, t.title AS test_title,
               u.full_name AS student_name, u.student_group
        FROM attempts a
        JOIN tests t ON t.id = a.test_id
        JOIN lectures l ON l.id = t.lecture_id
        JOIN users u ON u.id = a.student_id
        WHERE l.teacher_id = ?
        ORDER BY a.taken_at DESC
        """,
        (user["id"],),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    scores = [r["score"] for r in rows]
    return {
        "total_attempts": len(rows),
        "avg": round(sum(scores) / len(scores), 2) if scores else 0.0,
        "attempts": rows[:20],
    }


# ═══════════════════════════════════════════════════════════════
# Дисциплины
# ═══════════════════════════════════════════════════════════════

@router.get("/disciplines", tags=["Дисциплины"], summary="Все дисциплины", response_model=list[DisciplineOut])
def api_disciplines(request: Request):
    _require_user(request)
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM disciplines ORDER BY name")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


@router.post("/disciplines", tags=["Дисциплины"], summary="Создать дисциплину", response_model=DisciplineOut)
def api_create_discipline(request: Request, name: str = Form(...)):
    _require_role(request, "teacher", "admin")
    normalized = " ".join(name.strip().split())
    if not normalized:
        raise HTTPException(422, "Название не может быть пустым")
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id FROM disciplines WHERE lower(name) = lower(?)", (normalized,))
    existing = cur.fetchone()
    if existing:
        conn.close()
        raise HTTPException(409, "Дисциплина с таким названием уже существует")
    cur.execute("INSERT INTO disciplines (name) VALUES (?)", (normalized,))
    did = cur.lastrowid
    conn.commit()
    conn.close()
    return {"id": did, "name": normalized}


@router.get("/disciplines/{discipline_id}", tags=["Дисциплины"], summary="Детали дисциплины")
def api_discipline_detail(request: Request, discipline_id: int):
    _require_user(request)
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM disciplines WHERE id = ?", (discipline_id,))
    d = cur.fetchone()
    if not d:
        conn.close()
        raise HTTPException(404, "Дисциплина не найдена")
    cur.execute(
        "SELECT u.id, u.full_name, u.email FROM teacher_disciplines td "
        "JOIN users u ON u.id = td.teacher_id WHERE td.discipline_id = ?",
        (discipline_id,),
    )
    teachers = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"discipline": dict(d), "teachers": teachers}


# ═══════════════════════════════════════════════════════════════
# Группы
# ═══════════════════════════════════════════════════════════════

@router.get("/groups", tags=["Группы"], summary="Все группы", response_model=list[GroupOut])
def api_groups(request: Request):
    _require_user(request)
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT g.name, g.teacher_id, u.full_name AS teacher_name "
        "FROM groups g LEFT JOIN users u ON u.id = g.teacher_id ORDER BY g.name"
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


@router.get("/groups/{group_name}/students", tags=["Группы"], summary="Студенты группы", response_model=list[UserOut])
def api_group_students(request: Request, group_name: str):
    _require_role(request, "teacher", "admin")
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, role, full_name, email, student_group, assigned_teacher_id, last_login, discipline_id "
        "FROM users WHERE role = 'student' AND student_group = ? ORDER BY full_name",
        (group_name,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ═══════════════════════════════════════════════════════════════
# Пользователи (admin)
# ═══════════════════════════════════════════════════════════════

@router.get("/users", tags=["Пользователи"], summary="Список пользователей", response_model=list[UserOut])
def api_users(request: Request, role: str = Query(None, description="student | teacher | admin")):
    _require_role(request, "admin")
    conn = connect()
    cur = conn.cursor()
    if role:
        cur.execute(
            "SELECT id, role, full_name, email, student_group, assigned_teacher_id, last_login, discipline_id "
            "FROM users WHERE role = ? ORDER BY full_name", (role,),
        )
    else:
        cur.execute(
            "SELECT id, role, full_name, email, student_group, assigned_teacher_id, last_login, discipline_id "
            "FROM users ORDER BY full_name"
        )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


@router.get("/users/{user_id}", tags=["Пользователи"], summary="Пользователь по ID", response_model=UserOut)
def api_user_detail(request: Request, user_id: int):
    _require_role(request, "admin", "teacher")
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Пользователь не найден")
    return _user_out(row)


@router.delete("/users/{user_id}", tags=["Пользователи"], summary="Удалить пользователя", response_model=MessageOut)
def api_delete_user(request: Request, user_id: int):
    _require_role(request, "admin")
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT role FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Пользователь не найден")
    if row["role"] != "student":
        conn.close()
        raise HTTPException(403, "Можно удалять только студентов")
    cur.execute("DELETE FROM answers WHERE attempt_id IN (SELECT id FROM attempts WHERE student_id = ?)", (user_id,))
    cur.execute("DELETE FROM attempts WHERE student_id = ?", (user_id,))
    cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Пользователь удалён"}
