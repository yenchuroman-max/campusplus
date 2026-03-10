from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


ALLOWED_DIFFICULTY = {"easy", "medium", "hard"}
_LAST_PROVIDER_ERROR = ""
MAX_QUESTION_TEXT_LEN = 140
_MAX_RETRIES = 2  # кол-во повторных попыток при невалидном JSON

RU_STOPWORDS = {
    "который",
    "которая",
    "которые",
    "также",
    "этого",
    "этой",
    "этот",
    "между",
    "после",
    "перед",
    "через",
    "можно",
    "нужно",
    "важно",
    "является",
    "являются",
    "используется",
    "используются",
    "такой",
    "такая",
    "такие",
    "данный",
    "данная",
    "данные",
    "только",
    "чтобы",
    "всегда",
    "никогда",
    "очень",
    "просто",
}

NOISE_TOKENS = {
    "http",
    "https",
    "www",
    "wikipedia",
    "wiki",
    "org",
    "com",
    "ru",
    "net",
    "html",
    "php",
    "index",
    "источник",
    "source",
}

CODE_NOISE_TOKENS = {
    "select",
    "from",
    "where",
    "join",
    "left",
    "right",
    "inner",
    "outer",
    "union",
    "group",
    "order",
    "having",
    "distinct",
    "as",
    "on",
    "sql_variant_property",
    "basetype",
    "totalbytes",
    "maxlength",
    "stringlen",
    "replicate",
    "convert",
    "assumed",
    "ptr128",
}

ALLOWED_LATIN_TERMS = {
    "sql",
    "t-sql",
    "varchar",
    "nvarchar",
    "utf",
    "utf8",
    "utf-8",
    "unicode",
    "json",
    "xml",
    "api",
}

SOURCE_ARTIFACT_PATTERNS = (
    r"copyright",
    r"all rights reserved",
    r"and/or its affiliates",
    r"и/или ее дочерние компании",
)

GENERIC_FOCUS_TOKENS = {
    "возможного",
    "ключевая",
    "рассматриваемой",
    "практическом",
    "экспертном",
    "учебной",
    "данных",
    "информации",
    "системы",
    "подхода",
    "решения",
}

WEAK_FOCUS_PARTS = {
    "является",
    "являются",
    "включает",
    "включают",
    "применяется",
    "применяются",
    "используется",
    "используются",
    "должен",
    "должна",
    "должны",
    "нужно",
    "нужен",
    "может",
    "могут",
    "проводить",
    "делать",
    "сделать",
    "обеспечить",
    "обеспечивает",
    "будет",
    "быть",
    "были",
    "было",
    "если",
    "тогда",
    "ниже",
    "выше",
    "этом",
    "этом",
    "этой",
    "этот",
    "эта",
    "эти",
    "когда",
    "котором",
    "которые",
    "данном",
    "данной",
    "данные",
    "всего",
    "только",
    "далее",
}

EXTRACTOR_WEAK_TOKENS = {
    "результат",
    "данных",
    "данные",
    "выражении",
    "пример",
    "примеры",
    "случай",
    "случаи",
    "элемент",
    "элементы",
}

DISCIPLINE_PROFILES = [
    {
        "name": "формально-математический",
        "markers": [
            "теорем",
            "доказ",
            "уравнен",
            "интеграл",
            "производн",
            "матриц",
            "вероятност",
            "функц",
            "алгебр",
            "геомет",
        ],
        "guidance": (
            "Сфокусируй вопросы на корректности рассуждения, условиях применимости формул, "
            "типичных ошибках в шагах решения и интерпретации результата."
        ),
    },
    {
        "name": "технический",
        "markers": [
            "алгоритм",
            "программ",
            "код",
            "архитектур",
            "база дан",
            "протокол",
            "сеть",
            "компил",
            "интерфейс",
            "оптимизац",
        ],
        "guidance": (
            "Сфокусируй вопросы на выборе подхода, причинах проектных решений, "
            "диагностике ошибок и анализе ограничений."
        ),
    },
    {
        "name": "естественно-научный",
        "markers": [
            "биолог",
            "физ",
            "хим",
            "клетк",
            "реакц",
            "молекул",
            "экосистем",
            "энерг",
            "генет",
            "эксперимент",
        ],
        "guidance": (
            "Сфокусируй вопросы на механизмах явлений, причинно-следственных связях, "
            "интерпретации наблюдений и условиях эксперимента."
        ),
    },
    {
        "name": "гуманитарно-социальный",
        "markers": [
            "истор",
            "культур",
            "философ",
            "психолог",
            "социол",
            "прав",
            "эконом",
            "полит",
            "общест",
            "этик",
        ],
        "guidance": (
            "Сфокусируй вопросы на интерпретации понятий, сопоставлении подходов, "
            "аргументации и связи тезисов с контекстом."
        ),
    },
]

def _is_noise_token(token: str) -> bool:
    t = (token or "").strip().lower()
    if not t:
        return True
    if t in NOISE_TOKENS:
        return True
    if t in CODE_NOISE_TOKENS:
        return True
    if t.startswith("http") or t.startswith("www"):
        return True
    if re.search(r"\d{4,}", t):
        return True
    if t.endswith((".org", ".com", ".ru", ".net")):
        return True
    return False


def _looks_like_code_segment(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return True
    low = text.lower()

    sql_hits = sum(1 for token in CODE_NOISE_TOKENS if re.search(rf"\b{re.escape(token)}\b", low))
    if sql_hits >= 2:
        return True

    if re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*\)", text) and sql_hits >= 1:
        return True

    code_symbols = len(re.findall(r"[=<>*(){}\[\];,_]", text))
    letters = len(re.findall(r"[A-Za-zА-Яа-яЁё]", text))
    if letters and code_symbols / max(1, len(text)) > 0.08 and sql_hits >= 1:
        return True

    cyr = len(re.findall(r"[А-Яа-яЁё]", text))
    lat = len(re.findall(r"[A-Za-z]", text))
    if lat > max(12, cyr * 2) and code_symbols >= 3:
        return True

    return False


def _strip_code_fragments(value: str) -> str:
    chunks = re.split(r"(?<=[.!?;])\s+|\n+", value or "")
    kept: list[str] = []
    for raw in chunks:
        chunk = re.sub(r"\s+", " ", raw).strip()
        if len(chunk) < 8:
            continue
        if _looks_like_code_segment(chunk):
            continue
        kept.append(chunk)
    return " ".join(kept).strip()


def _has_source_artifacts(value: str) -> bool:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return True
    low = text.lower()

    if any(marker in low for marker in SOURCE_ARTIFACT_PATTERNS):
        return True
    if any(symbol in text for symbol in ("©", "®", "™")):
        return True
    if re.search(r"_{3,}", text):
        return True
    if re.search(r"\b(?:19|20)\d{2}\b", text) and any(symbol in text for symbol in ("©", "®", "™")):
        return True
    return False


def _is_weak_focus_unit(unit: str) -> bool:
    value = (unit or "").strip().lower()
    if not value:
        return True
    parts = [p for p in re.split(r"\s+", value) if p]
    if len(parts) > 3:
        return True
    if any(part in WEAK_FOCUS_PARTS for part in parts):
        return True
    if len(parts) > 1 and any(re.search(r"(ть|ться|ется|ются|ает|яют|ит|ют|ет|ут|ем|им|ешь|ете|ишь|ите|ировал|ировать)$", part) for part in parts):
        return True
    if len(parts) == 1:
        single = parts[0]
        if single in GENERIC_FOCUS_TOKENS:
            return True
        if single in EXTRACTOR_WEAK_TOKENS:
            return True
        if len(single) < 5:
            return True
        if re.search(r"(ого|ему|ыми|ими|ая|яя|ий|ый|ой)$", single):
            return True
        if re.search(r"(ено|ана|ены|ется|утся)$", single):
            return True
    return False


def _sanitize_focus_unit(unit: str) -> str:
    value = re.sub(r"\s+", " ", str(unit or "").strip().lower())
    if not value:
        return ""
    words = [
        w
        for w in value.split(" ")
        if w and w not in RU_STOPWORDS and w not in EXTRACTOR_WEAK_TOKENS and not _is_noise_token(w)
    ]
    if not words:
        return ""
    if any(len(w) <= 2 for w in words):
        return ""

    latin_words = [w for w in words if re.fullmatch(r"[a-z][a-z0-9_-]*", w)]
    if latin_words:
        filtered: list[str] = []
        for w in words:
            if re.fullmatch(r"[a-z][a-z0-9_-]*", w) and w not in ALLOWED_LATIN_TERMS:
                continue
            filtered.append(w)
        words = filtered
        if not words:
            return ""

    words = words[:3]
    cleaned = " ".join(words)
    if _is_weak_focus_unit(cleaned):
        return ""
    return cleaned


def _compact_focus_unit(unit: str) -> str:
    cleaned = _sanitize_focus_unit(unit)
    if not cleaned:
        return ""
    parts = [p for p in cleaned.split(" ") if p]
    if not parts:
        return ""
    if len(parts) > 2:
        parts = parts[:2]
    compact = " ".join(parts)
    if _is_weak_focus_unit(compact):
        return ""
    return compact


def _ensure_env_loaded() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if load_dotenv:
        try:
            load_dotenv(env_path, override=False)
        except Exception:
            pass

    # Hard fallback: parse .env manually to avoid runtime inconsistencies
    try:
        if env_path.exists():
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = (raw_line or "").strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                # Respect explicit empty env overrides (e.g. tests with KEY="")
                if key and value and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        pass


def _cfg(key: str, default: str = "") -> str:
    if key in os.environ:
        return (os.environ.get(key) or "").strip()

    env_path = Path(__file__).resolve().parent.parent / ".env"
    try:
        if env_path.exists():
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = (raw_line or "").strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                env_key, env_value = line.split("=", 1)
                if env_key.strip() == key:
                    parsed = env_value.strip().strip('"').strip("'")
                    if parsed:
                        return parsed
    except Exception:
        pass
    return default


