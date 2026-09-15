"""Files and links in the diary.

Gemini accepts images, PDFs, audio and video directly — which means a screenshot, a
receipt, a voice message or a page behind a link can become part of the conversation
without separate parsers and recognisers.

Two rules keep this from turning into a dump:

· THE FILE IS NOT STORED WHOLE. What goes into memory is what it MEANS ("a pharmacy
  receipt for 340 dollars, 3 September"), not megabytes of pixels. The original is
  kept alongside, and can be looked at with your own eyes.

· THE SOURCE IS MARKED. Something read from a file is not the same as something said
  by a person: a document has its own reliability, and the two must not be confused.
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

# What Gemini takes as is. Everything else we try to read as text.
INLINE_OK = {
    "image/png", "image/jpeg", "image/webp", "image/heic", "image/heif", "image/gif",
    "application/pdf",
    "audio/wav", "audio/mp3", "audio/mpeg", "audio/aiff", "audio/aac", "audio/ogg", "audio/flac",
    "video/mp4", "video/mpeg", "video/mov", "video/quicktime", "video/webm",
}
MAX_INLINE = 18 * 1024 * 1024      # above this Gemini requires an upload via the Files API
MAX_TEXT = 200_000                 # this many characters of text is enough for any document


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
    """Keep the original next to the diary — so it can be opened and looked at."""
    folder.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(data).hexdigest()[:10]
    safe = re.sub(r"[^\w.\-]+", "_", name)[-60:] or "file"
    p = folder / f"{time.strftime('%Y%m%d')}_{h}_{safe}"
    if not p.exists():
        p.write_bytes(data)
    return p


def as_part(data: bytes, mime: str, name: str) -> dict:
    """A piece for the request to the model: either the file itself, or its text."""
    if mime in INLINE_OK and len(data) <= MAX_INLINE:
        return {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}}
    # not multimedia — try it as text: code, csv, markdown, config files
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
    """A page behind a link → text. Without external libraries: we strip the tags
    ourselves, otherwise the build would have to carry a parser for one function."""
    if not re.match(r"https?://", url):
        url = "https://" + url
    r = httpx.get(url, timeout=30, follow_redirects=True,
                  headers={"User-Agent": "Mozilla/5.0 (diary)"})
    r.raise_for_status()
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip()

    if ctype in INLINE_OK:                       # the link points straight at an image or PDF
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
