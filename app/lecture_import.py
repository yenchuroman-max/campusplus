from __future__ import annotations

import base64
from io import BytesIO
import os
from pathlib import Path
import re
import zipfile
from html import unescape
import ipaddress
from urllib.parse import parse_qs, unquote, urlparse, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from fastapi import UploadFile


class LectureImportError(Exception):
    pass


def parse_source_urls(raw: str, max_urls: int = 5) -> list[str]:
    parts = re.split(r"[\n,;]+", (raw or "").strip())
    urls: list[str] = []
    seen: set[str] = set()
    for part in parts:
        candidate = part.strip()
        if not candidate:
            continue
        if not re.match(r"^https?://", candidate, flags=re.IGNORECASE):
            candidate = f"https://{candidate}"

        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise LectureImportError(f"Некорректная ссылка: {part}")

        normalized = parsed.geturl()
        if normalized in seen:
            continue
        seen.add(normalized)
        urls.append(normalized)

    if len(urls) > max_urls:
        raise LectureImportError(f"Можно указать не более {max_urls} ссылок за раз.")
    return urls


def _is_blocked_host(hostname: str) -> bool:
    host = (hostname or "").strip().lower()
    if not host:
        return True
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True

    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        # domain names are allowed; we block only explicit local/private IP literals
        return False


def _fetch_html(url: str, timeout: int = 10) -> str:
    parsed = urlparse(url)
    if _is_blocked_host(parsed.hostname or ""):
        raise LectureImportError(f"Ссылка ведёт на недоступный хост: {parsed.hostname}")

    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; LectureImporter/1.0; +https://example.local)"
        },
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                raise LectureImportError(f"Ссылка не содержит HTML-страницу: {url}")
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                return raw.decode(charset, errors="ignore")
            except Exception:
                return raw.decode("utf-8", errors="ignore")
    except LectureImportError:
        raise
    except Exception as exc:
        raise LectureImportError(f"Не удалось загрузить страницу: {url}") from exc


def _extract_html_text(html: str, is_wikipedia: bool = False) -> str:
    cleaned = re.sub(r"(?is)<(script|style|noscript|svg|img|iframe).*?>.*?</\1>", " ", html)

    if is_wikipedia:
        sections = re.findall(r"(?is)<p[^>]*>(.*?)</p>", cleaned)
    else:
        sections = re.findall(r"(?is)<(h1|h2|h3|p|li)[^>]*>(.*?)</\1>", cleaned)
        sections = [chunk[1] if isinstance(chunk, tuple) else chunk for chunk in sections]

    text_parts: list[str] = []
    for section in sections:
        text = re.sub(r"(?is)<br\s*/?>", "\n", section)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
        text = unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) >= 40:
            text_parts.append(text)

    if not text_parts:
        plain = re.sub(r"(?is)<[^>]+>", " ", cleaned)
        plain = unescape(plain)
        plain = re.sub(r"\s+", " ", plain).strip()
        if len(plain) < 80:
            return ""
        return plain

    return "\n\n".join(text_parts)


def _extract_wikipedia_title(url: str) -> str | None:
    parsed = urlparse(url)
    path = parsed.path or ""
    if "/wiki/" in path:
        title = path.split("/wiki/", 1)[1].strip("/")
        title = unquote(title).replace("_", " ").strip()
        return title or None
    query_title = parse_qs(parsed.query).get("title", [""])[0].strip()
    if query_title:
        return unquote(query_title).replace("_", " ").strip() or None
    return None


def _fetch_wikipedia_text(url: str, timeout: int = 10) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if "wikipedia.org" not in host:
        return ""

    title = _extract_wikipedia_title(url)
    if not title:
        return ""

    api_url = f"{parsed.scheme}://{host}/w/api.php?{urlencode({'action': 'query', 'prop': 'extracts', 'explaintext': 1, 'titles': title, 'format': 'json', 'redirects': 1})}"
    req = Request(
        api_url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; LectureImporter/1.0)"},
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""

    match = re.search(r'"extract"\s*:\s*"(.*?)"\s*(,|})', payload, flags=re.DOTALL)
    if not match:
        return ""

    text = match.group(1)
    text = text.encode("utf-8").decode("unicode_escape", errors="ignore")
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_text_from_urls(urls: list[str], max_total_chars: int = 50000) -> str:
    chunks: list[str] = []
    total = 0

    for url in urls:
        parsed = urlparse(url)
        is_wikipedia = "wikipedia.org" in (parsed.netloc or "").lower()

        text = ""
        if is_wikipedia:
            text = _fetch_wikipedia_text(url).strip()

        if not text:
            html = _fetch_html(url)
            text = _extract_html_text(html, is_wikipedia=is_wikipedia).strip()

        if not text:
            continue

        block = f"Источник: {url}\n{text}"
        total += len(block)
        if total > max_total_chars:
            break
        chunks.append(block)

    merged = "\n\n".join(chunks).strip()
    if not merged:
        raise LectureImportError("Не удалось извлечь текст из указанных ссылок.")
    return merged


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="ignore")


