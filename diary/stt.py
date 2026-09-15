"""Speech to text through Whisper on Groq.

Why a separate service when Gemini already accepts audio: Gemini returns only its
answer and keeps the transcription to itself. Because of that the diary held a
placeholder where the person's words should be, memory was searched blind, and
"she misunderstood me" could not be told apart from "the microphone recorded
silence".

Whisper returns text. Everything downstream then follows the ordinary text path:
what was recorded is visible, and memory is searched by words rather than by guess.
"""
from __future__ import annotations

import os

import httpx

URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = os.environ.get("DIARY_STT_MODEL", "whisper-large-v3-turbo")


class SttUnavailable(RuntimeError):
    """No key, or the service did not answer — not a reason to drop the conversation."""


def transcribe(wav: bytes, language: str | None = None) -> str:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise SttUnavailable("No Groq API key — speech will not be transcribed.")
    if not wav:
        return ""
    data = {"model": MODEL, "response_format": "json",
            "temperature": "0"}
    if language:
        data["language"] = language        # without it Whisper sometimes "translates" an accent
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
