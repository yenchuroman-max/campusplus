from __future__ import annotations

import asyncio
from base64 import b64decode
from datetime import datetime
import json
import os
import re
import secrets
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus

import itsdangerous
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from itsdangerous.exc import BadSignature

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

from app.ai import diagnose_ai_setup, generate_growth_topics, generate_questions
from app.db import connect, init_db, insert_ignore
from app.lecture_import import (
    LectureImportError,
    extract_lecture_text,
    extract_text_from_urls,
    parse_source_urls,
)

UPLOADS_DIR = Path(__file__).resolve().parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
FAVICON_PATH = Path(__file__).resolve().parent / "app" / "static" / "img" / "sgugit-mark.png"
from app.security import (
    CSRF_FIELD_NAME,
    generate_csrf_token,
    hash_password,
    login_limiter,
    needs_rehash,
    new_salt,
    sanitize_full_name,
    sanitize_string,
    validate_login,
    validate_password,
    verify_csrf_token,
    verify_password,
)


def _extract_text_from_bytes(raw_bytes: bytes, filename: str) -> str:
    """Извлечение текста из байтов файла (вызывается в thread pool)."""
    if not raw_bytes:
        raise LectureImportError("Файл пустой.")
    from app.lecture_import import _extract_text_from_file_bytes

    return _extract_text_from_file_bytes(raw_bytes, filename)


def format_last_login(ts: str | None) -> str:
    if not ts:
        return "-"
    try:
        # input stored as ISO format
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%d.%m.%Y")
    except Exception:
        return ts


def format_datetime_label(ts: str | None) -> str:
    if not ts:
        return "-"
    try:
        dt = datetime.fromisoformat(str(ts))
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return str(ts)


def user_row_to_dict(row) -> dict:
    d = dict(row)
    d["last_login"] = format_last_login(d.get("last_login"))
    return d


def fetch_managed_groups(cur) -> list[dict[str, Any]]:
    cur.execute("SELECT name FROM groups ORDER BY name")
    groups = [{"name": row["name"]} for row in cur.fetchall() if (row["name"] or "").strip()]
    names = {g.get("name") for g in groups}
    teacher_map: dict[str, list[dict[str, Any]]] = {}
    cur.execute(
        """
        SELECT gt.group_name, u.id, u.full_name, u.email
        FROM group_teachers gt
        JOIN users u ON u.id = gt.teacher_id
        WHERE u.role = 'teacher'
        ORDER BY gt.group_name, u.full_name
        """
    )
    for row in cur.fetchall():
        group_name = (row["group_name"] or "").strip()
        teacher_map.setdefault(group_name, []).append(
            {
                "id": int(row["id"]),
                "full_name": row["full_name"],
                "email": row["email"],
            }
        )
    for group in groups:
        teachers = teacher_map.get(group["name"], [])
        group["teachers"] = teachers
        group["teacher_id"] = teachers[0]["id"] if teachers else None
        group["teacher_name"] = ", ".join(teacher["full_name"] for teacher in teachers) if teachers else None
    if "Без группы" not in names:
        groups.append({"name": "Без группы", "teacher_id": None, "teacher_name": None, "teachers": []})
    return groups


def get_group_teachers(cur, group_name: str) -> list[dict[str, Any]]:
    normalized = normalize_group_name(group_name)
    if not normalized or normalized == "Без группы":
        return []
    cur.execute(
        """
        SELECT u.id, u.full_name, u.email, d.id AS discipline_id, d.name AS discipline_name
        FROM group_teachers gt
        JOIN users u ON u.id = gt.teacher_id
        LEFT JOIN teaching_assignments ta
          ON ta.group_name = gt.group_name
         AND ta.teacher_id = gt.teacher_id
        LEFT JOIN disciplines d ON d.id = ta.discipline_id
        WHERE gt.group_name = ? AND u.role = 'teacher'
        ORDER BY u.full_name, d.name
        """,
        (normalized,),
    )
    teachers: list[dict[str, Any]] = []
    teacher_index: dict[int, dict[str, Any]] = {}
    for row in cur.fetchall():
        teacher_id = int(row["id"])
        teacher = teacher_index.get(teacher_id)
        if teacher is None:
            teacher = {
                "id": teacher_id,
                "full_name": row["full_name"],
                "email": row["email"],
                "disciplines": [],
            }
            teacher_index[teacher_id] = teacher
            teachers.append(teacher)
        if row["discipline_id"] and row["discipline_name"]:
            teacher["disciplines"].append(
                {
                    "id": int(row["discipline_id"]),
                    "name": row["discipline_name"],
                }
            )
    for teacher in teachers:
        teacher["discipline_names"] = ", ".join(item["name"] for item in teacher["disciplines"])
    return teachers


def refresh_group_primary_teacher(cur, group_name: str) -> int | None:
    normalized = normalize_group_name(group_name)
    if not normalized or normalized == "Без группы":
        return None
    cur.execute(
        "SELECT MIN(teacher_id) AS teacher_id FROM group_teachers WHERE group_name = ?",
        (normalized,),
    )
    row = cur.fetchone()
    teacher_id = int(row["teacher_id"]) if row and row["teacher_id"] else None
    cur.execute("UPDATE groups SET teacher_id = ? WHERE name = ?", (teacher_id, normalized))
    return teacher_id


def find_group_teacher_id(cur, group_name: str) -> int | None:
    normalized = (group_name or "").strip()
    if not normalized or normalized == "Без группы":
        return None
    cur.execute(
        "SELECT MIN(teacher_id) AS teacher_id FROM group_teachers WHERE group_name = ?",
        (normalized,),
    )
    row = cur.fetchone()
    if row and row["teacher_id"]:
        return int(row["teacher_id"])
    cur.execute("SELECT teacher_id FROM groups WHERE name = ?", (normalized,))
    row = cur.fetchone()
    if not row:
        return None
    teacher_id = row["teacher_id"]
    return int(teacher_id) if teacher_id else None


def add_group_teacher(cur, group_name: str, teacher_id: int) -> bool:
    normalized = normalize_group_name(group_name)
    if not normalized or normalized == "Без группы":
        return False
    inserted = bool(
        insert_ignore(
            cur,
            "group_teachers",
            ("group_name", "teacher_id"),
            (normalized, teacher_id),
            conflict_columns=("group_name", "teacher_id"),
        )
    )
    refresh_group_primary_teacher(cur, normalized)
    if inserted:
        sync_teacher_group_assignments(cur, teacher_id, group_name=normalized)
        cur.execute(
            """
            UPDATE users
            SET assigned_teacher_id = COALESCE(NULLIF(assigned_teacher_id, 0), ?)
            WHERE role = 'student' AND student_group = ?
            """,
            (teacher_id, normalized),
        )
    return inserted


def remove_group_teacher(cur, group_name: str, teacher_id: int) -> bool:
    normalized = normalize_group_name(group_name)
    if not normalized or normalized == "Без группы":
        return False
    cur.execute(
        "DELETE FROM group_teachers WHERE group_name = ? AND teacher_id = ?",
        (normalized, teacher_id),
    )
    removed = cur.rowcount > 0
    if not removed:
        return False
    cur.execute(
        "DELETE FROM teaching_assignments WHERE teacher_id = ? AND group_name = ?",
        (teacher_id, normalized),
    )
    cur.execute(
        "DELETE FROM teaching_assignment_blocks WHERE teacher_id = ? AND group_name = ?",
        (teacher_id, normalized),
    )
    next_teacher_id = refresh_group_primary_teacher(cur, normalized)
    cur.execute(
        """
        UPDATE users
        SET assigned_teacher_id = ?
        WHERE role = 'student' AND student_group = ? AND assigned_teacher_id = ?
        """,
        (next_teacher_id, normalized, teacher_id),
    )
    return True


def delete_group_if_empty(cur, group_name: str) -> tuple[bool, str]:
    normalized = normalize_group_name(group_name)
    if not normalized or normalized == "Без группы":
        return False, "Эту группу удалить нельзя"
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM users WHERE role = 'student' AND student_group = ?",
        (normalized,),
    )
    row = cur.fetchone()
    student_count = int(row["cnt"]) if row and row["cnt"] is not None else 0
    if student_count > 0:
        return False, "Сначала уберите студентов из группы"
    cur.execute("DELETE FROM teaching_assignments WHERE group_name = ?", (normalized,))
    cur.execute("DELETE FROM teaching_assignment_blocks WHERE group_name = ?", (normalized,))
    cur.execute("DELETE FROM group_teachers WHERE group_name = ?", (normalized,))
    cur.execute("DELETE FROM groups WHERE name = ?", (normalized,))
    deleted = cur.rowcount > 0
    if not deleted:
        return False, "Группа не найдена"
    return True, "Группа удалена"


def get_teacher_assignment_blocks(
    cur,
    teacher_id: int | None,
    discipline_id: int | None = None,
) -> set[tuple[int, str]]:
    if not teacher_id:
        return set()
    params: tuple[Any, ...] = (teacher_id,)
    where_sql = "WHERE teacher_id = ?"
    if discipline_id:
        where_sql += " AND discipline_id = ?"
        params = (teacher_id, discipline_id)
    cur.execute(
        f"""
        SELECT discipline_id, group_name
        FROM teaching_assignment_blocks
        {where_sql}
        """,
        params,
    )
    return {
        (int(row["discipline_id"]), normalize_group_name(row["group_name"]))
        for row in cur.fetchall()
        if row["discipline_id"] and normalize_group_name(row["group_name"])
    }


def block_teacher_assignment(cur, teacher_id: int | None, discipline_id: int | None, group_name: str | None) -> bool:
    normalized_group = normalize_group_name(group_name)
    if not teacher_id or not discipline_id or not normalized_group:
        return False
    return bool(
        insert_ignore(
            cur,
            "teaching_assignment_blocks",
            ("teacher_id", "discipline_id", "group_name"),
            (teacher_id, discipline_id, normalized_group),
            conflict_columns=("teacher_id", "discipline_id", "group_name"),
        )
    )


def unblock_teacher_assignment(cur, teacher_id: int | None, discipline_id: int | None, group_name: str | None) -> bool:
    normalized_group = normalize_group_name(group_name)
    if not teacher_id or not discipline_id or not normalized_group:
        return False
    cur.execute(
        """
        DELETE FROM teaching_assignment_blocks
        WHERE teacher_id = ? AND discipline_id = ? AND group_name = ?
        """,
        (teacher_id, discipline_id, normalized_group),
    )
    return cur.rowcount > 0


def get_discipline_map(cur) -> dict[int, str]:
    cur.execute("SELECT id, name FROM disciplines ORDER BY name")
    return {int(row["id"]): row["name"] for row in cur.fetchall()}


def normalize_discipline_name(name: str) -> str:
    return " ".join((name or "").strip().split())


def create_or_get_discipline(cur, name: str) -> tuple[int, bool]:
    normalized = normalize_discipline_name(name)
    cur.execute("SELECT id FROM disciplines WHERE lower(name) = lower(?)", (normalized,))
    existing = cur.fetchone()
    if existing:
        return int(existing["id"]), False

    cur.execute("INSERT INTO disciplines (name) VALUES (?)", (normalized,))
    return int(cur.lastrowid), True


def get_teacher_discipline_ids(cur, teacher_id: int | None) -> list[int]:
    if not teacher_id:
        return []
    cur.execute(
        """
        SELECT td.discipline_id
        FROM teacher_disciplines td
        JOIN disciplines d ON d.id = td.discipline_id
        WHERE td.teacher_id = ?
        ORDER BY d.name
        """,
        (teacher_id,),
    )
    return [int(row["discipline_id"]) for row in cur.fetchall()]


def get_teacher_disciplines(cur, teacher_id: int | None) -> list[dict[str, Any]]:
    if not teacher_id:
        return []
    cur.execute(
        """
        SELECT d.id, d.name
        FROM teacher_disciplines td
        JOIN disciplines d ON d.id = td.discipline_id
        WHERE td.teacher_id = ?
        ORDER BY d.name
        """,
        (teacher_id,),
    )
    return [dict(row) for row in cur.fetchall()]


def get_teacher_discipline_id(cur, teacher_id: int | None) -> int | None:
    ids = get_teacher_discipline_ids(cur, teacher_id)
    return ids[0] if ids else None


def normalize_group_name(value: str | None) -> str:
    return (value or "").strip()


def natural_group_sort_key(value: str) -> tuple:
    parts = re.split(r"(\d+)", (value or "").lower())
    key: list[Any] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return tuple(key)


def get_teacher_owned_group_names(cur, teacher_id: int | None) -> list[str]:
    if not teacher_id:
        return []
    cur.execute(
        """
        SELECT DISTINCT group_name
        FROM (
            SELECT gt.group_name
            FROM group_teachers gt
            WHERE gt.teacher_id = ?
            UNION
            SELECT name AS group_name
            FROM groups
            WHERE teacher_id = ?
            UNION
            SELECT COALESCE(student_group, '') AS group_name
            FROM users
            WHERE role = 'student' AND assigned_teacher_id = ?
        ) src
        ORDER BY group_name
        """,
        (teacher_id, teacher_id, teacher_id),
    )
    return [normalize_group_name(row["group_name"]) for row in cur.fetchall()]


def sync_teacher_group_assignments(
    cur,
    teacher_id: int | None,
    discipline_id: int | None = None,
    group_name: str | None = None,
) -> int:
    if not teacher_id:
        return 0
    discipline_ids = [discipline_id] if discipline_id else get_teacher_discipline_ids(cur, teacher_id)
    if group_name is None:
        group_names = get_teacher_owned_group_names(cur, teacher_id)
    else:
        group_names = [normalize_group_name(group_name)]
    blocked_assignments = get_teacher_assignment_blocks(cur, teacher_id, discipline_id)

    inserted = 0
    for current_discipline_id in discipline_ids:
        if not current_discipline_id:
            continue
        for current_group_name in group_names:
            if (int(current_discipline_id), normalize_group_name(current_group_name)) in blocked_assignments:
                continue
            inserted += int(
                insert_ignore(
                    cur,
                    "teaching_assignments",
                    ("teacher_id", "discipline_id", "group_name"),
                    (teacher_id, current_discipline_id, current_group_name),
                    conflict_columns=("teacher_id", "discipline_id", "group_name"),
                )
            )
    return inserted


def detach_teacher_discipline_assignments(cur, teacher_id: int | None, discipline_id: int | None) -> None:
    if not teacher_id or not discipline_id:
        return
    cur.execute(
        "DELETE FROM teaching_assignments WHERE teacher_id = ? AND discipline_id = ?",
        (teacher_id, discipline_id),
    )
    cur.execute(
        "DELETE FROM teaching_assignment_blocks WHERE teacher_id = ? AND discipline_id = ?",
        (teacher_id, discipline_id),
    )


def get_teacher_assignment_groups(cur, teacher_id: int | None, discipline_id: int | None = None) -> list[str]:
    if not teacher_id:
        return []
    params: tuple[Any, ...] = (teacher_id,)
    where_sql = "WHERE teacher_id = ?"
    if discipline_id:
        where_sql += " AND discipline_id = ?"
        params = (teacher_id, discipline_id)
    cur.execute(
        f"""
        SELECT DISTINCT group_name
        FROM teaching_assignments
        {where_sql}
        ORDER BY group_name
        """,
        params,
    )
    return [normalize_group_name(row["group_name"]) for row in cur.fetchall()]


def get_all_group_names(cur) -> list[str]:
    cur.execute("SELECT name FROM groups ORDER BY name")
    names = {(row["name"] or "").strip() for row in cur.fetchall() if (row["name"] or "").strip()}
    cur.execute(
        """
        SELECT DISTINCT COALESCE(student_group, '') AS group_name
        FROM users
        WHERE role = 'student' AND TRIM(COALESCE(student_group, '')) <> ''
        """
    )
    for row in cur.fetchall():
        group_name = (row["group_name"] or "").strip()
        if group_name:
            names.add(group_name)
    return sorted(names, key=natural_group_sort_key)


def sync_teacher_attempt_group_assignments(
    cur,
    teacher_id: int | None,
    discipline_id: int | None = None,
) -> int:
    if not teacher_id:
        return 0
    params: tuple[Any, ...] = (teacher_id, teacher_id)
    filter_sql = ""
    if discipline_id:
        filter_sql = " AND lectures.discipline_id = ?"
        params = (teacher_id, teacher_id, discipline_id)

    cur.execute(
        f"""
        SELECT DISTINCT
            lectures.discipline_id AS discipline_id,
            COALESCE(users.student_group, '') AS group_name
        FROM attempts
        JOIN tests ON tests.id = attempts.test_id
        JOIN lectures ON lectures.id = tests.lecture_id
        JOIN users ON users.id = attempts.student_id
        JOIN teacher_disciplines td
          ON td.teacher_id = ?
         AND td.discipline_id = lectures.discipline_id
        WHERE lectures.teacher_id = ?
          AND users.role = 'student'
          AND TRIM(COALESCE(users.student_group, '')) <> ''{filter_sql}
        """,
        params,
    )

    attempt_rows = [dict(row) for row in cur.fetchall()]
    blocked_assignments = get_teacher_assignment_blocks(cur, teacher_id, discipline_id)
    inserted = 0
    for row in attempt_rows:
        normalized_group = normalize_group_name(row["group_name"])
        current_discipline_id = int(row["discipline_id"])
        if (current_discipline_id, normalized_group) in blocked_assignments:
            continue
        inserted += int(
            insert_ignore(
                cur,
                "teaching_assignments",
                ("teacher_id", "discipline_id", "group_name"),
                (teacher_id, current_discipline_id, normalized_group),
                conflict_columns=("teacher_id", "discipline_id", "group_name"),
            )
        )
    return inserted


def get_teacher_students(cur, teacher_id: int | None, query: str = "") -> list[dict[str, Any]]:
    if not teacher_id:
        return []
    params: tuple[Any, ...] = (teacher_id,)
    search_sql = ""
    if query.strip():
        like = f"%{query.strip()}%"
        search_sql = """
          AND (
            u.full_name LIKE ?
            OR u.email LIKE ?
            OR COALESCE(u.student_group, '') LIKE ?
          )
        """
        params = (teacher_id, like, like, like)
    cur.execute(
        f"""
        SELECT DISTINCT u.id, u.full_name, u.email, u.last_login, COALESCE(u.student_group, '') AS student_group
        FROM users u
        JOIN teaching_assignments ta
          ON ta.teacher_id = ?
         AND ta.group_name = COALESCE(u.student_group, '')
        WHERE u.role = 'student'
        {search_sql}
        ORDER BY student_group, u.full_name
        """,
        params,
    )
    return [user_row_to_dict(row) for row in cur.fetchall()]


def get_student_accessible_disciplines(cur, student_group: str | None) -> list[dict[str, Any]]:
    normalized_group = normalize_group_name(student_group)
    cur.execute(
        """
        SELECT DISTINCT d.id, d.name
        FROM teaching_assignments ta
        JOIN disciplines d ON d.id = ta.discipline_id
        WHERE ta.group_name = ?
        ORDER BY d.name
        """,
        (normalized_group,),
    )
    return [dict(row) for row in cur.fetchall()]


def get_student_accessible_discipline_ids(cur, student_group: str | None) -> list[int]:
    return [int(row["id"]) for row in get_student_accessible_disciplines(cur, student_group)]


def teacher_can_manage_student(cur, teacher_id: int | None, student_id: int) -> bool:
    if not teacher_id:
        return False
    cur.execute(
        """
        SELECT 1
        FROM users u
        JOIN teaching_assignments ta
          ON ta.teacher_id = ?
         AND ta.group_name = COALESCE(u.student_group, '')
        WHERE u.id = ? AND u.role = 'student'
        LIMIT 1
        """,
        (teacher_id, student_id),
    )
    return bool(cur.fetchone())


def get_teacher_student_discipline_ids(cur, teacher_id: int | None, student_id: int) -> list[int]:
    if not teacher_id:
        return []
    cur.execute(
        """
        SELECT DISTINCT ta.discipline_id
        FROM teaching_assignments ta
        JOIN users u ON ta.group_name = COALESCE(u.student_group, '')
        WHERE ta.teacher_id = ? AND u.id = ? AND u.role = 'student'
        ORDER BY ta.discipline_id
        """,
        (teacher_id, student_id),
    )
    return [int(row["discipline_id"]) for row in cur.fetchall()]


