"""Realtime-голос: Gemini Live API как посредник между микрофоном и памятью.

Почему прокси, а не прямое соединение из браузера: ключ остаётся на машине,
в страницу он не попадает вообще. Заодно через прокси проходят вызовы
инструментов — модель сама решает, когда заглянуть в дневник.

Поток: браузер шлёт PCM 16 кГц → мы в Live API → оттуда PCM 24 кГц обратно.
Модель говорит сразу голосом, без промежуточного текста, и её можно перебить.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os

import websockets
from websockets.asyncio.server import serve as ws_serve

from .memory import Memory, format_for_prompt
from .hsam import SRC_USER

MODEL = os.environ.get("DIARY_LIVE_MODEL", "gemini-2.5-flash-native-audio-latest")
VOICE = os.environ.get("DIARY_LIVE_VOICE", "Kore")
LIVE_URL = ("wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent")

SYSTEM = """You are a personal speaking diary — not an assistant. You remember one person's
life and talk with them about it, out loud.

Speak the way people speak: short sentences, no lists, no bullet points, no markdown.
Never open with praise and never close with a compliment.

You have two tools. Use recall_diary whenever the person refers to anything from their life —
before answering, not after. Use remember_fact when they tell you something worth keeping
a month from now: events, decisions, people, plans. Do not save your own opinions.

If the diary has nothing on it, say so plainly and ask. Not knowing is where the
conversation starts, not where it stops. Never invent names, dates or numbers."""

TOOLS = [{"functionDeclarations": [
    {"name": "recall_diary",
     "description": "Search the person's diary for what was written earlier.",
     "parameters": {"type": "object",
                    "properties": {"query": {"type": "string",
                                             "description": "what to look for"}},
                    "required": ["query"]}},
    {"name": "remember_fact",
     "description": "Save one fact about the person's life to the diary.",
     "parameters": {"type": "object",
                    "properties": {"fact": {"type": "string",
                                            "description": "self-contained fact, third person"}},
                    "required": ["fact"]}},
]}]


def _setup_msg(voice: str | None = None, model: str | None = None) -> dict:
    return {"setup": {
        "model": f"models/{model or MODEL}",
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice or VOICE}}},
        },
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "tools": TOOLS,
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
    }}


async def _handle_tool(mem: Memory, call: dict) -> dict:
    name, args = call.get("name", ""), call.get("args") or {}
    if name == "recall_diary":
        rows = await asyncio.to_thread(mem.recall, args.get("query", ""), 6)
        text = format_for_prompt(rows) or "Nothing in the diary about this yet."
        return {"id": call.get("id"), "name": name, "response": {"result": text}}
    if name == "remember_fact":
        fact = (args.get("fact") or "").strip()
        if fact:
            await asyncio.to_thread(mem.remember, fact, SRC_USER)
        return {"id": call.get("id"), "name": name, "response": {"result": "saved"}}
    return {"id": call.get("id"), "name": name, "response": {"result": "unknown tool"}}


async def bridge(client, mem: Memory, jr=None) -> None:
    """Один голосовой сеанс: браузер ↔ Live API, с памятью посередине.

    jr — журнал: живой разговор должен попадать в блокнот так же, как рация,
    иначе половина сказанного исчезает и книга врёт о том, что было."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        await client.send(json.dumps({"type": "error", "error": "no GEMINI_API_KEY"}))
        return
    try:
        up = await websockets.connect(f"{LIVE_URL}?key={key}", ping_interval=20,
                                      open_timeout=25, max_size=16 * 1024 * 1024)
    except Exception as e:
        await client.send(json.dumps({"type": "error", "error": f"live api: {e}"}))
        return

    async with up:
        # Первым сообщением браузер присылает выбранные в настройках голос и модель.
        # Ждём его недолго: если страница молчит — поднимаем сессию на значениях по умолчанию.
        voice, model = None, None
        try:
            first = await asyncio.wait_for(client.recv(), timeout=1.5)
            if isinstance(first, str):
                cfg = json.loads(first)
                if cfg.get("type") == "config":
                    voice, model = cfg.get("voice"), cfg.get("model")
        except (asyncio.TimeoutError, Exception):
            pass
        await up.send(json.dumps(_setup_msg(voice, model)))
        print(f"[voice] сеанс: голос {voice or VOICE}, модель {model or MODEL}")

        async def to_gemini():
            async for raw in client:
                if isinstance(raw, bytes):          # сырой звук с микрофона
                    await up.send(json.dumps({"realtimeInput": {"mediaChunks": [
                        {"mimeType": "audio/pcm;rate=16000",
                         "data": base64.b64encode(raw).decode()}]}}))
                else:
                    m = json.loads(raw)
                    if m.get("type") == "text":     # можно и написать вместо речи
                        await up.send(json.dumps({"clientContent": {
                            "turns": [{"role": "user", "parts": [{"text": m.get("text", "")}]}],
                            "turnComplete": True}}))

        said: dict[str, list[str]] = {"you": [], "diary": []}

        def flush_turn() -> None:
            """Реплики копятся по кусочкам — в журнал пишем целыми, в конце хода."""
            if jr is None:
                return
            for who in ("you", "diary"):
                text = "".join(said[who]).strip()
                if text:
                    jr.add_turn(who, text, voice=True)
                said[who] = []

        async def from_gemini():
            async for raw in up:
                d = json.loads(raw if isinstance(raw, str) else raw.decode())
                if "setupComplete" in d:
                    await client.send(json.dumps({"type": "ready"}))
                    continue
                if "toolCall" in d:
                    calls = d["toolCall"].get("functionCalls") or []
                    resp = [await _handle_tool(mem, c) for c in calls]
                    await up.send(json.dumps({"toolResponse": {"functionResponses": resp}}))
                    await client.send(json.dumps({"type": "tool",
                                                  "names": [c.get("name") for c in calls]}))
                    continue
                sc = d.get("serverContent") or {}
                if sc.get("interrupted"):
                    await client.send(json.dumps({"type": "interrupted"}))
                for part in (sc.get("modelTurn") or {}).get("parts", []) or []:
                    inline = part.get("inlineData") or {}
                    if inline.get("data"):
                        await client.send(base64.b64decode(inline["data"]))   # звук ответа
                    if part.get("text"):
                        await client.send(json.dumps({"type": "text", "text": part["text"]}))
                for key_, tag in (("inputTranscription", "you"), ("outputTranscription", "diary")):
                    t = (sc.get(key_) or {}).get("text")
                    if t:
                        said[tag].append(t)
                        await client.send(json.dumps({"type": "transcript", "who": tag, "text": t}))
                if sc.get("turnComplete"):
                    await asyncio.to_thread(flush_turn)
                    await client.send(json.dumps({"type": "turn_end"}))

        done, pending = await asyncio.wait(
            [asyncio.create_task(to_gemini()), asyncio.create_task(from_gemini())],
            return_when=asyncio.FIRST_COMPLETED)     # не FIRST_EXCEPTION: закрытие сокета им не ловится
        for t in pending:
            t.cancel()


async def run(mem: Memory, port: int = 8792, jr=None) -> None:
    async def handler(client):
        try:
            await bridge(client, mem, jr)
        except Exception as e:
            print(f"[voice] сеанс оборвался: {type(e).__name__}: {e}")
    async with ws_serve(handler, "127.0.0.1", port, max_size=16 * 1024 * 1024):
        print(f"  голос: ws://127.0.0.1:{port}  ({MODEL}, голос {VOICE})")
        await asyncio.Future()