def _prepare_source_text(text: str, max_chars: int = 18000) -> str:
    text = (text or "").replace("\r", "\n")
    text = re.sub(r"(?im)^.*(?:©|®|™|copyright|all rights reserved|and/or its affiliates|и/или ее дочерние компании).*$", "", text)
    text = re.sub(r"(?im)^\s*источник\s*:\s*$", "", text)
    text = re.sub(r"(?im)^\s*source\s*:\s*$", "", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"www\.\S+", " ", text)
    # Убираем MediaWiki / Wikipedia разметку
    text = re.sub(r"={2,}\s*([^=]+?)\s*={2,}", r"\1.", text)  # == Заголовок == -> Заголовок.
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", text)  # [[ссылка|текст]] -> текст
    text = re.sub(r"\{\{[^}]*\}\}", "", text)  # {{шаблоны}}
    text = re.sub(r"<[^>]+>", "", text)  # HTML-теги
    # Убираем языковые теги Wikipedia: [англ.], (англ.), [нем.], и т.д.
    text = re.sub(r"\[\s*англ\.?\s*\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\(\s*англ\.?\s*\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[\s*(?:нем|фр|лат|ит|исп|eng|fr|de)\.?\s*\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\(\s*(?:нем|фр|лат|ит|исп|eng|fr|de)\.?\s*\)", "", text, flags=re.IGNORECASE)
    # Убираем конструкции вида "(Information Security)" / "(information security)" когда это переводный тег
    text = re.sub(r"\[\s*[A-Z][A-Za-z ]{2,40}\s*\]", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    paragraphs = [re.sub(r"\s+", " ", part).strip() for part in text.split("\n\n")]
    paragraphs = [part for part in paragraphs if part]

    cleaned: list[str] = []
    for part in paragraphs:
        part = _strip_code_fragments(part)
        if not part:
            continue
        low = part.lower()
        if re.fullmatch(r"\d{1,4}", low):
            continue
        if re.search(r"^(стр\.?|страница)\s*\d+", low):
            continue
        if low.startswith("источник:") or low.startswith("source:"):
            continue
        if "wikipedia" in low and len(low) < 180:
            continue
        if re.fullmatch(r"[-–—_•·. ]+", part):
            continue
        if len(part) < 15:  # сниженный порог — сохраняем короткие буллеты из презентаций
            continue
        alpha = len(re.findall(r"[A-Za-zА-Яа-яЁё]", part))
        if alpha < 8:
            continue
        cleaned.append(part)

    if not cleaned:
        return text[:max_chars]

    # Для коротких текстов (презентации/слайды) — склеиваем мелкие параграфы
    avg_len = sum(len(p) for p in cleaned) / max(1, len(cleaned))
    if avg_len < 120 and len(cleaned) > 3:
        merged: list[str] = []
        buf: list[str] = []
        for part in cleaned:
            buf.append(part)
            if sum(len(b) for b in buf) >= 150:
                merged.append("\n".join(buf))
                buf = []
        if buf:
            if merged:
                merged[-1] += "\n" + "\n".join(buf)
            else:
                merged.append("\n".join(buf))
        cleaned = merged

    if not cleaned:
        return text[:max_chars]

    # Отсекаем слабые параграфы по информативности, но сохраняем порядок
    scored: list[tuple[int, int, str]] = []
    for idx, part in enumerate(cleaned):
        unique_words = len(set(re.findall(r"[A-Za-zА-Яа-яЁё0-9-]{4,}", part.lower())))
        score = unique_words + min(len(part) // 60, 12)
        scored.append((score, idx, part))

    # Отбрасываем самые слабые 20% параграфов, если их больше 5
    if len(scored) > 5:
        threshold = sorted(s[0] for s in scored)[len(scored) // 5]
        scored = [item for item in scored if item[0] > threshold]

    # Восстанавливаем оригинальный порядок для семантической связности
    scored.sort(key=lambda item: item[1])

    selected: list[str] = []
    total = 0
    for _, _, part in scored:
        if total + len(part) + 2 > max_chars:
            continue
        selected.append(part)
        total += len(part) + 2
        if total >= int(max_chars * 0.9):
            break

    if not selected:
        return "\n\n".join(cleaned)[:max_chars]
    return "\n\n".join(selected)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"[.!?]\s+", text.strip())
    return [part.strip() for part in parts if len(part.strip()) > 10]


def _keywords(text: str) -> list[str]:
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9-]{4,}", text.lower())
    freq: dict[str, int] = {}
    for word in words:
        freq[word] = freq.get(word, 0) + 1
    sorted_words = sorted(freq.items(), key=lambda item: item[1], reverse=True)
    return [word for word, _ in sorted_words[:30]]


def _shorten_text(value: Any, limit: int = MAX_QUESTION_TEXT_LEN) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if len(text) <= limit:
        return text
    trimmed = text[: limit - 1].rstrip()
    if " " in trimmed:
        trimmed = trimmed.rsplit(" ", 1)[0]
    return trimmed + "…"


def _extract_terms(text: str, limit: int = 48) -> list[str]:
    """Extract meaningful terms preserving original case."""
    # Собираем токены с оригинальным регистром
    raw_tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9-]{5,}", text or "")
    freq: dict[str, int] = {}  # key -> count
    original_case: dict[str, str] = {}  # lowkey -> original form (first seen)

    # Суффиксы, характерные для НЕ-терминов (причастия, деепричастия, прилагательные общего типа)
    _bad_suffixes = (
        "ющие", "ющий", "ющая", "ющих", "ющим",  # причастия
        "ящие", "ящий", "ящая", "ящих", "ящим",
        "ающий", "ающая", "ающие", "ающих",
        "ующий", "ующая", "ующие", "ующих",
        "вший", "вшая", "вшие", "вших",
        "анный", "анная", "анные", "анных",
        "енный", "енная", "енные", "енных",
        "ённый", "ённая", "ённые",
        "ический", "ическая", "ическое", "ических",  # прилагательные на -ический
        "ельный", "ельная", "ельное", "ельных",  # прилагательные на -ельный
    )

    for token in raw_tokens:
        low = token.lower()
        if low in RU_STOPWORDS:
            continue
        if low.isdigit():
            continue
        if _is_noise_token(low):
            continue
        # Фильтруем слишком общие слова
        if low in _GENERIC_TOPIC_BLACKLIST:
            continue
        # Фильтруем причастия и общие прилагательные
        if any(low.endswith(s) for s in _bad_suffixes):
            continue
        freq[low] = freq.get(low, 0) + 1
        if low not in original_case:
            original_case[low] = token  # сохраняем первое вхождение с оригинальным регистром

    ranked = sorted(freq.items(), key=lambda item: (item[1], len(item[0])), reverse=True)
    terms = [original_case.get(low, low) for low, _ in ranked[:limit]]
    if not terms:
        return ["подход", "критерий", "ограничение", "контроль", "метрика"]
    return terms


def _content_tokens(text: str) -> set[str]:
    tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9-]{3,}", (text or "").lower())
    return {
        token
        for token in tokens
        if token not in RU_STOPWORDS and not token.isdigit() and not _is_noise_token(token)
    }


def _text_similarity(a: str, b: str) -> float:
    ta = _content_tokens(a)
    tb = _content_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(1, len(ta | tb))


def _extract_focus_units(text: str, limit: int = 42) -> list[str]:
    prepared = _prepare_source_text(text, max_chars=12000)
    terms = _extract_terms(prepared, limit=max(limit, 24))

    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9-]{4,}", prepared.lower())
    filtered = [w for w in words if w not in RU_STOPWORDS and not _is_noise_token(w)]

    phrase_freq: dict[str, int] = {}
    for i in range(len(filtered) - 1):
        p2 = f"{filtered[i]} {filtered[i + 1]}"
        phrase_freq[p2] = phrase_freq.get(p2, 0) + 1
        if i + 2 < len(filtered):
            p3 = f"{filtered[i]} {filtered[i + 1]} {filtered[i + 2]}"
            phrase_freq[p3] = phrase_freq.get(p3, 0) + 1

    ranked_phrases = sorted(phrase_freq.items(), key=lambda item: (item[1], len(item[0])), reverse=True)
    top_phrases = []
    for phrase, _ in ranked_phrases[: int(limit * 0.8)]:
        if len(phrase) > 42:
            continue
        sanitized = _sanitize_focus_unit(phrase)
        if sanitized:
            top_phrases.append(sanitized)
        if len(top_phrases) >= int(limit * 0.65):
            break

    out: list[str] = []
    seen: set[str] = set()
    for unit in top_phrases + terms:
        candidate = _sanitize_focus_unit(unit)
        if not candidate:
            continue
        normalized = candidate.strip().lower()
        if not normalized or normalized in seen:
            continue
        if _is_weak_focus_unit(normalized):
            continue
        seen.add(normalized)
        out.append(candidate.strip())
        if len(out) >= limit:
            break

    if not out:
        return ["модель угроз", "оценка рисков", "критерии качества", "контроль доступа"]
    return out


def _extract_json(text: str) -> Any:
    if not text:
        raise ValueError("Empty response")
    cleaned = re.sub(r"```(?:json)?|```", "", text, flags=re.IGNORECASE).strip()
    list_match = re.search(r"\[[\s\S]*\]", cleaned)
    if list_match:
        return json.loads(list_match.group(0))
    dict_match = re.search(r"\{[\s\S]*\}", cleaned)
    if dict_match:
        return json.loads(dict_match.group(0))
    return json.loads(cleaned)


def _normalize_theses(items: Any, limit: int = 18) -> list[str]:
    if isinstance(items, dict):
        items = items.get("theses") or items.get("items") or []
    if not isinstance(items, list):
        return []

    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        thesis = re.sub(r"\s+", " ", str(raw or "")).strip(" -•\t\n\r")
        if len(thesis) < 25:
            continue
        key = thesis.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(thesis)
        if len(out) >= limit:
            break
    return out


def _build_thesis_prompt(text: str, limit: int = 18, discipline_name: str | None = None) -> str:
    discipline_hint = ""
    clean_discipline = re.sub(r"\s+", " ", str(discipline_name or "").strip())
    if clean_discipline:
        discipline_hint = (
            f"Дисциплина: {clean_discipline}. "
            "Учитывай название дисциплины только для приоритизации — какие тезисы важнее. "
            "Не добавляй факты, не содержащиеся в лекции. "
        )
    return (
        "Извлеки из учебной лекции только ключевые содержательные тезисы на русском языке. "
        "Игнорируй номера страниц, колонтитулы, заголовки разделов без смысла, OCR-артефакты, рекламные вставки. "
        "Верни строго JSON-массив строк длиной до {limit} элементов. "
        "Каждый тезис должен быть самостоятельным и проверяемым фактом/определением. "
        "{discipline_hint}"
        "Лекция:\n{text}"
    ).format(limit=limit, text=text, discipline_hint=discipline_hint)