def student_can_access_test(cur, student_id: int, test_id: int) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM tests
        JOIN lectures ON lectures.id = tests.lecture_id
        JOIN users ON users.id = ?
        JOIN teaching_assignments ta
          ON ta.teacher_id = lectures.teacher_id
         AND ta.discipline_id = lectures.discipline_id
         AND ta.group_name = COALESCE(users.student_group, '')
        WHERE tests.id = ? AND tests.status = 'published' AND users.role = 'student'
        LIMIT 1
        """,
        (student_id, test_id),
    )
    return bool(cur.fetchone())


def ensure_catalog_groups(groups_map: dict[str, list[dict]], managed_groups: list[dict[str, Any]]) -> dict[str, list[dict]]:
    for g in managed_groups:
        name = (g.get("name") or "").strip()
        if not name:
            continue
        groups_map.setdefault(name, [])
    groups_map.setdefault("Без группы", groups_map.get("Без группы", []))
    return groups_map


def build_groups_page_context(cur, selected_group: str | None = None) -> dict[str, Any]:
    cur.execute("SELECT id, full_name, email FROM users WHERE role = 'teacher' ORDER BY full_name")
    teachers = [dict(r) for r in cur.fetchall()]
    managed_groups = fetch_managed_groups(cur)
    cur.execute("SELECT id, full_name, student_group FROM users WHERE role = 'student' ORDER BY full_name")
    assign_students = [dict(r) for r in cur.fetchall()]

    selected = None
    if selected_group:
        name = selected_group.strip()
        teachers_for_group = get_group_teachers(cur, name) if name != "Без группы" else []
        selected_teacher_ids = {int(item["id"]) for item in teachers_for_group}
        available_teachers = [teacher for teacher in teachers if int(teacher["id"]) not in selected_teacher_ids]

        if name == "Без группы":
            cur.execute(
                """
                SELECT id, role, full_name, email, last_login, assigned_teacher_id, student_group
                FROM users
                WHERE role = 'student' AND (student_group IS NULL OR student_group = '')
                ORDER BY full_name
                """
            )
        else:
            cur.execute(
                """
                SELECT id, role, full_name, email, last_login, assigned_teacher_id, student_group
                FROM users
                WHERE role = 'student' AND student_group = ?
                ORDER BY full_name
                """,
                (name,),
            )
        members = [user_row_to_dict(r) for r in cur.fetchall()]
        selected = {
            "name": name,
            "teachers": teachers_for_group,
            "available_teachers": available_teachers,
            "members": members,
        }

    return {
        "teachers": teachers,
        "managed_groups": managed_groups,
        "assign_students": assign_students,
        "selected_group": selected,
    }


def fetch_users_by_role(cur, role: str, query: str = "") -> list[dict[str, Any]]:
    q = (query or "").strip()
    if q:
        cur.execute(
            """
            SELECT id, role, full_name, email, last_login, assigned_teacher_id, student_group
            FROM users
            WHERE role = ?
              AND (
                full_name LIKE ?
                OR email LIKE ?
                OR COALESCE(student_group, '') LIKE ?
              )
            ORDER BY full_name
            """,
            (role, f"%{q}%", f"%{q}%", f"%{q}%"),
        )
    else:
        cur.execute(
            "SELECT id, role, full_name, email, last_login, assigned_teacher_id, student_group FROM users WHERE role = ? ORDER BY full_name",
            (role,),
        )
    return [user_row_to_dict(r) for r in cur.fetchall()]


def group_students_by_group(students: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for student in students:
        group_name = (student.get("student_group") or "").strip() or "Без группы"
        grouped.setdefault(group_name, []).append(student)

    for name in grouped.keys():
        grouped[name] = sorted(
            grouped[name],
            key=lambda student: ((student.get("full_name") or "").strip().lower(), int(student.get("id") or 0)),
        )

    ordered_names = sorted(
        [name for name in grouped.keys() if name != "Без группы"],
        key=natural_group_sort_key,
    )
    if "Без группы" in grouped:
        ordered_names.append("Без группы")

    return [
        {"name": name, "count": len(grouped[name]), "students": grouped[name]}
        for name in ordered_names
    ]


def _role_label(role: str) -> str:
    normalized = (role or "").strip().lower()
    if normalized == "student":
        return "Студент"
    if normalized == "teacher":
        return "Преподаватель"
    if normalized == "admin":
        return "Администратор"
    return normalized or "Пользователь"


def build_global_search_sections(cur, user: dict | None, query: str, limit_per_section: int = 8) -> list[dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []

    like = f"%{q}%"
    role = (user or {}).get("role")
    sections: list[dict[str, Any]] = []

    if role == "student":
        cur.execute(
            """
            SELECT DISTINCT t.id, t.title, l.title AS lecture_title, COALESCE(d.name, 'Без дисциплины') AS discipline_name
            FROM tests t
            JOIN lectures l ON l.id = t.lecture_id
            LEFT JOIN disciplines d ON d.id = l.discipline_id
            JOIN teaching_assignments ta
              ON ta.teacher_id = l.teacher_id
             AND ta.discipline_id = l.discipline_id
            WHERE t.status = 'published'
              AND ta.group_name = ?
              AND (t.title LIKE ? OR l.title LIKE ? OR COALESCE(d.name, '') LIKE ?)
            ORDER BY t.id DESC
            LIMIT ?
            """,
            (normalize_group_name(user.get("student_group")), like, like, like, limit_per_section),
        )
        tests_items = [
            {
                "title": row["title"],
                "meta": f"{row['lecture_title']} · {row['discipline_name']}",
                "href": f"/student/tests/{int(row['id'])}/entry",
            }
            for row in cur.fetchall()
        ]
        if tests_items:
            sections.append({"title": "Тесты", "items": tests_items})

        cur.execute(
            """
            SELECT DISTINCT d.id, d.name
            FROM teaching_assignments ta
            JOIN disciplines d ON d.id = ta.discipline_id
            WHERE ta.group_name = ? AND d.name LIKE ?
            ORDER BY d.name
            LIMIT ?
            """,
            (normalize_group_name(user.get("student_group")), like, limit_per_section),
        )
        discipline_items = [
            {"title": row["name"], "meta": "Дисциплина", "href": f"/student/tests?discipline_id={int(row['id'])}"}
            for row in cur.fetchall()
        ]
        if discipline_items:
            sections.append({"title": "Дисциплины", "items": discipline_items})

        return sections

    if role == "teacher":
        cur.execute(
            """
            SELECT l.id, l.title, COALESCE(d.name, 'Без дисциплины') AS discipline_name
            FROM lectures l
            LEFT JOIN disciplines d ON d.id = l.discipline_id
            WHERE l.teacher_id = ?
              AND (l.title LIKE ? OR l.body LIKE ? OR COALESCE(d.name, '') LIKE ?)
            ORDER BY l.id DESC
            LIMIT ?
            """,
            (user["id"], like, like, like, limit_per_section),
        )
        lecture_items = [
            {
                "title": row["title"],
                "meta": f"Лекция · {row['discipline_name']}",
                "href": f"/teacher/lectures/{int(row['id'])}",
            }
            for row in cur.fetchall()
        ]
        if lecture_items:
            sections.append({"title": "Лекции", "items": lecture_items})

        cur.execute(
            """
            SELECT t.id, t.title, t.status, l.title AS lecture_title
            FROM tests t
            JOIN lectures l ON l.id = t.lecture_id
            WHERE l.teacher_id = ?
              AND (t.title LIKE ? OR l.title LIKE ?)
            ORDER BY t.id DESC
            LIMIT ?
            """,
            (user["id"], like, like, limit_per_section),
        )
        test_items = [
            {
                "title": row["title"],
                "meta": f"Тест ({row['status']}) · {row['lecture_title']}",
                "href": f"/teacher/tests/{int(row['id'])}/edit",
            }
            for row in cur.fetchall()
        ]
        if test_items:
            sections.append({"title": "Тесты", "items": test_items})

        teacher_students = get_teacher_students(cur, int(user["id"]), q)
        student_items = [
            {
                "title": row["full_name"],
                "meta": f"Логин: {row['email']}" + (f" · {row['student_group']}" if row["student_group"] else ""),
                "href": f"/v2/teacher/students/{int(row['id'])}/edit",
            }
            for row in teacher_students[:limit_per_section]
        ]
        if student_items:
            sections.append({"title": "Студенты", "items": student_items})

        cur.execute(
            """
            SELECT d.id, d.name
            FROM teacher_disciplines td
            JOIN disciplines d ON d.id = td.discipline_id
            WHERE td.teacher_id = ? AND d.name LIKE ?
            ORDER BY d.name
            LIMIT ?
            """,
            (user["id"], like, limit_per_section),
        )
        discipline_items = [
            {"title": row["name"], "meta": "Дисциплина", "href": "/v2/teacher/disciplines"}
            for row in cur.fetchall()
        ]
        if discipline_items:
            sections.append({"title": "Дисциплины", "items": discipline_items})

        return sections

    if role == "admin":
        cur.execute(
            """
            SELECT l.id, l.title, COALESCE(d.name, 'Без дисциплины') AS discipline_name, u.full_name AS teacher_name
            FROM lectures l
            LEFT JOIN disciplines d ON d.id = l.discipline_id
            LEFT JOIN users u ON u.id = l.teacher_id
            WHERE l.title LIKE ? OR l.body LIKE ? OR COALESCE(d.name, '') LIKE ?
            ORDER BY l.id DESC
            LIMIT ?
            """,
            (like, like, like, limit_per_section),
        )
        lecture_items = [
            {
                "title": row["title"],
                "meta": f"Лекция · {row['discipline_name']}" + (f" · {row['teacher_name']}" if row["teacher_name"] else ""),
                "href": f"/teacher/lectures/{int(row['id'])}",
            }
            for row in cur.fetchall()
        ]
        if lecture_items:
            sections.append({"title": "Лекции", "items": lecture_items})

        cur.execute(
            """
            SELECT t.id, t.title, t.status, l.title AS lecture_title
            FROM tests t
            JOIN lectures l ON l.id = t.lecture_id
            WHERE t.title LIKE ? OR l.title LIKE ?
            ORDER BY t.id DESC
            LIMIT ?
            """,
            (like, like, limit_per_section),
        )
        test_items = [
            {
                "title": row["title"],
                "meta": f"Тест ({row['status']}) · {row['lecture_title']}",
                "href": f"/teacher/tests/{int(row['id'])}/edit",
            }
            for row in cur.fetchall()
        ]
        if test_items:
            sections.append({"title": "Тесты", "items": test_items})

        cur.execute(
            """
            SELECT id, role, full_name, email, COALESCE(student_group, '') AS student_group
            FROM users
            WHERE full_name LIKE ? OR email LIKE ? OR COALESCE(student_group, '') LIKE ?
            ORDER BY full_name
            LIMIT ?
            """,
            (like, like, like, limit_per_section),
        )
        user_items = [
            {
                "title": row["full_name"],
                "meta": f"{_role_label(row['role'])} · Логин: {row['email']}"
                + (f" · {row['student_group']}" if row["student_group"] else ""),
                "href": f"/admin/users/{int(row['id'])}/edit",
            }
            for row in cur.fetchall()
        ]
        if user_items:
            sections.append({"title": "Пользователи", "items": user_items})

        cur.execute(
            "SELECT id, name FROM disciplines WHERE name LIKE ? ORDER BY name LIMIT ?",
            (like, limit_per_section),
        )
        discipline_items = [
            {"title": row["name"], "meta": "Дисциплина", "href": f"/admin/disciplines/{int(row['id'])}"}
            for row in cur.fetchall()
        ]
        if discipline_items:
            sections.append({"title": "Дисциплины", "items": discipline_items})

        cur.execute(
            "SELECT name FROM groups WHERE name LIKE ? ORDER BY name LIMIT ?",
            (like, limit_per_section),
        )
        group_items = [
            {
                "title": row["name"],
                "meta": "Группа",
                "href": f"/admin/groups/{str(row['name']).replace(' ', '_')}",
            }
            for row in cur.fetchall()
        ]
        if group_items:
            sections.append({"title": "Группы", "items": group_items})

        return sections

    quick_items = [
        {"title": "Вход", "meta": "Авторизация в системе", "href": "/login"},
        {"title": "Регистрация студента", "meta": "Создать учетную запись студента", "href": "/register"},
    ]
    return [{"title": "Быстрые ссылки", "items": quick_items}]

BASE_DIR = Path(__file__).resolve().parent

if load_dotenv:
    load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _session_cookie_settings() -> tuple[bool, str, int]:
    # Default to Lax for stable first-party auth and safer cross-browser behaviour.
    # Allow explicit override through env when an embedding/webview scenario needs it.
    running_on_render = bool((os.getenv("RENDER") or "").strip())
    secure_cookie = _env_bool("SESSION_COOKIE_SECURE", running_on_render)

    same_site = (os.getenv("SESSION_COOKIE_SAMESITE") or "").strip().lower()
    if same_site not in {"lax", "strict", "none"}:
        same_site = "lax"

    if same_site == "none":
        secure_cookie = True

    try:
        max_age = int((os.getenv("SESSION_COOKIE_MAX_AGE") or "").strip() or str(60 * 60 * 24 * 30))
    except Exception:
        max_age = 60 * 60 * 24 * 30

    return secure_cookie, same_site, max_age


app = FastAPI(
    title="КампусПлюс СГУГиТ — API",
    description=(
        "REST JSON API платформы мониторинга успеваемости студентов.\n\n"
        "Все эндпоинты `/api/*` возвращают JSON и отображаются в Swagger.\n"
        "HTML-маршруты (фронтенд) скрыты из документации."
    ),
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)
_session_secret = os.environ.get("SESSION_SECRET_KEY") or os.environ.get("SECRET_KEY") or ""
if not _session_secret:
    import warnings
    _session_secret = "dev-secret-change-me"
    warnings.warn(
        "SESSION_SECRET_KEY not set — using insecure default. "
        "Set SESSION_SECRET_KEY env var in production!",
        stacklevel=1,
    )
    if os.environ.get("RENDER") or os.environ.get("DATABASE_URL"):
        raise RuntimeError(
            "FATAL: SESSION_SECRET_KEY must be set in production. "
            "Generate one with: python -c 'import secrets; print(secrets.token_hex(32))'"
        )
_session_cookie_secure, _session_cookie_samesite, _session_cookie_max_age = _session_cookie_settings()
_session_cookie_name = "campusplus_session"
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret,
    session_cookie=_session_cookie_name,
    same_site=_session_cookie_samesite,
    https_only=_session_cookie_secure,
    max_age=_session_cookie_max_age,
)

# ── JSON API (виден в /docs) ────────────────────────────────────
from app.api import router as api_router  # noqa: E402
app.include_router(api_router)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")
app.mount("/presentation_assets", StaticFiles(directory=str(BASE_DIR / "presentation_assets")), name="presentation_assets")
app.mount("/mobile_screens", StaticFiles(directory=str(BASE_DIR / "mobile_screens")), name="mobile_screens")
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(FAVICON_PATH, media_type="image/png")


def _coerce_session_version(value: Any, default: int = 1) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return parsed if parsed > 0 else default


def get_current_user(request: Request) -> dict | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    session_version = _coerce_session_version(request.session.get("session_version"), default=0)
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        request.session.clear()
        return None

    db_session_version = _coerce_session_version(row["session_version"])
    if session_version != db_session_version:
        request.session.clear()
        return None

    return dict(row)


def establish_user_session(
    request: Request,
    *,
    user_id: int,
    email: str,
    role: str,
    session_version: int = 1,
) -> None:
    # Always reset the signed session when switching identities.
    request.session.clear()
    request.session["user_id"] = int(user_id)
    request.session["user_email"] = email
    request.session["session_version"] = _coerce_session_version(session_version)
    if role == "admin":
        request.session["admin_authenticated"] = True
        request.session["admin_email"] = email


TEMP_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!_-"
PASSWORD_CHANGE_ALLOWED_PATHS = {
    "/dashboard",
    "/dashboard/profile/password",
    "/login",
    "/logout",
    "/v1/admin/logout",
    "/v2/admin",
    "/v2/admin/login",
    "/favicon.ico",
}


def user_must_change_password(user: dict | Any | None) -> bool:
    if not user:
        return False
    if hasattr(user, "keys"):
        try:
            value = user["must_change_password"]
        except Exception:
            value = None
    else:
        value = user.get("must_change_password") if hasattr(user, "get") else None
    try:
        return bool(int(value or 0))
    except Exception:
        return bool(value)


def generate_temporary_password(length: int = 12) -> str:
    while True:
        password = "".join(secrets.choice(TEMP_PASSWORD_ALPHABET) for _ in range(length))
        ok, _ = validate_password(password)
        if ok:
            return password


def set_user_password(cur, user_id: int, password: str, *, force_change: bool = False) -> int:
    salt = new_salt()
    password_hash = hash_password(password, salt)
    cur.execute(
        """
        UPDATE users
        SET password_hash = ?,
            salt = ?,
            must_change_password = ?,
            session_version = COALESCE(session_version, 1) + 1
        WHERE id = ?
        """,
        (password_hash, salt, 1 if force_change else 0, user_id),
    )
    cur.execute("SELECT session_version FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    if row:
        return _coerce_session_version(row["session_version"])
    return 1


def require_user(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise RedirectResponse("/login", status_code=302)
    return user


def render(request: Request, name: str, context: dict[str, Any]) -> HTMLResponse:
    base = {"request": request, "current_user": get_current_user(request)}
    # pop flashes from session and pass to template
    flashes = request.session.pop("flashes", []) if request.session.get("flashes") else []
    base["flashes"] = flashes
    # CSRF-токен для всех шаблонов
    base["csrf_token"] = generate_csrf_token(request.session)
    base["global_search_query"] = (request.query_params.get("q") or "").strip()
    base.update(context)
    return templates.TemplateResponse(request, name, base)


VKR_PUBLIC_BASE_URL = "https://campusplus.sgugit.ru"
VKR_MANUAL_TEST_CASES: list[dict[str, Any]] = [
    {
        "id": "TC-01",
        "title": "Авторизация преподавателя",
        "role": "Преподаватель",
        "goal": "Проверить успешный вход преподавателя в рабочий контур.",
        "steps": [
            "Открыть страницу авторизации сервиса.",
            "Ввести корректный логин и пароль преподавателя.",
            "Нажать кнопку «Войти».",
        ],
        "expected": "Пользователь авторизуется и попадает в панель преподавателя без ошибок доступа.",
    },
    {
        "id": "TC-02",
        "title": "Создание дисциплины преподавателем",
        "role": "Преподаватель",
        "goal": "Проверить создание новой дисциплины из интерфейса преподавателя.",
        "steps": [
            "Перейти в раздел «Дисциплины».",
            "Ввести название новой дисциплины.",
            "Нажать кнопку добавления дисциплины.",
        ],
        "expected": "Новая дисциплина отображается в списке дисциплин преподавателя.",
    },
    {
        "id": "TC-03",
        "title": "Создание лекции и публикация теста",
        "role": "Преподаватель",
        "goal": "Проверить создание лекции, теста и публикацию доступа по QR-коду.",
        "steps": [
            "Открыть раздел «Лекции и тесты» выбранной дисциплины.",
            "Создать лекцию и сохранить материал.",
            "Создать тест и опубликовать его.",
        ],
        "expected": "Тест отображается в списке опубликованных и доступен для прохождения студентом.",
    },
    {
        "id": "TC-04",
        "title": "Регистрация студента",
        "role": "Студент",
        "goal": "Проверить регистрацию нового студента с выбором группы.",
        "steps": [
            "Открыть страницу регистрации.",
            "Заполнить ФИО, логин, пароль и выбрать группу.",
            "Подтвердить регистрацию.",
        ],
        "expected": "Студент создаётся в системе и может выполнить вход под своими учётными данными.",
    },
    {
        "id": "TC-05",
        "title": "Прохождение теста студентом",
        "role": "Студент",
        "goal": "Проверить успешное прохождение опубликованного теста.",
        "steps": [
            "Открыть тест по QR-коду или из списка доступных тестов.",
            "Ответить на вопросы теста.",
            "Нажать кнопку отправки результата.",
        ],
        "expected": "Результат фиксируется, студенту отображается набранный балл.",
    },
    {
        "id": "TC-06",
        "title": "Просмотр аналитики студентом",
        "role": "Студент",
        "goal": "Проверить отображение истории тестирования и точек роста.",
        "steps": [
            "Открыть личный кабинет студента.",
            "Перейти в раздел аналитики после прохождения теста.",
        ],
        "expected": "Отображаются история попыток, баллы и темы для повторения.",
    },
    {
        "id": "TC-07",
        "title": "Мониторинг успеваемости преподавателем",
        "role": "Преподаватель",
        "goal": "Проверить появление студента и его результата в аналитике преподавателя.",
        "steps": [
            "Открыть раздел «Студенты» после прохождения теста студентом.",
            "Перейти в раздел «Успеваемость».",
            "Выбрать нужную дисциплину или просмотр всех дисциплин.",
        ],
        "expected": "Студент отображается в списке и в преподавательской аналитике с корректным баллом.",
    },
    {
        "id": "TC-08",
        "title": "Сброс пароля администратором",
        "role": "Администратор",
        "goal": "Проверить выдачу временного пароля пользователю.",
        "steps": [
            "Открыть карточку пользователя в административном интерфейсе.",
            "Нажать кнопку сброса пароля.",
            "Передать временный пароль пользователю и выполнить повторный вход.",
        ],
        "expected": "Пользователь входит по временному паролю и принудительно меняет его в личном кабинете.",
    },
    {
        "id": "TC-09",
        "title": "AI-генерация теста по лекции",
        "role": "Преподаватель",
        "goal": "Проверить корректность автоматической генерации тестовых вопросов на основе текста лекции.",
        "steps": [
            "Открыть раздел лекций выбранной дисциплины.",
            "Выбрать лекцию с достаточным объёмом текста.",
            "Нажать «Сгенерировать тест» и дождаться результата.",
        ],
        "expected": "Система формирует набор вопросов, каждый содержит текст, варианты ответов и отмеченный правильный ответ.",
    },
    {
        "id": "TC-10",
        "title": "Ручное создание теста",
        "role": "Преподаватель",
        "goal": "Проверить создание теста через ручной конструктор вопросов.",
        "steps": [
            "Перейти к созданию теста вручную для выбранной лекции.",
            "Добавить несколько вопросов с вариантами ответов.",
            "Указать правильные ответы и сохранить тест.",
        ],
        "expected": "Тест сохраняется с корректными вопросами и вариантами, доступен для публикации.",
    },
    {
        "id": "TC-11",
        "title": "Редактирование вопросов теста",
        "role": "Преподаватель",
        "goal": "Проверить возможность редактирования вопросов после создания теста.",
        "steps": [
            "Открыть ранее созданный тест.",
            "Изменить текст вопроса и варианты ответов.",
            "Сохранить изменения.",
        ],
        "expected": "Обновлённые вопросы корректно сохраняются и отображаются при повторном открытии теста.",
    },
    {
        "id": "TC-12",
        "title": "QR-код доступа к тесту",
        "role": "Преподаватель",
        "goal": "Проверить генерацию QR-кода для опубликованного теста.",
        "steps": [
            "Опубликовать тест.",
            "Перейти на страницу QR-кода теста.",
            "Отсканировать QR-код с мобильного устройства.",
        ],
        "expected": "QR-код ведёт на страницу прохождения опубликованного теста без дополнительной авторизации.",
    },
    {
        "id": "TC-13",
        "title": "Просмотр точек роста студентом",
        "role": "Студент",
        "goal": "Проверить отображение слабых тем и рекомендаций после прохождения тестов.",
        "steps": [
            "Пройти несколько тестов по разным лекциям.",
            "Перейти в раздел «Точки роста» в личном кабинете.",
        ],
        "expected": "Система отображает темы, по которым допущены ошибки, и рекомендации для повторения.",
    },
    {
        "id": "TC-14",
        "title": "Управление группами администратором",
        "role": "Администратор",
        "goal": "Проверить создание, редактирование и удаление учебных групп.",
        "steps": [
            "Открыть раздел «Группы» в административной панели.",
            "Создать новую группу с уникальным названием.",
            "Проверить отображение группы в списке.",
        ],
        "expected": "Группа создаётся, отображается в списке и доступна для назначения преподавателей.",
    },
    {
        "id": "TC-15",
        "title": "Назначение дисциплины группе",
        "role": "Преподаватель",
        "goal": "Проверить привязку дисциплины к учебной группе.",
        "steps": [
            "Открыть раздел «Дисциплины» в панели преподавателя.",
            "Выбрать дисциплину и назначить её группе.",
            "Проверить отображение назначения в интерфейсе.",
        ],
        "expected": "Группа привязана к дисциплине, студенты группы получают доступ к тестам дисциплины.",
    },
    {
        "id": "TC-16",
        "title": "Ролевое ограничение доступа",
        "role": "Студент",
        "goal": "Проверить, что студент не может получить доступ к преподавательским и административным страницам.",
        "steps": [
            "Авторизоваться как студент.",
            "Попытаться открыть URL панели преподавателя.",
            "Попытаться открыть URL административной панели.",
        ],
        "expected": "Сервис перенаправляет пользователя на страницу входа или отображает сообщение об ошибке доступа.",
    },
    {
        "id": "TC-17",
        "title": "Повторное прохождение теста",
        "role": "Студент",
        "goal": "Проверить корректность фиксации нескольких попыток прохождения одного теста.",
        "steps": [
            "Пройти опубликованный тест.",
            "Вернуться к тесту и пройти его повторно.",
            "Проверить историю попыток в аналитике.",
        ],
        "expected": "Каждая попытка фиксируется отдельно, аналитика отображает все результаты.",
    },
    {
        "id": "TC-18",
        "title": "Поиск лекций по ключевому слову",
        "role": "Преподаватель",
        "goal": "Проверить работу поиска по заголовкам и содержимому лекций.",
        "steps": [
            "Перейти в раздел поиска.",
            "Ввести ключевое слово, содержащееся в одной из лекций.",
            "Проверить результаты поиска.",
        ],
        "expected": "Система находит и отображает лекции, содержащие искомое ключевое слово.",
    },
    {
        "id": "TC-19",
        "title": "Смена пароля пользователем",
        "role": "Студент",
        "goal": "Проверить самостоятельную смену пароля через личный кабинет.",
        "steps": [
            "Авторизоваться в системе.",
            "Открыть настройки профиля.",
            "Ввести текущий и новый пароль, подтвердить изменение.",
        ],
        "expected": "Пароль изменяется, следующий вход выполняется по новому паролю.",
    },
    {
        "id": "TC-20",
        "title": "Привязка преподавателя к группе",
        "role": "Администратор",
        "goal": "Проверить назначение преподавателя куратором учебной группы.",
        "steps": [
            "Открыть карточку группы в административной панели.",
            "Назначить преподавателя из списка пользователей.",
            "Проверить, что преподаватель видит группу в своём интерфейсе.",
        ],
        "expected": "Преподаватель получает доступ к группе и может работать с её студентами.",
    },
]
VKR_EVIDENCE_PAGES: dict[str, dict[str, Any]] = {
    "aprobation": {
        "slug": "aprobation",
        "route": "/vkr/aprobation",
        "title": "Апробация проекта",
        "kicker": "ВКР · Апробация",
        "portal_title": "Апробация",
        "portal_description": "Скриншоты испытаний и материалы по апробации проекта.",
        "heading": "Материалы апробации проекта",
        "description": "Раздел содержит подтверждающие материалы по проведению апробации веб-сервиса.",
        "file_url": "/static/vkr/aprobation-sheet.svg",
        "file_label": "Открыть материал апробации",
    },
    "implementation-act": {
        "slug": "implementation-act",
        "route": "/vkr/implementation-act",
        "title": "Акт внедрения",
        "kicker": "ВКР · Акт внедрения",
        "portal_title": "Акт внедрения",
        "portal_description": "Акт внедрения и сопроводительные документы.",
        "heading": "Акт внедрения",
        "description": "По кнопке открывается файл акта внедрения. В дальнейшем сюда подставляется реальный скан документа.",
        "file_url": "/static/vkr/implementation-act-sheet.svg",
        "file_label": "Открыть акт внедрения",
    },
    "results": {
        "slug": "results",
        "route": "/vkr/results",
        "title": "Результаты внедрения",
        "kicker": "ВКР · Результаты внедрения",
        "portal_title": "Результаты внедрения",
        "portal_description": "Итоговые скриншоты и материалы по результатам внедрения.",
        "heading": "Результаты внедрения",
        "description": "Раздел предназначен для публикации итоговых материалов по эксплуатации сервиса.",
        "file_url": "/static/vkr/results-sheet.svg",
        "file_label": "Открыть результаты внедрения",
    },
    "testing": {
        "slug": "testing",
        "route": "/vkr/testing",
        "title": "Тестирование",
        "kicker": "ВКР · Тестирование",
        "portal_title": "Тестирование",
        "portal_description": "Ручные тест-кейсы по ключевым пользовательским сценариям.",
        "heading": "Ручные тест-кейсы",
        "description": "На странице собраны основные сценарии ручного тестирования веб-сервиса.",
    },
    "specification": {
        "slug": "specification",
        "route": "/vkr/specification",
        "title": "Техническое задание",
        "kicker": "ВКР · Техническое задание",
        "portal_title": "Техническое задание",
        "portal_description": "Техническое задание и исходные требования к системе.",
        "heading": "Техническое задание",
        "description": "По кнопке открывается файл технического задания. Позже сюда подставляется утверждённый документ.",
        "file_url": "/static/vkr/specification-sheet.svg",
        "file_label": "Открыть техническое задание",
    },
    "certificates": {
        "slug": "certificates",
        "route": "/vkr/certificates",
        "title": "Сертификаты и приложения",
        "kicker": "ВКР · Сертификаты",
        "portal_title": "Сертификаты",
        "portal_description": "Сертификаты, приложения и сопровождающие материалы.",
        "heading": "Сертификаты и приложения",
        "description": "По кнопке открывается файл с приложениями и подтверждающими материалами.",
        "file_url": "/static/vkr/certificates-sheet.svg",
        "file_label": "Открыть сертификаты и приложения",
    },
}


def _render_vkr_evidence_page(request: Request, slug: str) -> HTMLResponse:
    page = VKR_EVIDENCE_PAGES[slug]
    public_url = f"{VKR_PUBLIC_BASE_URL}{page['route']}"
    return render(
        request,
        "vkr_evidence_page.html",
        {
            "title": page["title"],
            "is_index": False,
            "page": page,
            "public_url": public_url,
            "manual_cases": VKR_MANUAL_TEST_CASES if slug == "testing" else [],
        },
    )


def _build_vkr_portal_sections() -> list[dict[str, str]]:
    order = ["aprobation", "testing", "specification", "implementation-act", "results", "certificates"]
    sections: list[dict[str, str]] = []
    for slug in order:
        page = VKR_EVIDENCE_PAGES[slug]
        sections.append(
            {
                "title": page["portal_title"],
                "description": page["portal_description"],
                "route": page["route"],
                "kicker": page["kicker"],
                "file_url": page.get("file_url", ""),
                "file_label": page.get("file_label", ""),
                "manual_cases": VKR_MANUAL_TEST_CASES if slug == "testing" else [],
            }
        )
    return sections


def _merge_vary_cookie(headers) -> None:
    current = headers.get("Vary", "")
    values = [part.strip() for part in current.split(",") if part.strip()]
    if "Cookie" not in values:
        values.append("Cookie")
    headers["Vary"] = ", ".join(values)


@app.middleware("http")
async def disable_cache_for_dynamic_pages(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path or "/"
    if path.startswith("/static"):
        return response

    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    _merge_vary_cookie(response.headers)
    return response


# ── CSRF-верификация (вспомогательная функция для эндпоинтов) ────
def _check_csrf(session: dict, form_data) -> bool:
    """Проверяет CSRF-токен из формы. Вызывается в POST-эндпоинтах вручную."""
    token = form_data.get(CSRF_FIELD_NAME)
    if not token:
        return False
    return verify_csrf_token(session, token)


def add_flash(request: Request, message: str, level: str = "info") -> None:
    fs = request.session.get("flashes") or []
    fs.append({"message": message, "level": level})
    request.session["flashes"] = fs


def add_flash_once(request: Request, message: str, level: str = "info") -> None:
    fs = request.session.get("flashes") or []
    if any(item.get("message") == message and item.get("level") == level for item in fs):
        return
    fs.append({"message": message, "level": level})
    request.session["flashes"] = fs


class ForcePasswordChangeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path or "/"
        if path.startswith("/static") or path in PASSWORD_CHANGE_ALLOWED_PATHS:
            return await call_next(request)

        raw_cookie = request.cookies.get(_session_cookie_name)
        user_id = None
        session_version = 0
        if raw_cookie:
            signer = itsdangerous.TimestampSigner(str(_session_secret))
            try:
                unsigned = signer.unsign(raw_cookie.encode("utf-8"), max_age=_session_cookie_max_age)
                session_data = json.loads(b64decode(unsigned))
                user_id = int(session_data.get("user_id") or 0) or None
                session_version = _coerce_session_version(session_data.get("session_version"), default=0)
            except (BadSignature, ValueError, TypeError, json.JSONDecodeError):
                user_id = None
        if not user_id:
            return await call_next(request)

        conn = connect()
        cur = conn.cursor()
        cur.execute("SELECT must_change_password, session_version FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        conn.close()

        raw_value = None
        db_session_version = 0
        if row:
            if hasattr(row, "keys"):
                raw_value = row["must_change_password"]
                db_session_version = _coerce_session_version(row["session_version"], default=0)
            else:
                raw_value = row[0]
                db_session_version = _coerce_session_version(row[1], default=0)

        if not row or session_version != db_session_version:
            response = RedirectResponse("/login", status_code=302)
            response.delete_cookie(_session_cookie_name, path="/")
            return response

        try:
            must_change = bool(int(raw_value or 0))
        except Exception:
            must_change = bool(raw_value)

        if must_change:
            return RedirectResponse("/dashboard#profile-settings", status_code=302)

        return await call_next(request)


app.add_middleware(ForcePasswordChangeMiddleware)


def _safe_next_path(raw: str | None, default: str = "/dashboard") -> str:
    value = (raw or "").strip()
    if not value:
        return default
    # Allow only in-site absolute paths and block scheme-relative redirects.
    if not value.startswith("/") or value.startswith("//"):
        return default
    return value


def audit_log(request: Request | None, action: str, target_user_id: int | None = None, details: str | None = None) -> None:
    try:
        conn = connect()
        cur = conn.cursor()
        actor_email = None
        actor_role = None
        if request:
            # prefer admin session email if present
            actor_email = request.session.get("admin_email") or request.session.get("user_email")
            # get current_user role if available
            cu = get_current_user(request)
            if cu:
                actor_role = cu.get("role")
        cur.execute(
            "INSERT INTO audit (actor_email, actor_role, action, target_user_id, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (actor_email, actor_role, action, target_user_id, details, datetime.utcnow().isoformat()),
        )
        conn.commit()
    except Exception:
        # do not fail main flow on logging errors
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def make_sparkline(points: list[float], width: int = 260, height: int = 60) -> str:
    if not points:
        return ""
    min_v = min(points)
    max_v = max(points)
    span = max_v - min_v if max_v != min_v else 1.0
    step_x = width / max(1, len(points) - 1)
    coords = []
    for i, v in enumerate(points):
        x = i * step_x
        y = height - ((v - min_v) / span) * height
        coords.append(f"{x:.1f},{y:.1f}")
    return " ".join(coords)


def ensure_bootstrap_admin() -> None:
    bootstrap_login = (os.getenv("BOOTSTRAP_ADMIN_LOGIN") or os.getenv("BOOTSTRAP_ADMIN_EMAIL") or "").strip()
    bootstrap_password = (os.getenv("BOOTSTRAP_ADMIN_PASSWORD") or "").strip()
    bootstrap_full_name = (os.getenv("BOOTSTRAP_ADMIN_FULL_NAME") or "Администратор").strip() or "Администратор"

    if not bootstrap_login or not bootstrap_password:
        return

    clean_login = validate_login(bootstrap_login)
    if not clean_login:
        return

    pw_ok, _ = validate_password(bootstrap_password)
    if not pw_ok:
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE email = ?", (clean_login,))
    existing = cur.fetchone()
    if existing:
        cur.execute(
            "UPDATE users SET role = 'admin', full_name = ? WHERE id = ?",
            (bootstrap_full_name, existing["id"]),
        )
    else:
        salt = new_salt()
        cur.execute(
            "INSERT INTO users (role, full_name, email, password_hash, salt) VALUES (?, ?, ?, ?, ?)",
            (
                "admin",
                bootstrap_full_name,
                clean_login,
                hash_password(bootstrap_password, salt),
                salt,
            ),
        )
    conn.commit()
    conn.close()


@app.on_event("startup")
def _startup() -> None:
    init_db()
    ensure_bootstrap_admin()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    search_query = (request.query_params.get("q") or "").strip()
    if search_query:
        return RedirectResponse(f"/search?q={quote_plus(search_query)}", status_code=302)

    chart_data = {
        "is_real": False,
        "line_points": "20,142 80,126 138,132 192,108 246,114 300,82 356,66 400,44",
        "area_path": "M20 142 L80 126 L138 132 L192 108 L246 114 L300 82 L356 66 L400 44 L400 150 L20 150 Z",
        "final_x": 400,
        "final_y": 44,
        "avg": "82%",
        "activity": "98",
        "trend": "↑ вверх",
        "trend_class": "metric-up",
        "chip": "+24%",
    }

    current = get_current_user(request)
    if current and current.get("role") == "student":
        chart_data = {
            "is_real": True,
            "line_points": "20,140 146,140 273,140 400,140",
            "area_path": "M20 140 L146 140 L273 140 L400 140 L400 150 L20 150 Z",
            "final_x": 400,
            "final_y": 140,
            "avg": "0%",
            "activity": "0",
            "trend": "→ нет данных",
            "trend_class": "metric-neutral",
            "chip": "0%",
        }

        conn = connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT score
            FROM attempts
            WHERE student_id = ?
            ORDER BY taken_at DESC
            LIMIT 8
            """,
            (current["id"],),
        )
        raw_scores = [float(r["score"]) for r in cur.fetchall()]
        cur.execute("SELECT COUNT(*) AS cnt FROM attempts WHERE student_id = ?", (current["id"],))
        total_attempts = int(cur.fetchone()["cnt"])
        conn.close()

        if raw_scores:
            scores = list(reversed(raw_scores))
            min_v = min(scores)
            max_v = max(scores)
            span = (max_v - min_v) if max_v != min_v else 1.0

            left, right = 20.0, 400.0
            top, bottom = 44.0, 142.0
            width = right - left
            height = bottom - top
            step_x = width / max(1, len(scores) - 1)

            points: list[tuple[float, float]] = []
            for i, value in enumerate(scores):
                x = left + (i * step_x)
                y = bottom - (((value - min_v) / span) * height)
                points.append((x, y))

            line_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
            area_path = (
                "M"
                + " L".join(f"{x:.1f} {y:.1f}" for x, y in points)
                + f" L{right:.1f} 150 L{left:.1f} 150 Z"
            )

            avg_score = round(sum(scores) / len(scores), 1)
            trend_value = 0.0
            if len(scores) >= 2:
                trend_value = round(scores[-1] - scores[-2], 1)

            if trend_value > 0:
                trend_text = f"↑ +{trend_value}%"
                trend_class = "metric-up"
                chip = f"+{trend_value}%"
            elif trend_value < 0:
                trend_text = f"↓ {trend_value}%"
                trend_class = "metric-down"
                chip = f"{trend_value}%"
            else:
                trend_text = "→ без изменений"
                trend_class = "metric-neutral"
                chip = "0%"

            chart_data = {
                "is_real": True,
                "line_points": line_points,
                "area_path": area_path,
                "final_x": round(points[-1][0], 1),
                "final_y": round(points[-1][1], 1),
                "avg": f"{avg_score}%",
                "activity": str(total_attempts),
                "trend": trend_text,
                "trend_class": trend_class,
                "chip": chip,
            }

    # mark this render as the index so templates can hide certain nav items
    return render(
        request,
        "index.html",
        {"is_index": True, "title": "КампусПлюс СГУГиТ", "main_chart": chart_data},
    )