def _from_txt(raw: bytes) -> str:
    return _decode_text(raw)


def _from_docx(raw: bytes) -> str:
    try:
        from docx import Document
    except Exception as exc:
        raise LectureImportError("Для DOCX нужен пакет python-docx.") from exc
    document = Document(BytesIO(raw))
    lines = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    return "\n".join(lines)


def _from_pptx(raw: bytes) -> str:
    """Извлекает текст из PPTX без внешних зависимостей."""
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            slide_files = sorted(
                name
                for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            )
            chunks: list[str] = []
            ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}

            for slide_name in slide_files:
                xml_bytes = archive.read(slide_name)
                root = ET.fromstring(xml_bytes)
                texts = [(node.text or "").strip() for node in root.findall(".//a:t", ns)]
                slide_text = "\n".join(t for t in texts if t)
                if slide_text:
                    chunks.append(slide_text)
            return "\n\n".join(chunks)
    except Exception as exc:
        raise LectureImportError("Не удалось прочитать PPTX файл.") from exc


def _from_pdf(raw: bytes) -> str:
    # Сначала пробуем pymupdf (лучшее качество + извлечение картинок)
    try:
        return _from_pdf_with_images(raw)
    except Exception:
        pass
    # Fallback на pypdf (только текст)
    return _from_pdf_text_only(raw)


def _ocr_image_via_openai(image_bytes: bytes, mime: str = "image/png") -> str:
    """Отправляет изображение в OpenAI Vision API и возвращает извлечённый текст."""
    try:
        from dotenv import load_dotenv
    except Exception:
        load_dotenv = None

    env_path = Path(__file__).resolve().parent.parent / ".env"
    if load_dotenv:
        try:
            load_dotenv(env_path, override=False)
        except Exception:
            pass

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        # Попробуем прочитать из .env вручную
        try:
            if env_path.exists():
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("OPENAI_API_KEY="):
                        api_key = line.split("=", 1)[1].strip().strip("\"'")
                        break
        except Exception:
            pass
    if not api_key:
        return ""

    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"

    model = os.getenv("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
    # Для vision нужна модель с поддержкой изображений
    vision_model = "gpt-4o-mini"

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Извлеки весь текст с этого изображения из учебной лекции. "
                                "Если это диаграмма, схема или таблица — опиши её содержание текстом. "
                                "Отвечай только извлечённым текстом, без пояснений."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url, "detail": "low"},
                        },
                    ],
                }
            ],
            max_tokens=1024,
            temperature=0.0,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


def _is_likely_content_image(doc, xref: int, img_bytes: bytes, page_width: float, page_height: float) -> bool:
    """Определяет, является ли изображение содержательным (диаграмма, схема, таблица)
    или декоративным (логотип, фон, иконка). Возвращает True для содержательных."""
    size = len(img_bytes)

    # Слишком маленькие — иконки, маркеры, буллеты
    if size < 5_000:
        return False

    # Слишком большие однородные — скорее всего фоновые изображения
    if size > 5_000_000:
        return False

    try:
        import fitz
        pix = fitz.Pixmap(doc, xref)
        w, h = pix.width, pix.height
        pix = None  # освобождаем
    except Exception:
        return size > 10_000  # fallback: берём если не совсем мелкое

    # Слишком мелкие по пикселям — иконки
    if w < 80 or h < 80:
        return False

    # Узкие полоски — декоративные линии, разделители
    aspect = max(w, h) / max(min(w, h), 1)
    if aspect > 8:
        return False

    # Маленькие относительно страницы — скорее логотипы/декор
    page_area = page_width * page_height
    img_area = w * h
    if page_area > 0 and img_area / page_area < 0.03:
        return False

    # Крупные изображения, занимающие значимую часть страницы — содержательные
    return True