def _discipline_terms(name: str | None, limit: int = 6) -> list[str]:
    cleaned = re.sub(r"\s+", " ", str(name or "").strip().lower())
    if not cleaned:
        return []
    tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9-]{4,}", cleaned)
    out: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in RU_STOPWORDS or token in EXTRACTOR_WEAK_TOKENS or _is_noise_token(token):
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= limit:
            break
    return out


def _infer_discipline_guidance(
    text: str,
    theses: list[str] | None = None,
    discipline_name: str | None = None,
) -> tuple[str, str]:
    source = (text or "").lower()
    if theses:
        source = f"{source}\n" + "\n".join(str(item).lower() for item in theses if item)
    discipline_context = re.sub(r"\s+", " ", str(discipline_name or "").strip().lower())
    if discipline_context:
        source = f"{discipline_context}\n{discipline_context}\n{source}"

    best_name = "универсальный"
    best_guidance = (
        "Сфокусируй вопросы на ключевых определениях, причинно-следственных связях, "
        "условиях применения и типичных ошибках по теме лекции."
    )
    best_score = 0

    for profile in DISCIPLINE_PROFILES:
        markers = profile.get("markers", [])
        score = sum(1 for marker in markers if marker in source)
        if score > best_score:
            best_score = score
            best_name = str(profile.get("name") or best_name)
            best_guidance = str(profile.get("guidance") or best_guidance)

    return best_name, best_guidance


def _difficulty_guidance(difficulty: str) -> str:
    return {
        "easy": "Преобладают базовые вопросы на понимание определений и прямых связей.",
        "medium": "Баланс: понимание + применение концепций в типовых ситуациях.",
        "hard": "Преобладают аналитические вопросы на сравнение подходов, ограничения и диагностику ошибок.",
    }.get(difficulty, "Баланс: понимание + применение концепций в типовых ситуациях.")


