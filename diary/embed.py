"""Векторы через Gemini. Локальной модели нет намеренно: sentence-transformers
тянет torch (328 МБ) плюс веса (458 МБ) — почти гигабайт ради того, чтобы
превратить текст в числа. Раз ключ Gemini всё равно нужен для разговора,
эмбеддинги берём там же.

⚠️ Это значит, что текст записей уходит в Google. Пользователь предупреждается
об этом на первом экране — не мелким шрифтом.
"""
from __future__ import annotations

import os
import time

import numpy as np
import httpx

MODEL = os.environ.get("DIARY_EMBED_MODEL", "gemini-embedding-2")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:embedContent"
BATCH_URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:batchEmbedContents"
DIM = 768          # усечение до 768: качество почти то же, памяти втрое меньше


class EmbedError(RuntimeError):
    pass


def _key() -> str:
    k = os.environ.get("GEMINI_API_KEY", "").strip()
    if not k:
        raise EmbedError("No Gemini API key. Open settings and paste one.")
    return k


def _post(url: str, body: dict, tries: int = 3) -> dict:
    last = None
    for i in range(tries):
        try:
            r = httpx.post(url.format(m=MODEL), params={"key": _key()},
                           json=body, timeout=60)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code in (429, 500, 503):      # временное — ждём и повторяем
                time.sleep(2 * (i + 1))
                continue
            break
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(2 * (i + 1))
    raise EmbedError(last or "unknown")


def _norm(v: list[float]) -> np.ndarray:
    a = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(a)
    return a / n if n else a          # нормируем сразу: косинус = скалярное произведение


def embed(text: str, task: str = "RETRIEVAL_DOCUMENT") -> np.ndarray:
    """task разный для записи и для запроса — Gemini использует его как подсказку,
    и перепутать их значит просесть в качестве поиска на ровном месте."""
    body = {"content": {"parts": [{"text": (text or "")[:8000]}]},
            "taskType": task, "outputDimensionality": DIM}
    d = _post(URL, body)
    return _norm(d["embedding"]["values"])


def embed_query(text: str) -> np.ndarray:
    return embed(text, task="RETRIEVAL_QUERY")


def embed_many(texts: list[str], task: str = "RETRIEVAL_DOCUMENT",
               chunk: int = 100) -> list[np.ndarray]:
    """Пакетно — для первичной загрузки и переиндексации."""
    out: list[np.ndarray] = []
    for i in range(0, len(texts), chunk):
        part = texts[i:i + chunk]
        body = {"requests": [
            {"model": f"models/{MODEL}",
             "content": {"parts": [{"text": (t or "")[:8000]}]},
             "taskType": task, "outputDimensionality": DIM} for t in part]}
        d = _post(BATCH_URL, body)
        out += [_norm(e["values"]) for e in d.get("embeddings", [])]
    return out
