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

from . import chat as _chat
from .memory import Memory, format_for_prompt, source_of
from .hsam import SRC_USER, SRC_SELF

MODEL = os.environ.get("DIARY_LIVE_MODEL", "gemini-2.5-flash-native-audio-latest")
VOICE = os.environ.get("DIARY_LIVE_VOICE", "Kore")
LIVE_URL = ("wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent")

# Характер у голоса и у текста ОДИН — берётся из chat.SYSTEM. Раньше здесь жил
# свой куцый промпт («personal speaking diary»), и живой режим вёл себя иначе:
# правка про свободу тем доехала до текста и не доехала до голоса, отчего вслух
# собеседница возвращала человека «к его жизни» вместо разговора о чём угодно.
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
    """Что дневник знает о человеке — ДО первого слова.

    Текстовый режим кладёт найденное в промпт на каждом ходу, живой не клал ничего:
    модель узнавала о человеке, только если сама догадается позвать recall_diary.
    Отсюда и «собеседник без контекста». Поиском тут не возьмёшь — запроса ещё нет,
    разговор не начался, — поэтому берём снимок памяти и самое важное из него.
    """
    try:
        nodes, _ = mem.nodes_with_vectors()
    except Exception:
        return ""
    # 🔴 Карантин самоописаний движок держит на ПОИСКЕ, а здесь мы читаем снимок
    # напрямую, в обход поиска — значит отсекаем сами. В снимке провенанс лежит
    # строкой, и сравнение с числом молча не отсекало бы ничего.
    nodes = [n for n in nodes if source_of(n) != SRC_SELF]
    nodes.sort(key=lambda n: float(n.get("importance") or 0), reverse=True)
    return format_for_prompt(nodes[:limit])


def _recent(jr, n: int = 10) -> str:
    """Хвост последнего разговора: человек продолжает мысль, а не начинает с нуля."""
    try:
        days = jr.days() if jr is not None else []
    except Exception:
        return ""
    for day in days:                       # дни уже отсортированы от новых к старым
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
        ctx = await asyncio.to_thread(_context, mem, jr)
        await up.send(json.dumps(_setup_msg(voice, model, ctx)))
        print(f"[voice] сеанс: голос {voice or VOICE}, модель {model or MODEL}, "
              f"контекст {len(ctx)} знаков")

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
            """Реплики копятся по кусочкам — в журнал пишем целыми, в конце хода.

            Разбор на факты здесь НЕ делаем. Он идёт раз в сессию общим проходом
            (server._extract_sweep) по тому же журналу: на каждом ходу он съедал
            по запросу к модели, а бесплатный тариф даёт 20 в сутки на модель.
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