def _normalize_questions(items: list[dict], count: int, strict: bool = True) -> list[dict]:
    """Нормализация и фильтрация вопросов. strict=True для AI, False для fallback."""

    def _build_options(question_text: str, raw_options: list[Any]) -> list[str]:
        base_options = [str(option).strip() for option in raw_options if str(option).strip()]
        if not base_options:
            base_options = []

        deduped: list[str] = []
        seen: set[str] = set()
        seen_stems: set[str] = set()  # для ловли "команды" vs "команда"
        for option in base_options:
            normalized_option = _shorten_text(option, limit=80)
            low_option = normalized_option.lower()
            if _has_source_artifacts(normalized_option):
                continue
            # Фильтруем мусор: URL, wiki-разметка, обрезанные тексты
            if any(marker in low_option for marker in ("http", "https", "www.", "wikipedia", "источник", "source", "англ")):
                continue
            if re.search(r"={2,}", normalized_option):  # wiki-разметка
                continue
            if re.search(r"\[\[|\{\{|\[\s*[A-Z]", normalized_option):  # wiki-ссылки/шаблоны/языковые теги
                continue
            # Обрезанные фрагменты
            if normalized_option.endswith("…") or re.search(r"[a-zа-яё]…$", normalized_option):
                continue
            if normalized_option.startswith("…") or re.match(r"^[а-яё]{1,3}[.,:]", normalized_option):
                continue
            # Ловим мешанину языков: латинское слово внутри кириллического текста
            # (допускаем общепринятые аббревиатуры: SQL, СУБД, ORM, API, etc.)
            cyr_words = re.findall(r"[А-Яа-яЁё]{3,}", normalized_option)
            lat_words = re.findall(r"[A-Za-z]{3,}", normalized_option)
            if cyr_words and lat_words:
                # Латинские слова допустимы если это аббревиатуры (все заглавные) или из ALLOWED_LATIN_TERMS
                bad_lat = [w for w in lat_words if not w.isupper() and w.lower() not in ALLOWED_LATIN_TERMS]
                if bad_lat:
                    continue
            key = low_option
            if key in seen:
                continue
            # Проверяем почти одинаковые варианты (различие только в окончании)
            stem = re.sub(r"[аеёиоуыэюяь]$", "", key.rstrip(".,;:!? "))
            if stem and stem in seen_stems:
                continue
            seen.add(key)
            if stem:
                seen_stems.add(stem)
            deduped.append(normalized_option)

        # Если < 4 вариантов — не вставляем мусорные заглушки, вернём что есть
        # (вопрос с < 4 вариантами будет отброшен ниже)
        return deduped[:4]

    def _sanitize_question_text(raw: Any) -> str:
        text = re.sub(r"\s+", " ", str(raw or "").strip())
        text = re.sub(
            r"^(?:базовый|средний|продвинутый|легк(?:ий|ая)|сложн(?:ый|ая)|easy|medium|hard)\s+уровень\.?\s*[:.\-]?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"^(?:уровень\s*[:.\-]?\s*)", "", text, flags=re.IGNORECASE)
        text = re.sub(r"по\s+фрагменту\s*[:\-]?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"из\s+фрагмента\s*[:\-]?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"по\s+тексту\s+лекции\s*[:\-]?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"из\s+источника\s*[:\-]?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"«[^»]{40,}»", "", text)
        text = re.sub(r'"[^"]{40,}"', "", text)
        # Убираем wiki-разметку из вопроса
        text = re.sub(r"={2,}\s*([^=]+?)\s*={2,}", r"\1", text)
        text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", text)
        text = re.sub(r"\s{2,}", " ", text).strip(" .:-")
        if text and not text.endswith("?"):
            text = text.rstrip(".!") + "?"
        return _shorten_text(text)

    normalized: list[dict] = []
    seen_questions: set[str] = set()
    for item in items:
        text = _sanitize_question_text(item.get("text", ""))
        options = item.get("options", [])
        correct_index = item.get("correct_index", 0)

        if not text or not isinstance(options, list):
            continue

        low_text = text.lower()
        if _has_source_artifacts(text):
            continue
        if any(marker in low_text for marker in ("http", "https", "www.", "wikipedia", "источник", "source", "англ")):
            continue

        # Убиваем вопросы типа "Вставьте пропущенный термин" в любом режиме.
        if re.search(r"вставьте\s+пропущенн|заполните\s+пропуск|пропущенный\s+термин|_{3,}", low_text):
            continue

        # Убиваем шаблонные вопросы "Что верно о понятии...", "Какое утверждение верно..." (strict mode)
        if strict and re.search(
            r"что\s+верно\s+о\s+понятии|какое\s+утверждение\s+верно|какое\s+из\s+утверждений\s+соответствует|"
            r"какой\s+факт\s+подтверждается|какое\s+высказывание\s+является\s+корректным|"
            r"что\s+верно\s+согласно\s+тексту",
            low_text,
        ):
            continue

        # Убиваем вопросы начинающиеся посередине слова (обрезка)
        if re.match(r"^\.{2,}", text) or re.match(r"^…", text):
            continue

        clean_options = _build_options(text, options)
        # Требуем минимум 4 варианта от AI; для fallback допускаем 3
        if len(clean_options) < 3:
            continue

        # Отбрасываем вопросы где варианты содержат обрезанный текст с многоточием
        truncated_opts = sum(1 for opt in clean_options if opt.rstrip().endswith("…") or "…" in opt)
        if truncated_opts >= 1:
            continue

        try:
            correct_index = int(correct_index)
        except Exception:
            correct_index = 0

        if correct_index < 0 or correct_index >= len(clean_options):
            correct_index = 0

        question_key = re.sub(r"\s+", " ", text.lower())
        if question_key in seen_questions:
            continue

        too_similar = False
        for prev in seen_questions:
            if _text_similarity(question_key, prev) >= 0.82:
                too_similar = True
                break
        if too_similar:
            continue

        if len(_content_tokens(text)) < 4:
            continue

        # Проверка качества: правильный ответ не должен быть почти идентичен неправильному
        if len(clean_options) >= 2:
            correct_text = clean_options[correct_index].lower()
            option_quality_ok = True
            for oi, opt in enumerate(clean_options):
                if oi == correct_index:
                    continue
                if _text_similarity(correct_text, opt.lower()) >= 0.9:
                    option_quality_ok = False
                    break
            if not option_quality_ok:
                continue

        # Перемешиваем варианты, чтобы правильный не был всегда первым
        correct_text_val = clean_options[correct_index]
        shuffled_options = clean_options[:]
        random.shuffle(shuffled_options)
        correct_index = shuffled_options.index(correct_text_val)

        seen_questions.add(question_key)

        normalized.append(
            {
                "text": text,
                "options": shuffled_options,
                "correct_index": correct_index,
            }
        )
        if len(normalized) >= count:
            break
    return normalized


def _finalize_questions(
    result: list[dict],
    text: str,
    count: int,
    difficulty: str,
    discipline_name: str | None = None,
) -> list[dict]:
    normalized = _normalize_questions(result, count, strict=True)
    if len(normalized) < count:
        extra = _generate_fallback(text, max(count * 2, count + 6), difficulty, discipline_name)
        normalized = _normalize_questions(normalized + extra, count, strict=False)

    if len(normalized) < count:
        extra = _generate_fallback(text, max(count * 4, count + 20), difficulty, discipline_name)
        normalized = _normalize_questions(normalized + extra, count, strict=False)

    return _top_up_questions(normalized, text, count)


def _top_up_questions(existing: list[dict], text: str, count: int) -> list[dict]:
    if len(existing) >= count:
        return existing[:count]

    prepared = _prepare_source_text(text, max_chars=10000)
    sentences = _extract_content_sentences(prepared, min_len=12, max_len=230)
    if len(sentences) < 4:
        for sentence in _sentences(prepared):
            sentence_clean = _shorten_text(sentence, limit=100)
            if len(sentence_clean) < 12:
                continue
            if sentence_clean.lower() in {s.lower() for s in sentences}:
                continue
            sentences.append(sentence_clean)
            if len(sentences) >= 8:
                break

    if len(sentences) < 4:
        sentences.extend(
            [
                "Ключевые понятия темы раскрываются через определения и примеры.",
                "Материал лекции описывает связи между основными элементами темы.",
                "В лекции акцент сделан на практическом применении рассмотренного подхода.",
                "Тема включает типичные ошибки и способы их предотвращения.",
            ]
        )

    # Делаем банк предложений компактным и уникальным, чтобы избежать одинаковых вариантов.
    sentence_bank: list[str] = []
    seen_sentences: set[str] = set()
    for sentence in sentences:
        compact = _shorten_text(re.sub(r"\s+", " ", sentence).strip(), limit=100)
        key = compact.lower()
        if not compact or key in seen_sentences:
            continue
        seen_sentences.add(key)
        sentence_bank.append(compact)

    if not sentence_bank:
        sentence_bank = [
            "Материал лекции описывает базовые понятия выбранной темы.",
            "В лекции рассматриваются ключевые условия применения методов.",
            "Содержание курса связывает теорию с практическими кейсами.",
            "В теме выделены распространенные ошибки и их причины.",
        ]

    result = existing[:]
    existing_keys = {
        re.sub(r"\s+", " ", str(item.get("text", "")).strip().lower())
        for item in result
        if str(item.get("text", "")).strip()
    }

    cursor = 0
    max_iterations = max(40, count * 10)
    while len(result) < count and cursor < max_iterations:
        q_text = f"Какое утверждение соответствует материалу лекции? (вопрос {len(result) + 1})"
        q_key = q_text.lower()
        if q_key in existing_keys:
            cursor += 1
            continue

        correct = sentence_bank[cursor % len(sentence_bank)]
        wrong_options: list[str] = []
        used_option_keys = {correct.lower()}
        shift = 1
        while len(wrong_options) < 3 and shift <= len(sentence_bank) + 4:
            candidate = sentence_bank[(cursor + shift) % len(sentence_bank)]
            candidate_key = candidate.lower()
            if candidate_key not in used_option_keys:
                wrong_options.append(candidate)
                used_option_keys.add(candidate_key)
            shift += 1

        filler_idx = 1
        while len(wrong_options) < 3:
            filler = f"Не отражает содержание лекции ({filler_idx})"
            filler_idx += 1
            filler_key = filler.lower()
            if filler_key in used_option_keys:
                continue
            wrong_options.append(filler)
            used_option_keys.add(filler_key)

        options = [correct] + wrong_options[:3]
        random.shuffle(options)
        correct_index = options.index(correct)
        result.append({"text": q_text, "options": options, "correct_index": correct_index})
        existing_keys.add(q_key)
        cursor += 1

    return result[:count]


def _build_prompt(
    text: str,
    count: int,
    difficulty: str,
    theses: list[str] | None = None,
    discipline_name: str | None = None,
) -> tuple[str, str]:
    """Возвращает (system_message, user_message) для генерации вопросов."""
    difficulty_map = {
        "easy": "легкий",
        "medium": "средний",
        "hard": "сложный",
    }
    level = difficulty_map.get(difficulty, "средний")
    theses_block = ""
    if theses:
        numbered = "\n".join(f"{i + 1}. {thesis}" for i, thesis in enumerate(theses))
        theses_block = (
            "\nКлючевые тезисы для покрытия вопросами (равномерно, без повторов):\n"
            f"{numbered}\n"
        )
    profile_name, profile_guidance = _infer_discipline_guidance(text, theses, discipline_name)
    difficulty_guidance = _difficulty_guidance(difficulty)
    discipline_block = ""
    discipline_label = re.sub(r"\s+", " ", str(discipline_name or "").strip())
    if discipline_label:
        discipline_block = (
            f"Название дисциплины: {discipline_label}. "
            "Используй название дисциплины только как тематическую рамку и приоритизацию терминов. "
            "Факты, формулировки и правильные ответы разрешено брать только из текста лекции. "
        )

    system_message = (
        "Ты методист по образовательному тестированию. Твоя задача — генерировать тестовые вопросы "
        "строго по материалу предоставленной лекции.\n\n"
        "## Правила генерации\n"
        "- Строго опирайся на содержание лекции: не добавляй внешние факты, термины и примеры.\n"
        "- Не используй знания вне лекции, даже очевидные.\n"
        "- Вопросы покрывают разные смысловые блоки лекции, а не одну мысль разными словами.\n"
        "- Сначала внутренне составь план покрытия тем, затем сформируй вопросы; план не выводи.\n"
        "- Если текст лекции состоит из кратких буллетов или слайдов — целенаправленно формулируй "
        "вопросы на основе этих буллетов, не пытаясь искать повествовательный текст.\n"
        "- Игнорируй служебные фрагменты: номера страниц, колонтитулы, OCR-артефакты, wiki-разметку.\n\n"
        "## Требования к вопросам\n"
        "- КАЖДЫЙ вопрос задаёт конкретный, содержательный вопрос по теме (НЕ «что верно?», а «Какова роль X?», «Чем X отличается от Y?», «Для чего применяется X?»).\n"
        "- Используй разные типы: причинно-следственные, применение, диагностика ошибки, "
        "выбор критерия, сравнение понятий, интерпретация кейса.\n"
        "- Минимум 3 разных типа на каждые 6 вопросов.\n"
        "- АБСОЛЮТНО ЗАПРЕЩЕНЫ вопросы типа 'Вставьте пропущенный термин' / 'Заполните пропуск' — НИКОГДА.\n"
        "- ЗАПРЕЩЕНЫ шаблонные вопросы: 'Что верно о…', 'Какое утверждение верно…', "
        "'Какое из утверждений соответствует…', 'Какой факт подтверждается…'. "
        "Вместо этого задавай конкретный вопрос по содержанию.\n\n"
        "## Примеры ХОРОШИХ формулировок\n"
        "- «Какую роль выполняет диспетчер запросов в архитектуре СУБД?»\n"
        "- «Чем кортеж отличается от атрибута?»\n"
        "- «Какой оператор SQL используется для добавления записей?»\n"
        "- «Какое преимущество реляционных СУБД связано с проектированием?»\n"
        "- «В каких случаях нельзя использовать подзапросы в UPDATE?»\n\n"
        "## Примеры ПЛОХИХ формулировок (ЗАПРЕЩЕНЫ)\n"
        "- «Что верно о понятии X?»\n"
        "- «Какое утверждение верно согласно лекции?»\n"
        "- «Какое из утверждений соответствует материалу?»\n"
        "- «Вставьте пропущенный термин: X - ___ Y»\n\n"
        "## Требования к вариантам ответа\n"
        "- Ровно 4 варианта, только 1 правильный (correct_index 0..3).\n"
        "- Каждый вариант — КОРОТКАЯ фраза (до 80 символов), НЕ целое предложение из лекции.\n"
        "- Все варианты на одном языке (если вопрос на русском — варианты на русском).\n"
        "- НЕ смешивай кириллицу и латиницу в одном варианте (кроме общепринятых аббревиатур: SQL, СУБД).\n"
        "- Варианты НЕ должны содержать многоточие (…), обрезанный текст или незаконченные предложения.\n"
        "- Варианты НЕ должны отличаться только окончанием слова.\n"
        "- Неверные варианты содержательно близки, но однозначно менее корректны по лекции.\n"
        "- Запрещены: «все перечисленное», «оба варианта», «нет верного ответа».\n\n"
        "## Запреты\n"
        "- Не начинай вопрос с фразы про уровень сложности.\n"
        "- Не вставляй цитаты, ссылки, «фрагмент», «текст лекции», «источник».\n"
        "- Никаких URL, wikipedia, source, html, php, номеров страниц.\n"
        "- Никакой wiki-разметки (==, ===, [[, {{) в тексте вопросов и вариантов.\n"
        f"- Длина текста вопроса до {MAX_QUESTION_TEXT_LEN} символов.\n"
        "- Длина варианта ответа до 80 символов.\n\n"
        "## Качество\n"
        "- Пиши грамматически корректно, без лексических ошибок.\n"
        "- Каждый вариант ответа должен быть осмысленным, законченным текстом без обрезки.\n"
        "- Перед выводом проверь каждый вопрос на однозначность правильного ответа.\n\n"
        "## Формат ответа\n"
        "Строго JSON-массив. Никаких пояснений, markdown, лишнего текста.\n"
        '[{"text":"...","options":["A","B","C","D"],"correct_index":0}]'
    )

    user_message = (
        "Сгенерируй ровно {count} тестовых вопросов. Сложность: {level}.\n"
        "{discipline_block}"
        "Профиль дисциплины: {profile_name}. {profile_guidance}\n"
        "Указание по сложности: {difficulty_guidance}\n"
        "{theses_block}\n"
        "Лекция:\n{text}"
    ).format(
        count=count,
        level=level,
        theses_block=theses_block,
        discipline_block=discipline_block,
        discipline_label=discipline_label,
        profile_name=profile_name,
        profile_guidance=profile_guidance,
        difficulty_guidance=difficulty_guidance,
        text=text,
    )

    return system_message, user_message


def _extract_theses_with_openai(prepared_text: str, limit: int = 18, discipline_name: str | None = None) -> list[str]:
    api_key = _cfg("OPENAI_API_KEY")
    if not api_key:
        return []
    try:
        from openai import OpenAI
    except Exception:
        return []

    model = _cfg("OPENAI_MODEL", "gpt-4.1")
    prompt = _build_thesis_prompt(prepared_text, limit, discipline_name)

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Ты извлекаешь ключевые тезисы из учебных лекций. Отвечай только валидным JSON-массивом строк."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=2048,
        )
        content = response.choices[0].message.content if response.choices else ""
        return _normalize_theses(_extract_json(content or ""), limit=limit)
    except Exception:
        return []


# ── Gemini REST API helper ────────────────────────────────────────────────────

def _gemini_chat(
    system: str,
    user: str,
    *,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> str | None:
    """
    Вызов Gemini через REST API (без SDK, чтобы не тянуть grpcio).
    Возвращает текст ответа или None при ошибке.
    """
    api_key = _cfg("GEMINI_API_KEY")
    if not api_key:
        return None
    model = _cfg("GEMINI_MODEL", "gemini-2.0-flash")

    try:
        import requests
    except ImportError:
        return None

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
        f":generateContent?key={api_key}"
    )
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        },
    }
    try:
        resp = requests.post(url, json=body, timeout=60)
        if resp.status_code != 200:
            return None
        data = resp.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        return parts[0].get("text", "") if parts else None
    except Exception:
        return None


