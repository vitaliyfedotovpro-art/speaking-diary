"""Распознавание речи через Whisper на Groq.

Зачем отдельно, если Gemini и так принимает аудио: Gemini возвращает только
ответ, а расшифровку держит при себе. Из-за этого в дневнике вместо слов
человека оставалась заглушка, поиск по памяти шёл вслепую, а разобрать
«она меня не поняла» от «микрофон записал тишину» было нечем.

Whisper отдаёт текст. Дальше всё идёт обычным текстовым путём: видно, что
записано, память ищется по словам, а не по догадке.
"""
from __future__ import annotations

import os

import httpx

URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = os.environ.get("DIARY_STT_MODEL", "whisper-large-v3-turbo")


class SttUnavailable(RuntimeError):
    """Ключа нет или сервис не ответил — не повод ронять разговор."""


def transcribe(wav: bytes, language: str | None = None) -> str:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise SttUnavailable("No Groq API key — speech will not be transcribed.")
    if not wav:
        return ""
    data = {"model": MODEL, "response_format": "json",
            "temperature": "0"}
    if language:
        data["language"] = language        # без него Whisper иногда «переводит» акцент
    try:
        r = httpx.post(URL, headers={"Authorization": f"Bearer {key}"},
                       files={"file": ("speech.wav", wav, "audio/wav")},
                       data=data, timeout=90)
    except Exception as e:
        raise SttUnavailable(f"{type(e).__name__}: {e}")
    if r.status_code != 200:
        raise SttUnavailable(f"HTTP {r.status_code}: {r.text[:200]}")
    return (r.json().get("text") or "").strip()


def available() -> bool:
    return bool(os.environ.get("GROQ_API_KEY", "").strip())