@app.get("/vkr", response_class=HTMLResponse)
def vkr_portal_page(request: Request):
    return render(
        request,
        "vkr_portal.html",
        {
            "title": "Материалы ВКР",
            "is_index": False,
            "public_url": f"{VKR_PUBLIC_BASE_URL}/vkr",
            "sections": _build_vkr_portal_sections(),
        },
    )


@app.get("/presentation", response_class=HTMLResponse)
def presentation_page():
    pres_path = Path(__file__).parent / "campusplus_presentation_vkr_2026.html"
    html = pres_path.read_text(encoding="utf-8")
    html = html.replace('src="app/static/', 'src="/static/')
    html = html.replace('src="presentation_assets/', 'src="/presentation_assets/')
    html = html.replace('src="mobile_screens/', 'src="/mobile_screens/')
    return HTMLResponse(html)


@app.get("/vkr/aprobation", response_class=HTMLResponse)
def vkr_aprobation_page(request: Request):
    return _render_vkr_evidence_page(request, "aprobation")


@app.get("/vkr/testing", response_class=HTMLResponse)
def vkr_testing_page(request: Request):
    return _render_vkr_evidence_page(request, "testing")


@app.get("/vkr/specification", response_class=HTMLResponse)
def vkr_specification_page(request: Request):
    return _render_vkr_evidence_page(request, "specification")


@app.get("/vkr/certificates", response_class=HTMLResponse)
def vkr_certificates_page(request: Request):
    return _render_vkr_evidence_page(request, "certificates")


@app.get("/vkr/implementation-act", response_class=HTMLResponse)
def vkr_implementation_act_page(request: Request):
    return _render_vkr_evidence_page(request, "implementation-act")


@app.get("/vkr/results", response_class=HTMLResponse)
def vkr_results_page(request: Request):
    return _render_vkr_evidence_page(request, "results")


@app.get("/search", response_class=HTMLResponse)
def global_search(request: Request):
    query = (request.query_params.get("q") or "").strip()
    sections: list[dict[str, Any]] = []
    if query:
        conn = connect()
        cur = conn.cursor()
        sections = build_global_search_sections(cur, get_current_user(request), query)
        conn.close()

    result_total = sum(len(section.get("items", [])) for section in sections)
    return render(
        request,
        "search_results.html",
        {
            "title": "Поиск",
            "query": query,
            "sections": sections,
            "result_total": result_total,
        },
    )


@app.get("/register", response_class=HTMLResponse)
def register_form(request: Request):
    next_path = _safe_next_path(request.query_params.get("next", ""), default="")
    return _render_register_form(request, next_path=next_path)


def _load_group_names() -> list[str]:
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT name FROM groups ORDER BY name")
    groups = [row["name"] for row in cur.fetchall() if row["name"]]
    conn.close()
    return groups


def _render_register_form(
    request: Request,
    error: str | None = None,
    form_data: dict[str, str] | None = None,
    next_path: str = "",
) -> HTMLResponse:
    return render(
        request,
        "register.html",
        {
            "error": error,
            "available_groups": _load_group_names(),
            "form_data": form_data or {},
            "next": next_path,
        },
    )


@app.post("/register")
def register(
    request: Request,
    role: str = Form("student"),
    full_name: str = Form(...),
    login: str = Form(""),
    email: str = Form(""),
    password: str = Form(...),
    student_group: str = Form(""),
    next: str = Form(""),
):
    next_path = _safe_next_path(next, default="")
    role = (role or "student").strip().lower()
    form_state = {
        "full_name": (full_name or "").strip(),
        "login": (login or email or "").strip(),
        "student_group": (student_group or "").strip(),
    }
    if role != "student":
        return _render_register_form(
            request,
            "Самостоятельная регистрация доступна только студентам.",
            form_state,
            next_path=next_path,
        )

    clean_login = validate_login(form_state["login"])
    if not clean_login:
        return _render_register_form(
            request,
            "Некорректный логин. Используйте 3-80 символов без пробелов.",
            form_state,
            next_path=next_path,
        )

    # --- Валидация пароля ---
    pw_ok, pw_err = validate_password(password)
    if not pw_ok:
        return _render_register_form(request, pw_err, form_state, next_path=next_path)

    # --- Санитизация имени ---
    clean_name = sanitize_full_name(full_name)
    if not clean_name:
        return _render_register_form(request, "Укажите ФИО.", form_state, next_path=next_path)

    normalized_group = (student_group or "").strip()
    if not normalized_group:
        return _render_register_form(
            request,
            "Для студента необходимо выбрать группу.",
            form_state,
            next_path=next_path,
        )

    salt = new_salt()
    password_hash = hash_password(password, salt)
    conn = connect()
    cur = conn.cursor()
    assigned_teacher_id = find_group_teacher_id(cur, normalized_group)
    try:
        cur.execute(
            "INSERT INTO users (role, full_name, email, password_hash, salt, student_group, assigned_teacher_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "student",
                clean_name,
                clean_login,
                password_hash,
                salt,
                normalized_group,
                assigned_teacher_id,
            ),
        )
        user_id = int(cur.lastrowid)
        conn.commit()
    except Exception:
        conn.close()
        return _render_register_form(request, "Логин уже используется.", form_state, next_path=next_path)
    conn.close()
    establish_user_session(request, user_id=user_id, email=clean_login, role="student", session_version=1)
    add_flash(request, "Регистрация успешна.", "success")
    if next_path:
        return RedirectResponse(next_path, status_code=302)
    return RedirectResponse("/dashboard", status_code=302)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    next_path = _safe_next_path(request.query_params.get("next", ""), default="")
    login_value = request.query_params.get("login", "")
    return render(request, "login.html", {"error": None, "next": next_path, "login": login_value})


@app.post("/login")
def login(
    request: Request,
    login: str = Form(""),
    email: str = Form(""),
    password: str = Form(...),
    next: str = Form(""),
):
    next_path = _safe_next_path(next, default="")
    # normalize login
    raw_login = (login or email or "").strip()
    clean_login = validate_login(raw_login)
    if not clean_login:
        return render(
            request,
            "login.html",
            {"error": "Укажите корректный логин.", "next": next_path, "login": raw_login},
        )

    # --- Rate-limit по IP ---
    client_ip = request.client.host if request.client else "unknown"
    if login_limiter.is_blocked(client_ip):
        wait = login_limiter.remaining_seconds(client_ip)
        return render(request, "login.html", {
            "error": f"Слишком много попыток входа. Подождите {wait} сек.",
            "next": next_path,
            "login": raw_login,
        })

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email = ?", (clean_login,))
    row = cur.fetchone()
    conn.close()
    if not row:
        login_limiter.record(client_ip)
        return render(
            request,
            "login.html",
            {"error": "Неверный логин или пароль.", "next": next_path, "login": raw_login},
        )
    if not verify_password(password, row["salt"], row["password_hash"]):
        login_limiter.record(client_ip)
        return render(
            request,
            "login.html",
            {"error": "Неверный логин или пароль.", "next": next_path, "login": raw_login},
        )

    if row["role"] not in {"student", "teacher", "admin"}:
        return render(
            request,
            "login.html",
            {"error": "Для этой учетной записи вход отключен.", "next": next_path, "login": raw_login},
        )

    # Сброс rate-limiter при успехе
    login_limiter.reset(client_ip)

    # Автоматический rehash (миграция SHA-256 → PBKDF2)
    if needs_rehash(row["password_hash"]):
        new_s = new_salt()
        new_h = hash_password(password, new_s)
        conn = connect()
        cur = conn.cursor()
        cur.execute("UPDATE users SET password_hash = ?, salt = ? WHERE id = ?", (new_h, new_s, row["id"]))
        conn.commit()
        conn.close()

    # update last_login
    conn = connect()
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_login = ? WHERE id = ?", (datetime.utcnow().isoformat(), row["id"]))
    conn.commit()
    conn.close()
    establish_user_session(
        request,
        user_id=row["id"],
        email=row["email"],
        role=row["role"],
        session_version=_coerce_session_version(row["session_version"]),
    )
    if user_must_change_password(row):
        add_flash_once(
            request,
            "Пароль был сброшен. Используйте временный пароль, выданный преподавателем или администратором, и сразу задайте новый пароль в личном кабинете.",
            "error",
        )
        return RedirectResponse("/dashboard#profile-settings", status_code=302)
    if row["role"] == "admin":
        return RedirectResponse("/admin/students", status_code=302)
    if row["role"] == "teacher" and not next_path:
        return RedirectResponse("/v2/teacher", status_code=302)
    # redirect to requested path if safe
    redirect_to = "/dashboard"
    if next_path:
        redirect_to = next_path
    return RedirectResponse(redirect_to, status_code=302)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=302)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    context: dict[str, Any] = {"password_reset_required": user_must_change_password(user)}
    if user["role"] == "student":
        conn = connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT attempts.id AS attempt_id, attempts.score, attempts.taken_at, tests.title AS test_title
            FROM attempts
            JOIN tests ON tests.id = attempts.test_id
            WHERE attempts.student_id = ?
            ORDER BY attempts.taken_at DESC
            LIMIT 10
            """,
            (user["id"],),
        )
        history = [dict(row) for row in cur.fetchall()]

        disciplines = get_student_accessible_disciplines(cur, user.get("student_group"))

        conn.close()
        context["testing_history"] = history
        context["student_disciplines"] = disciplines

    if user["role"] == "teacher":
        conn = connect()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM lectures WHERE teacher_id = ?", (user["id"],))
        lecture_total = int(cur.fetchone()["cnt"])

        cur.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM tests
            JOIN lectures ON lectures.id = tests.lecture_id
            WHERE lectures.teacher_id = ?
            """,
            (user["id"],),
        )
        test_total = int(cur.fetchone()["cnt"])

        cur.execute(
            """
            SELECT COUNT(DISTINCT u.id) AS cnt
            FROM users u
            JOIN teaching_assignments ta
              ON ta.teacher_id = ?
             AND ta.group_name = COALESCE(u.student_group, '')
            WHERE u.role = 'student'
            """,
            (user["id"],),
        )
        student_total = int(cur.fetchone()["cnt"])

        cur.execute(
            """
            SELECT AVG(attempts.score) AS avg_score
            FROM attempts
            JOIN tests ON tests.id = attempts.test_id
            JOIN lectures ON lectures.id = tests.lecture_id
            JOIN users students ON students.id = attempts.student_id
            JOIN teaching_assignments ta
              ON ta.teacher_id = lectures.teacher_id
             AND ta.discipline_id = lectures.discipline_id
             AND ta.group_name = COALESCE(students.student_group, '')
            WHERE lectures.teacher_id = ?
            """,
            (user["id"],),
        )
        avg_row = cur.fetchone()
        average_score = round(float(avg_row["avg_score"]), 2) if avg_row and avg_row["avg_score"] is not None else 0.0

        conn.close()
        context["teacher_summary"] = {
            "lecture_total": lecture_total,
            "test_total": test_total,
            "student_total": student_total,
            "average_score": average_score,
        }

    return render(request, "dashboard.html", context)


@app.post("/dashboard/profile/name")
def dashboard_update_full_name(
    request: Request,
    full_name: str = Form(...),
):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    clean_name = sanitize_full_name(full_name)
    if not clean_name:
        add_flash(request, "Укажите корректное ФИО.", "error")
        return RedirectResponse("/dashboard#profile-settings", status_code=302)

    old_name = (user.get("full_name") or "").strip()
    if clean_name == old_name:
        add_flash(request, "ФИО не изменилось.", "info")
        return RedirectResponse("/dashboard#profile-settings", status_code=302)

    conn = connect()
    cur = conn.cursor()
    cur.execute("UPDATE users SET full_name = ? WHERE id = ?", (clean_name, user["id"]))
    conn.commit()
    conn.close()

    audit_log(request, "self_update_full_name", target_user_id=user["id"], details=f"{old_name} -> {clean_name}")
    add_flash(request, "ФИО обновлено.", "success")
    return RedirectResponse("/dashboard#profile-settings", status_code=302)


@app.post("/dashboard/profile/password")
def dashboard_update_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    if not verify_password(current_password, user["salt"], user["password_hash"]):
        if user_must_change_password(user):
            add_flash(
                request,
                "Текущий пароль указан неверно. После сброса нужно ввести временный пароль, который выдал преподаватель или администратор.",
                "error",
            )
        else:
            add_flash(request, "Текущий пароль указан неверно.", "error")
        return RedirectResponse("/dashboard#profile-settings", status_code=302)

    if new_password != new_password_confirm:
        add_flash(request, "Новый пароль и подтверждение не совпадают.", "error")
        return RedirectResponse("/dashboard#profile-settings", status_code=302)

    ok, err = validate_password(new_password)
    if not ok:
        add_flash(request, err, "error")
        return RedirectResponse("/dashboard#profile-settings", status_code=302)

    if verify_password(new_password, user["salt"], user["password_hash"]):
        add_flash(request, "Новый пароль должен отличаться от текущего.", "error")
        return RedirectResponse("/dashboard#profile-settings", status_code=302)

    conn = connect()
    cur = conn.cursor()
    new_session_version = set_user_password(cur, int(user["id"]), new_password, force_change=False)
    conn.commit()
    conn.close()
    request.session["session_version"] = new_session_version

    audit_log(request, "self_update_password", target_user_id=user["id"], details="password changed in dashboard")
    add_flash(request, "Пароль успешно обновлен.", "success")
    return RedirectResponse("/dashboard#profile-settings", status_code=302)


def _password_reset_flash_message(target: dict[str, Any], temporary_password: str) -> str:
    display_name = (target.get("full_name") or "").strip() or (target.get("email") or "пользователь")
    login = target.get("email") or "-"
    return (
        f"Пароль для «{display_name}» сброшен. "
        f"Логин: {login}. Временный пароль: {temporary_password}. "
        "Передайте его пользователю: при следующем входе пароль нужно будет сменить."
    )


def _admin_reset_user_password_impl(
    request: Request,
    user_id: int,
    *,
    next_path: str,
    login_redirect: str,
) -> RedirectResponse:
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse(login_redirect, status_code=302)

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id, role, full_name, email FROM users WHERE id = ?", (user_id,))
    target_row = cur.fetchone()
    if not target_row:
        conn.close()
        add_flash(request, "Пользователь не найден.", "error")
        return RedirectResponse(next_path, status_code=302)

    target = dict(target_row)
    if int(target["id"]) == int(user["id"]):
        conn.close()
        add_flash(request, "Собственный пароль нужно менять через личный кабинет, а не через сброс.", "info")
        return RedirectResponse(next_path, status_code=302)

    temporary_password = generate_temporary_password()
    set_user_password(cur, int(target["id"]), temporary_password, force_change=True)
    conn.commit()
    conn.close()

    audit_log(
        request,
        "admin_reset_user_password",
        target_user_id=int(target["id"]),
        details=f"temporary password issued for {target.get('email')}",
    )
    add_flash(request, _password_reset_flash_message(target, temporary_password), "success")
    return RedirectResponse(next_path, status_code=302)


def _teacher_reset_student_password_impl(
    request: Request,
    user_id: int,
    *,
    next_path: str,
) -> RedirectResponse:
    ensure_start_session_cookie(request)
    teacher = get_current_user(request)
    if not teacher or teacher["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)

    conn = connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, full_name, email FROM users WHERE id = ? AND role = 'student'",
        (user_id,),
    )
    target_row = cur.fetchone()
    if not target_row:
        conn.close()
        add_flash(request, "Студент не найден.", "error")
        return RedirectResponse(next_path, status_code=302)

    target = dict(target_row)
    if not teacher_can_manage_student(cur, int(teacher["id"]), int(user_id)):
        conn.close()
        add_flash(request, "Нет доступа к сбросу пароля этого студента.", "error")
        return RedirectResponse(next_path, status_code=302)

    temporary_password = generate_temporary_password()
    set_user_password(cur, int(target["id"]), temporary_password, force_change=True)
    conn.commit()
    conn.close()

    audit_log(
        request,
        "teacher_reset_student_password",
        target_user_id=int(target["id"]),
        details=f"temporary password issued for {target.get('email')}",
    )
    add_flash(request, _password_reset_flash_message(target, temporary_password), "success")
    return RedirectResponse(next_path, status_code=302)


@app.post("/v1/admin/users/{user_id}/reset_password")
def v1_admin_reset_user_password(request: Request, user_id: int, next: str = Form("")):
    next_path = _safe_next_path(next, default="/v1/admin/students")
    return _admin_reset_user_password_impl(
        request,
        user_id,
        next_path=next_path,
        login_redirect="/v1/admin",
    )


@app.post("/admin/users/{user_id}/reset_password")
def admin_reset_user_password(request: Request, user_id: int, next: str = Form("")):
    next_path = _safe_next_path(next, default="/admin/students")
    return _admin_reset_user_password_impl(
        request,
        user_id,
        next_path=next_path,
        login_redirect="/login",
    )


@app.post("/v1/teacher/users/{user_id}/reset_password")
def v1_teacher_reset_student_password(request: Request, user_id: int, next: str = Form("")):
    next_path = _safe_next_path(next, default="/v1/teacher/users")
    return _teacher_reset_student_password_impl(request, user_id, next_path=next_path)


@app.post("/v2/teacher/students/{user_id}/reset_password")
def v2_teacher_reset_student_password(request: Request, user_id: int, next: str = Form("")):
    next_path = _safe_next_path(next, default="/v2/teacher/students")
    return _teacher_reset_student_password_impl(request, user_id, next_path=next_path)


