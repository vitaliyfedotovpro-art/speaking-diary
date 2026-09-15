"""Realtime voice: the Gemini Live API as a go-between for the microphone and memory.

Why a proxy rather than a direct connection from the browser: the key stays on the
machine and never reaches the page at all. Tool calls pass through the proxy too —
the model decides for itself when to look into the diary.

The flow: the browser sends PCM at 16 kHz → we pass it to the Live API → PCM at
24 kHz comes back. The model speaks straight away, with no intermediate text, and it
can be interrupted.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os

import websockets
from websockets.asyncio.server import serve as ws_serve

from . import chat as _chat
from .memory import Memory, format_for_prompt, source_of
from .hsam import SRC_USER, SRC_SELF

MODEL = os.environ.get("DIARY_LIVE_MODEL", "gemini-2.5-flash-native-audio-latest")
VOICE = os.environ.get("DIARY_LIVE_VOICE", "Kore")
LIVE_URL = ("wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent")

# Voice and text share ONE character — it is taken from chat.SYSTEM. There used to be
# a stunted prompt of its own here ("personal speaking diary"), and the live mode
# behaved differently because of it: the edit about freedom of topics reached the text
# path and never reached the voice, so out loud she kept steering the person "back to
# their own life" instead of talking about anything at all.
SPOKEN = """

ЭТО РАЗГОВОР ВСЛУХ. Короткие фразы, как в живой речи: без списков, без разметки,
без заголовков и без длинных абзацев — тебя слушают, а не читают. Можно переспросить,
хмыкнуть, оборвать себя на полуслове.