def _from_pdf_with_images(raw: bytes) -> str:
    """Извлечение текста и изображений из PDF через pymupdf + OpenAI Vision.
    Умная фильтрация + параллельный OCR для максимальной скорости."""
    import fitz  # pymupdf
    from concurrent.futures import ThreadPoolExecutor, as_completed

    doc = fitz.open(stream=raw, filetype="pdf")

    # ── Фаза 1: быстро извлекаем весь текст и собираем кандидатов на OCR ──
    page_texts: list[str] = []          # текст каждой страницы
    ocr_candidates: list[tuple] = []    # (page_num, img_bytes, mime)
    seen_xrefs: set[int] = set()
    max_ocr_per_doc = 6

    for page_num in range(len(doc)):
        page = doc[page_num]
        page_rect = page.rect
        page_w, page_h = page_rect.width, page_rect.height

        text = (page.get_text("text") or "").strip()
        page_texts.append(text)

        page_has_rich_text = len(text) > 500

        if len(ocr_candidates) >= max_ocr_per_doc:
            continue

        for img_info in page.get_images(full=True):
            if len(ocr_candidates) >= max_ocr_per_doc:
                break
            xref = img_info[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            try:
                base_image = doc.extract_image(xref)
                if not base_image:
                    continue
                img_bytes = base_image.get("image", b"")
                img_ext = base_image.get("ext", "png")

                if not _is_likely_content_image(doc, xref, img_bytes, page_w, page_h):
                    continue
                if page_has_rich_text and len(img_bytes) < 30_000:
                    continue

                mime = f"image/{img_ext}" if img_ext != "jpg" else "image/jpeg"
                ocr_candidates.append((page_num, img_bytes, mime))
            except Exception:
                continue

    doc.close()

    # ── Фаза 2: параллельный OCR (все картинки одновременно) ──
    ocr_results: dict[int, list[str]] = {}  # page_num -> [texts]

    if ocr_candidates:
        def _do_ocr(item):
            pg, img_b, m = item
            try:
                result = _ocr_image_via_openai(img_b, m)
                return pg, result
            except Exception:
                return pg, ""

        with ThreadPoolExecutor(max_workers=min(len(ocr_candidates), 4)) as pool:
            futures = [pool.submit(_do_ocr, c) for c in ocr_candidates]
            for fut in as_completed(futures):
                try:
                    pg, ocr_text = fut.result()
                    if ocr_text and len(ocr_text) > 10:
                        ocr_results.setdefault(pg, []).append(ocr_text)
                except Exception:
                    pass

    # ── Фаза 3: собираем результат ──
    page_results: list[str] = []
    for page_num, text in enumerate(page_texts):
        parts = []
        if text:
            parts.append(text)
        img_texts = ocr_results.get(page_num, [])
        if img_texts:
            parts.append("\n".join(img_texts))
        combined = "\n".join(parts).strip()
        if combined:
            page_results.append(combined)

    if not page_results:
        return ""
    return "\n\n".join(page_results)


def _from_pdf_text_only(raw: bytes) -> str:
    """Fallback: извлечение только текста через pypdf."""
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise LectureImportError("Для PDF нужен пакет pypdf.") from exc
    reader = PdfReader(BytesIO(raw))
    page_lines: list[list[str]] = []

    for page in reader.pages:
        text = page.extract_text() or ""
        text = text.replace("\r", "\n")
        raw_lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
        lines = [line for line in raw_lines if line]
        if lines:
            page_lines.append(lines)

    if not page_lines:
        return ""

    repeated: dict[str, int] = {}
    for lines in page_lines:
        edge = lines[:2] + lines[-2:]
        for line in edge:
            repeated[line] = repeated.get(line, 0) + 1

    repeated_threshold = max(2, len(page_lines) // 3)

    def _is_noise(line: str) -> bool:
        low = line.lower()
        if re.fullmatch(r"\d{1,4}", low):
            return True
        if re.fullmatch(r"(стр\.?|страница)\s*\d{1,4}(\s*(из|/)\s*\d{1,4})?", low):
            return True
        if re.fullmatch(r"\d{1,4}\s*(из|/)\s*\d{1,4}", low):
            return True
        if len(line) <= 2:
            return True
        if re.fullmatch(r"[-–—_•·. ]+", line):
            return True
        return False

    cleaned_pages: list[str] = []
    for lines in page_lines:
        cleaned_lines = []
        for line in lines:
            if repeated.get(line, 0) >= repeated_threshold:
                continue
            if _is_noise(line):
                continue
            cleaned_lines.append(line)
        page_text = "\n".join(cleaned_lines).strip()
        if page_text:
            cleaned_pages.append(page_text)

    return "\n\n".join(cleaned_pages)


def _extract_text_from_file_bytes(raw: bytes, filename: str) -> str:
    """Унифицированный импорт лекции из байтов файла."""
    ext = Path(filename).suffix.lower()

    if ext == ".txt":
        text = _from_txt(raw)
    elif ext == ".pdf":
        text = _from_pdf(raw)
    elif ext == ".docx":
        text = _from_docx(raw)
    elif ext == ".pptx":
        text = _from_pptx(raw)
    elif ext == ".doc":
        raise LectureImportError("Формат .doc не поддерживается. Сохраните файл как .docx или .pdf.")
    else:
        raise LectureImportError("Поддерживаются только файлы .txt, .pdf, .docx, .pptx.")

    text = (text or "").strip()
    if not text:
        raise LectureImportError("Не удалось извлечь текст из файла.")
    return text


def extract_lecture_text(upload: UploadFile) -> str:
    filename = (upload.filename or "").strip()
    if not filename:
        raise LectureImportError("Не удалось определить имя файла.")
    raw = upload.file.read()
    if not raw:
        raise LectureImportError("Файл пустой.")
    return _extract_text_from_file_bytes(raw, filename)
