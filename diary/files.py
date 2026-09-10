"""Файлы и ссылки в дневнике.

Gemini принимает картинки, PDF, аудио и видео напрямую — значит скриншот, чек,
голосовое или страница по ссылке могут стать частью разговора без отдельных
парсеров и распознавалок.

Два правила, из-за которых это не превращается в свалку:

· ФАЙЛ НЕ ХРАНИТСЯ ЦЕЛИКОМ. В память идёт то, что он ЗНАЧИТ («чек из аптеки на
  340 долларов, 3 сентября»), а не мегабайты пикселей. Оригинал кладётся рядом,
  на него можно посмотреть глазами.

· ИСТОЧНИК ПОМЕЧАЕТСЯ. Прочитанное из файла — не то же самое, что сказанное
  человеком: у документа своя достоверность, и путать их нельзя.
"""
from __future__ import annotations

import base64
import hashlib
import mimetypes
import re
import shutil
import time
from pathlib import Path

import httpx

# Что Gemini берёт как есть. Остальное пробуем прочитать как текст.
INLINE_OK = {
    "image/png", "image/jpeg", "image/webp", "image/heic", "image/heif", "image/gif",
    "application/pdf",
    "audio/wav", "audio/mp3", "audio/mpeg", "audio/aiff", "audio/aac", "audio/ogg", "audio/flac",
    "video/mp4", "video/mpeg", "video/mov", "video/quicktime", "video/webm",
}
MAX_INLINE = 18 * 1024 * 1024      # выше — Gemini требует загрузку через Files API
MAX_TEXT = 200_000                 # столько символов текста хватает на любой документ


def guess_mime(name: str, data: bytes) -> str:
    mt = mimetypes.guess_type(name)[0]
    if mt:
        return mt
    if data[:4] == b"%PDF":
        return "application/pdf"
    if data[:8].startswith(b"\x89PNG"):
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "application/octet-stream"


def store(data: bytes, name: str, folder: Path) -> Path:
    """Оригинал кладём рядом с дневником — чтобы можно было открыть глазами."""
    folder.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(data).hexdigest()[:10]
    safe = re.sub(r"[^\w.\-]+", "_", name)[-60:] or "file"
    p = folder / f"{time.strftime('%Y%m%d')}_{h}_{safe}"
    if not p.exists():
        p.write_bytes(data)
    return p


def as_part(data: bytes, mime: str, name: str) -> dict:
    """Кусок для запроса в модель: либо сам файл, либо его текст."""
    if mime in INLINE_OK and len(data) <= MAX_INLINE:
        return {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}}
    # не мультимедиа — пробуем как текст: код, csv, markdown, конфиги
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp1251")
        except Exception:
            raise ValueError(f"cannot read {name}: type {mime}, {len(data)//1024} KB")
    return {"text": f"[файл: {name}]\n\n{text[:MAX_TEXT]}"}


_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_HTML = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\n{3,}")


def fetch_link(url: str) -> dict:
    """Страница по ссылке → текст. Без внешних библиотек: тег вырезаем сами,
    иначе в сборку пришлось бы тащить парсер ради одной функции."""
    if not re.match(r"https?://", url):
        url = "https://" + url
    r = httpx.get(url, timeout=30, follow_redirects=True,
                  headers={"User-Agent": "Mozilla/5.0 (diary)"})
    r.raise_for_status()
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip()

    if ctype in INLINE_OK:                       # ссылка сразу на картинку или PDF
        return {"kind": "file", "mime": ctype, "data": r.content,
                "name": url.rsplit("/", 1)[-1] or "download"}

    html = r.text
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if m:
        title = _HTML.sub("", m.group(1)).strip()[:200]
    body = _TAG.sub(" ", html)
    body = _HTML.sub("\n", body)
    body = _SPACE.sub("\n\n", body)
    body = "\n".join(ln.strip() for ln in body.splitlines() if ln.strip())
    return {"kind": "text", "title": title, "url": url, "text": body[:MAX_TEXT]}