ИНСТРУМЕНТЫ. recall_diary — когда нужна подробность из прошлого, которой нет в
выдержке ниже: спрашивай ДО ответа, а не после. remember_fact — когда человек сказал
о своей жизни то, что будет важно через месяц: события, решения, люди, планы.
manage_calendar — расписание, дела, встречи и экспресс-стикеры. Когда человек просит
«запиши в календарь...», «напомни...», «какие планы на сегодня» или «прикрепи стикер» —
вызывай manage_calendar или используй данные расписания из контекста. Свои мнения и советы не сохраняй никогда."""

SYSTEM = _chat.SYSTEM + SPOKEN

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
    {"name": "manage_calendar",
     "description": "Manage user's calendar schedule, events, or express task stickers. Use to add, view, or check affairs and upcoming plans.",
     "parameters": {"type": "object",
                    "properties": {"action": {"type": "string",
                                             "enum": ["add_event", "add_sticker", "list_events", "get_schedule"],
                                             "description": "action to perform"},
                                   "title": {"type": "string", "description": "Title of event or text of sticker"},
                                   "date": {"type": "string", "description": "Date in YYYY-MM-DD or 'today', 'tomorrow'"},
                                   "time": {"type": "string", "description": "Time in HH:MM like '15:00' or 'all-day'"},
                                   "notes": {"type": "string", "description": "Optional notes or details"}},
                    "required": ["action"]}},
]}]


def _digest(mem: Memory, limit: int = 60) -> str:
    """What the diary knows about the person — BEFORE the first word.

    The text mode puts what it found into the prompt on every turn; the live mode put
    in nothing: the model learned about the person only if it thought to call
    recall_diary itself. Hence "an interlocutor with no context". Search will not do
    here — there is no query yet, the conversation has not started — so we take the
    memory snapshot and the most important of it.
    """
    try:
        nodes, _ = mem.nodes_with_vectors()
    except Exception:
        return ""
    # 🔴 The engine enforces the quarantine of self-descriptions inside SEARCH, and
    # here we read the snapshot directly, bypassing search — so we must cut them out
    # ourselves. In the snapshot provenance is a string, and comparing it with a
    # number would silently cut out nothing.
    nodes = [n for n in nodes if source_of(n) != SRC_SELF]
    nodes.sort(key=lambda n: float(n.get("importance") or 0), reverse=True)
    return format_for_prompt(nodes[:limit])


def _recent(jr, n: int = 10) -> str:
    """The tail of the last conversation: a person continues a thought rather than
    starting from nothing."""
    try:
        days = jr.days() if jr is not None else []
    except Exception:
        return ""
    for day in days:                       # days are already sorted newest first
        for s in reversed(day.get("sessions") or []):
            turns = (s.get("turns") or [])[-n:]
            if not turns:
                continue
            body = "\n".join(f"{'Он' if t.get('who') == 'you' else 'Ты'}: {(t.get('text') or '')[:300]}"
                             for t in turns)
            return (f"\n\nПРОШЛЫЙ РАЗГОВОР ({day.get('date')}, «{s.get('title') or 'без названия'}»):"
                    f"\n{body}")
    return ""


def _context(mem: Memory, jr=None) -> str:
    from . import calendar_store
    cal_ctx = f"\n\nРАСПИСАНИЕ И ДЕЛА:\n{calendar_store.get_today_summary(None)}\n"
    return _digest(mem) + _recent(jr) + cal_ctx


def _setup_msg(voice: str | None = None, model: str | None = None,
               context: str = "") -> dict:
    return {"setup": {
        "model": f"models/{model or MODEL}",
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice or VOICE}}},
        },
        "systemInstruction": {"parts": [{"text": SYSTEM + context}]},
        "tools": TOOLS,
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
    }}


async def _handle_tool(mem: Memory, call: dict, client=None) -> dict:
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
    if name == "manage_calendar":
        from . import calendar_store
        action = args.get("action", "")
        title = args.get("title", "")
        date_str = args.get("date", "today")
        time_str = args.get("time", "all-day")
        notes = args.get("notes", "")

        if action == "add_event":
            evt = calendar_store.add_event(None, title, date_str, time_str, notes)
            if client:
                try:
                    await client.send(json.dumps({"type": "calendar_update", "action": "add_event", "event": evt}))
                except Exception:
                    pass
            return {"id": call.get("id"), "name": name, "response": {"result": f"Записано в календарь: {evt['title']} на {evt['date']} в {evt['time']}"}}
        elif action == "add_sticker":
            stk = calendar_store.add_sticker(None, title or notes or "Заметка")
            if client:
                try:
                    await client.send(json.dumps({"type": "calendar_update", "action": "add_sticker", "sticker": stk}))
                except Exception:
                    pass
            return {"id": call.get("id"), "name": name, "response": {"result": f"Стикер прикреплен на экран: {stk['text']}"}}
        else:
            summary = calendar_store.get_today_summary(None)
            return {"id": call.get("id"), "name": name, "response": {"result": summary}}

    return {"id": call.get("id"), "name": name, "response": {"result": "unknown tool"}}


async def bridge(client, mem: Memory, jr=None) -> None:
    """One voice session: browser ↔ Live API, with memory in between.

    `jr` is the journal: a live conversation must reach the notebook just as the
    walkie-talkie does, otherwise half of what was said disappears and the book lies
    about what happened."""
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
        # The browser sends the voice and model chosen in settings as its first message.
        # We do not wait long for it: if the page stays quiet, the session comes up on
        # the defaults.
        voice, model = None, None
        try:
            first = await asyncio.wait_for(client.recv(), timeout=1.5)
            if isinstance(first, str):
                cfg = json.loads(first)
                if cfg.get("type") == "config":
                    voice, model = cfg.get("voice"), cfg.get("model")
        except (asyncio.TimeoutError, Exception):
            pass
        ctx = await asyncio.to_thread(_context, mem, jr)
        await up.send(json.dumps(_setup_msg(voice, model, ctx)))
        print(f"[voice] сеанс: голос {voice or VOICE}, модель {model or MODEL}, "
              f"контекст {len(ctx)} знаков")

        async def to_gemini():
            async for raw in client:
                if isinstance(raw, bytes):          # raw sound from the microphone
                    await up.send(json.dumps({"realtimeInput": {"mediaChunks": [
                        {"mimeType": "audio/pcm;rate=16000",
                         "data": base64.b64encode(raw).decode()}]}}))
                else:
                    m = json.loads(raw)
                    if m.get("type") == "text":     # writing instead of speaking also works
                        await up.send(json.dumps({"clientContent": {
                            "turns": [{"role": "user", "parts": [{"text": m.get("text", "")}]}],
                            "turnComplete": True}}))

        said: dict[str, list[str]] = {"you": [], "diary": []}

        def flush_turn() -> None:
            """Turns arrive in pieces — we write them into the journal whole, at the
            end of a turn.

            Facts are NOT distilled here. That happens once per session in a shared
            pass (server._extract_sweep) over the same journal: doing it on every turn
            cost one request to the model each time, and the free tier allows 20 a day
            per model.
            """
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
                    resp = [await _handle_tool(mem, c, client) for c in calls]
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
                        await client.send(base64.b64decode(inline["data"]))   # the sound of the answer
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
            return_when=asyncio.FIRST_COMPLETED)     # not FIRST_EXCEPTION: it does not catch a socket close
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