@app.get("/teacher/lectures", response_class=HTMLResponse)
def teacher_lectures(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or (user["role"] != "teacher" and user["role"] != "admin"):
        return RedirectResponse("/login", status_code=302)

    discipline_filter_raw = (request.query_params.get("discipline_id") or "").strip()
    discipline_filter: int | None = None
    if discipline_filter_raw:
        try:
            discipline_filter = int(discipline_filter_raw)
        except Exception:
            discipline_filter = None

    conn = connect()
    cur = conn.cursor()
    disciplines = get_discipline_map(cur)

    grouped: dict[int, dict[str, Any]] = {}

    def ensure_group(discipline_key: int, discipline_name: str) -> dict[str, Any]:
        safe_name = (discipline_name or "").strip() or "Без дисциплины"
        if discipline_key not in grouped:
            grouped[discipline_key] = {
                "discipline_key": discipline_key,
                "discipline_id": discipline_key if discipline_key > 0 else None,
                "discipline_name": safe_name,
                "lectures": [],
                "tests": [],
                "lecture_count": 0,
                "test_count": 0,
            }
        return grouped[discipline_key]

    # teacher sees only own lectures/tests; admin sees all
    if user["role"] == "teacher":
        cur.execute(
            """
            SELECT
                l.*,
                COALESCE(l.discipline_id, 0) AS discipline_key,
                COALESCE(d.name, 'Без дисциплины') AS discipline_name
            FROM lectures l
            LEFT JOIN disciplines d ON d.id = l.discipline_id
            WHERE l.teacher_id = ?
            ORDER BY COALESCE(d.name, 'Без дисциплины'), l.id DESC
            """,
            (user["id"],),
        )
    else:
        cur.execute(
            """
            SELECT
                l.*,
                COALESCE(l.discipline_id, 0) AS discipline_key,
                COALESCE(d.name, 'Без дисциплины') AS discipline_name
            FROM lectures l
            LEFT JOIN disciplines d ON d.id = l.discipline_id
            ORDER BY COALESCE(d.name, 'Без дисциплины'), l.id DESC
            """
        )

    lectures = []
    for row in cur.fetchall():
        lecture = dict(row)
        discipline_key = int(lecture.get("discipline_key") or lecture.get("discipline_id") or 0)
        discipline_name = lecture.get("discipline_name") or disciplines.get(discipline_key, "Без дисциплины")
        lecture["discipline_name"] = discipline_name
        lectures.append(lecture)
        ensure_group(discipline_key, discipline_name)["lectures"].append(lecture)

    if user["role"] == "teacher":
        cur.execute(
            """
            SELECT
                t.id,
                t.title,
                t.status,
                t.created_at,
                t.lecture_id,
                l.title AS lecture_title,
                COALESCE(l.discipline_id, 0) AS discipline_key,
                COALESCE(d.name, 'Без дисциплины') AS discipline_name
            FROM tests t
            JOIN lectures l ON l.id = t.lecture_id
            LEFT JOIN disciplines d ON d.id = l.discipline_id
            WHERE l.teacher_id = ?
            ORDER BY COALESCE(d.name, 'Без дисциплины'), t.id DESC
            """,
            (user["id"],),
        )
    else:
        cur.execute(
            """
            SELECT
                t.id,
                t.title,
                t.status,
                t.created_at,
                t.lecture_id,
                l.title AS lecture_title,
                COALESCE(l.discipline_id, 0) AS discipline_key,
                COALESCE(d.name, 'Без дисциплины') AS discipline_name
            FROM tests t
            JOIN lectures l ON l.id = t.lecture_id
            LEFT JOIN disciplines d ON d.id = l.discipline_id
            ORDER BY COALESCE(d.name, 'Без дисциплины'), t.id DESC
            """
        )

    tests = []
    for row in cur.fetchall():
        test = dict(row)
        discipline_key = int(test.get("discipline_key") or 0)
        discipline_name = test.get("discipline_name") or disciplines.get(discipline_key, "Без дисциплины")
        tests.append(test)
        ensure_group(discipline_key, discipline_name)["tests"].append(test)

    grouped_disciplines = sorted(
        grouped.values(),
        key=lambda item: (1 if int(item.get("discipline_key") or 0) == 0 else 0, str(item.get("discipline_name") or "").lower()),
    )
    for item in grouped_disciplines:
        item["lectures"].sort(key=lambda lecture: int(lecture.get("id") or 0), reverse=True)
        item["tests"].sort(key=lambda test: int(test.get("id") or 0), reverse=True)
        item["lecture_count"] = len(item["lectures"])
        item["test_count"] = len(item["tests"])

    selected_discipline_key: int | None = None
    available_keys = {int(item.get("discipline_key") or 0) for item in grouped_disciplines}
    if discipline_filter is not None and discipline_filter in available_keys:
        selected_discipline_key = discipline_filter
    elif grouped_disciplines:
        selected_discipline_key = int(grouped_disciplines[0].get("discipline_key") or 0)

    conn.close()
    return render(
        request,
        "teacher_lectures.html",
        {
            "lectures": lectures,
            "tests": tests,
            "grouped_disciplines": grouped_disciplines,
            "selected_discipline_key": selected_discipline_key,
        },
    )


@app.get("/teacher/lectures/new", response_class=HTMLResponse)
def new_lecture_form(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or (user["role"] != "teacher" and user["role"] != "admin"):
        return RedirectResponse("/login", status_code=302)
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id, full_name FROM users WHERE role = 'teacher' ORDER BY full_name")
    teachers = [dict(r) for r in cur.fetchall()]
    disciplines = get_discipline_map(cur)

    selected_discipline_id = None
    teacher_discipline_options: list[dict[str, Any]] = []
    if user.get("role") == "teacher":
        teacher_discipline_options = get_teacher_disciplines(cur, user.get("id"))
        if teacher_discipline_options:
            selected_discipline_id = int(teacher_discipline_options[0]["id"])
    elif user.get("role") == "admin" and teachers:
        teacher_discipline_options = get_teacher_disciplines(cur, int(teachers[0]["id"]))
        if teacher_discipline_options:
            selected_discipline_id = int(teacher_discipline_options[0]["id"])

    conn.close()
    return render(
        request,
        "lecture_new.html",
        {
            "error": None,
            "teachers": teachers,
            "disciplines": disciplines,
            "teacher_discipline_options": teacher_discipline_options,
            "selected_discipline_id": selected_discipline_id,
        },
    )


@app.get("/lecture/new")
def lecture_new_legacy_redirect():
    return RedirectResponse("/teacher/lectures/new", status_code=302)


@app.get("/lectures/new")
def lectures_new_legacy_redirect():
    return RedirectResponse("/teacher/lectures/new", status_code=302)


@app.post("/teacher/lectures/new")
async def new_lecture(
    request: Request,
    title: str = Form(...),
    body: str = Form(""),
    source_urls: str = Form(""),
    discipline_id: str = Form(""),
    lecture_file: Optional[UploadFile] = File(None),
):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or (user["role"] != "teacher" and user["role"] != "admin"):
        return RedirectResponse("/login", status_code=302)

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id, full_name FROM users WHERE role = 'teacher' ORDER BY full_name")
    teachers = [dict(r) for r in cur.fetchall()]
    disciplines = get_discipline_map(cur)
    teacher_discipline_options = get_teacher_disciplines(cur, user.get("id")) if user.get("role") == "teacher" else []
    conn.close()

    body_text = body.strip()
    imported_parts: list[str] = []

    if body_text:
        imported_parts.append(body_text)

    saved_filename: str | None = None
    if lecture_file and (lecture_file.filename or "").strip():
        try:
            # Ограничение размера загружаемого файла (50 МБ)
            MAX_UPLOAD_SIZE = 50 * 1024 * 1024
            raw_bytes = await lecture_file.read()
            if len(raw_bytes) > MAX_UPLOAD_SIZE:
                return render(
                    request,
                    "lecture_new.html",
                    {
                        "error": "Файл слишком большой. Максимальный размер — 50 МБ.",
                        "teachers": teachers,
                        "disciplines": disciplines,
                        "teacher_discipline_options": teacher_discipline_options,
                        "selected_discipline_id": None,
                    },
                )
            original_name = lecture_file.filename or "file"

            loop = asyncio.get_event_loop()
            file_text = await loop.run_in_executor(
                None, lambda: _extract_text_from_bytes(raw_bytes, original_name)
            )

            # Сохраняем оригинальный файл для скачивания
            ext = Path(original_name).suffix.lower()
            unique_name = f"{uuid.uuid4().hex}{ext}"
            (UPLOADS_DIR / unique_name).write_bytes(raw_bytes)
            saved_filename = unique_name
        except LectureImportError as exc:
            return render(
                request,
                "lecture_new.html",
                {
                    "error": str(exc),
                    "teachers": teachers,
                    "disciplines": disciplines,
                    "teacher_discipline_options": teacher_discipline_options,
                    "selected_discipline_id": None,
                },
            )
        imported_parts.append(file_text)

    urls_text = source_urls.strip()
    if urls_text:
        try:
            urls = parse_source_urls(urls_text)
            url_body = extract_text_from_urls(urls)
        except LectureImportError as exc:
            return render(
                request,
                "lecture_new.html",
                {
                    "error": str(exc),
                    "teachers": teachers,
                    "disciplines": disciplines,
                    "teacher_discipline_options": teacher_discipline_options,
                    "selected_discipline_id": None,
                },
            )
        imported_parts.append(url_body)

    body_text = "\n\n".join(part.strip() for part in imported_parts if part.strip()).strip()

    if len(body_text) < 20:
        return render(
            request,
            "lecture_new.html",
            {
                "error": "Текст лекции слишком короткий (минимум 20 символов).",
                "teachers": teachers,
                "disciplines": disciplines,
                "teacher_discipline_options": teacher_discipline_options,
                "selected_discipline_id": None,
            },
        )
    # determine teacher_id: if admin provided selection, use it; otherwise use current user id
    teacher_id = user["id"]
    if user["role"] == "admin":
        form = await request.form()
        sel = form.get("teacher_id")
        try:
            if sel:
                teacher_id = int(sel)
        except Exception:
            teacher_id = user["id"]

    conn = connect()
    cur = conn.cursor()
    teacher_discipline_ids = get_teacher_discipline_ids(cur, teacher_id)
    selected_discipline_id = None
    if discipline_id:
        try:
            selected_discipline_id = int(discipline_id)
        except Exception:
            selected_discipline_id = None

    if selected_discipline_id is None and teacher_discipline_ids:
        selected_discipline_id = int(teacher_discipline_ids[0])
    if selected_discipline_id and teacher_discipline_ids and selected_discipline_id not in teacher_discipline_ids:
        selected_discipline_id = int(teacher_discipline_ids[0]) if teacher_discipline_ids else selected_discipline_id

    cur.execute(
        "INSERT INTO lectures (teacher_id, title, body, created_at, discipline_id, original_filename) VALUES (?, ?, ?, ?, ?, ?)",
        (teacher_id, title.strip(), body_text, datetime.utcnow().isoformat(), selected_discipline_id, saved_filename),
    )
    conn.commit()
    conn.close()
    return RedirectResponse("/teacher/lectures", status_code=302)


@app.get("/teacher/lectures/{lecture_id}/download")
def download_lecture_file(request: Request, lecture_id: int):
    """Скачать оригинальный файл лекции."""
    user = get_current_user(request)
    if not user or user["role"] not in ("teacher", "admin"):
        return RedirectResponse("/login", status_code=302)
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM lectures WHERE id = ?", (lecture_id,))
    lecture = cur.fetchone()
    conn.close()
    if not lecture:
        return RedirectResponse("/teacher/lectures", status_code=302)
    fname = lecture["original_filename"] if "original_filename" in lecture.keys() else None
    if not fname:
        return HTMLResponse("Оригинальный файл не найден.", status_code=404)
    # Защита от path-traversal: берём только имя файла
    safe_fname = Path(fname).name
    if safe_fname != fname:
        return HTMLResponse("Некорректное имя файла.", status_code=400)
    file_path = UPLOADS_DIR / safe_fname
    # Дополнительная проверка: resolve-путь внутри UPLOADS_DIR
    if not file_path.resolve().is_relative_to(UPLOADS_DIR.resolve()):
        return HTMLResponse("Доступ запрещён.", status_code=403)
    if not file_path.exists():
        return HTMLResponse("Файл был удалён с сервера.", status_code=404)
    # Определяем имя для скачивания на основе названия лекции
    ext = Path(fname).suffix
    download_name = f"{lecture['title']}{ext}"
    return FileResponse(
        path=str(file_path),
        filename=download_name,
        media_type="application/octet-stream",
    )


@app.post("/teacher/lectures/import-urls")
async def import_lecture_urls(request: Request, source_urls: str = Form("")):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or (user["role"] != "teacher" and user["role"] != "admin"):
        return JSONResponse({"ok": False, "error": "Требуется авторизация преподавателя."}, status_code=403)

    raw_urls = (source_urls or "").strip()
    if not raw_urls:
        return JSONResponse({"ok": False, "error": "Добавьте хотя бы одну ссылку."}, status_code=400)

    try:
        urls = parse_source_urls(raw_urls)
        imported_text = extract_text_from_urls(urls)
    except LectureImportError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    return JSONResponse(
        {
            "ok": True,
            "text": imported_text,
            "url_count": len(urls),
            "char_count": len(imported_text),
        }
    )


@app.post("/teacher/lectures/{lecture_id}/delete")
def delete_lecture(request: Request, lecture_id: int):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or (user["role"] != "teacher" and user["role"] != "admin"):
        return RedirectResponse("/login", status_code=302)

    conn = connect()
    cur = conn.cursor()

    if user["role"] == "teacher":
        cur.execute("SELECT id FROM lectures WHERE id = ? AND teacher_id = ?", (lecture_id, user["id"]))
    else:
        cur.execute("SELECT id FROM lectures WHERE id = ?", (lecture_id,))

    lecture = cur.fetchone()
    if not lecture:
        conn.close()
        add_flash(request, "Лекция не найдена или нет доступа", "error")
        return RedirectResponse("/teacher/lectures", status_code=302)

    cur.execute("SELECT id FROM tests WHERE lecture_id = ?", (lecture_id,))
    test_ids = [row["id"] for row in cur.fetchall()]

    for test_id in test_ids:
        cur.execute("DELETE FROM answers WHERE attempt_id IN (SELECT id FROM attempts WHERE test_id = ?)", (test_id,))
        cur.execute("DELETE FROM attempts WHERE test_id = ?", (test_id,))
        cur.execute("DELETE FROM questions WHERE test_id = ?", (test_id,))
        cur.execute("DELETE FROM tests WHERE id = ?", (test_id,))

    cur.execute("DELETE FROM lectures WHERE id = ?", (lecture_id,))
    conn.commit()
    conn.close()

    audit_log(request, "delete_lecture", target_user_id=None, details=f"lecture_id={lecture_id}")
    add_flash(request, "Лекция удалена", "success")
    return RedirectResponse("/teacher/lectures", status_code=302)


@app.get("/teacher/lectures/{lecture_id}", response_class=HTMLResponse)
def lecture_detail(request: Request, lecture_id: int):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or (user["role"] != "teacher" and user["role"] != "admin"):
        return RedirectResponse("/login", status_code=302)
    conn = connect()
    cur = conn.cursor()
    if user["role"] == "teacher":
        cur.execute("SELECT * FROM lectures WHERE id = ? AND teacher_id = ?", (lecture_id, user["id"]))
    else:
        cur.execute("SELECT * FROM lectures WHERE id = ?", (lecture_id,))
    lecture = cur.fetchone()
    if not lecture:
        conn.close()
        return RedirectResponse("/teacher/lectures", status_code=302)
    cur.execute("SELECT * FROM tests WHERE lecture_id = ? ORDER BY id DESC", (lecture_id,))
    tests = [dict(r) for r in cur.fetchall()]
    conn.close()
    return render(request, "lecture_detail.html", {"lecture": dict(lecture), "tests": tests})


def _load_accessible_lectures(cur, user: dict[str, Any]) -> list[dict[str, Any]]:
    if user["role"] == "teacher":
        cur.execute("SELECT id, title, created_at FROM lectures WHERE teacher_id = ? ORDER BY id DESC", (user["id"],))
    else:
        cur.execute("SELECT id, title, created_at FROM lectures ORDER BY id DESC")
    return [dict(r) for r in cur.fetchall()]


@app.get("/teacher/tests/manual/new", response_class=HTMLResponse)
def manual_test_new_form(request: Request, lecture_id: int | None = None):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or (user["role"] != "teacher" and user["role"] != "admin"):
        return RedirectResponse("/login", status_code=302)

    conn = connect()
    cur = conn.cursor()
    lectures = _load_accessible_lectures(cur, user)
    conn.close()

    if not lectures:
        add_flash(request, "Сначала создайте лекцию, затем можно собрать тест вручную.", "info")
        return RedirectResponse("/teacher/lectures/new", status_code=302)

    lecture_ids = {int(item["id"]) for item in lectures}
    selected_lecture_id = int(lecture_id) if lecture_id and int(lecture_id) in lecture_ids else int(lectures[0]["id"])
    lecture_by_id = {int(item["id"]): item for item in lectures}
    selected_lecture = lecture_by_id.get(selected_lecture_id, lectures[0])

    return render(
        request,
        "test_manual_new.html",
        {
            "error": None,
            "lectures": lectures,
            "selected_lecture_id": selected_lecture_id,
            "title_value": f"Ручной тест: {selected_lecture['title']}",
            "questions": [{"text": "", "options": ["", "", "", ""], "correct_index": 0}],
        },
    )


@app.post("/teacher/tests/manual/new")
async def manual_test_new_submit(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or (user["role"] != "teacher" and user["role"] != "admin"):
        return RedirectResponse("/login", status_code=302)

    form = await request.form()
    title = (form.get("title") or "").strip()
    lecture_id_raw = (form.get("lecture_id") or "").strip()

    question_texts = [str(v) for v in form.getlist("question_text")]
    option_a = [str(v) for v in form.getlist("option_a")]
    option_b = [str(v) for v in form.getlist("option_b")]
    option_c = [str(v) for v in form.getlist("option_c")]
    option_d = [str(v) for v in form.getlist("option_d")]
    correct_indexes = [str(v) for v in form.getlist("correct_index")]

    conn = connect()
    cur = conn.cursor()
    lectures = _load_accessible_lectures(cur, user)
    lecture_by_id = {int(item["id"]): item for item in lectures}

    try:
        lecture_id = int(lecture_id_raw)
    except Exception:
        lecture_id = 0

    max_len = max(
        len(question_texts),
        len(option_a),
        len(option_b),
        len(option_c),
        len(option_d),
        len(correct_indexes),
        1,
    )
    raw_questions: list[dict[str, Any]] = []
    for i in range(max_len):
        raw_questions.append(
            {
                "text": (question_texts[i] if i < len(question_texts) else "").strip(),
                "options": [
                    (option_a[i] if i < len(option_a) else "").strip(),
                    (option_b[i] if i < len(option_b) else "").strip(),
                    (option_c[i] if i < len(option_c) else "").strip(),
                    (option_d[i] if i < len(option_d) else "").strip(),
                ],
                "correct_index": int(correct_indexes[i]) if i < len(correct_indexes) and str(correct_indexes[i]).isdigit() else 0,
            }
        )

    if lecture_id not in lecture_by_id:
        conn.close()
        return render(
            request,
            "test_manual_new.html",
            {
                "error": "Выберите лекцию из списка.",
                "lectures": lectures,
                "selected_lecture_id": int(lectures[0]["id"]) if lectures else None,
                "title_value": title,
                "questions": raw_questions,
            },
        )

    clean_questions: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_questions, start=1):
        text = item["text"]
        options = item["options"]
        has_any_data = bool(text or any(options))
        if not has_any_data:
            continue

        if not text:
            conn.close()
            return render(
                request,
                "test_manual_new.html",
                {
                    "error": f"Вопрос №{idx}: заполните текст вопроса.",
                    "lectures": lectures,
                    "selected_lecture_id": lecture_id,
                    "title_value": title,
                    "questions": raw_questions,
                },
            )

        if any(not opt for opt in options):
            conn.close()
            return render(
                request,
                "test_manual_new.html",
                {
                    "error": f"Вопрос №{idx}: заполните все 4 варианта ответа.",
                    "lectures": lectures,
                    "selected_lecture_id": lecture_id,
                    "title_value": title,
                    "questions": raw_questions,
                },
            )

        correct_index = int(item["correct_index"])
        if correct_index < 0 or correct_index > 3:
            conn.close()
            return render(
                request,
                "test_manual_new.html",
                {
                    "error": f"Вопрос №{idx}: укажите корректный правильный ответ.",
                    "lectures": lectures,
                    "selected_lecture_id": lecture_id,
                    "title_value": title,
                    "questions": raw_questions,
                },
            )

        clean_questions.append(
            {
                "text": text,
                "options_json": json.dumps(options, ensure_ascii=False),
                "correct_index": correct_index,
            }
        )

    if not clean_questions:
        conn.close()
        return render(
            request,
            "test_manual_new.html",
            {
                "error": "Добавьте хотя бы один полностью заполненный вопрос.",
                "lectures": lectures,
                "selected_lecture_id": lecture_id,
                "title_value": title,
                "questions": raw_questions,
            },
        )

    final_title = title if title else f"Ручной тест: {lecture_by_id[lecture_id]['title']}"
    cur.execute(
        "INSERT INTO tests (lecture_id, title, status, created_at) VALUES (?, ?, 'draft', ?)",
        (lecture_id, final_title, datetime.utcnow().isoformat()),
    )
    test_id = cur.lastrowid
    for item in clean_questions:
        cur.execute(
            "INSERT INTO questions (test_id, text, options_json, correct_index) VALUES (?, ?, ?, ?)",
            (test_id, item["text"], item["options_json"], item["correct_index"]),
        )
    conn.commit()
    conn.close()

    add_flash(request, "Ручной тест создан в статусе draft. Проверьте и опубликуйте.", "success")
    return RedirectResponse(f"/teacher/tests/{test_id}/edit", status_code=302)


@app.post("/teacher/lectures/{lecture_id}/generate")
def generate_test(
    request: Request,
    lecture_id: int,
    question_count: int = Form(5),
    difficulty: str = Form("medium"),
):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or (user["role"] != "teacher" and user["role"] != "admin"):
        return RedirectResponse("/login", status_code=302)
    conn = connect()
    cur = conn.cursor()
    if user["role"] == "teacher":
        cur.execute("SELECT * FROM lectures WHERE id = ? AND teacher_id = ?", (lecture_id, user["id"]))
    else:
        cur.execute("SELECT * FROM lectures WHERE id = ?", (lecture_id,))
    lecture = cur.fetchone()
    if not lecture:
        conn.close()
        return RedirectResponse("/teacher/lectures", status_code=302)

    count = max(1, min(int(question_count), 50))
    difficulty = (difficulty or "medium").strip().lower()
    if difficulty not in {"easy", "medium", "hard"}:
        difficulty = "medium"

    is_ajax = request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"

    discipline_name = None
    discipline_id_raw = lecture["discipline_id"] if "discipline_id" in lecture.keys() else None
    try:
        discipline_id = int(discipline_id_raw) if discipline_id_raw else None
    except Exception:
        discipline_id = None
    if discipline_id:
        cur.execute("SELECT name FROM disciplines WHERE id = ?", (discipline_id,))
        discipline_row = cur.fetchone()
        if discipline_row:
            discipline_name = discipline_row["name"]

    questions = generate_questions(
        lecture["body"],
        count=count,
        difficulty=difficulty,
        discipline_name=discipline_name,
    )
    if not questions:
        conn.close()
        details = diagnose_ai_setup()
        message = f"[AI-DIAG-V2] AI не вернул качественные вопросы. {details}"
        if is_ajax:
            return JSONResponse(
                {
                    "ok": False,
                    "message": message,
                },
                status_code=422,
            )
        add_flash(
            request,
            message,
            "error",
        )
        return RedirectResponse(f"/teacher/lectures/{lecture_id}", status_code=302)

    test_title = f"Тест по теме: {lecture['title']} ({difficulty}, {count} вопр.)"
    cur.execute(
        "INSERT INTO tests (lecture_id, title, status, created_at) VALUES (?, ?, ?, ?)",
        (lecture_id, test_title, "draft", datetime.utcnow().isoformat()),
    )
    test_id = cur.lastrowid

    for q in questions:
        cur.execute(
            "INSERT INTO questions (test_id, text, options_json, correct_index) VALUES (?, ?, ?, ?)",
            (test_id, q["text"], json.dumps(q["options"], ensure_ascii=False), q["correct_index"]),
        )
    conn.commit()
    conn.close()
    if is_ajax:
        return JSONResponse({"ok": True, "redirect": f"/teacher/tests/{test_id}/edit"})
    return RedirectResponse(f"/teacher/tests/{test_id}/edit", status_code=302)


@app.get("/teacher/tests/{test_id}/edit", response_class=HTMLResponse)
def edit_test_form(request: Request, test_id: int):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or (user["role"] != "teacher" and user["role"] != "admin"):
        return RedirectResponse("/login", status_code=302)
    conn = connect()
    cur = conn.cursor()
    if user["role"] == "teacher":
        cur.execute(
            """
            SELECT tests.* FROM tests
            JOIN lectures ON lectures.id = tests.lecture_id
            WHERE tests.id = ? AND lectures.teacher_id = ?
            """,
            (test_id, user["id"]),
        )
    else:
        cur.execute("SELECT tests.* FROM tests WHERE tests.id = ?", (test_id,))
    test = cur.fetchone()
    if not test:
        conn.close()
        return RedirectResponse("/teacher/lectures", status_code=302)
    cur.execute("SELECT * FROM questions WHERE test_id = ? ORDER BY id", (test_id,))
    questions = []
    for q in cur.fetchall():
        questions.append({**dict(q), "options": json.loads(q["options_json"])})
    conn.close()
    return render(request, "test_edit.html", {"test": dict(test), "questions": questions})


@app.post("/teacher/tests/{test_id}/edit")
async def edit_test_submit(request: Request, test_id: int):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or (user["role"] != "teacher" and user["role"] != "admin"):
        return RedirectResponse("/login", status_code=302)
    form = await request.form()
    conn = connect()
    cur = conn.cursor()
    if user["role"] == "teacher":
        cur.execute(
            """
            SELECT tests.* FROM tests
            JOIN lectures ON lectures.id = tests.lecture_id
            WHERE tests.id = ? AND lectures.teacher_id = ?
            """,
            (test_id, user["id"]),
        )
    else:
        cur.execute("SELECT tests.* FROM tests WHERE tests.id = ?", (test_id,))
    test = cur.fetchone()
    if not test:
        conn.close()
        return RedirectResponse("/teacher/lectures", status_code=302)

    cur.execute("SELECT id FROM questions WHERE test_id = ? ORDER BY id", (test_id,))
    question_ids = [r["id"] for r in cur.fetchall()]

    for qid in question_ids:
        text = form.get(f"q_{qid}_text", "").strip()
        options = [
            form.get(f"q_{qid}_opt_0", "").strip(),
            form.get(f"q_{qid}_opt_1", "").strip(),
            form.get(f"q_{qid}_opt_2", "").strip(),
            form.get(f"q_{qid}_opt_3", "").strip(),
        ]
        try:
            correct_index = int(form.get(f"q_{qid}_correct", "0"))
        except ValueError:
            correct_index = 0
        cur.execute(
            "UPDATE questions SET text = ?, options_json = ?, correct_index = ? WHERE id = ?",
            (text, json.dumps(options, ensure_ascii=False), correct_index, qid),
        )
    conn.commit()
    conn.close()
    return RedirectResponse(f"/teacher/tests/{test_id}/edit", status_code=302)


@app.post("/teacher/tests/{test_id}/publish")
def publish_test(request: Request, test_id: int):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or (user["role"] != "teacher" and user["role"] != "admin"):
        return RedirectResponse("/login", status_code=302)
    conn = connect()
    cur = conn.cursor()
    if user["role"] == "teacher":
        cur.execute(
            """
            UPDATE tests SET status = 'published'
            WHERE id = ? AND lecture_id IN (SELECT id FROM lectures WHERE teacher_id = ?)
            """,
            (test_id, user["id"]),
        )
    else:
        cur.execute("UPDATE tests SET status = 'published' WHERE id = ?", (test_id,))
    conn.commit()
    conn.close()
    return RedirectResponse("/teacher/lectures", status_code=302)


@app.post("/teacher/tests/{test_id}/delete")
def delete_test(request: Request, test_id: int):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or (user["role"] != "teacher" and user["role"] != "admin"):
        return RedirectResponse("/login", status_code=302)

    conn = connect()
    cur = conn.cursor()

    if user["role"] == "teacher":
        cur.execute(
            """
            SELECT tests.id
            FROM tests
            JOIN lectures ON lectures.id = tests.lecture_id
            WHERE tests.id = ? AND lectures.teacher_id = ?
            """,
            (test_id, user["id"]),
        )
    else:
        cur.execute("SELECT id FROM tests WHERE id = ?", (test_id,))

    exists = cur.fetchone()
    if not exists:
        conn.close()
        add_flash(request, "Тест не найден или нет доступа", "error")
        return RedirectResponse("/v2/teacher/tests", status_code=302)

    cur.execute(
        "DELETE FROM answers WHERE attempt_id IN (SELECT id FROM attempts WHERE test_id = ?)",
        (test_id,),
    )
    cur.execute("DELETE FROM attempts WHERE test_id = ?", (test_id,))
    cur.execute("DELETE FROM questions WHERE test_id = ?", (test_id,))
    cur.execute("DELETE FROM tests WHERE id = ?", (test_id,))
    conn.commit()
    conn.close()

    audit_log(request, "delete_test", target_user_id=None, details=f"test_id={test_id}")
    add_flash(request, "Тест удалён", "success")
    return RedirectResponse("/v2/teacher/tests", status_code=302)


@app.get("/teacher/tests/{test_id}/qr", response_class=HTMLResponse)
def teacher_test_qr(request: Request, test_id: int):
    user = get_current_user(request)
    if not user or (user["role"] != "teacher" and user["role"] != "admin"):
        return RedirectResponse("/login", status_code=302)

    conn = connect()
    cur = conn.cursor()
    if user["role"] == "teacher":
        cur.execute(
            """
            SELECT tests.id, tests.title, tests.status, lectures.title AS lecture_title
            FROM tests
            JOIN lectures ON lectures.id = tests.lecture_id
            WHERE tests.id = ? AND lectures.teacher_id = ?
            """,
            (test_id, user["id"]),
        )
    else:
        cur.execute(
            """
            SELECT tests.id, tests.title, tests.status, lectures.title AS lecture_title
            FROM tests
            JOIN lectures ON lectures.id = tests.lecture_id
            WHERE tests.id = ?
            """,
            (test_id,),
        )

    test = cur.fetchone()
    conn.close()
    if not test:
        add_flash(request, "Тест не найден или нет доступа", "error")
        return RedirectResponse("/v2/teacher/tests", status_code=302)

    entry_path = f"/student/tests/{test_id}/entry"
    entry_url = f"{str(request.base_url).rstrip('/')}{entry_path}"
    qr_image_url = "https://api.qrserver.com/v1/create-qr-code/?size=320x320&data=" + quote_plus(entry_url)

    return render(
        request,
        "test_qr.html",
        {
            "test": dict(test),
            "entry_url": entry_url,
            "qr_image_url": qr_image_url,
            "entry_path": entry_path,
        },
    )


@app.get("/student/tests/{test_id}/entry")
def student_test_entry(request: Request, test_id: int):
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id FROM tests WHERE id = ? AND status = 'published'", (test_id,))
    test = cur.fetchone()
    conn.close()

    if not test:
        add_flash(request, "Тест недоступен по этой ссылке.", "error")
        return RedirectResponse("/", status_code=302)

    user = get_current_user(request)
    if not user:
        target = quote_plus(f"/student/tests/{test_id}/take")
        return RedirectResponse(f"/login?next={target}", status_code=302)

    if user.get("role") != "student":
        add_flash(request, "Прохождение тестов доступно только для студентов.", "error")
        return RedirectResponse("/dashboard", status_code=302)

    conn = connect()
    cur = conn.cursor()
    can_access = student_can_access_test(cur, int(user["id"]), int(test_id))
    conn.close()
    if not can_access:
        add_flash(request, "Этот тест не назначен вашей группе.", "error")
        return RedirectResponse("/student/tests", status_code=302)

    return RedirectResponse(f"/student/tests/{test_id}/take", status_code=302)


@app.get("/student/tests", response_class=HTMLResponse)
def student_tests(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "student":
        return RedirectResponse("/login", status_code=302)
    conn = connect()
    cur = conn.cursor()
    discipline_filter_raw = request.query_params.get("discipline_id", "")
    discipline_filter: int | None = None
    try:
        if discipline_filter_raw:
            discipline_filter = int(discipline_filter_raw)
    except Exception:
        discipline_filter = None

    all_disciplines = get_student_accessible_disciplines(cur, user.get("student_group"))
    student_disciplines = list(all_disciplines)

    if discipline_filter and discipline_filter not in {int(item["id"]) for item in student_disciplines}:
        discipline_filter = None

    query_params: tuple[Any, ...] = (normalize_group_name(user.get("student_group")), user["id"])
    discipline_sql = ""
    if discipline_filter:
        discipline_sql = " AND lectures.discipline_id = ?"
        query_params = (normalize_group_name(user.get("student_group")), user["id"], discipline_filter)
    cur.execute(
        f"""
        SELECT DISTINCT tests.*, lectures.title AS lecture_title, lectures.discipline_id,
               a.id AS attempt_id, a.score AS attempt_score, a.taken_at AS attempt_taken_at
        FROM tests
        JOIN lectures ON lectures.id = tests.lecture_id
        JOIN teaching_assignments ta
          ON ta.teacher_id = lectures.teacher_id
         AND ta.discipline_id = lectures.discipline_id
         AND ta.group_name = ?
        LEFT JOIN attempts a ON a.id = (
            SELECT MAX(ax.id)
            FROM attempts ax
            WHERE ax.test_id = tests.id AND ax.student_id = ?
        )
        WHERE tests.status = 'published'{discipline_sql}
        ORDER BY tests.id DESC
        """,
        query_params,
    )
    tests = [dict(r) for r in cur.fetchall()]
    conn.close()
    discipline_name_by_id = {int(d["id"]): d["name"] for d in all_disciplines}
    for item in tests:
        item["discipline_name"] = discipline_name_by_id.get(int(item.get("discipline_id") or 0), "Без дисциплины")
        item["is_completed"] = bool(item.get("attempt_id"))

    return render(
        request,
        "student_tests.html",
        {
            "tests": tests,
            "student_disciplines": student_disciplines,
            "selected_discipline_id": discipline_filter,
        },
    )


@app.get("/student/tests/{test_id}/take", response_class=HTMLResponse)
def take_test_form(request: Request, test_id: int):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "student":
        return RedirectResponse("/login", status_code=302)
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tests WHERE id = ? AND status = 'published'", (test_id,))
    test = cur.fetchone()
    if not test:
        conn.close()
        return RedirectResponse("/student/tests", status_code=302)
    if not student_can_access_test(cur, int(user["id"]), int(test_id)):
        conn.close()
        add_flash(request, "Этот тест не назначен вашей группе.", "error")
        return RedirectResponse("/student/tests", status_code=302)

    cur.execute(
        "SELECT id FROM attempts WHERE test_id = ? AND student_id = ? LIMIT 1",
        (test_id, user["id"]),
    )
    existing_attempt = cur.fetchone()
    if existing_attempt:
        conn.close()
        add_flash(request, "Этот тест уже пройден. Повторное прохождение недоступно.", "info")
        return RedirectResponse(f"/student/attempts/{int(existing_attempt['id'])}", status_code=302)

    cur.execute("SELECT * FROM questions WHERE test_id = ? ORDER BY id", (test_id,))
    questions = []
    for q in cur.fetchall():
        questions.append({**dict(q), "options": json.loads(q["options_json"])})
    conn.close()
    return render(request, "test_take.html", {"test": dict(test), "questions": questions})


@app.post("/student/tests/{test_id}/take")
async def take_test_submit(request: Request, test_id: int):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "student":
        return RedirectResponse("/login", status_code=302)
    form = await request.form()
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tests WHERE id = ? AND status = 'published'", (test_id,))
    test = cur.fetchone()
    if not test:
        conn.close()
        return RedirectResponse("/student/tests", status_code=302)
    if not student_can_access_test(cur, int(user["id"]), int(test_id)):
        conn.close()
        add_flash(request, "Этот тест не назначен вашей группе.", "error")
        return RedirectResponse("/student/tests", status_code=302)

    cur.execute(
        "SELECT id FROM attempts WHERE test_id = ? AND student_id = ? LIMIT 1",
        (test_id, user["id"]),
    )
    existing_attempt = cur.fetchone()
    if existing_attempt:
        conn.close()
        add_flash(request, "Этот тест уже пройден. Повторное прохождение недоступно.", "info")
        return RedirectResponse(f"/student/attempts/{int(existing_attempt['id'])}", status_code=302)

    cur.execute("SELECT * FROM questions WHERE test_id = ? ORDER BY id", (test_id,))
    questions = [dict(r) for r in cur.fetchall()]

    correct = 0
    for q in questions:
        selected = int(form.get(f"q_{q['id']}", "-1"))
        if selected == q["correct_index"]:
            correct += 1
    score = round(100 * correct / max(1, len(questions)), 2)

    cur.execute(
        "INSERT INTO attempts (test_id, student_id, score, taken_at) VALUES (?, ?, ?, ?)",
        (test_id, user["id"], score, datetime.utcnow().isoformat()),
    )
    attempt_id = cur.lastrowid
    for q in questions:
        selected = int(form.get(f"q_{q['id']}", "-1"))
        is_correct = 1 if selected == q["correct_index"] else 0
        cur.execute(
            "INSERT INTO answers (attempt_id, question_id, selected_index, is_correct) VALUES (?, ?, ?, ?)",
            (attempt_id, q["id"], selected, is_correct),
        )
    conn.commit()
    conn.close()
    add_flash(request, f"Тест отправлен. Результат: {score}%.", "success")
    return RedirectResponse(f"/student/attempts/{attempt_id}", status_code=302)


@app.get("/student/attempts/{attempt_id}", response_class=HTMLResponse)
def student_attempt_review(request: Request, attempt_id: int):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "student":
        return RedirectResponse("/login", status_code=302)

    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT attempts.id, attempts.score, attempts.taken_at, tests.title AS test_title
        FROM attempts
        JOIN tests ON tests.id = attempts.test_id
        WHERE attempts.id = ? AND attempts.student_id = ?
        """,
        (attempt_id, user["id"]),
    )
    attempt = cur.fetchone()
    if not attempt:
        conn.close()
        return RedirectResponse("/student/analytics", status_code=302)

    cur.execute(
        """
        SELECT
            q.id AS question_id,
            q.text AS question_text,
            q.options_json,
            q.correct_index,
            a.selected_index,
            a.is_correct
        FROM answers a
        JOIN questions q ON q.id = a.question_id
        WHERE a.attempt_id = ?
        ORDER BY q.id
        """,
        (attempt_id,),
    )
    answer_rows = cur.fetchall()
    conn.close()

    review_items: list[dict[str, Any]] = []
    correct_count = 0
    for row in answer_rows:
        options = json.loads(row["options_json"])
        selected_index = int(row["selected_index"])
        correct_index = int(row["correct_index"])

        option_items = []
        for index, text in enumerate(options):
            option_items.append(
                {
                    "text": text,
                    "is_selected": index == selected_index,
                    "is_correct": index == correct_index,
                    "is_wrong_selected": index == selected_index and index != correct_index,
                }
            )

        is_correct = bool(row["is_correct"])
        if is_correct:
            correct_count += 1

        review_items.append(
            {
                "question_text": row["question_text"],
                "options": option_items,
                "is_correct": is_correct,
            }
        )

    total_questions = len(review_items)
    wrong_count = max(0, total_questions - correct_count)
    return render(
        request,
        "student_attempt_review.html",
        {
            "attempt": dict(attempt),
            "review_items": review_items,
            "correct_count": correct_count,
            "wrong_count": wrong_count,
            "total_questions": total_questions,
        },
    )


@app.get("/student/analytics", response_class=HTMLResponse)
def student_analytics(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "student":
        return RedirectResponse("/login", status_code=302)
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT attempts.id AS attempt_id, attempts.score, attempts.taken_at, tests.id AS test_id, tests.title AS test_title
        FROM attempts
        JOIN tests ON tests.id = attempts.test_id
        WHERE attempts.student_id = ?
        ORDER BY attempts.taken_at DESC
        """,
        (user["id"],),
    )
    rows = cur.fetchall()
    conn.close()
    scores = [r["score"] for r in rows]
    avg = round(sum(scores) / len(scores), 2) if scores else 0.0
    trend = 0.0
    if len(scores) >= 2:
        trend = round(scores[0] - scores[1], 2)
    best = max(scores) if scores else 0.0
    worst = min(scores) if scores else 0.0

    last_7_days = 0.0
    if rows:
        cutoff = datetime.utcnow()
        cutoff = cutoff.replace(microsecond=0)
        recent_scores = []
        for r in rows:
            try:
                taken_at = datetime.fromisoformat(r["taken_at"])
            except Exception:
                continue
            if (cutoff - taken_at).days <= 7:
                recent_scores.append(r["score"])
        if recent_scores:
            last_7_days = round(sum(recent_scores) / len(recent_scores), 2)

    per_test_latest: dict[int, dict[str, Any]] = {}
    for r in rows:
        test_id = int(r["test_id"])
        if test_id not in per_test_latest:
            per_test_latest[test_id] = {
                "title": r["test_title"],
                "score": r["score"],
                "taken_at": r["taken_at"],
                "attempt_id": r["attempt_id"],
            }
    per_test_list = list(per_test_latest.values())

    recent = [
        {
            "score": r["score"],
            "taken_at": r["taken_at"],
            "test_title": r["test_title"],
            "attempt_id": r["attempt_id"],
        }
        for r in rows[:5]
    ]
    spark_points = list(reversed(scores[:10]))
    sparkline = make_sparkline(spark_points)
    return render(
        request,
        "student_analytics.html",
        {
            "avg": avg,
            "trend": trend,
            "recent": recent,
            "total": len(scores),
            "best": best,
            "worst": worst,
            "last7": last_7_days,
            "per_test": per_test_list,
            "sparkline": sparkline,
            "spark_points": spark_points,
        },
    )


@app.get("/growth", response_class=HTMLResponse)
def growth_module(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "student":
        return RedirectResponse("/login?next=/growth", status_code=302)

    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            attempts.taken_at,
            tests.title AS test_title,
            q.text AS question_text,
            q.options_json,
            q.correct_index,
            a.selected_index
        FROM answers a
        JOIN attempts ON attempts.id = a.attempt_id
        JOIN questions q ON q.id = a.question_id
        JOIN tests ON tests.id = attempts.test_id
        WHERE attempts.student_id = ? AND a.is_correct = 0
        ORDER BY attempts.taken_at DESC
        LIMIT 120
        """,
        (user["id"],),
    )
    wrong_rows = cur.fetchall()
    conn.close()

    mistakes: list[dict[str, Any]] = []
    context_blocks: list[str] = []
    for row in wrong_rows:
        options = json.loads(row["options_json"])
        selected_index = int(row["selected_index"])
        correct_index = int(row["correct_index"])

        selected_text = "Без ответа"
        if 0 <= selected_index < len(options):
            selected_text = options[selected_index]

        correct_text = ""
        if 0 <= correct_index < len(options):
            correct_text = options[correct_index]

        mistake_item = {
            "taken_at": row["taken_at"],
            "test_title": row["test_title"],
            "question_text": row["question_text"],
            "selected_text": selected_text,
            "correct_text": correct_text,
        }
        mistakes.append(mistake_item)

        context_blocks.append(
            "\n".join(
                [
                    f"Тест: {row['test_title']}",
                    f"Вопрос: {row['question_text']}",
                    f"Ответ студента: {selected_text}",
                    f"Правильный ответ: {correct_text}",
                ]
            )
        )

    topics: list[dict[str, str]] = []
    if context_blocks:
        context = "\n\n".join(context_blocks)
        generated = generate_growth_topics(context, limit=8)
        topics = [
            {
                "topic": str(item.get("topic") or "").strip(),
                "reason": str(item.get("reason") or "").strip(),
                "query": str(item.get("query") or item.get("topic") or "").strip(),
            }
            for item in generated
            if str(item.get("topic") or "").strip()
        ]

        for item in topics:
            item["search_url"] = f"https://www.google.com/search?q={quote_plus(item['query'])}"

    return render(
        request,
        "growth.html",
        {
            "topics": topics,
            "mistakes_total": len(mistakes),
            "mistakes_preview": mistakes[:10],
        },
    )


@app.get("/admin/users", response_class=HTMLResponse)
def admin_users_redirect(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse("/admin/students", status_code=302)


@app.get("/admin/students", response_class=HTMLResponse)
def admin_students(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)
    q = request.query_params.get("q", "").strip()
    conn = connect()
    cur = conn.cursor()
    users = fetch_users_by_role(cur, "student", q)
    grouped_students = group_students_by_group(users)
    conn.close()
    return render(
        request,
        "admin_users.html",
        {
            "users": users,
            "grouped_students": grouped_students,
            "mode": "admin",
            "query": q,
            "page_kind": "students",
            "page_title": "Студенты",
        },
    )


@app.get("/admin/teachers", response_class=HTMLResponse)
def admin_teachers(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)
    q = request.query_params.get("q", "").strip()
    conn = connect()
    cur = conn.cursor()
    users = fetch_users_by_role(cur, "teacher", q)
    conn.close()
    return render(request, "admin_users.html", {"users": users, "mode": "admin", "query": q, "page_kind": "teachers", "page_title": "Преподаватели"})


@app.get("/admin/groups", response_class=HTMLResponse)
def admin_groups(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)
    selected_name = (request.query_params.get("group") or "").replace("_", " ").strip()
    conn = connect()
    cur = conn.cursor()
    context = build_groups_page_context(cur, selected_name if selected_name else None)
    conn.close()
    return render(request, "admin_groups.html", {"mode": "admin", **context})


def build_disciplines_page_context(cur) -> dict[str, Any]:
    cur.execute("SELECT id, name FROM disciplines ORDER BY name")
    disciplines = [dict(row) for row in cur.fetchall()]

    for discipline in disciplines:
        discipline_id = int(discipline["id"])
        cur.execute("SELECT COUNT(*) AS cnt FROM teacher_disciplines WHERE discipline_id = ?", (discipline_id,))
        teacher_count = int(cur.fetchone()["cnt"])

        cur.execute("SELECT COUNT(*) AS cnt FROM lectures WHERE discipline_id = ?", (discipline_id,))
        lecture_count = int(cur.fetchone()["cnt"])

        cur.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM tests
            JOIN lectures ON lectures.id = tests.lecture_id
            WHERE lectures.discipline_id = ?
            """,
            (discipline_id,),
        )
        test_count = int(cur.fetchone()["cnt"])

        discipline["teacher_count"] = teacher_count
        discipline["lecture_count"] = lecture_count
        discipline["test_count"] = test_count

    return {"disciplines": disciplines}


def build_discipline_detail_context(cur, discipline_id: int) -> dict[str, Any] | None:
    cur.execute("SELECT id, name FROM disciplines WHERE id = ?", (discipline_id,))
    discipline = cur.fetchone()
    if not discipline:
        return None

    discipline_dict = dict(discipline)

    cur.execute(
        """
        SELECT u.id, u.full_name, u.email
        FROM teacher_disciplines td
        JOIN users u ON u.id = td.teacher_id
        WHERE td.discipline_id = ? AND u.role = 'teacher'
        ORDER BY u.full_name
        """,
        (discipline_id,),
    )
    assigned_teachers = [dict(row) for row in cur.fetchall()]
    assigned_ids = {int(row["id"]) for row in assigned_teachers}

    cur.execute("SELECT id, full_name, email FROM users WHERE role = 'teacher' ORDER BY full_name")
    all_teachers = [dict(row) for row in cur.fetchall()]
    available_teachers = [t for t in all_teachers if int(t["id"]) not in assigned_ids]

    cur.execute("SELECT COUNT(*) AS cnt FROM lectures WHERE discipline_id = ?", (discipline_id,))
    lecture_count = int(cur.fetchone()["cnt"])

    cur.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM tests
        JOIN lectures ON lectures.id = tests.lecture_id
        WHERE lectures.discipline_id = ?
        """,
        (discipline_id,),
    )
    test_count = int(cur.fetchone()["cnt"])

    discipline_dict["lecture_count"] = lecture_count
    discipline_dict["test_count"] = test_count

    return {
        "discipline": discipline_dict,
        "assigned_teachers": assigned_teachers,
        "available_teachers": available_teachers,
    }


@app.get("/admin/disciplines", response_class=HTMLResponse)
def admin_disciplines(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)

    conn = connect()
    cur = conn.cursor()
    context = build_disciplines_page_context(cur)
    conn.close()
    return render(request, "admin_disciplines.html", {"mode": "admin", **context})


@app.get("/v1/admin/disciplines", response_class=HTMLResponse)
def v1_admin_disciplines(request: Request):
    if not admin_panel_auth(request):
        return RedirectResponse("/v1/admin", status_code=302)

    conn = connect()
    cur = conn.cursor()
    context = build_disciplines_page_context(cur)
    conn.close()
    return templates.TemplateResponse("admin_disciplines.html", {"request": request, "mode": "v1_admin", **context})


@app.get("/admin/disciplines/{discipline_id}", response_class=HTMLResponse)
def admin_discipline_detail(request: Request, discipline_id: int):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)

    conn = connect()
    cur = conn.cursor()
    context = build_discipline_detail_context(cur, discipline_id)
    conn.close()
    if not context:
        add_flash(request, "Дисциплина не найдена", "error")
        return RedirectResponse("/admin/disciplines", status_code=302)
    return render(request, "admin_discipline_detail.html", {"mode": "admin", **context})


@app.get("/v1/admin/disciplines/{discipline_id}", response_class=HTMLResponse)
def v1_admin_discipline_detail(request: Request, discipline_id: int):
    if not admin_panel_auth(request):
        return RedirectResponse("/v1/admin", status_code=302)

    conn = connect()
    cur = conn.cursor()
    context = build_discipline_detail_context(cur, discipline_id)
    conn.close()
    if not context:
        add_flash(request, "Дисциплина не найдена", "error")
        return RedirectResponse("/v1/admin/disciplines", status_code=302)
    return templates.TemplateResponse("admin_discipline_detail.html", {"request": request, "mode": "v1_admin", **context})


@app.post("/admin/disciplines/{discipline_id}/assign-teacher")
def admin_assign_teacher_to_discipline(request: Request, discipline_id: int, teacher_id: int = Form(...)):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id FROM disciplines WHERE id = ?", (discipline_id,))
    discipline = cur.fetchone()
    cur.execute("SELECT id FROM users WHERE id = ? AND role = 'teacher'", (teacher_id,))
    teacher = cur.fetchone()
    if not discipline or not teacher:
        conn.close()
        add_flash(request, "Некорректная дисциплина или преподаватель", "error")
        return RedirectResponse("/admin/disciplines", status_code=302)

    cur.execute(
        "SELECT 1 FROM teacher_disciplines WHERE teacher_id = ? AND discipline_id = ?",
        (teacher_id, discipline_id),
    )
    if cur.fetchone():
        conn.close()
        add_flash(request, "Преподаватель уже назначен", "info")
        return RedirectResponse(f"/admin/disciplines/{discipline_id}", status_code=302)

    cur.execute(
        "INSERT INTO teacher_disciplines (teacher_id, discipline_id) VALUES (?, ?)",
        (teacher_id, discipline_id),
    )
    sync_teacher_group_assignments(cur, int(teacher_id), discipline_id=int(discipline_id))
    conn.commit()
    conn.close()
    add_flash(request, "Преподаватель назначен", "success")
    return RedirectResponse(f"/admin/disciplines/{discipline_id}", status_code=302)


@app.post("/admin/disciplines/{discipline_id}/unassign-teacher")
def admin_unassign_teacher_from_discipline(request: Request, discipline_id: int, teacher_id: int = Form(...)):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)

    conn = connect()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM teacher_disciplines WHERE teacher_id = ? AND discipline_id = ?",
        (teacher_id, discipline_id),
    )
    detach_teacher_discipline_assignments(cur, int(teacher_id), int(discipline_id))
    conn.commit()
    conn.close()
    add_flash(request, "Преподаватель снят с дисциплины", "success")
    return RedirectResponse(f"/admin/disciplines/{discipline_id}", status_code=302)


@app.post("/v1/admin/disciplines/{discipline_id}/assign-teacher")
def v1_admin_assign_teacher_to_discipline(request: Request, discipline_id: int, teacher_id: int = Form(...)):
    if not admin_panel_auth(request):
        return RedirectResponse("/v1/admin", status_code=302)

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id FROM disciplines WHERE id = ?", (discipline_id,))
    discipline = cur.fetchone()
    cur.execute("SELECT id FROM users WHERE id = ? AND role = 'teacher'", (teacher_id,))
    teacher = cur.fetchone()
    if not discipline or not teacher:
        conn.close()
        add_flash(request, "Некорректная дисциплина или преподаватель", "error")
        return RedirectResponse("/v1/admin/disciplines", status_code=302)

    cur.execute(
        "SELECT 1 FROM teacher_disciplines WHERE teacher_id = ? AND discipline_id = ?",
        (teacher_id, discipline_id),
    )
    if cur.fetchone():
        conn.close()
        add_flash(request, "Преподаватель уже назначен", "info")
        return RedirectResponse(f"/v1/admin/disciplines/{discipline_id}", status_code=302)

    cur.execute(
        "INSERT INTO teacher_disciplines (teacher_id, discipline_id) VALUES (?, ?)",
        (teacher_id, discipline_id),
    )
    sync_teacher_group_assignments(cur, int(teacher_id), discipline_id=int(discipline_id))
    conn.commit()
    conn.close()
    add_flash(request, "Преподаватель назначен", "success")
    return RedirectResponse(f"/v1/admin/disciplines/{discipline_id}", status_code=302)


@app.post("/v1/admin/disciplines/{discipline_id}/unassign-teacher")
def v1_admin_unassign_teacher_from_discipline(request: Request, discipline_id: int, teacher_id: int = Form(...)):
    if not admin_panel_auth(request):
        return RedirectResponse("/v1/admin", status_code=302)

    conn = connect()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM teacher_disciplines WHERE teacher_id = ? AND discipline_id = ?",
        (teacher_id, discipline_id),
    )
    detach_teacher_discipline_assignments(cur, int(teacher_id), int(discipline_id))
    conn.commit()
    conn.close()
    add_flash(request, "Преподаватель снят с дисциплины", "success")
    return RedirectResponse(f"/v1/admin/disciplines/{discipline_id}", status_code=302)


@app.get("/v1/admin/groups/{group_name}", response_class=HTMLResponse)
def v1_admin_group(request: Request, group_name: str):
    if not admin_panel_auth(request):
        return RedirectResponse("/v1/admin", status_code=302)
    name = group_name.replace("_", " ").strip()
    conn = connect()
    cur = conn.cursor()
    context = build_groups_page_context(cur, name)
    conn.close()
    return templates.TemplateResponse("admin_groups.html", {"request": request, "mode": "v1_admin", **context})


@app.get("/v1/admin/groups", response_class=HTMLResponse)
def v1_admin_groups(request: Request):
    if not admin_panel_auth(request):
        return RedirectResponse("/v1/admin", status_code=302)
    selected_name = (request.query_params.get("group") or "").replace("_", " ").strip()
    conn = connect()
    cur = conn.cursor()
    context = build_groups_page_context(cur, selected_name if selected_name else None)
    conn.close()
    return templates.TemplateResponse("admin_groups.html", {"request": request, "mode": "v1_admin", **context})


@app.get("/admin/groups/{group_name}", response_class=HTMLResponse)
def admin_group(request: Request, group_name: str):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)
    name = group_name.replace("_", " ").strip()
    conn = connect()
    cur = conn.cursor()
    context = build_groups_page_context(cur, name)
    conn.close()
    return render(request, "admin_groups.html", {"mode": "admin", **context})
# --- Separate admin panel (v1) with its own simple auth ---
def admin_panel_auth(request: Request) -> bool:
    # Legacy v1 admin routes must verify both the admin marker and the actual user role.
    user = get_current_user(request)
    return bool(request.session.get("admin_authenticated") and user and user.get("role") == "admin")


def ensure_start_session_cookie(request: Request) -> None:
    # Backward-compatible no-op: keep calls in handlers, but do not enforce
    # auxiliary cookie presence. Real access control is done via get_current_user().
    return None


@app.get("/v1/admin", response_class=HTMLResponse)
def v1_admin_index(request: Request):
    return RedirectResponse("/v2/admin", status_code=302)


@app.post("/v1/admin/login")
def v1_admin_login(
    request: Request,
    login: str = Form(""),
    email: str = Form(""),
    password: str = Form(...),
):
    return RedirectResponse("/v2/admin", status_code=302)


@app.get("/v2/admin", response_class=HTMLResponse)
def v2_admin_index(request: Request):
    user = get_current_user(request)
    if user and user.get("role") == "admin":
        return RedirectResponse("/admin/students", status_code=302)
    return templates.TemplateResponse(
        "admin_login.html",
        {"request": request, "error": None, "login_action": "/v2/admin/login"},
    )


@app.get("/v2/admin/disciplines", response_class=HTMLResponse)
def v2_admin_disciplines_alias(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse("/admin/disciplines", status_code=302)


@app.post("/v2/admin/disciplines/create")
def v2_admin_create_discipline_alias(request: Request, discipline_name: str = Form(...)):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)
    return admin_create_discipline(request, discipline_name)


@app.get("/v2/admin/disciplines/{discipline_id}", response_class=HTMLResponse)
def v2_admin_discipline_detail_alias(request: Request, discipline_id: int):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse(f"/admin/disciplines/{discipline_id}", status_code=302)


@app.post("/v2/admin/disciplines/{discipline_id}/assign-teacher")
def v2_admin_assign_teacher_alias(request: Request, discipline_id: int, teacher_id: int = Form(...)):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)
    return admin_assign_teacher_to_discipline(request, discipline_id, teacher_id)


@app.post("/v2/admin/disciplines/{discipline_id}/unassign-teacher")
def v2_admin_unassign_teacher_alias(request: Request, discipline_id: int, teacher_id: int = Form(...)):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)
    return admin_unassign_teacher_from_discipline(request, discipline_id, teacher_id)


@app.post("/v2/admin/login")
def v2_admin_login(
    request: Request,
    login: str = Form(""),
    email: str = Form(""),
    password: str = Form(...),
):
    raw_login = (login or email or "").strip()
    clean_login = validate_login(raw_login)
    if not clean_login:
        return templates.TemplateResponse(
            "admin_login.html",
            {
                "request": request,
                "error": "Укажите корректный логин.",
                "login_action": "/v2/admin/login",
                "login": raw_login,
            },
        )

    client_ip = request.client.host if request.client else "unknown"
    if login_limiter.is_blocked(client_ip):
        wait = login_limiter.remaining_seconds(client_ip)
        return templates.TemplateResponse(
            "admin_login.html",
            {
                "request": request,
                "error": f"Слишком много попыток. Подождите {wait} сек.",
                "login_action": "/v2/admin/login",
                "login": raw_login,
            },
        )
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email = ?", (clean_login,))
    row = cur.fetchone()
    conn.close()
    if not row:
        login_limiter.record(client_ip)
        return templates.TemplateResponse(
            "admin_login.html",
            {
                "request": request,
                "error": "Неверный логин или пароль.",
                "login_action": "/v2/admin/login",
                "login": raw_login,
            },
        )
    if not verify_password(password, row["salt"], row["password_hash"]):
        login_limiter.record(client_ip)
        return templates.TemplateResponse(
            "admin_login.html",
            {
                "request": request,
                "error": "Неверный логин или пароль.",
                "login_action": "/v2/admin/login",
                "login": raw_login,
            },
        )
    if row["role"] != "admin":
        return templates.TemplateResponse(
            "admin_login.html",
            {
                "request": request,
                "error": "Учетная запись не является админом.",
                "login_action": "/v2/admin/login",
                "login": raw_login,
            },
        )
    login_limiter.reset(client_ip)
    # rehash if legacy
    if needs_rehash(row["password_hash"]):
        s = new_salt()
        h = hash_password(password, s)
        conn = connect()
        cur = conn.cursor()
        cur.execute("UPDATE users SET password_hash = ?, salt = ? WHERE id = ?", (h, s, row["id"]))
        conn.commit()
        conn.close()
    establish_user_session(
        request,
        user_id=row["id"],
        email=row["email"],
        role="admin",
        session_version=_coerce_session_version(row["session_version"]),
    )
    if user_must_change_password(row):
        add_flash_once(
            request,
            "Пароль был сброшен. Используйте временный пароль, выданный преподавателем или администратором, и сразу задайте новый пароль в личном кабинете.",
            "error",
        )
        return RedirectResponse("/dashboard#profile-settings", status_code=302)
    return RedirectResponse("/admin/students", status_code=302)


@app.post("/v1/admin/logout")
def v1_admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/v2/admin", status_code=302)


@app.get("/v1/admin/dashboard", response_class=HTMLResponse)
def v1_admin_dashboard(request: Request):
    if not admin_panel_auth(request):
        return RedirectResponse("/v1/admin", status_code=302)
    return templates.TemplateResponse("admin_dashboard.html", {"request": request})


@app.get("/v1/admin/users", response_class=HTMLResponse)
def v1_admin_users_redirect(request: Request):
    if not admin_panel_auth(request):
        return RedirectResponse("/v1/admin", status_code=302)
    return RedirectResponse("/v1/admin/students", status_code=302)


@app.get("/v1/admin/students", response_class=HTMLResponse)
def v1_admin_students(request: Request):
    if not admin_panel_auth(request):
        return RedirectResponse("/v1/admin", status_code=302)
    q = request.query_params.get("q", "").strip()
    conn = connect()
    cur = conn.cursor()
    users = fetch_users_by_role(cur, "student", q)
    grouped_students = group_students_by_group(users)
    conn.close()
    return templates.TemplateResponse(
        "admin_users.html",
        {
            "request": request,
            "users": users,
            "grouped_students": grouped_students,
            "mode": "v1_admin",
            "query": q,
            "page_kind": "students",
            "page_title": "Студенты",
        },
    )


@app.get("/v1/admin/teachers", response_class=HTMLResponse)
def v1_admin_teachers(request: Request):
    if not admin_panel_auth(request):
        return RedirectResponse("/v1/admin", status_code=302)
    q = request.query_params.get("q", "").strip()
    conn = connect()
    cur = conn.cursor()
    users = fetch_users_by_role(cur, "teacher", q)
    conn.close()
    return templates.TemplateResponse("admin_users.html", {"request": request, "users": users, "mode": "v1_admin", "query": q, "page_kind": "teachers", "page_title": "Преподаватели"})


@app.post("/v1/admin/groups/create")
def v1_admin_create_group(request: Request, group_name: str = Form(...), teacher_id: str = Form("")):
    if not admin_panel_auth(request):
        return RedirectResponse("/v1/admin", status_code=302)
    normalized_name = (group_name or "").strip()
    if not normalized_name:
        add_flash(request, "Название группы обязательно", "error")
        return RedirectResponse("/v1/admin/groups", status_code=302)
    if not teacher_id:
        add_flash(request, "Для группы нужно выбрать преподавателя", "error")
        return RedirectResponse("/v1/admin/groups", status_code=302)
    assigned_teacher = None
    if teacher_id:
        try:
            assigned_teacher = int(teacher_id)
        except ValueError:
            assigned_teacher = None
    if not assigned_teacher:
        add_flash(request, "Некорректный преподаватель", "error")
        return RedirectResponse("/v1/admin/groups", status_code=302)

    conn = connect()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM users WHERE id = ? AND role = 'teacher'", (assigned_teacher,))
        if not cur.fetchone():
            conn.close()
            add_flash(request, "Выбранный преподаватель не найден", "error")
            return RedirectResponse("/v1/admin/groups", status_code=302)
        cur.execute("INSERT INTO groups (name, teacher_id) VALUES (?, ?)", (normalized_name, assigned_teacher))
        add_group_teacher(cur, normalized_name, int(assigned_teacher))
        conn.commit()
    except Exception:
        conn.close()
        add_flash(request, "Не удалось создать группу (возможно, уже существует)", "error")
        return RedirectResponse("/v1/admin/groups", status_code=302)
    conn.close()
    audit_log(request, "create_group", details=f"group={normalized_name}, teacher_id={assigned_teacher}")
    add_flash(request, "Группа создана и преподаватель привязан", "success")
    return RedirectResponse(f"/v1/admin/groups/{normalized_name.replace(' ', '_')}", status_code=302)


@app.post("/admin/groups/create")
def admin_create_group(request: Request, group_name: str = Form(...), teacher_id: str = Form("")):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)
    normalized_name = (group_name or "").strip()
    if not normalized_name:
        add_flash(request, "Название группы обязательно", "error")
        return RedirectResponse("/admin/groups", status_code=302)
    if not teacher_id:
        add_flash(request, "Для группы нужно выбрать преподавателя", "error")
        return RedirectResponse("/admin/groups", status_code=302)
    assigned_teacher = None
    if teacher_id:
        try:
            assigned_teacher = int(teacher_id)
        except ValueError:
            assigned_teacher = None
    if not assigned_teacher:
        add_flash(request, "Некорректный преподаватель", "error")
        return RedirectResponse("/admin/groups", status_code=302)

    conn = connect()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM users WHERE id = ? AND role = 'teacher'", (assigned_teacher,))
        if not cur.fetchone():
            conn.close()
            add_flash(request, "Выбранный преподаватель не найден", "error")
            return RedirectResponse("/admin/groups", status_code=302)
        cur.execute("INSERT INTO groups (name, teacher_id) VALUES (?, ?)", (normalized_name, assigned_teacher))
        add_group_teacher(cur, normalized_name, int(assigned_teacher))
        conn.commit()
    except Exception:
        conn.close()
        add_flash(request, "Не удалось создать группу (возможно, уже существует)", "error")
        return RedirectResponse("/admin/groups", status_code=302)
    conn.close()
    audit_log(request, "create_group", details=f"group={normalized_name}, teacher_id={assigned_teacher}")
    add_flash(request, "Группа создана и преподаватель привязан", "success")
    return RedirectResponse(f"/admin/groups/{normalized_name.replace(' ', '_')}", status_code=302)


@app.post("/admin/disciplines/create")
def admin_create_discipline(request: Request, discipline_name: str = Form(...)):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)

    normalized_name = normalize_discipline_name(discipline_name)
    if not normalized_name:
        add_flash(request, "Название дисциплины обязательно", "error")
        return RedirectResponse("/admin/disciplines", status_code=302)

    conn = connect()
    cur = conn.cursor()
    try:
        _, created = create_or_get_discipline(cur, normalized_name)
        conn.commit()
    except Exception:
        conn.close()
        add_flash(request, "Не удалось добавить дисциплину", "error")
        return RedirectResponse("/admin/disciplines", status_code=302)
    conn.close()

    audit_log(request, "create_discipline", details=f"admin_id={user['id']}, discipline={normalized_name}, created={created}")
    if created:
        add_flash(request, "Дисциплина создана", "success")
    else:
        add_flash(request, "Такая дисциплина уже существует", "info")
    return RedirectResponse("/admin/disciplines", status_code=302)


@app.post("/v1/admin/disciplines/create")
def v1_admin_create_discipline(request: Request, discipline_name: str = Form(...)):
    if not admin_panel_auth(request):
        return RedirectResponse("/v1/admin", status_code=302)

    normalized_name = normalize_discipline_name(discipline_name)
    if not normalized_name:
        add_flash(request, "Название дисциплины обязательно", "error")
        return RedirectResponse("/v1/admin/disciplines", status_code=302)

    conn = connect()
    cur = conn.cursor()
    try:
        _, created = create_or_get_discipline(cur, normalized_name)
        conn.commit()
    except Exception:
        conn.close()
        add_flash(request, "Не удалось добавить дисциплину", "error")
        return RedirectResponse("/v1/admin/disciplines", status_code=302)
    conn.close()

    audit_log(request, "create_discipline", details=f"v1_admin discipline={normalized_name}, created={created}")
    if created:
        add_flash(request, "Дисциплина создана", "success")
    else:
        add_flash(request, "Такая дисциплина уже существует", "info")
    return RedirectResponse("/v1/admin/disciplines", status_code=302)


@app.post("/v1/admin/groups/{group_name}/teacher")
def v1_admin_bind_group_teacher(request: Request, group_name: str, teacher_id: str = Form(...)):
    if not admin_panel_auth(request):
        return RedirectResponse("/v1/admin", status_code=302)
    try:
        teacher_value = int(teacher_id)
    except ValueError:
        add_flash(request, "Некорректный преподаватель", "error")
        return RedirectResponse(f"/v1/admin/groups/{group_name}", status_code=302)

    name = group_name.replace("_", " ").strip()
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM groups WHERE name = ?", (name,))
    if not cur.fetchone():
        conn.close()
        add_flash(request, "Группа не найдена", "error")
        return RedirectResponse("/v1/admin/groups", status_code=302)
    cur.execute("SELECT id FROM users WHERE id = ? AND role = 'teacher'", (teacher_value,))
    if not cur.fetchone():
        conn.close()
        add_flash(request, "Выбранный преподаватель не найден", "error")
        return RedirectResponse(f"/v1/admin/groups/{group_name}", status_code=302)
    linked = add_group_teacher(cur, name, teacher_value)
    conn.commit()
    conn.close()
    audit_log(request, "bind_group_teacher", details=f"group={name}, teacher_id={teacher_value}, linked={linked}")
    if linked:
        add_flash(request, "Преподаватель добавлен к группе", "success")
    else:
        add_flash(request, "Этот преподаватель уже добавлен к группе", "info")
    return RedirectResponse(f"/v1/admin/groups/{group_name}", status_code=302)


@app.post("/admin/groups/{group_name}/teacher")
def admin_bind_group_teacher(request: Request, group_name: str, teacher_id: str = Form(...)):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)
    try:
        teacher_value = int(teacher_id)
    except ValueError:
        add_flash(request, "Некорректный преподаватель", "error")
        return RedirectResponse(f"/admin/groups/{group_name}", status_code=302)

    name = group_name.replace("_", " ").strip()
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM groups WHERE name = ?", (name,))
    if not cur.fetchone():
        conn.close()
        add_flash(request, "Группа не найдена", "error")
        return RedirectResponse("/admin/groups", status_code=302)
    cur.execute("SELECT id FROM users WHERE id = ? AND role = 'teacher'", (teacher_value,))
    if not cur.fetchone():
        conn.close()
        add_flash(request, "Выбранный преподаватель не найден", "error")
        return RedirectResponse(f"/admin/groups/{group_name}", status_code=302)
    linked = add_group_teacher(cur, name, teacher_value)
    conn.commit()
    conn.close()
    audit_log(request, "bind_group_teacher", details=f"group={name}, teacher_id={teacher_value}, linked={linked}")
    if linked:
        add_flash(request, "Преподаватель добавлен к группе", "success")
    else:
        add_flash(request, "Этот преподаватель уже добавлен к группе", "info")
    return RedirectResponse(f"/admin/groups/{group_name}", status_code=302)


@app.post("/v1/admin/groups/{group_name}/teachers/{teacher_id}/delete")
def v1_admin_unbind_group_teacher(request: Request, group_name: str, teacher_id: int):
    if not admin_panel_auth(request):
        return RedirectResponse("/v1/admin", status_code=302)
    name = group_name.replace("_", " ").strip()
    conn = connect()
    cur = conn.cursor()
    removed = remove_group_teacher(cur, name, int(teacher_id))
    conn.commit()
    conn.close()
    audit_log(request, "unbind_group_teacher", details=f"group={name}, teacher_id={teacher_id}, removed={removed}")
    if removed:
        add_flash(request, "Преподаватель убран из группы", "success")
    else:
        add_flash(request, "Связь преподавателя с группой не найдена", "info")
    return RedirectResponse(f"/v1/admin/groups/{group_name}", status_code=302)


@app.post("/v1/admin/groups/{group_name}/delete")
def v1_admin_delete_group(request: Request, group_name: str):
    if not admin_panel_auth(request):
        return RedirectResponse("/v1/admin", status_code=302)
    name = group_name.replace("_", " ").strip()
    conn = connect()
    cur = conn.cursor()
    deleted, message = delete_group_if_empty(cur, name)
    conn.commit()
    conn.close()
    audit_log(request, "delete_group", details=f"group={name}, deleted={deleted}")
    add_flash(request, message, "success" if deleted else "error")
    return RedirectResponse("/v1/admin/groups", status_code=302)


@app.post("/admin/groups/{group_name}/teachers/{teacher_id}/delete")
def admin_unbind_group_teacher(request: Request, group_name: str, teacher_id: int):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)
    name = group_name.replace("_", " ").strip()
    conn = connect()
    cur = conn.cursor()
    removed = remove_group_teacher(cur, name, int(teacher_id))
    conn.commit()
    conn.close()
    audit_log(request, "unbind_group_teacher", details=f"group={name}, teacher_id={teacher_id}, removed={removed}")
    if removed:
        add_flash(request, "Преподаватель убран из группы", "success")
    else:
        add_flash(request, "Связь преподавателя с группой не найдена", "info")
    return RedirectResponse(f"/admin/groups/{group_name}", status_code=302)


@app.post("/admin/groups/{group_name}/delete")
def admin_delete_group(request: Request, group_name: str):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)
    name = group_name.replace("_", " ").strip()
    conn = connect()
    cur = conn.cursor()
    deleted, message = delete_group_if_empty(cur, name)
    conn.commit()
    conn.close()
    audit_log(request, "delete_group", details=f"group={name}, deleted={deleted}")
    add_flash(request, message, "success" if deleted else "error")
    return RedirectResponse("/admin/groups", status_code=302)


@app.post("/v1/admin/groups/assign")
def v1_admin_assign_group(request: Request, student_id: int = Form(...), student_group: str = Form("")):
    if not admin_panel_auth(request):
        return RedirectResponse("/v1/admin", status_code=302)
    conn = connect()
    cur = conn.cursor()
    normalized_group = "" if (student_group or "").strip() == "__none__" else (student_group or "").strip()
    teacher_id = find_group_teacher_id(cur, normalized_group)
    cur.execute("UPDATE users SET student_group = ?, assigned_teacher_id = ? WHERE id = ? AND role = 'student'", (normalized_group, teacher_id, student_id))
    conn.commit()
    conn.close()
    audit_log(request, "assign_group", target_user_id=student_id, details=f"set to {normalized_group}")
    add_flash(request, "Студент добавлен в группу", "success")
    if normalized_group:
        return RedirectResponse(f"/v1/admin/groups/{normalized_group.replace(' ', '_')}", status_code=302)
    return RedirectResponse("/v1/admin/groups/Без_группы", status_code=302)


@app.post("/admin/groups/assign")
def admin_assign_group(request: Request, student_id: int = Form(...), student_group: str = Form("")):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)
    conn = connect()
    cur = conn.cursor()
    normalized_group = "" if (student_group or "").strip() == "__none__" else (student_group or "").strip()
    teacher_id = find_group_teacher_id(cur, normalized_group)
    cur.execute("UPDATE users SET student_group = ?, assigned_teacher_id = ? WHERE id = ? AND role = 'student'", (normalized_group, teacher_id, student_id))
    conn.commit()
    conn.close()
    audit_log(request, "assign_group", target_user_id=student_id, details=f"set to {normalized_group}")
    add_flash(request, "Студент добавлен в группу", "success")
    if normalized_group:
        return RedirectResponse(f"/admin/groups/{normalized_group.replace(' ', '_')}", status_code=302)
    return RedirectResponse("/admin/groups/Без_группы", status_code=302)


@app.post("/v1/admin/users/{user_id}/set_group")
def v1_admin_set_group(request: Request, user_id: int, student_group: str = Form("")):
    if not admin_panel_auth(request):
        return RedirectResponse("/v1/admin", status_code=302)
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT student_group FROM users WHERE id = ?", (user_id,))
    prev = cur.fetchone()
    prev_group = prev[0] if prev else None
    normalized_group = (student_group or "").strip()
    teacher_id = find_group_teacher_id(cur, normalized_group)
    cur.execute("UPDATE users SET student_group = ?, assigned_teacher_id = ? WHERE id = ?", (normalized_group, teacher_id, user_id))
    conn.commit()
    conn.close()
    if (prev_group or "") != ((student_group or "") or ""):
        audit_log(request, "change_group", target_user_id=user_id, details=f"from {prev_group} to {student_group}")
    add_flash(request, "Группа обновлена", "success")
    return RedirectResponse("/v1/admin/users", status_code=302)


@app.post("/admin/users/{user_id}/set_group")
def admin_set_group(request: Request, user_id: int, student_group: str = Form("")):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT student_group FROM users WHERE id = ?", (user_id,))
    prev = cur.fetchone()
    prev_group = prev[0] if prev else None
    normalized_group = (student_group or "").strip()
    teacher_id = find_group_teacher_id(cur, normalized_group)
    cur.execute("UPDATE users SET student_group = ?, assigned_teacher_id = ? WHERE id = ?", (normalized_group, teacher_id, user_id))
    conn.commit()
    conn.close()
    if (prev_group or "") != ((student_group or "") or ""):
        audit_log(request, "change_group", target_user_id=user_id, details=f"from {prev_group} to {student_group}")
    add_flash(request, "Группа обновлена", "success")
    return RedirectResponse("/admin/users", status_code=302)


@app.post("/v1/admin/users/create")
def v1_admin_create_user(
    request: Request,
    full_name: str = Form(...),
    login: str = Form(""),
    email: str = Form(""),
    password: str = Form(...),
    role: str = Form("teacher"),
    assigned_teacher_id: str = Form(""),
    student_group: str = Form(""),
):
    if not admin_panel_auth(request):
        return RedirectResponse("/v1/admin", status_code=302)
    target_page = "/v1/admin/teachers"
    normalized_role = (role or "teacher").strip().lower()
    if normalized_role != "teacher":
        add_flash(request, "Через эту форму можно создать только преподавателя.", "error")
        return RedirectResponse(target_page, status_code=302)

    clean_name = sanitize_full_name(full_name)
    if not clean_name:
        add_flash(request, "Укажите ФИО.", "error")
        return RedirectResponse(target_page, status_code=302)

    clean_login = validate_login(login or email)
    if not clean_login:
        add_flash(request, "Некорректный логин. Используйте 3-80 символов без пробелов.", "error")
        return RedirectResponse(target_page, status_code=302)

    pw_ok, pw_err = validate_password(password)
    if not pw_ok:
        add_flash(request, pw_err, "error")
        return RedirectResponse(target_page, status_code=302)

    salt = new_salt()
    password_hash = hash_password(password, salt)

    conn = connect()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (role, full_name, email, password_hash, salt, assigned_teacher_id, student_group) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "teacher",
                clean_name,
                clean_login,
                password_hash,
                salt,
                None,
                "",
            ),
        )
        new_user_id = cur.lastrowid
        conn.commit()
    except Exception:
        conn.close()
        add_flash(request, "Не удалось создать преподавателя (проверьте уникальность логина)", "error")
        return RedirectResponse(target_page, status_code=302)
    conn.close()

    audit_log(request, "create_user", target_user_id=new_user_id, details=f"created teacher {clean_login}")
    add_flash(request, "Преподаватель создан", "success")
    return RedirectResponse(target_page, status_code=302)


@app.post("/admin/users/create")
def admin_create_user(
    request: Request,
    full_name: str = Form(...),
    login: str = Form(""),
    email: str = Form(""),
    password: str = Form(...),
    role: str = Form("teacher"),
    assigned_teacher_id: str = Form(""),
    student_group: str = Form(""),
):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)
    target_page = "/admin/teachers"
    normalized_role = (role or "teacher").strip().lower()
    if normalized_role != "teacher":
        add_flash(request, "Через эту форму можно создать только преподавателя.", "error")
        return RedirectResponse(target_page, status_code=302)

    clean_name = sanitize_full_name(full_name)
    if not clean_name:
        add_flash(request, "Укажите ФИО.", "error")
        return RedirectResponse(target_page, status_code=302)

    clean_login = validate_login(login or email)
    if not clean_login:
        add_flash(request, "Некорректный логин. Используйте 3-80 символов без пробелов.", "error")
        return RedirectResponse(target_page, status_code=302)

    pw_ok, pw_err = validate_password(password)
    if not pw_ok:
        add_flash(request, pw_err, "error")
        return RedirectResponse(target_page, status_code=302)

    salt = new_salt()
    password_hash = hash_password(password, salt)

    conn = connect()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (role, full_name, email, password_hash, salt, assigned_teacher_id, student_group) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "teacher",
                clean_name,
                clean_login,
                password_hash,
                salt,
                None,
                "",
            ),
        )
        new_user_id = cur.lastrowid
        conn.commit()
    except Exception:
        conn.close()
        add_flash(request, "Не удалось создать преподавателя (проверьте уникальность логина)", "error")
        return RedirectResponse(target_page, status_code=302)
    conn.close()

    audit_log(request, "create_user", target_user_id=new_user_id, details=f"created teacher {clean_login}")
    add_flash(request, "Преподаватель создан", "success")
    return RedirectResponse(target_page, status_code=302)


@app.get("/v1/admin/users/{user_id}/edit", response_class=HTMLResponse)
def v1_admin_user_edit(request: Request, user_id: int):
    if not admin_panel_auth(request):
        return RedirectResponse("/v1/admin", status_code=302)
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id, role, full_name, email, last_login, assigned_teacher_id, student_group FROM users WHERE id = ?", (user_id,))
    raw = cur.fetchone()
    row = user_row_to_dict(raw) if raw else None
    if not row:
        conn.close()
        return RedirectResponse("/v1/admin/users", status_code=302)
    cur.execute("SELECT id, full_name FROM users WHERE role = 'teacher' ORDER BY full_name")
    teachers = [dict(r) for r in cur.fetchall()]
    conn.close()
    return templates.TemplateResponse("admin_user_edit.html", {"request": request, "mode": "v1_admin", "user": dict(row), "teachers": teachers})


@app.post("/v1/admin/users/{user_id}/edit")
def v1_admin_user_edit_post(
    request: Request,
    user_id: int,
    full_name: str = Form(...),
    login: str = Form(""),
    email: str = Form(""),
    role: str = Form(...),
    assigned_teacher_id: str = Form(""),
    student_group: str = Form(""),
):
    if not admin_panel_auth(request):
        return RedirectResponse("/v1/admin", status_code=302)

    clean_login = validate_login(login or email)
    if not clean_login:
        add_flash(request, "Некорректный логин. Используйте 3-80 символов без пробелов.", "error")
        return RedirectResponse(f"/v1/admin/users/{user_id}/edit", status_code=302)

    clean_name = sanitize_full_name(full_name)
    if not clean_name:
        add_flash(request, "Укажите ФИО.", "error")
        return RedirectResponse(f"/v1/admin/users/{user_id}/edit", status_code=302)

    assigned = None
    if assigned_teacher_id:
        try:
            assigned = int(assigned_teacher_id)
        except ValueError:
            assigned = None
    conn = connect()
    cur = conn.cursor()
    # read previous value for audit
    cur.execute("SELECT student_group FROM users WHERE id = ?", (user_id,))
    prev = cur.fetchone()
    prev_group = prev[0] if prev else None
    try:
        cur.execute(
            "UPDATE users SET full_name = ?, email = ?, role = ?, assigned_teacher_id = ?, student_group = ? WHERE id = ?",
            (clean_name, clean_login, role.strip(), assigned, (student_group or "").strip(), user_id),
        )
        conn.commit()
    except Exception:
        conn.close()
        add_flash(request, "Не удалось обновить пользователя (логин уже занят).", "error")
        return RedirectResponse(f"/v1/admin/users/{user_id}/edit", status_code=302)
    conn.close()
    # audit and flash
    if (prev_group or "") != ((student_group or "") or ""):
        audit_log(request, "change_group", target_user_id=user_id, details=f"from {prev_group} to {student_group}")
    add_flash(request, "Информация пользователя обновлена", "success")
    return RedirectResponse("/v1/admin/users", status_code=302)


@app.post("/v1/admin/users/{user_id}/delete")
def v1_admin_user_delete(request: Request, user_id: int):
    if not admin_panel_auth(request):
        return RedirectResponse("/v1/admin", status_code=302)
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT role FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return RedirectResponse("/v1/admin/users", status_code=302)
    # Only allow deleting students via admin panel for anti-fraud
    if row["role"] != "student":
        # do not delete non-students; just redirect back to users list
        conn.close()
        return RedirectResponse("/v1/admin/users", status_code=302)

    # remove dependent records: answers -> attempts -> user
    cur.execute("DELETE FROM answers WHERE attempt_id IN (SELECT id FROM attempts WHERE student_id = ?)", (user_id,))
    cur.execute("DELETE FROM attempts WHERE student_id = ?", (user_id,))
    cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    # audit log and flash
    audit_log(request, "delete_user", target_user_id=user_id, details="deleted student via v1 admin")
    add_flash(request, "Пользователь удалён", "success")
    return RedirectResponse("/v1/admin/users", status_code=302)


@app.post("/admin/users/{user_id}/delete")
def admin_user_delete(request: Request, user_id: int):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT role FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return RedirectResponse("/admin/users", status_code=302)
    if row["role"] != "student":
        conn.close()
        return RedirectResponse("/admin/users", status_code=302)

    cur.execute("DELETE FROM answers WHERE attempt_id IN (SELECT id FROM attempts WHERE student_id = ?)", (user_id,))
    cur.execute("DELETE FROM attempts WHERE student_id = ?", (user_id,))
    cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    audit_log(request, "delete_user", target_user_id=user_id, details="deleted student via admin")
    add_flash(request, "Пользователь удалён", "success")
    return RedirectResponse("/admin/users", status_code=302)


@app.get("/admin/users/{user_id}/edit", response_class=HTMLResponse)
def admin_user_edit(request: Request, user_id: int):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id, role, full_name, email, last_login, assigned_teacher_id, student_group FROM users WHERE id = ?", (user_id,))
    raw = cur.fetchone()
    row = user_row_to_dict(raw) if raw else None
    if not row:
        conn.close()
        return RedirectResponse("/admin/users", status_code=302)
    cur.execute("SELECT id, full_name FROM users WHERE role = 'teacher' ORDER BY full_name")
    teachers = [dict(r) for r in cur.fetchall()]
    conn.close()
    return render(request, "admin_user_edit.html", {"mode": "admin", "user": dict(row), "teachers": teachers})


@app.post("/admin/users/{user_id}/edit")
def admin_user_edit_post(
    request: Request,
    user_id: int,
    full_name: str = Form(...),
    login: str = Form(""),
    email: str = Form(""),
    role: str = Form(...),
    assigned_teacher_id: str = Form(""),
    student_group: str = Form(""),
):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/login", status_code=302)

    clean_login = validate_login(login or email)
    if not clean_login:
        add_flash(request, "Некорректный логин. Используйте 3-80 символов без пробелов.", "error")
        return RedirectResponse(f"/admin/users/{user_id}/edit", status_code=302)

    clean_name = sanitize_full_name(full_name)
    if not clean_name:
        add_flash(request, "Укажите ФИО.", "error")
        return RedirectResponse(f"/admin/users/{user_id}/edit", status_code=302)

    assigned = None
    if assigned_teacher_id:
        try:
            assigned = int(assigned_teacher_id)
        except ValueError:
            assigned = None
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT student_group FROM users WHERE id = ?", (user_id,))
    prev = cur.fetchone()
    prev_group = prev[0] if prev else None
    try:
        cur.execute(
            "UPDATE users SET full_name = ?, email = ?, role = ?, assigned_teacher_id = ?, student_group = ? WHERE id = ?",
            (clean_name, clean_login, role.strip(), assigned, (student_group or "").strip(), user_id),
        )
        conn.commit()
    except Exception:
        conn.close()
        add_flash(request, "Не удалось обновить пользователя (логин уже занят).", "error")
        return RedirectResponse(f"/admin/users/{user_id}/edit", status_code=302)
    conn.close()
    if (prev_group or "") != ((student_group or "") or ""):
        audit_log(request, "change_group", target_user_id=user_id, details=f"from {prev_group} to {student_group}")
    add_flash(request, "Информация пользователя обновлена", "success")
    return RedirectResponse("/admin/users", status_code=302)


@app.get("/teacher/analytics", response_class=HTMLResponse)
def teacher_analytics(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT attempts.score, attempts.taken_at, tests.id AS test_id,
               tests.title AS test_title, users.full_name AS student_name
        FROM attempts
        JOIN tests ON tests.id = attempts.test_id
        JOIN lectures ON lectures.id = tests.lecture_id
        JOIN users ON users.id = attempts.student_id
        JOIN teaching_assignments ta
          ON ta.teacher_id = lectures.teacher_id
         AND ta.discipline_id = lectures.discipline_id
         AND ta.group_name = COALESCE(users.student_group, '')
        WHERE lectures.teacher_id = ?
        ORDER BY attempts.taken_at DESC
        """,
        (user["id"],),
    )
    rows = cur.fetchall()

    cur.execute(
        """
        SELECT tests.id AS test_id, tests.title AS test_title
        FROM tests
        JOIN lectures ON lectures.id = tests.lecture_id
        WHERE lectures.teacher_id = ?
        ORDER BY tests.id DESC
        """,
        (user["id"],),
    )
    tests = {r["test_id"]: r["test_title"] for r in cur.fetchall()}
    conn.close()

    total_attempts = len(rows)
    unique_students = len({r["student_name"] for r in rows}) if rows else 0
    scores = [r["score"] for r in rows]
    overall_avg = round(sum(scores) / len(scores), 2) if scores else 0.0

    per_test: dict[int, dict[str, Any]] = {}
    for tid, title in tests.items():
        per_test[tid] = {
            "title": title,
            "attempts": 0,
            "avg": 0.0,
            "best": 0.0,
            "worst": 0.0,
        }

    for r in rows:
        tid = r["test_id"]
        entry = per_test.get(tid)
        if not entry:
            continue
        entry.setdefault("_scores", []).append(r["score"])

    for entry in per_test.values():
        scores_list = entry.pop("_scores", [])
        if scores_list:
            entry["attempts"] = len(scores_list)
            entry["avg"] = round(sum(scores_list) / len(scores_list), 2)
            entry["best"] = max(scores_list)
            entry["worst"] = min(scores_list)

    per_test_list = list(per_test.values())
    per_test_list.sort(key=lambda x: x["attempts"], reverse=True)

    student_totals: dict[str, list[float]] = {}
    for r in rows:
        student_totals.setdefault(r["student_name"], []).append(r["score"])
    student_avg = [
        {
            "name": name,
            "avg": round(sum(vals) / len(vals), 2),
            "attempts": len(vals),
        }
        for name, vals in student_totals.items()
    ]
    student_avg.sort(key=lambda x: x["avg"], reverse=True)

    return render(
        request,
        "teacher_analytics.html",
        {
            "total_attempts": total_attempts,
            "unique_students": unique_students,
            "overall_avg": overall_avg,
            "per_test": per_test_list,
            "student_avg": student_avg,
        },
    )


@app.get("/v1/teacher/users", response_class=HTMLResponse)
def v1_teacher_users(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)
    conn = connect()
    cur = conn.cursor()
    students = get_teacher_students(cur, int(user["id"]))
    grouped_students = group_students_by_group(students)
    conn.close()
    return templates.TemplateResponse(
        "admin_users.html",
        {
            "request": request,
            "users": students,
            "grouped_students": grouped_students,
            "mode": "v1_teacher",
            "query": "",
            "page_kind": "students",
            "page_title": "Студенты",
        },
    )


@app.get("/v1/teacher/groups/{group_name}", response_class=HTMLResponse)
def v1_teacher_group(request: Request, group_name: str):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)
    name = group_name.replace("_", " ")
    conn = connect()
    cur = conn.cursor()
    rows = [row for row in get_teacher_students(cur, int(user["id"])) if ((row.get("student_group") or "").strip() or "Без группы") == name]
    grouped_students = group_students_by_group(rows)
    conn.close()
    return templates.TemplateResponse(
        "admin_users.html",
        {
            "request": request,
            "users": rows,
            "grouped_students": grouped_students,
            "mode": "v1_teacher",
            "query": "",
            "page_kind": "students",
            "page_title": f"Студенты группы {name}",
        },
    )


