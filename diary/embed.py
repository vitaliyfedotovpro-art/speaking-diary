"""Vectors through Gemini. There is deliberately no local model: sentence-transformers
drags in torch (328 MB) plus weights (458 MB) — almost a gigabyte just to turn text
into numbers. Since a Gemini key is needed for the conversation anyway, the
embeddings are taken from the same place.

⚠️ This means the text of the entries goes to Google. The user is warned about it on
the first screen — not in fine print.
"""
from __future__ import annotations

import os
import time

import numpy as np
import httpx

MODEL = os.environ.get("DIARY_EMBED_MODEL", "gemini-embedding-2")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:embedContent"
BATCH_URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:batchEmbedContents"
DIM = 768          # truncated to 768: nearly the same quality, three times less memory


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
            if r.status_code in (429, 500, 503):      # transient — wait and retry
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
    return a / n if n else a          # normalise here: cosine becomes a dot product


def embed(text: str, task: str = "RETRIEVAL_DOCUMENT") -> np.ndarray:
    """The task differs for storing and for querying — Gemini uses it as a hint, and
    mixing the two costs search quality for nothing."""
    body = {"content": {"parts": [{"text": (text or "")[:8000]}]},
            "taskType": task, "outputDimensionality": DIM}
    d = _post(URL, body)
    return _norm(d["embedding"]["values"])


def embed_query(text: str) -> np.ndarray:
    return embed(text, task="RETRIEVAL_QUERY")


def embed_many(texts: list[str], task: str = "RETRIEVAL_DOCUMENT",
               chunk: int = 100) -> list[np.ndarray]:
    """In batches — for the initial load and for reindexing."""
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
