"""Speech through Gemini TTS — the voice for walkie-talkie mode.

Separate from the Live API: that model speaks by itself and costs money, while this
one simply reads out ready text. It is available on the free tier, though measured on
13.09.2026 that tier allows only 10 calls a day. Returns WAV so the browser has
nothing left to do.
"""
from __future__ import annotations

import base64
import os
import struct

import httpx

MODEL = os.environ.get("DIARY_TTS_MODEL", "gemini-3.1-flash-tts-preview")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
RATE = 24000          # Gemini TTS returns PCM at 24 kHz, mono


def _wav(pcm: bytes, rate: int = RATE) -> bytes:
    """PCM → WAV: the browser needs a header, otherwise it cannot tell what to play."""
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) +
            b"data" + struct.pack("<I", len(pcm)) + pcm)


def speak(text: str, voice: str = "Kore", model: str | None = None) -> bytes | None:
    """→ WAV, or None if speech is unavailable (tier, quota, network)."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key or not (text or "").strip():
        return None
    body = {"contents": [{"parts": [{"text": text[:4000]}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice}}}}}
    try:
        r = httpx.post(URL.format(m=model or MODEL), params={"key": key},
                       json=body, timeout=120)
        if r.status_code != 200:
            return None
        parts = r.json()["candidates"][0]["content"]["parts"]
        for p in parts:
            data = (p.get("inlineData") or {}).get("data")
            if data:
                return _wav(base64.b64decode(data))
    except Exception:
        return None
    return None