@app.get("/v1/teacher/users/{user_id}/edit", response_class=HTMLResponse)
def v1_teacher_user_edit(request: Request, user_id: int):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id, role, full_name, email, last_login, assigned_teacher_id, student_group FROM users WHERE id = ?", (user_id,))
    raw = cur.fetchone()
    row = user_row_to_dict(raw) if raw else None
    if not row:
        conn.close()
        return RedirectResponse("/v1/teacher/users", status_code=302)
    # only allow teacher to edit students assigned to them
    if not teacher_can_manage_student(cur, int(user["id"]), int(user_id)):
        conn.close()
        return RedirectResponse("/v1/teacher/users", status_code=302)
    conn.close()
    return templates.TemplateResponse("admin_user_edit.html", {"request": request, "user": dict(row), "teachers": []})


@app.post("/v1/teacher/users/{user_id}/edit")
def v1_teacher_user_edit_post(request: Request, user_id: int, student_group: str = Form("")):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)
    conn = connect()
    cur = conn.cursor()
    if not teacher_can_manage_student(cur, int(user["id"]), int(user_id)):
        conn.close()
        return RedirectResponse("/v1/teacher/users", status_code=302)
    cur.execute("SELECT student_group FROM users WHERE id = ?", (user_id,))
    prev = cur.fetchone()
    prev_group = prev[0] if prev else None
    cur.execute("UPDATE users SET student_group = ? WHERE id = ?", ((student_group or "").strip(), user_id))
    conn.commit()
    conn.close()
    if (prev_group or "") != ((student_group or "") or ""):
        audit_log(request, "change_group_by_teacher", target_user_id=user_id, details=f"from {prev_group} to {student_group}")
    add_flash(request, "Группа обновлена", "success")
    return RedirectResponse("/v1/teacher/users", status_code=302)