def _extract_theses_with_gemini(prepared_text: str, limit: int = 18, discipline_name: str | None = None) -> list[str]:
    prompt = _build_thesis_prompt(prepared_text, limit, discipline_name)
    content = _gemini_chat(
        "Ты извлекаешь ключевые тезисы из учебных лекций. Отвечай только валидным JSON-массивом строк.",
        prompt,
        temperature=0.0,
        max_tokens=2048,
    )
    if not content:
        return []
    return _normalize_theses(_extract_json(content), limit=limit)


def _generate_with_gemini(text: str, count: int, difficulty: str, discipline_name: str | None = None) -> list[dict]:
    global _LAST_PROVIDER_ERROR
    api_key = _cfg("GEMINI_API_KEY")
    if not api_key:
        return []

    prepared = _prepare_source_text(text)
    theses = _extract_theses_with_gemini(prepared, discipline_name=discipline_name)
    system_message, user_message = _build_prompt(prepared, count, difficulty, theses, discipline_name)

    max_tokens = min(max(count * 220 + 200, 1200), 8192)
    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES + 1):
        temperature = 0.25 + (attempt * 0.15)
        try:
            content = _gemini_chat(
                system_message,
                user_message,
                temperature=min(temperature, 0.7),
                max_tokens=max_tokens,
            )
            if not content:
                last_exc = ValueError("Gemini returned empty response")
                continue
            data = _extract_json(content)
            if isinstance(data, dict) and "questions" in data:
                data = data["questions"]
            if isinstance(data, list) and len(data) >= max(1, count // 2):
                return _finalize_questions(data, text, count, difficulty, discipline_name)
            last_exc = ValueError(f"Got only {len(data) if isinstance(data, list) else 0} questions, need {count}")
        except Exception as exc:
            last_exc = exc

    _LAST_PROVIDER_ERROR = f"gemini: {str(last_exc)}"[:600] if last_exc else ""
    return []


def _growth_with_gemini(context: str, limit: int = 8) -> list[dict]:
    global _LAST_PROVIDER_ERROR
    api_key = _cfg("GEMINI_API_KEY")
    if not api_key:
        return []

    prompt = _build_growth_prompt(_prepare_source_text(context, max_chars=9000), limit)
    try:
        content = _gemini_chat(
            "Отвечай только валидным JSON-массивом.",
            prompt,
            temperature=0.1,
        )
        if not content:
            return []
        return _normalize_topics(_extract_json(content), limit=limit)
    except Exception as exc:
        _LAST_PROVIDER_ERROR = f"gemini-growth: {str(exc)}"[:600]
        return []


def _generate_with_openai(text: str, count: int, difficulty: str, discipline_name: str | None = None) -> list[dict]:
    global _LAST_PROVIDER_ERROR
    api_key = _cfg("OPENAI_API_KEY")
    if not api_key:
        return []

    try:
        from openai import OpenAI
    except Exception:
        return []

    model = _cfg("OPENAI_MODEL", "gpt-4.1")
    prepared = _prepare_source_text(text)
    theses = _extract_theses_with_openai(prepared, discipline_name=discipline_name)
    system_message, user_message = _build_prompt(prepared, count, difficulty, theses, discipline_name)

    # Адаптивный max_tokens: ~200 на вопрос (4 варианта + текст + JSON-обёртка)
    max_tokens = min(max(count * 220 + 200, 1200), 8192)

    client = OpenAI(api_key=api_key)
    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES + 1):
        temperature = 0.25 + (attempt * 0.15)  # 0.25 → 0.40 → 0.55
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_message},
                ],
                temperature=min(temperature, 0.7),
                max_tokens=max_tokens,
            )
            content = response.choices[0].message.content if response.choices else ""
            data = _extract_json(content or "")
            if isinstance(data, dict) and "questions" in data:
                data = data["questions"]
            if isinstance(data, list) and len(data) >= max(1, count // 2):
                return _finalize_questions(data, text, count, difficulty, discipline_name)
            # Получили слишком мало вопросов — повтор с более высокой temperature
            last_exc = ValueError(f"Got only {len(data) if isinstance(data, list) else 0} questions, need {count}")
        except Exception as exc:
            last_exc = exc

    _LAST_PROVIDER_ERROR = f"openai: {str(last_exc)}"[:600] if last_exc else ""
    return []


def _extract_content_sentences(text: str, min_len: int = 20, max_len: int = 250) -> list[str]:
    """
    Извлекает чистые, содержательные, законченные предложения из текста лекции.
    Каждое — самостоятельная мысль на русском языке, без мусора и кода.
    """
    raw_parts = re.split(r"(?<=[.!?])\s+", text.strip())
    sentences: list[str] = []
    seen: set[str] = set()
    for raw in raw_parts:
        s = re.sub(r"\s+", " ", raw).strip().rstrip(".!?").strip()
        if len(s) < min_len or len(s) > max_len:
            continue
        cyr = len(re.findall(r"[А-Яа-яЁё]", s))
        if cyr < 8:
            continue
        if _looks_like_code_segment(s):
            continue
        low = s.lower()
        if any(w in low for w in ("http", "www.", "wikipedia", "источник:", "source:", "стр.", "страница")):
            continue
        key = re.sub(r"\s+", " ", low)
        if key in seen:
            continue
        seen.add(key)
        sentences.append(s)
    return sentences


def _find_best_term_in_sentence(sentence: str, terms: list[str], skip: int = 0) -> str | None:
    """
    Находит термин из списка, присутствующий в предложении.
    skip=0 — самый длинный, skip=1 — второй по длине, и т.д.
    Термины могут быть в оригинальном регистре — поиск case-insensitive.
    """
    low = sentence.lower()
    found = [(t, len(t)) for t in terms if len(t) >= 4 and t.lower() in low]
    if not found:
        return None
    found.sort(key=lambda x: x[1], reverse=True)
    # Убираем дубликаты
    unique = []
    seen = set()
    for t, l in found:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    if skip >= len(unique):
        return None
    return unique[skip]


# Слова, которые НЕ являются настоящими понятиями (не годятся для "Что верно о понятии X?")
_GENERIC_TOPIC_BLACKLIST = {
    # Общие слова-пустышки, которые НЕ должны быть темами вопросов
    # Формы слова "данные"
    "данных", "данные", "данной", "данного", "данным", "данном",
    # Формы "значение"
    "значений", "значение", "значения",
    # Формы "запись"
    "записей", "записи", "записью",
    # Формы "название"
    "названий", "названия", "название",
    # Формы "условие"
    "условий", "условия", "условие",
    # Формы "правило"
    "правилам", "правил", "правила", "правило",
    # Формы "пример"
    "примеры", "примеров", "пример", "примере",
    # Формы "таблица"
    "таблице", "таблицы", "таблица", "таблиц", "таблицу",
    # Формы "столбец"
    "столбца", "столбцов", "столбец", "столбцы",
    # Формы "строка"
    "строка", "строки", "строке", "строку",
    # Формы "поле"
    "поля", "полей", "поле", "полями", "полем",
    # Формы "тип"
    "типов", "типы", "типа", "типу",
    # Формы "любой"
    "любой", "любая", "любого", "любые", "любых",
    # Формы "набор"
    "набор", "набора", "набору", "набором",
    # Формы "список"
    "список", "списка", "массив",
    # Формы "число"
    "числа", "число", "числе",
    # Формы "часть"
    "части", "часть",
    # Формы "основа"
    "основные", "основной", "основных", "основная",
    # Формы "средство"
    "средство", "средства", "средством",
    # Формы "уровень"
    "уровень", "уровня", "уровне", "уровней",
    # Формы "система/управление/хранение"
    "систем", "система", "системы", "системе", "системой", "системным", "системного",
    "управления", "управление", "управлению",
    "хранения", "хранение", "хранению",
    # Формы "операция"
    "операций", "операции", "операция",
    # Формы "метод"
    "метод", "методы", "методов", "методом",
    # Формы "свойство"
    "свойства", "свойство", "свойством", "свойствами",
    # Формы "процесс"
    "процесс", "процессы", "процессов", "процессе",
    # Формы "область"
    "область", "области",
    # Формы "инструмент"
    "инструмент", "инструменты", "инструментов",
    # Формы "результат"
    "результат", "результаты", "результатов",
    # Формы "работа/структура"
    "работы", "работа", "работе", "работу",
    "структуры", "структура", "структуре", "структур",
    # Формы "объект/класс/формат/компонент"
    "объект", "объекты", "объектов", "объектом",
    "класс", "формат", "компонент", "компоненты",
    # Формы "база/базовый"
    "база", "базы", "базой", "базу", "базами", "базовые", "базовый", "базовых",
    # Формы "доступ"
    "доступа", "доступ", "доступом", "доступе",
    # Формы "схема/проект/курс"
    "схема", "схемы", "схеме", "проект", "проекта", "проекте",
    "курса", "курс", "курсе",
    # Формы "информация"
    "информации", "информация", "информационных", "информационной",
    # Формы "потребность/характеристика"
    "потребностей", "потребности", "характеристики", "характеристик",
    # Формы "конкретный/несколько/удобство"
    "конкретных", "конкретный", "конкретного",
    "нескольких", "несколько",
    "удобства", "удобство",
    # Общие глаголы и наречия (не термины)
    "наиболее", "если", "стать", "есть", "одной", "одного", "одну",
    "указан", "указана", "указано", "может", "могут",
    "также", "этого", "более", "менее", "всего", "каждый", "каждого",
    "должен", "должна", "нужно", "можно", "после", "перед", "между",
    "другой", "другие", "других", "только", "всегда", "никогда",
    "который", "которая", "которые", "которого", "которой",
    # Формы "задача/функция/модель/элемент/параметр/определение"
    "задача", "задачи", "задачу", "задач",
    "функция", "функции", "функций", "функцию",
    "модель", "модели", "моделей", "модели",
    "элемент", "элементы", "элементов",
    "параметр", "параметры", "параметров",
    "определение", "определения", "понятие", "понятия",
    # Формы "использование/применение/создание/выполнение"
    "использование", "использования", "применение", "применения",
    "создание", "создания", "выполнение", "выполнения",
    # Формы "возможность/способ/вариант/раздел/группа"
    "возможность", "возможности", "способ", "способы", "способов",
    "вариант", "варианты", "вариантов",
    "раздел", "разделы", "группа", "группы", "групп",
    # Общие формы от "документ/пользователь/сервер" — слишком общие для quiz
    "документ", "документы", "документов",
    "пользователь", "пользователя", "пользователей", "пользователю",
    "сервер", "сервера", "серверов", "сервере",
}


def _is_cyrillic_term(term: str) -> bool:
    """Проверяет, что термин написан кириллицей."""
    cyr = len(re.findall(r"[А-Яа-яЁё]", term))
    lat = len(re.findall(r"[A-Za-z]", term))
    return cyr > lat


_NEGATION_VERBS = [
    "является", "используется", "применяется", "позволяет", "включает",
    "обеспечивает", "определяет", "содержит", "требует", "может",
    "описывает", "представляет", "выполняет", "поддерживает", "предоставляет",
    "реализует", "создаёт", "создает", "формирует", "задаёт", "задает",
]


def _modify_sentence_wrong(sentence: str, terms: list[str]) -> str | None:
    """
    Модифицирует предложение, делая его фактически неверным.
    Эдинственная надёжная стратегия: вставка/удаление отрицания.
    (Подстановка случайных слов удалена — создавала бессмыслицу вроде 'набор субд конкретных атрибутов'.)
    """
    low = sentence.lower()
    for verb in _NEGATION_VERBS:
        if verb in low:
            if f"не {verb}" in low:
                return sentence.replace(f"не {verb}", verb, 1)
            return re.sub(rf"\b({re.escape(verb)})", r"не \1", sentence, count=1)
    return None


# ── Парсер определений из текста ──────────────────────────────────────────

def _extract_definitions(text: str) -> list[dict]:
    """
    Извлекает определения вида «Термин — описание» или «Термин - описание».
    Работает построчно для надёжности. Склеивает строки-продолжения.
    Возвращает [{"term": "Сущность", "definition": "класс, хранящийся в БД, таблица"}].
    """
    definitions: list[dict] = []
    seen_terms: set[str] = set()
    lines = (text or "").split("\n")

    def_pattern = re.compile(
        r"^[\-•*]?\s*"
        r"([A-ZА-ЯЁa-zа-яё][A-Za-zА-Яа-яЁё0-9 ]{0,35}?)"  # термин
        r"\s*[\-—–]\s+"                                         # разделитель
        r"(.{10,})",                                            # описание
    )

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or len(line) < 10:
            continue

        clean = line.lstrip("-•* \t")
        m = def_pattern.match(clean)
        if not m:
            continue

        term = m.group(1).strip()
        defn = m.group(2).strip()

        # Склеиваем строки-продолжения (строки, которые НЕ начинаются с заглавной
        # и НЕ начинаются с маркера списка — значит, продолжение предыдущей строки)
        while i < len(lines):
            next_line = lines[i].strip()
            if not next_line:
                break
            # Это новое определение или заголовок?
            next_clean = next_line.lstrip("-•* \t")
            if def_pattern.match(next_clean):
                break
            # Если начинается с заглавной и без "-" — это заголовок
            if next_clean and next_clean[0].isupper() and not next_line.startswith("-"):
                break
            # Это продолжение — склеиваем
            defn += " " + next_line
            i += 1

        defn = re.sub(r"\s+", " ", defn).strip().rstrip(".")

        # Фильтр: термин 1-3 слова
        words = term.split()
        if len(words) > 3 or len(words) == 0:
            continue
        # Фильтр: первое слово должно начинаться с заглавной
        if not words[0][0].isupper():
            continue
        # Фильтр: термин не в blacklist
        if term.lower() in _GENERIC_TOPIC_BLACKLIST:
            continue
        # Фильтр: описание ≥15 символов и ≥3 слова
        if len(defn) < 15 or len(defn.split()) < 3:
            continue
        # Дедупликация
        key = term.lower()
        if key in seen_terms:
            continue
        seen_terms.add(key)

        definitions.append({"term": term, "definition": defn})

    # Сортируем: настоящие определения (1-2 слова в термине, описание начинается с сущ.)
    # идут первыми, а "план курса" (описание начинается с "как", "от", "когда") — в конец
    _plan_starters = ("как ", "от ", "когда ", "связь ", "для ", "зачем ", "и как ", "не ")

    def _def_quality(d: dict) -> int:
        desc_low = d["definition"].lower().strip()
        term_words = len(d["term"].split())
        # Описания-планы курса — последние
        if any(desc_low.startswith(s) for s in _plan_starters):
            return 10 + term_words
        # Короткие термины с длинным описанием — лучшие
        return term_words

    definitions.sort(key=_def_quality)

    return definitions


def _fb_definition(
    definitions: list[dict],
    idx: int,
    used_def_terms: set[str],
) -> dict | None:
    """
    Вопрос: «Что такое [Термин]?»
    Правильный ответ — определение из лекции.
    Неверные — определения ДРУГИХ терминов.
    """
    if len(definitions) < 3:
        return None

    # Выбираем определение, которое ещё не использовалось
    target = None
    for i in range(len(definitions)):
        candidate = definitions[(idx + i) % len(definitions)]
        if candidate["term"].lower() not in used_def_terms:
            target = candidate
            break

    if not target:
        return None

    correct_opt = _shorten_text(target["definition"], limit=160)

    # Неверные — определения других терминов
    # Приоритет: определения "того же типа" (количество слов в термине ±1)
    target_tw = len(target["term"].split())
    wrong_opts: list[str] = []
    seen = {correct_opt.lower()}

    # Первый проход: близкие по типу
    for d in definitions:
        if d["term"].lower() == target["term"].lower():
            continue
        tw = len(d["term"].split())
        if abs(tw - target_tw) > 1:
            continue
        short = _shorten_text(d["definition"], limit=160)
        if short.lower() in seen:
            continue
        seen.add(short.lower())
        wrong_opts.append(short)
        if len(wrong_opts) >= 3:
            break

    # Второй проход: любые определения (если не хватило)
    if len(wrong_opts) < 3:
        for d in definitions:
            if d["term"].lower() == target["term"].lower():
                continue
            short = _shorten_text(d["definition"], limit=160)
            if short.lower() in seen:
                continue
            seen.add(short.lower())
            wrong_opts.append(short)
            if len(wrong_opts) >= 3:
                break

    if len(wrong_opts) < 2:
        return None

    used_def_terms.add(target["term"].lower())

    display = target["term"]
    stems = [
        f"Что такое «{display}» согласно лекции?",
        f"Какое определение «{display}» даётся в лекции?",
        f"Как определяется «{display}» в материале?",
        f"Что означает «{display}» в контексте лекции?",
        f"Как характеризуется «{display}» в лекции?",
    ]
    q_text = stems[idx % len(stems)]

    options = [correct_opt] + wrong_opts[:3]
    random.shuffle(options)
    correct_index = options.index(correct_opt)

    return {"text": q_text, "options": options, "correct_index": correct_index}


def _generate_fallback(text: str, count: int, difficulty: str, discipline_name: str | None = None) -> list[dict]:
    """
    Генерация вопросов без AI — на основе реального содержания лекции.

    Приоритет типов:
    1. Вопросы по определениям (если найдены паттерны «Термин - описание»).
    2. Верное утверждение — одно настоящее + три модифицированных (отрицание).
    3. Вопрос по теме — «Что верно о [topic]?» с правильным описанием и ложными.
    4. Cloze (пропуск термина) — только как последний резерв.
    """
    prepared = _prepare_source_text(text, max_chars=12000)
    sentences = _extract_content_sentences(prepared)
    terms = _extract_terms(prepared, limit=40)
    focus_units = _extract_fallback_focus_units(text, limit=max(count * 2, 10))

    # ── Шаг 0: Извлечь определения из ОРИГИНАЛЬНОГО текста (до prepare) ───
    #    _prepare_source_text склеивает строки и портит формат "Термин - описание"
    definitions = _extract_definitions(text)

    # Если мало предложений — добавляем из простого разбивателя (пониженный порог)
    if len(sentences) < 6:
        seen_low = {s.lower() for s in sentences}
        for s in _sentences(prepared):
            if s.lower() not in seen_low and len(s) >= 12:
                sentences.append(s)
                seen_low.add(s.lower())

    # Если предложения по-прежнему слишком короткие — склеиваем пары
    if sentences and all(len(s) < 30 for s in sentences):
        merged: list[str] = []
        for i in range(0, len(sentences) - 1, 2):
            merged.append(f"{sentences[i]}. {sentences[i + 1]}")
        if len(sentences) % 2 == 1:
            merged.append(sentences[-1])
        sentences = merged

    if len(sentences) < 2 or len(terms) < 3:
        if len(definitions) < 3 and not focus_units:
            return []

    # Построить индекс термин → предложения (case-insensitive)
    term_sents: dict[str, list[str]] = {}
    for sent in sentences:
        low = sent.lower()
        for t in terms:
            if t.lower() in low:
                term_sents.setdefault(t, []).append(sent)

    questions: list[dict] = []
    used_texts: set[str] = set()
    used_topic_terms: set[str] = set()
    used_def_terms: set[str] = set()

    # ── Шаг 1: Вопросы по определениям (приоритет!) ───────────────
    if len(definitions) >= 3:
        for i in range(min(count, len(definitions))):
            q = _fb_definition(definitions, i, used_def_terms)
            if q and q["text"] not in used_texts:
                used_texts.add(q["text"])
                questions.append(q)
            if len(questions) >= count:
                break

    # ── Шаг 2: Добиваем true_statement и topic ────────────────────
    sent_idx = 0
    type_names = [1, 2, 1, 2, 1, 2]
    max_attempts = count * 8
    for attempt in range(max_attempts):
        if len(questions) >= count:
            break
        qtype = type_names[attempt % len(type_names)]
        sent = sentences[sent_idx % len(sentences)] if sentences else None
        sent_idx += 1
        if not sent:
            break

        q = None
        if qtype == 1:
            q = _fb_true_statement(sent, sentences, terms, attempt)
        elif qtype == 2:
            q = _fb_topic(sent, terms, term_sents, sentences, attempt, used_topic_terms)

        if q and q["text"] not in used_texts:
            used_texts.add(q["text"])
            questions.append(q)

    # ── Шаг 3: резервная добивка только содержательными вопросами ─
    if len(questions) < count:
        for attempt in range(count * 6):
            if len(questions) >= count:
                break
            sent = sentences[attempt % len(sentences)]
            q = _fb_true_statement(sent, sentences, terms, attempt)
            if not q:
                q = _fb_topic(sent, terms, term_sents, sentences, attempt, used_topic_terms)
            if not q and len(definitions) >= 3:
                q = _fb_definition(definitions, attempt, used_def_terms)
            if q and q["text"] not in used_texts:
                used_texts.add(q["text"])
                questions.append(q)

    # ── Шаг 4: последний безопасный резерв — темы, реально упомянутые в лекции ─
    if len(questions) < count and focus_units:
        for attempt in range(count * 4):
            if len(questions) >= count:
                break
            q = _fb_mentioned_unit(focus_units, attempt, discipline_name=discipline_name)
            if q and q["text"] not in used_texts:
                used_texts.add(q["text"])
                questions.append(q)

    return questions


_FALLBACK_DISTRACTOR_UNITS = [
    "реляционная схема",
    "нормализация данных",
    "транзакционная изоляция",
    "хеш-таблица",
    "дерево решений",
    "маршрутизация пакетов",
    "контроль доступа",
    "виртуальная память",
    "контейнеризация сервисов",
    "объектно-ориентированное проектирование",
    "система контроля версий",
    "асимметричное шифрование",
]


def _extract_fallback_focus_units(text: str, limit: int = 12) -> list[str]:
    source = (text or "").strip()
    source_low = " ".join(source.lower().split())
    candidates = _extract_focus_units(source, limit=max(limit * 2, 12))

    units: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        candidate = " ".join((raw or "").strip().split())
        normalized = candidate.lower()
        if len(candidate) < 5:
            continue
        if _has_source_artifacts(candidate):
            continue
        if normalized not in source_low:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        units.append(candidate)
        if len(units) >= limit:
            return units

    for raw in _extract_terms(source, limit=max(limit * 3, 12)):
        candidate = " ".join((raw or "").strip().split())
        normalized = candidate.lower()
        if len(candidate) < 5:
            continue
        if _has_source_artifacts(candidate):
            continue
        if normalized not in source_low:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        units.append(candidate)
        if len(units) >= limit:
            break

    return units


def _fb_mentioned_unit(
    focus_units: list[str],
    idx: int,
    discipline_name: str | None = None,
) -> dict | None:
    if not focus_units:
        return None

    target = focus_units[idx % len(focus_units)]
    correct_opt = target if target[0].isupper() else target.capitalize()
    seen = {correct_opt.lower()}
    distractors: list[str] = []
    for item in _FALLBACK_DISTRACTOR_UNITS:
        normalized = item.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        distractors.append(item if item[0].isupper() else item.capitalize())
        if len(distractors) >= 3:
            break
    if len(distractors) < 3:
        return None

    stems = [
        "Какая тема прямо упоминается в лекции?",
        "Какой термин относится к содержанию лекции?",
        "Какое понятие встречается в учебном материале?",
        "Какой из вариантов действительно присутствует в лекции?",
        "Какой термин рассматривается в данном материале?",
    ]
    if discipline_name:
        stems[1] = f"Какой термин относится к дисциплине «{discipline_name}» и встречается в лекции?"

    options = [correct_opt] + distractors[:3]
    random.shuffle(options)
    correct_index = options.index(correct_opt)
    return {
        "text": stems[idx % len(stems)],
        "options": options,
        "correct_index": correct_index,
    }


def _fb_cloze(sentence: str, terms: list[str], idx: int) -> dict | None:
    """Cloze-вопрос: пропуск термина в реальном предложении."""
    # Только кириллические термины — латинские (mysql, redis) создают мусорные cloze-вопросы
    cyr_terms = [t for t in terms if _is_cyrillic_term(t) and t.lower() not in _GENERIC_TOPIC_BLACKLIST]
    if not cyr_terms:
        return None
    # Ротация: для одного предложения пробуем разные термины (skip)
    target = _find_best_term_in_sentence(sentence, cyr_terms, skip=idx % max(1, len([t for t in cyr_terms if t.lower() in sentence.lower()])))
    if not target:
        target = _find_best_term_in_sentence(sentence, cyr_terms, skip=0)
    if not target:
        return None

    # Заменяем термин на ___
    pattern = re.compile(re.escape(target), re.IGNORECASE)
    match = pattern.search(sentence)
    if not match:
        return None
    original_case = match.group(0)
    blanked = sentence[: match.start()] + "___" + sentence[match.end() :]
    blanked = re.sub(r"\s+", " ", blanked).strip()

    # Укорачиваем при необходимости (оставляем контекст вокруг ___)
    if len(blanked) > 110:
        bp = blanked.index("___")
        start = max(0, bp - 45)
        end = min(len(blanked), bp + 48 + 3)
        blanked = ("…" if start > 0 else "") + blanked[start:end].strip() + ("…" if end < len(blanked) else "")

    q_text = f"Вставьте пропущенный термин: {blanked}"
    if not q_text.endswith("?"):
        q_text = q_text.rstrip(".!") + "?"

    correct_low = target.lower()
    target_is_cyr = _is_cyrillic_term(target)
    # Подбираем дистракторы — похожие по длине, ТОГО ЖЕ ЯЗЫКА
    pool = [
        t for t in terms
        if t.lower() != correct_low
        and len(t) >= 3
        and _is_cyrillic_term(t) == target_is_cyr
    ]
    pool.sort(key=lambda t: abs(len(t) - len(target)))
    distractors = []
    seen = {correct_low}
    for t in pool:
        if t.lower() not in seen:
            seen.add(t.lower())
            distractors.append(t)
        if len(distractors) >= 3:
            break
    if len(distractors) < 3:
        return None

    options = [original_case] + distractors[:3]
    random.shuffle(options)
    correct_index = next(i for i, o in enumerate(options) if o.lower() == correct_low)

    return {"text": _shorten_text(q_text), "options": options, "correct_index": correct_index}


def _fb_true_statement(sentence: str, all_sentences: list[str], terms: list[str], idx: int) -> dict | None:
    """
    Вопрос: «Какое утверждение верно?»
    Правильный вариант — реальное предложение.
    Неверные — другие предложения с добавленным отрицанием.
    Если отрицание невозможно — используем предложения из ДРУГИХ тем как неверные.
    """
    correct_opt = _shorten_text(sentence, limit=100)

    wrong_opts: list[str] = []
    tried: set[int] = set()

    # Стратегия 1: отрицание предложений
    for j in range(min(len(all_sentences), 15)):
        other_idx = (idx + j + 1) % len(all_sentences)
        if other_idx in tried:
            continue
        tried.add(other_idx)
        other = all_sentences[other_idx]
        if other == sentence:
            continue
        modified = _modify_sentence_wrong(other, terms)
        if modified:
            short = _shorten_text(modified, limit=100)
            if short != correct_opt and short.lower() != correct_opt.lower():
                wrong_opts.append(short)
        if len(wrong_opts) >= 3:
            break

    # Стратегия 2 (запасная): если не достаточно отрицаний — используем предложения
    # про ДРУГИЕ темы как неверные (они фактически неверны для вопроса о конкретной теме)
    if len(wrong_opts) < 3:
        main_term = _find_best_term_in_sentence(sentence, terms)
        if main_term:
            for other in all_sentences:
                if len(wrong_opts) >= 3:
                    break
                if main_term.lower() in other.lower():
                    continue  # пропускаем предложения с тем же термином
                short = _shorten_text(other, limit=100)
                if short.lower() == correct_opt.lower():
                    continue
                if short in wrong_opts:
                    continue
                wrong_opts.append(short)

    if len(wrong_opts) < 3:
        return None

    # Формулировка вопроса
    main_term = _find_best_term_in_sentence(sentence, terms)
    if main_term and _is_cyrillic_term(main_term) and main_term.lower() not in _GENERIC_TOPIC_BLACKLIST:
        display = main_term.capitalize()
        stems = [
            f"Какое определение «{display}» соответствует материалу лекции?",
            f"Что характеризует «{display}» согласно лекции?",
            f"Как описывается «{display}» в материале?",
            f"Какое свойство «{display}» указано в лекции?",
            f"Какое описание «{display}» является верным?",
        ]
    else:
        stems = [
            "Какое определение из лекции сформулировано корректно?",
            "Какое описание из материала является верным?",
            "Какая характеристика из лекции указана правильно?",
            "Какое свойство описано верно в материале лекции?",
            "Какая формулировка из лекции точна?",
        ]
    q_text = stems[idx % len(stems)]

    options = [correct_opt] + wrong_opts[:3]
    random.shuffle(options)
    correct_index = options.index(correct_opt)

    return {"text": q_text, "options": options, "correct_index": correct_index}


def _fb_topic(
    sentence: str,
    terms: list[str],
    term_sents: dict[str, list[str]],
    all_sentences: list[str],
    idx: int,
    used_topic_terms: set[str] | None = None,
) -> dict | None:
    """
    Вопрос: «Что верно о [термин]?»
    Правильный вариант — предложение, реально описывающее термин.
    Неверные — предложения о других терминах (подставленные как ответы).
    """
    target = _find_best_term_in_sentence(sentence, terms)
    if not target:
        return None

    # Не повторяем термин в разных вопросах
    if used_topic_terms is not None:
        if target.lower() in used_topic_terms:
            return None

    # Находим предложение, содержащее термин (правильный ответ)
    # Приоритет: предложения, где термин стоит В НАЧАЛЕ (это определения)
    related = term_sents.get(target, [])
    if not related:
        return None
    target_low = target.lower()
    # Сортируем: предложения-определения (термин в начале) идут первыми
    def _definition_score(s: str) -> int:
        low = s.lower().strip()
        if low.startswith(target_low):
            return 0  # лучший приоритет — начинается с термина
        pos = low.find(target_low)
        if pos >= 0 and pos < 20:
            return 1  # термин близко к началу
        return 2  # термин где-то в середине/конце
    ranked_related = sorted(related, key=_definition_score)
    correct_sent = ranked_related[idx % len(ranked_related)]
    correct_opt = _shorten_text(correct_sent, limit=100)

    # Ищем неверные варианты — предложения, НЕ содержащие целевой термин
    wrong_opts: list[str] = []
    seen = {correct_opt.lower()}
    for j, other in enumerate(all_sentences):
        if target.lower() in other.lower():
            continue
        short = _shorten_text(other, limit=100)
        if short.lower() in seen:
            continue
        seen.add(short.lower())
        wrong_opts.append(short)
        if len(wrong_opts) >= 3:
            break

    if len(wrong_opts) < 3:
        return None

    # Фильтруем: термин должен быть содержательным понятием, а не общим словом
    if target.lower() in _GENERIC_TOPIC_BLACKLIST:
        return None
    if not _is_cyrillic_term(target):
        return None  # латинские термины (mysql, redis) не годятся для "Что верно о понятии..."
    if len(target) < 5:
        return None
    # Капитализируем термин для вопроса
    display_term = target if target[0].isupper() else target.capitalize()
    q_text = f"Что описывает понятие «{display_term}» согласно лекции?"

    options = [correct_opt] + wrong_opts[:3]
    random.shuffle(options)
    correct_index = options.index(correct_opt)

    # Отмечаем термин как использованный
    if used_topic_terms is not None:
        used_topic_terms.add(target.lower())

    return {"text": _shorten_text(q_text), "options": options, "correct_index": correct_index}


def _normalize_topics(items: Any, limit: int = 8) -> list[dict]:
    if isinstance(items, dict):
        items = items.get("topics") or items.get("items") or []
    if not isinstance(items, list):
        return []

    out: list[dict] = []
    seen: set[str] = set()
    for raw in items:
        if isinstance(raw, dict):
            topic = str(raw.get("topic") or raw.get("title") or "").strip()
            reason = str(raw.get("reason") or raw.get("why") or "").strip()
            query = str(raw.get("query") or topic).strip()
        else:
            topic = str(raw or "").strip()
            reason = ""
            query = topic

        topic = re.sub(r"\s+", " ", topic)
        reason = re.sub(r"\s+", " ", reason)
        query = re.sub(r"\s+", " ", query)

        if len(topic) < 4:
            continue
        key = topic.lower()
        if key in seen:
            continue
        seen.add(key)

        out.append(
            {
                "topic": topic,
                "reason": reason if reason else "Эта тема связана с ошибками в ответах.",
                "query": query if query else topic,
            }
        )
        if len(out) >= limit:
            break
    return out


def _build_growth_prompt(context: str, limit: int = 8) -> str:
    return (
        "Ты учебный наставник. Ниже ошибки студента в тестах. "
        "Определи ключевые темы, которые нужно изучить глубже. "
        "Верни строго JSON-массив объектов формата: "
        '[{"topic":"...","reason":"...","query":"..."}]. '
        "topic: короткое название темы; reason: почему это важно; query: фраза для поиска в Google. "
        f"Нужно до {limit} тем, без воды, на русском языке.\n\n"
        f"Ошибки:\n{context}"
    )


def _growth_with_openai(context: str, limit: int = 8) -> list[dict]:
    global _LAST_PROVIDER_ERROR
    api_key = _cfg("OPENAI_API_KEY")
    if not api_key:
        return []
    try:
        from openai import OpenAI
    except Exception:
        return []

    model = _cfg("OPENAI_MODEL", "gpt-4.1")
    prompt = _build_growth_prompt(_prepare_source_text(context, max_chars=9000), limit)

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Отвечай только валидным JSON-массивом."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
        content = response.choices[0].message.content if response.choices else ""
        return _normalize_topics(_extract_json(content or ""), limit=limit)
    except Exception as exc:
        _LAST_PROVIDER_ERROR = f"openai-growth: {str(exc)}"[:600]
        return []


_GROWTH_NOISE_WORDS = {
    "вопрос", "ответ", "студент", "студента", "правильный", "наиболее",
    "средний", "уровень", "точно", "тест", "верно", "неверно", "является",
    "какой", "какая", "какое", "какие", "текст", "лекция", "лекции",
    "вариант", "выбор", "пропущенный", "термин", "утверждение",
    "более", "менее", "очень", "можно", "нужно", "должен", "может",
    "этого", "этой", "этот", "после", "перед", "через", "между",
    "также", "всего", "только", "далее", "будет", "было", "были",
    "правильный", "корректный", "согласно", "материал", "верный",
    "следующий", "данный", "данная", "данные", "основной", "главный",
}


def _growth_fallback(context: str, limit: int = 8) -> list[dict]:
    """
    Fallback для точек роста — извлекает реальные тематические понятия
    из контекста ошибок студента (тест/вопрос/правильный ответ).
    """
    # Парсим структурированный контекст — ищем строки с "Вопрос:" и "Правильный ответ:"
    question_lines: list[str] = []
    answer_lines: list[str] = []
    test_titles: list[str] = []
    for line in (context or "").split("\n"):
        stripped = line.strip()
        if stripped.startswith("Вопрос:"):
            question_lines.append(stripped[len("Вопрос:"):].strip())
        elif stripped.startswith("Правильный ответ:"):
            answer_lines.append(stripped[len("Правильный ответ:"):].strip())
        elif stripped.startswith("Тест:"):
            test_titles.append(stripped[len("Тест:"):].strip())

    # Собираем содержательный текст для извлечения тем
    source_text = " ".join(question_lines + answer_lines + test_titles)
    if not source_text.strip():
        source_text = context or ""

    # Извлекаем термины, фильтруя шумовые слова
    tokens = re.findall(r"[А-Яа-яЁё0-9A-Za-z-]{4,}", source_text.lower())
    freq: dict[str, int] = {}
    for token in tokens:
        if token in RU_STOPWORDS or token in _GROWTH_NOISE_WORDS:
            continue
        if token.isdigit():
            continue
        if _is_noise_token(token):
            continue
        freq[token] = freq.get(token, 0) + 1

    # Извлекаем биграммы — более содержательные темы
    filtered_tokens = [t for t in tokens if t not in RU_STOPWORDS and t not in _GROWTH_NOISE_WORDS and not t.isdigit()]
    bigram_freq: dict[str, int] = {}
    for i in range(len(filtered_tokens) - 1):
        bg = f"{filtered_tokens[i]} {filtered_tokens[i + 1]}"
        if len(bg) >= 8:
            bigram_freq[bg] = bigram_freq.get(bg, 0) + 1

    # Приоритет: биграммы (более содержательные) → одиночные термины
    ranked_bigrams = sorted(bigram_freq.items(), key=lambda x: x[1], reverse=True)
    ranked_words = sorted(freq.items(), key=lambda x: (x[1], len(x[0])), reverse=True)

    topics: list[dict] = []
    seen: set[str] = set()

    # Сначала добавляем уникальные названия тестов как темы
    unique_tests: set[str] = set()
    for title in test_titles:
        title = re.sub(r"\s+", " ", title).strip()
        if title and len(title) >= 5 and title.lower() not in unique_tests:
            unique_tests.add(title.lower())
            topics.append({
                "topic": title,
                "reason": f"В тесте \"{title}\" допущены ошибки — стоит повторить материал.",
                "query": f"{title} теория основы",
            })
            seen.add(title.lower())
            if len(topics) >= limit:
                return topics

    # Затем биграммы
    for bg, cnt in ranked_bigrams:
        if bg in seen:
            continue
        seen.add(bg)
        topic = bg.capitalize()
        topics.append({
            "topic": topic,
            "reason": f"Понятие встречается в {cnt} ошибочных ответах — рекомендуется повторить.",
            "query": f"{topic} определение примеры",
        })
        if len(topics) >= limit:
            break

    # Дополняем одиночными терминами
    for word, cnt in ranked_words:
        if word in seen or any(word in s for s in seen):
            continue
        seen.add(word)
        topic = word.capitalize()
        topics.append({
            "topic": topic,
            "reason": f"Термин связан с {cnt} ошибками — рекомендуется разобрать подробнее.",
            "query": f"{topic} определение примеры применение",
        })
        if len(topics) >= limit:
            break

    if not topics:
        topics = [{
            "topic": "Повторение пройденного материала",
            "reason": "Общая рекомендация по итогам допущенных ошибок.",
            "query": "как эффективно повторять учебный материал",
        }]

    return topics


def generate_growth_topics(context: str, limit: int = 8) -> list[dict]:
    global _LAST_PROVIDER_ERROR
    _ensure_env_loaded()
    _LAST_PROVIDER_ERROR = ""
    limit = max(3, min(int(limit), 12))

    # Gemini first
    result = _growth_with_gemini(context, limit)
    if result:
        return result

    # Then OpenAI
    result = _growth_with_openai(context, limit)
    if result:
        return result

    return _growth_fallback(context, limit)


def diagnose_ai_setup() -> str:
    global _LAST_PROVIDER_ERROR
    _ensure_env_loaded()
    gemini_key = bool(_cfg("GEMINI_API_KEY"))
    openai_key = bool(_cfg("OPENAI_API_KEY"))
    allow_fallback = _cfg("AI_ALLOW_FALLBACK", "true").strip().lower() in {"1", "true", "yes", "on"}

    if not gemini_key and not openai_key:
        return "Не найден ни GEMINI_API_KEY, ни OPENAI_API_KEY."

    if _LAST_PROVIDER_ERROR:
        return f"Ошибка провайдера: {_LAST_PROVIDER_ERROR}"

    if not allow_fallback:
        return "Провайдер ответил пусто/невалидно, fallback отключен (AI_ALLOW_FALLBACK=false)."

    return "Провайдер вернул пустой или невалидный JSON вопросов."


def generate_questions(
    text: str,
    count: int = 5,
    difficulty: str = "medium",
    discipline_name: str | None = None,
) -> list[dict]:
    global _LAST_PROVIDER_ERROR
    _ensure_env_loaded()
    _LAST_PROVIDER_ERROR = ""
    count = max(1, min(int(count), 50))
    difficulty = (difficulty or "medium").strip().lower()
    if difficulty not in ALLOWED_DIFFICULTY:
        difficulty = "medium"

    # Gemini first
    result = _generate_with_gemini(text, count, difficulty, discipline_name)
    if result:
        return result

    # Then OpenAI
    result = _generate_with_openai(text, count, difficulty, discipline_name)
    if result:
        return result

    allow_fallback = _cfg("AI_ALLOW_FALLBACK", "true").strip().lower() in {"1", "true", "yes", "on"}
    if allow_fallback:
        raw = _generate_fallback(text, max(count * 2, count + 6), difficulty, discipline_name)
        normalized = _normalize_questions(raw, count, strict=False) if raw else []
        if len(normalized) < count:
            extra = _generate_fallback(text, max(count * 4, count + 20), difficulty, discipline_name)
            normalized = _normalize_questions(normalized + extra, count, strict=False)
        return _top_up_questions(normalized, text, count)
    return []