@app.post("/v1/teacher/users/{user_id}/set_group")
def v1_teacher_set_group(request: Request, user_id: int, student_group: str = Form("")):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT student_group FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    if not row or not teacher_can_manage_student(cur, int(user["id"]), int(user_id)):
        conn.close()
        add_flash(request, "Нет доступа для изменения группы этого студента", "error")
        return RedirectResponse("/v1/teacher/users", status_code=302)
    prev_group = row["student_group"]
    cur.execute("UPDATE users SET student_group = ? WHERE id = ?", ((student_group or "").strip(), user_id))
    conn.commit()
    conn.close()
    if (prev_group or "") != ((student_group or "") or ""):
        audit_log(request, "change_group_by_teacher", target_user_id=user_id, details=f"from {prev_group} to {student_group}")
    add_flash(request, "Группа обновлена", "success")
    return RedirectResponse("/v1/teacher/users", status_code=302)


@app.get("/v2/teacher", response_class=HTMLResponse)
def v2_teacher_index(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse("/v2/teacher/disciplines", status_code=302)


@app.get("/teacher/disciplines", response_class=HTMLResponse)
def teacher_disciplines_alias(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse("/v2/teacher/disciplines", status_code=302)


@app.get("/v2/teacher/disciplines", response_class=HTMLResponse)
def v2_teacher_disciplines(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)

    conn = connect()
    cur = conn.cursor()
    if sync_teacher_attempt_group_assignments(cur, int(user["id"])):
        conn.commit()
    cur.execute(
        """
        SELECT d.id, d.name
        FROM teacher_disciplines td
        JOIN disciplines d ON d.id = td.discipline_id
        WHERE td.teacher_id = ?
        ORDER BY d.name
        """,
        (user["id"],),
    )
    disciplines = [dict(row) for row in cur.fetchall()]
    assigned_ids = {int(item["id"]) for item in disciplines}
    all_group_names = get_all_group_names(cur)

    cur.execute("SELECT id, name FROM disciplines ORDER BY name")
    all_disciplines = [dict(row) for row in cur.fetchall()]
    available_disciplines = [d for d in all_disciplines if int(d["id"]) not in assigned_ids]

    cur.execute(
        """
        SELECT ta.discipline_id, ta.group_name
        FROM teaching_assignments ta
        WHERE ta.teacher_id = ?
        ORDER BY ta.group_name
        """,
        (user["id"],),
    )
    assigned_groups_by_discipline: dict[int, list[str]] = {}
    for row in cur.fetchall():
        discipline_id = int(row["discipline_id"])
        group_name = normalize_group_name(row["group_name"])
        if not group_name:
            continue
        assigned_groups_by_discipline.setdefault(discipline_id, []).append(group_name)

    for discipline in disciplines:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM lectures WHERE teacher_id = ? AND discipline_id = ?",
            (user["id"], discipline["id"]),
        )
        lecture_count = int(cur.fetchone()["cnt"])

        cur.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM tests
            JOIN lectures ON lectures.id = tests.lecture_id
            WHERE lectures.teacher_id = ? AND lectures.discipline_id = ?
            """,
            (user["id"], discipline["id"]),
        )
        test_count = int(cur.fetchone()["cnt"])

        cur.execute(
            """
            SELECT COUNT(DISTINCT u.id) AS cnt
            FROM users u
            JOIN teaching_assignments ta
              ON ta.teacher_id = ?
             AND ta.discipline_id = ?
             AND ta.group_name = COALESCE(u.student_group, '')
            WHERE u.role = 'student'
            """,
            (user["id"], discipline["id"]),
        )
        student_count = int(cur.fetchone()["cnt"])

        cur.execute(
            """
            SELECT COUNT(DISTINCT ta.group_name) AS cnt
            FROM teaching_assignments ta
            WHERE ta.teacher_id = ? AND ta.discipline_id = ?
            """,
            (user["id"], discipline["id"]),
        )
        group_count = int(cur.fetchone()["cnt"])

        discipline["lecture_count"] = lecture_count
        discipline["test_count"] = test_count
        discipline["student_count"] = student_count
        discipline["group_count"] = group_count
        assigned_groups = sorted(
            {group for group in assigned_groups_by_discipline.get(int(discipline["id"]), []) if group},
            key=natural_group_sort_key,
        )
        discipline["assigned_groups"] = assigned_groups
        discipline["available_groups"] = [
            group_name for group_name in all_group_names if group_name not in set(assigned_groups)
        ]

    conn.close()
    return render(
        request,
        "v2_teacher_disciplines.html",
        {
            "active_tab": "disciplines",
            "disciplines": disciplines,
            "available_disciplines": available_disciplines,
        },
    )


@app.post("/v2/teacher/disciplines/create")
def v2_teacher_create_discipline(request: Request, discipline_name: str = Form(...)):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)

    normalized_name = normalize_discipline_name(discipline_name)
    if not normalized_name:
        add_flash(request, "Название дисциплины обязательно", "error")
        return RedirectResponse("/v2/teacher/disciplines", status_code=302)

    conn = connect()
    cur = conn.cursor()
    try:
        discipline_id, created = create_or_get_discipline(cur, normalized_name)
        assigned = bool(
            insert_ignore(
                cur,
                "teacher_disciplines",
                ("teacher_id", "discipline_id"),
                (user["id"], discipline_id),
                conflict_columns=("teacher_id", "discipline_id"),
            )
        )
        sync_teacher_group_assignments(cur, int(user["id"]), discipline_id=int(discipline_id))
        conn.commit()
    except Exception:
        conn.close()
        add_flash(request, "Не удалось добавить дисциплину", "error")
        return RedirectResponse("/v2/teacher/disciplines", status_code=302)
    conn.close()

    audit_log(
        request,
        "create_discipline",
        details=f"teacher_id={user['id']}, discipline={normalized_name}, created={created}, assigned={assigned}",
    )
    if created:
        add_flash(request, "Дисциплина создана и добавлена в ваш список", "success")
    elif assigned:
        add_flash(request, "Дисциплина уже существовала и добавлена в ваш список", "success")
    else:
        add_flash(request, "Эта дисциплина уже есть в вашем списке", "info")
    return RedirectResponse("/v2/teacher/disciplines", status_code=302)


@app.get("/v2/teacher/disciplines/create")
def v2_teacher_create_discipline_get(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse("/v2/teacher/disciplines", status_code=302)


@app.post("/v2/teacher/disciplines/attach")
def v2_teacher_attach_discipline(request: Request, discipline_id: int = Form(...)):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM disciplines WHERE id = ?", (discipline_id,))
    discipline = cur.fetchone()
    if not discipline:
        conn.close()
        add_flash(request, "Дисциплина не найдена", "error")
        return RedirectResponse("/v2/teacher/disciplines", status_code=302)

    linked = bool(
        insert_ignore(
            cur,
            "teacher_disciplines",
            ("teacher_id", "discipline_id"),
            (user["id"], discipline_id),
            conflict_columns=("teacher_id", "discipline_id"),
        )
    )
    sync_teacher_group_assignments(cur, int(user["id"]), discipline_id=int(discipline_id))
    conn.commit()
    conn.close()

    discipline_name = discipline["name"]
    audit_log(
        request,
        "attach_discipline",
        details=f"teacher_id={user['id']}, discipline_id={discipline_id}, linked={linked}",
    )
    if linked:
        add_flash(request, f"Дисциплина «{discipline_name}» добавлена в ваш список", "success")
    else:
        add_flash(request, f"Дисциплина «{discipline_name}» уже была у вас", "info")
    return RedirectResponse("/v2/teacher/disciplines", status_code=302)


@app.get("/v2/teacher/groups", response_class=HTMLResponse)
def v2_teacher_groups(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)

    conn = connect()
    cur = conn.cursor()
    if sync_teacher_attempt_group_assignments(cur, int(user["id"])):
        conn.commit()

    disciplines = get_teacher_disciplines(cur, int(user["id"]))
    all_group_names = get_all_group_names(cur)

    cur.execute(
        """
        SELECT id, full_name, email, last_login, student_group
        FROM users
        WHERE role = 'student'
        ORDER BY full_name
        """
    )
    all_students = [user_row_to_dict(row) for row in cur.fetchall()]
    members_by_group = {
        item["name"]: item["students"]
        for item in group_students_by_group(all_students)
    }

    cur.execute(
        """
        SELECT ta.group_name, ta.discipline_id, d.name AS discipline_name
        FROM teaching_assignments ta
        JOIN disciplines d ON d.id = ta.discipline_id
        WHERE ta.teacher_id = ?
        ORDER BY ta.group_name, d.name
        """,
        (user["id"],),
    )
    rows = [dict(row) for row in cur.fetchall()]
    assigned_map: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        group_name = normalize_group_name(row["group_name"])
        assigned_map.setdefault(group_name, []).append(
            {
                "id": int(row["discipline_id"]),
                "name": row["discipline_name"],
            }
        )

    managed_group_names = sorted(
        [name for name in assigned_map.keys() if name],
        key=natural_group_sort_key,
    )
    managed_groups: list[dict[str, Any]] = []
    for group_name in managed_group_names:
        assigned_disciplines = sorted(assigned_map.get(group_name, []), key=lambda item: item["name"].lower())
        available_disciplines = [
            item for item in disciplines if int(item["id"]) not in {int(d["id"]) for d in assigned_disciplines}
        ]
        managed_groups.append(
            {
                "name": group_name,
                "student_count": len(members_by_group.get(group_name, [])),
                "students": members_by_group.get(group_name, []),
                "disciplines": assigned_disciplines,
                "available_disciplines": available_disciplines,
            }
        )

    unassigned_groups = [
        {
            "name": name,
            "student_count": len(members_by_group.get(name, [])),
        }
        for name in all_group_names
        if name and name not in assigned_map
    ]

    conn.close()
    return render(
        request,
        "v2_teacher_groups.html",
        {
            "active_tab": "groups",
            "disciplines": disciplines,
            "managed_groups": managed_groups,
            "unassigned_groups": unassigned_groups,
        },
    )


@app.post("/v2/teacher/groups/assign")
def v2_teacher_assign_group_to_discipline(
    request: Request,
    group_name: str = Form(""),
    discipline_id: int = Form(...),
):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)

    normalized_group = normalize_group_name(group_name)
    if not normalized_group:
        add_flash(request, "Выберите группу", "error")
        return RedirectResponse("/v2/teacher/groups", status_code=302)

    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT d.id, d.name
        FROM teacher_disciplines td
        JOIN disciplines d ON d.id = td.discipline_id
        WHERE td.teacher_id = ? AND td.discipline_id = ?
        """,
        (user["id"], discipline_id),
    )
    discipline = cur.fetchone()
    if not discipline:
        conn.close()
        add_flash(request, "Дисциплина недоступна", "error")
        return RedirectResponse("/v2/teacher/groups", status_code=302)

    unblock_teacher_assignment(cur, int(user["id"]), int(discipline_id), normalized_group)
    linked = bool(
        insert_ignore(
            cur,
            "teaching_assignments",
            ("teacher_id", "discipline_id", "group_name"),
            (user["id"], discipline_id, normalized_group),
            conflict_columns=("teacher_id", "discipline_id", "group_name"),
        )
    )
    conn.commit()
    conn.close()

    if linked:
        add_flash(request, f"Группа «{normalized_group}» привязана к дисциплине «{discipline['name']}»", "success")
    else:
        add_flash(request, f"Группа «{normalized_group}» уже привязана к дисциплине «{discipline['name']}»", "info")
    return RedirectResponse("/v2/teacher/groups", status_code=302)


@app.post("/v2/teacher/groups/unassign")
def v2_teacher_unassign_group_from_discipline(
    request: Request,
    group_name: str = Form(""),
    discipline_id: int = Form(...),
):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)

    normalized_group = normalize_group_name(group_name)
    if not normalized_group:
        add_flash(request, "Группа не найдена", "error")
        return RedirectResponse("/v2/teacher/groups", status_code=302)

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT name FROM disciplines WHERE id = ?", (discipline_id,))
    discipline = cur.fetchone()
    if not discipline:
        conn.close()
        add_flash(request, "Дисциплина не найдена", "error")
        return RedirectResponse("/v2/teacher/groups", status_code=302)

    cur.execute(
        """
        DELETE FROM teaching_assignments
        WHERE teacher_id = ? AND discipline_id = ? AND group_name = ?
        """,
        (user["id"], discipline_id, normalized_group),
    )
    detached = cur.rowcount > 0
    block_teacher_assignment(cur, int(user["id"]), int(discipline_id), normalized_group)
    conn.commit()
    conn.close()

    if detached:
        add_flash(request, f"Группа «{normalized_group}» отвязана от дисциплины «{discipline['name']}»", "success")
    else:
        add_flash(request, f"Привязка группы «{normalized_group}» к дисциплине «{discipline['name']}» не найдена", "info")
    return RedirectResponse("/v2/teacher/groups", status_code=302)


@app.post("/v2/teacher/disciplines/{discipline_id}/detach")
def v2_teacher_detach_discipline(request: Request, discipline_id: int):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT name FROM disciplines WHERE id = ?", (discipline_id,))
    discipline = cur.fetchone()
    if not discipline:
        conn.close()
        add_flash(request, "Дисциплина не найдена", "error")
        return RedirectResponse("/v2/teacher/disciplines", status_code=302)

    cur.execute(
        "DELETE FROM teacher_disciplines WHERE teacher_id = ? AND discipline_id = ?",
        (user["id"], discipline_id),
    )
    detached = cur.rowcount > 0
    detach_teacher_discipline_assignments(cur, int(user["id"]), int(discipline_id))
    conn.commit()
    conn.close()

    discipline_name = discipline["name"]
    audit_log(
        request,
        "detach_discipline",
        details=f"teacher_id={user['id']}, discipline_id={discipline_id}, detached={detached}",
    )
    if detached:
        add_flash(request, f"Дисциплина «{discipline_name}» отвязана", "success")
    else:
        add_flash(request, f"Дисциплина «{discipline_name}» не была привязана", "info")
    return RedirectResponse("/v2/teacher/disciplines", status_code=302)


@app.get("/v2/teacher/tests", response_class=HTMLResponse)
def v2_teacher_tests(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)

    discipline_filter_raw = request.query_params.get("discipline_id", "")
    discipline_filter: int | None = None
    try:
        if discipline_filter_raw:
            discipline_filter = int(discipline_filter_raw)
    except Exception:
        discipline_filter = None

    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT d.id, d.name
        FROM teacher_disciplines td
        JOIN disciplines d ON d.id = td.discipline_id
        WHERE td.teacher_id = ?
        ORDER BY d.name
        """,
        (user["id"],),
    )
    disciplines = [dict(row) for row in cur.fetchall()]

    if not discipline_filter and disciplines:
        discipline_filter = int(disciplines[0]["id"])

    if discipline_filter:
        cur.execute(
            "SELECT id, title, created_at FROM lectures WHERE teacher_id = ? AND discipline_id = ? ORDER BY id DESC",
            (user["id"], discipline_filter),
        )
    else:
        cur.execute("SELECT id, title, created_at FROM lectures WHERE teacher_id = ? ORDER BY id DESC", (user["id"],))
    lectures = [dict(r) for r in cur.fetchall()]

    if discipline_filter:
        cur.execute(
            """
            SELECT tests.id, tests.title, tests.status, tests.created_at, lectures.title AS lecture_title
            FROM tests JOIN lectures ON lectures.id = tests.lecture_id
            WHERE lectures.teacher_id = ? AND lectures.discipline_id = ?
            ORDER BY tests.id DESC
            """,
            (user["id"], discipline_filter),
        )
    else:
        cur.execute(
            """
            SELECT tests.id, tests.title, tests.status, tests.created_at, lectures.title AS lecture_title
            FROM tests JOIN lectures ON lectures.id = tests.lecture_id
            WHERE lectures.teacher_id = ?
            ORDER BY tests.id DESC
            """,
            (user["id"],),
        )
    tests = [dict(r) for r in cur.fetchall()]
    conn.close()
    return render(
        request,
        "v2_teacher_tests.html",
        {
            "active_tab": "tests",
            "lectures": lectures,
            "tests": tests,
            "disciplines": disciplines,
            "selected_discipline_id": discipline_filter,
        },
    )


@app.get("/v2/teacher/students", response_class=HTMLResponse)
def v2_teacher_students(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)
    conn = connect()
    cur = conn.cursor()
    if sync_teacher_attempt_group_assignments(cur, int(user["id"])):
        conn.commit()
    available_groups = [group for group in get_teacher_assignment_groups(cur, int(user["id"])) if group]
    students = get_teacher_students(cur, int(user["id"]))
    grouped_students = group_students_by_group(students)
    conn.close()
    return render(
        request,
        "v2_teacher_students.html",
        {
            "active_tab": "students",
            "students": students,
            "grouped_students": grouped_students,
            "available_groups": available_groups,
        },
    )


def build_teacher_student_performance_context(
    cur,
    teacher_id: int,
    student_id: int,
    discipline_filter: int | None = None,
) -> dict[str, Any] | None:
    sync_teacher_attempt_group_assignments(cur, teacher_id, discipline_filter)
    cur.execute(
        """
        SELECT id, full_name, email, last_login, student_group
        FROM users
        WHERE id = ? AND role = 'student'
        """,
        (student_id,),
    )
    student_row = cur.fetchone()
    if not student_row:
        return None
    if not teacher_can_manage_student(cur, teacher_id, student_id):
        return None

    student = user_row_to_dict(dict(student_row))

    allowed_discipline_ids = set(get_teacher_student_discipline_ids(cur, teacher_id, student_id))
    if not allowed_discipline_ids:
        return None
    cur.execute(
        f"""
        SELECT id, name
        FROM disciplines
        WHERE id IN ({', '.join('?' for _ in allowed_discipline_ids)})
        ORDER BY name
        """,
        tuple(sorted(allowed_discipline_ids)),
    )
    disciplines = [dict(row) for row in cur.fetchall()]

    selected_discipline_id: int | None = None
    if discipline_filter and discipline_filter in allowed_discipline_ids:
        selected_discipline_id = discipline_filter

    student_group_key = normalize_group_name(student.get("student_group"))
    tests_params: tuple[Any, ...] = (student_group_key, student_id, teacher_id)
    tests_filter_sql = ""
    if selected_discipline_id:
        tests_filter_sql = " AND lectures.discipline_id = ?"
        tests_params = (student_group_key, student_id, teacher_id, selected_discipline_id)

    cur.execute(
        f"""
        SELECT
            tests.id AS test_id,
            tests.title AS test_title,
            lectures.title AS lecture_title,
            COALESCE(lectures.discipline_id, 0) AS discipline_id,
            COALESCE(d.name, 'Без дисциплины') AS discipline_name,
            a.id AS attempt_id,
            a.score AS score,
            a.taken_at AS taken_at
        FROM tests
        JOIN lectures ON lectures.id = tests.lecture_id
        LEFT JOIN disciplines d ON d.id = lectures.discipline_id
        JOIN teaching_assignments ta
          ON ta.teacher_id = lectures.teacher_id
         AND ta.discipline_id = lectures.discipline_id
         AND ta.group_name = ?
        LEFT JOIN attempts a ON a.id = (
            SELECT MAX(ax.id)
            FROM attempts ax
            WHERE ax.test_id = tests.id AND ax.student_id = ?
        )
        WHERE lectures.teacher_id = ? AND tests.status = 'published'{tests_filter_sql}
        ORDER BY COALESCE(d.name, 'Без дисциплины'), lectures.title, tests.id DESC
        """,
        tests_params,
    )
    tests_rows = [dict(row) for row in cur.fetchall()]

    attempts_params: tuple[Any, ...] = (student_group_key, student_id, teacher_id)
    attempts_filter_sql = ""
    if selected_discipline_id:
        attempts_filter_sql = " AND lectures.discipline_id = ?"
        attempts_params = (student_group_key, student_id, teacher_id, selected_discipline_id)

    cur.execute(
        f"""
        SELECT
            attempts.id AS attempt_id,
            attempts.score AS score,
            attempts.taken_at AS taken_at,
            tests.id AS test_id,
            tests.title AS test_title,
            lectures.title AS lecture_title,
            COALESCE(d.name, 'Без дисциплины') AS discipline_name
        FROM attempts
        JOIN tests ON tests.id = attempts.test_id
        JOIN lectures ON lectures.id = tests.lecture_id
        LEFT JOIN disciplines d ON d.id = lectures.discipline_id
        JOIN teaching_assignments ta
          ON ta.teacher_id = lectures.teacher_id
         AND ta.discipline_id = lectures.discipline_id
         AND ta.group_name = ?
        WHERE attempts.student_id = ? AND lectures.teacher_id = ?{attempts_filter_sql}
        ORDER BY attempts.taken_at DESC, attempts.id DESC
        """,
        attempts_params,
    )
    attempts_rows = [dict(row) for row in cur.fetchall()]

    for row in tests_rows:
        score_raw = row.get("score")
        score_value = None
        if score_raw is not None:
            try:
                score_value = round(float(score_raw), 2)
            except Exception:
                score_value = None
        row["score_value"] = score_value
        row["is_completed"] = bool(row.get("attempt_id"))
        row["is_passed"] = bool(score_value is not None and score_value >= 60.0)
        row["taken_at_display"] = format_datetime_label(row.get("taken_at"))

    for row in attempts_rows:
        try:
            score_value = round(float(row.get("score") or 0), 2)
        except Exception:
            score_value = 0.0
        row["score"] = score_value
        row["is_passed"] = bool(score_value >= 60.0)
        row["taken_at_display"] = format_datetime_label(row.get("taken_at"))

    total_tests = len(tests_rows)
    completed_tests = [row for row in tests_rows if row["is_completed"]]
    completed_count = len(completed_tests)
    scores = [float(row["score_value"]) for row in completed_tests if row.get("score_value") is not None]

    passed_count = len([row for row in completed_tests if row["is_passed"]])
    failed_count = max(0, completed_count - passed_count)
    average_score = round(sum(scores) / len(scores), 2) if scores else 0.0
    best_score = max(scores) if scores else 0.0
    worst_score = min(scores) if scores else 0.0
    completion_rate = round((completed_count / total_tests) * 100, 2) if total_tests else 0.0
    pass_rate = round((passed_count / completed_count) * 100, 2) if completed_count else 0.0

    by_discipline: dict[str, dict[str, Any]] = {}
    for row in tests_rows:
        discipline_name = (row.get("discipline_name") or "Без дисциплины").strip() or "Без дисциплины"
        entry = by_discipline.setdefault(
            discipline_name,
            {
                "discipline": discipline_name,
                "tests_total": 0,
                "completed": 0,
                "passed": 0,
                "scores": [],
            },
        )
        entry["tests_total"] += 1
        if row["is_completed"]:
            entry["completed"] += 1
            if row.get("score_value") is not None:
                entry["scores"].append(float(row["score_value"]))
            if row["is_passed"]:
                entry["passed"] += 1

    discipline_stats: list[dict[str, Any]] = []
    for entry in by_discipline.values():
        avg_score = round(sum(entry["scores"]) / len(entry["scores"]), 2) if entry["scores"] else 0.0
        completion = round((entry["completed"] / entry["tests_total"]) * 100, 2) if entry["tests_total"] else 0.0
        pass_rate_discipline = round((entry["passed"] / entry["completed"]) * 100, 2) if entry["completed"] else 0.0
        discipline_stats.append(
            {
                "discipline": entry["discipline"],
                "tests_total": entry["tests_total"],
                "completed": entry["completed"],
                "avg_score": avg_score,
                "completion_rate": completion,
                "pass_rate": pass_rate_discipline,
            }
        )
    discipline_stats.sort(key=lambda item: item["discipline"].lower())

    recent_attempt_scores = [float(row["score"]) for row in attempts_rows[:10]]
    spark_points = list(reversed(recent_attempt_scores))
    sparkline = make_sparkline(spark_points)

    return {
        "student": student,
        "disciplines": disciplines,
        "selected_discipline_id": selected_discipline_id,
        "tests": tests_rows,
        "recent_attempts": attempts_rows[:40],
        "discipline_stats": discipline_stats,
        "summary": {
            "total_tests": total_tests,
            "completed_count": completed_count,
            "passed_count": passed_count,
            "failed_count": failed_count,
            "average_score": average_score,
            "best_score": best_score,
            "worst_score": worst_score,
            "completion_rate": completion_rate,
            "pass_rate": pass_rate,
        },
        "spark_points": spark_points,
        "sparkline": sparkline,
    }


@app.get("/v2/teacher/students/{user_id}/performance", response_class=HTMLResponse)
def v2_teacher_student_performance(request: Request, user_id: int):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)

    discipline_filter_raw = (request.query_params.get("discipline_id") or "").strip()
    discipline_filter: int | None = None
    if discipline_filter_raw:
        try:
            discipline_filter = int(discipline_filter_raw)
        except Exception:
            discipline_filter = None

    conn = connect()
    cur = conn.cursor()
    context = build_teacher_student_performance_context(cur, int(user["id"]), int(user_id), discipline_filter)
    conn.close()

    if not context:
        add_flash(request, "Студент не найден или недоступен для просмотра.", "error")
        return RedirectResponse("/v2/teacher/students", status_code=302)

    return render(
        request,
        "v2_teacher_student_performance.html",
        {
            "active_tab": "students",
            **context,
        },
    )


@app.get("/v1/teacher/users/{user_id}/performance")
def v1_teacher_user_performance(request: Request, user_id: int):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)

    query_suffix = ""
    discipline_id = (request.query_params.get("discipline_id") or "").strip()
    if discipline_id:
        query_suffix = f"?discipline_id={discipline_id}"
    return RedirectResponse(f"/v2/teacher/students/{user_id}/performance{query_suffix}", status_code=302)


@app.post("/v2/teacher/students/{user_id}/set_group")
def v2_teacher_set_group(request: Request, user_id: int, student_group: str = Form("")):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)

    next_path = request.query_params.get("next", "/v2/teacher/students")
    if not next_path.startswith("/"):
        next_path = "/v2/teacher/students"

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT student_group FROM users WHERE id = ? AND role = 'student'", (user_id,))
    row = cur.fetchone()
    if not row or not teacher_can_manage_student(cur, int(user["id"]), int(user_id)):
        conn.close()
        add_flash(request, "Нет доступа к этому студенту", "error")
        return RedirectResponse(next_path, status_code=302)

    normalized_group = "" if (student_group or "").strip() == "__none__" else (student_group or "").strip()
    allowed_groups = set(get_teacher_assignment_groups(cur, int(user["id"])))
    if normalized_group and normalized_group not in allowed_groups:
        conn.close()
        add_flash(request, "Нельзя переместить студента в группу вне ваших дисциплин.", "error")
        return RedirectResponse(next_path, status_code=302)
    cur.execute("UPDATE users SET student_group = ? WHERE id = ?", (normalized_group, user_id))
    conn.commit()
    conn.close()
    add_flash(request, "Группа студента обновлена", "success")
    return RedirectResponse(next_path, status_code=302)


@app.get("/v2/teacher/students/{user_id}/edit", response_class=HTMLResponse)
def v2_teacher_student_edit(request: Request, user_id: int):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)

    conn = connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, full_name, email, student_group FROM users WHERE id = ? AND role = 'student'",
        (user_id,),
    )
    row = cur.fetchone()
    if not row or not teacher_can_manage_student(cur, int(user["id"]), int(user_id)):
        conn.close()
        return RedirectResponse("/v2/teacher/students", status_code=302)

    available_groups = [group for group in get_teacher_assignment_groups(cur, int(user["id"])) if group]
    conn.close()

    return render(
        request,
        "v2_teacher_student_edit.html",
        {
            "active_tab": "students",
            "student": dict(row),
            "available_groups": available_groups,
            "error": None,
        },
    )


@app.post("/v2/teacher/students/{user_id}/edit")
def v2_teacher_student_edit_post(
    request: Request,
    user_id: int,
    full_name: str = Form(...),
    login: str = Form(""),
    email: str = Form(""),
    student_group: str = Form(""),
):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE id = ? AND role = 'student'", (user_id,))
    row = cur.fetchone()
    if not row or not teacher_can_manage_student(cur, int(user["id"]), int(user_id)):
        conn.close()
        return RedirectResponse("/v2/teacher/students", status_code=302)

    normalized_group = "" if (student_group or "").strip() == "__none__" else (student_group or "").strip()
    allowed_groups = set(get_teacher_assignment_groups(cur, int(user["id"])))
    if normalized_group and normalized_group not in allowed_groups:
        available_groups = [group for group in allowed_groups if group]
        cur.execute(
            "SELECT id, full_name, email, student_group FROM users WHERE id = ?",
            (user_id,),
        )
        student_row = cur.fetchone()
        conn.close()
        return render(
            request,
            "v2_teacher_student_edit.html",
            {
                "active_tab": "students",
                "student": dict(student_row) if student_row else {"id": user_id, "full_name": full_name, "email": login or email, "student_group": normalized_group},
                "available_groups": available_groups,
                "error": "Нельзя назначить группу вне ваших дисциплин.",
            },
        )
    clean_login = validate_login(login or email)
    clean_name = sanitize_full_name(full_name)
    if not clean_login or not clean_name:
        available_groups = [group for group in allowed_groups if group]
        cur.execute(
            "SELECT id, full_name, email, student_group FROM users WHERE id = ?",
            (user_id,),
        )
        student_row = cur.fetchone()
        conn.close()
        return render(
            request,
            "v2_teacher_student_edit.html",
            {
                "active_tab": "students",
                "student": dict(student_row) if student_row else {"id": user_id, "full_name": clean_name or full_name, "email": clean_login or (login or email), "student_group": normalized_group},
                "available_groups": available_groups,
                "error": "Проверьте ФИО и логин (логин: 3-80 символов без пробелов).",
            },
        )
    try:
        cur.execute(
            "UPDATE users SET full_name = ?, email = ?, student_group = ? WHERE id = ?",
            (clean_name, clean_login, normalized_group, user_id),
        )
        conn.commit()
    except Exception:
        available_groups = [group for group in allowed_groups if group]
        cur.execute(
            "SELECT id, full_name, email, student_group FROM users WHERE id = ?",
            (user_id,),
        )
        student_row = cur.fetchone()
        conn.close()
        return render(
            request,
            "v2_teacher_student_edit.html",
            {
                "active_tab": "students",
                "student": dict(student_row) if student_row else {"id": user_id, "full_name": clean_name, "email": clean_login, "student_group": normalized_group},
                "available_groups": available_groups,
                "error": "Не удалось сохранить (возможно, логин уже используется).",
            },
        )

    conn.close()
    add_flash(request, "Профиль студента обновлён", "success")
    return RedirectResponse("/v2/teacher/students", status_code=302)


@app.post("/v2/teacher/students/{user_id}/delete")
def v2_teacher_student_delete(request: Request, user_id: int):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE id = ? AND role = 'student'", (user_id,))
    row = cur.fetchone()
    if not row or not teacher_can_manage_student(cur, int(user["id"]), int(user_id)):
        conn.close()
        add_flash(request, "Нет доступа к этому студенту", "error")
        return RedirectResponse("/v2/teacher/students", status_code=302)

    cur.execute("DELETE FROM answers WHERE attempt_id IN (SELECT id FROM attempts WHERE student_id = ?)", (user_id,))
    cur.execute("DELETE FROM attempts WHERE student_id = ?", (user_id,))
    cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()

    add_flash(request, "Студент удалён", "success")
    return RedirectResponse("/v2/teacher/students", status_code=302)


@app.get("/v2/teacher/analytics", response_class=HTMLResponse)
def v2_teacher_analytics(request: Request):
    ensure_start_session_cookie(request)
    user = get_current_user(request)
    if not user or user["role"] != "teacher":
        return RedirectResponse("/login", status_code=302)

    discipline_filter_raw = request.query_params.get("discipline_id", "")
    discipline_filter: int | None = None
    try:
        if discipline_filter_raw:
            discipline_filter = int(discipline_filter_raw)
    except Exception:
        discipline_filter = None

    conn = connect()
    cur = conn.cursor()
    if sync_teacher_attempt_group_assignments(cur, int(user["id"]), discipline_filter):
        conn.commit()
    cur.execute(
        """
        SELECT d.id, d.name
        FROM teacher_disciplines td
        JOIN disciplines d ON d.id = td.discipline_id
        WHERE td.teacher_id = ?
        ORDER BY d.name
        """,
        (user["id"],),
    )
    disciplines = [dict(row) for row in cur.fetchall()]

    if discipline_filter:
        cur.execute(
            """
            SELECT users.id AS student_id, users.full_name AS student_name, attempts.score, attempts.taken_at,
                   users.student_group
            FROM attempts
            JOIN tests ON tests.id = attempts.test_id
            JOIN lectures ON lectures.id = tests.lecture_id
            JOIN users ON users.id = attempts.student_id
            JOIN teaching_assignments ta
              ON ta.teacher_id = lectures.teacher_id
             AND ta.discipline_id = lectures.discipline_id
             AND ta.group_name = COALESCE(users.student_group, '')
            WHERE lectures.teacher_id = ? AND lectures.discipline_id = ?
            ORDER BY users.full_name
            """,
            (user["id"], discipline_filter),
        )
    else:
        cur.execute(
            """
            SELECT users.id AS student_id, users.full_name AS student_name, attempts.score, attempts.taken_at,
                   users.student_group
            FROM attempts
            JOIN tests ON tests.id = attempts.test_id
            JOIN lectures ON lectures.id = tests.lecture_id
            JOIN users ON users.id = attempts.student_id
            JOIN teaching_assignments ta
              ON ta.teacher_id = lectures.teacher_id
             AND ta.discipline_id = lectures.discipline_id
             AND ta.group_name = COALESCE(users.student_group, '')
            WHERE lectures.teacher_id = ?
            ORDER BY users.full_name
            """,
            (user["id"],),
        )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    by_student: dict[int, dict[str, Any]] = {}
    by_group: dict[str, list[float]] = {}
    for row in rows:
        student_id = row["student_id"]
        if student_id not in by_student:
            by_student[student_id] = {"name": row["student_name"], "scores": []}
        by_student[student_id]["scores"].append(row["score"])
        group_name = (row.get("student_group") or "Без группы").strip() or "Без группы"
        by_group.setdefault(group_name, []).append(float(row["score"]))

    metrics = []
    for data in by_student.values():
        scores = data["scores"]
        attempts = len(scores)
        avg_score = round(sum(scores) / attempts, 2) if attempts else 0.0
        passed = len([score for score in scores if score >= 60])
        pass_rate = round((passed / attempts) * 100, 2) if attempts else 0.0
        metrics.append({"name": data["name"], "attempts": attempts, "avg_score": avg_score, "pass_rate": pass_rate})
    metrics.sort(key=lambda entry: entry["avg_score"], reverse=True)

    group_metrics: list[dict[str, Any]] = []
    for group_name, group_scores in by_group.items():
        avg_group_score = round(sum(group_scores) / len(group_scores), 2) if group_scores else 0.0
        group_metrics.append({"group": group_name, "avg_score": avg_group_score, "attempts": len(group_scores)})
    group_metrics.sort(key=lambda entry: entry["avg_score"], reverse=True)

    timeline: dict[str, list[float]] = {}
    timeline_order: dict[str, int] = {}
    for row in rows:
        raw_ts = row.get("taken_at")
        if not raw_ts:
            continue
        try:
            dt = datetime.fromisoformat(str(raw_ts))
            key = dt.strftime("%d.%m")
            timeline.setdefault(key, []).append(float(row["score"]))
            order_value = dt.date().toordinal()
            if key not in timeline_order or order_value < timeline_order[key]:
                timeline_order[key] = order_value
        except Exception:
            continue

    ordered_labels = [label for label, _ in sorted(timeline_order.items(), key=lambda pair: pair[1])]
    trend_labels = ordered_labels
    trend_values = [round(sum(values) / len(values), 2) for values in timeline.values()]
    if ordered_labels:
        trend_values = [round(sum(timeline[label]) / len(timeline[label]), 2) for label in ordered_labels]

    trend_svg = ""
    if trend_values:
        min_v = min(trend_values)
        max_v = max(trend_values)
        span = max_v - min_v if max_v != min_v else 1.0
        width = 420.0
        height = 140.0
        step_x = width / max(1, len(trend_values) - 1)
        points: list[tuple[float, float]] = []
        for i, value in enumerate(trend_values):
            x = i * step_x
            y = height - ((value - min_v) / span) * height
            points.append((x, y))
        trend_svg = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)

    overall_avg = round(sum(float(r["score"]) for r in rows) / len(rows), 2) if rows else 0.0
    total_attempts = len(rows)
    total_students = len(by_student)

    return render(
        request,
        "v2_teacher_analytics.html",
        {
            "active_tab": "analytics",
            "metrics": metrics,
            "group_metrics": group_metrics,
            "trend_labels": trend_labels,
            "trend_values": trend_values,
            "trend_svg": trend_svg,
            "overall_avg": overall_avg,
            "total_attempts": total_attempts,
            "total_students": total_students,
            "disciplines": disciplines,
            "selected_discipline_id": discipline_filter,
        },
    )


# ── Скрываем HTML-маршруты из OpenAPI-схемы (/docs) ──────────
# Оставляем только /api/* эндпоинты видимыми в Swagger.
from starlette.routing import Route as _StarletteRoute  # noqa: E402
for _route in app.routes:
    _path = getattr(_route, "path", "")
    if isinstance(_route, _StarletteRoute) and not _path.startswith("/api"):
        _route.include_in_schema = False
