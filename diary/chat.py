"""Conversation on top of the diary: gemini-3.8-flash plus search over the entries.

Two decisions taken from other people's experience rather than invented here:

· NOT EVERYTHING IS WRITTEN DOWN. Recording every turn looks tempting and kills the
  diary: it fills up with the model's own chatter, and search starts finding its
  retellings instead of the person's words. So the decision "is this worth
  remembering" is made by a SEPARATE quiet pass after the answer, and it picks out
  facts, not dialogue.

· A PASSPORT FOR THE SOURCE. A person's words and a model's guesses are marked
  differently, otherwise in a month the model quotes its own invention back as
  somebody's biography.
"""
from __future__ import annotations

import json
import os

import httpx

from . import memory as _memory
from .memory import Memory
from .hsam import SRC_USER, SRC_SELF

CHAT_MODEL = os.environ.get("DIARY_CHAT_MODEL", "gemini-3.8-flash")
# 🔴 Free-tier quota is counted PER MODEL and PER PROJECT: measured on a live key on
# 13.09.2026 it gave 5 requests a minute and 20 a day (quotaId ...PerDay...-FreeTier).
# So the background passes are moved onto THEIR OWN models: the conversation, the
# distillation and the titles each get a separate bucket instead of one shared by
# three. This is not a way around the limit but a legitimate use of it — the buckets
# are separate by construction.
EXTRACT_MODEL = os.environ.get("DIARY_EXTRACT_MODEL", "gemini-2.5-flash")
TITLE_MODEL = os.environ.get("DIARY_TITLE_MODEL", "gemini-3.5-flash")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"

SYSTEM = """Ты — живая собеседница, которая помнит. Не ассистент, не анкета: тот, с кем
человек думает вслух. Дневник — потому что ты держишь в памяти прожитое и сказанное, а не
потому что вы обсуждаете только быт.

О ЧЁМ ГОВОРИТЬ. О ЧЁМ УГОДНО. Его день и его работа — да. Но так же и идеи, книги,
политика, квантовое бессмертие, страх смерти, музыка, спор ни о чём — всё, что его
занимает. НИКОГДА не возвращай его «к своей жизни» и не говори «дневник больше про
личные события» — это ложь и это убивает разговор. Именно В разговоре о большом
человек и проговаривает то, что потом станет фактом о нём. Мысль о квантовом
бессмертии — это тоже он. Заткнуть её ради «сходил ли ты к зубному» — предательство.

ЯЗЫК. Отвечай на языке ПОСЛЕДНЕЙ его реплики, а не на том, на котором шёл разговор
раньше. Сказал по-украински — отвечай по-украински, сказал по-английски — отвечай
по-английски, сказал по-русски — отвечай по-русски. Прошлые реплики в истории — не
указание, на каком языке говорить сейчас.

КАК ГОВОРИШЬ. Живо, как человек, а не как справочная. У тебя есть характер: сухой юмор,
своё мнение, лёгкая ирония. Ты можешь поддеть, не согласиться, посмеяться, сказать
«да ну брось» или «слушай, это ерунда». Реагируй на СМЫСЛ и настроение, а не отвечай
дежурными «Понятно. Какие планы?» — от такого веет анкетой, и человек закрывается.
Коротко, живым языком, без канцелярита и без списков там, где хватит фразы.
Не сыпь похвалой и не закрывай реплику комплиментом — это шум. Тепло у тебя не в
«какой ты молодец», а в том, что ты вникаешь и не отпускаешь тему на полуслове.
Не бойся пауз и коротких реплик: живой человек не выдаёт абзац на каждое «привет».

ЧТО ПОМНИШЬ. Ниже даётся выдержка из дневника — то, что записано раньше. Пометки
показывают источник: ⟨сказал ты⟩ — его собственные слова, ⟨мои слова⟩ — твоя прежняя
догадка. Ссылаться на догадку как на факт его жизни нельзя.

ЧЕГО НЕ ЗНАЕШЬ. Если в дневнике этого нет — так и скажи. Но незнание это начало
разговора, а не конец: не отделывайся «не знаю», а спрашивай. «Не помню такого. Когда
это было, кто ещё там был, чем кончилось?» Три вопроса дают больше, чем догадка.
Названия, имена, годы, цифры не выдумывай никогда: назвать наугад хуже, чем не назвать —
он запомнит это как твоё знание.

И СПОРЬ. Если он неправ — скажи первой фразой, с причиной. Если посылка самого вопроса
шаткая — покажи это вопросом, а не лекцией. Согласие имеет цену, только если ты
способна не согласиться."""

EXTRACT = """From this conversation, pick out FACTS ABOUT THE PERSON'S LIFE worth remembering
a month from now: events, decisions, people, plans, preferences, circumstances.

ONE TOPIC — ONE FACT. Never split a single subject into atoms: "is preparing for a course",
"the course has a test", "the course is called X" is ONE fact, not three. Gather what
belongs together into a single self-contained sentence.

Do NOT take: your own reasoning or advice, politeness, questions left unanswered, general
observations about the world, anything already obvious from other facts. Do NOT take
passing states — how the evening is going, the mood of the minute: none of it will matter
in a month. Exhaustion that is the point of the story is a fact; "is having a nice evening"
is not.

THE CONVERSATION MAY HAVE BEEN SPOKEN, and speech recognition mangles names. If a name,
title or number came through garbled, or you are unsure of it, leave it out of the fact
instead of guessing — a fact without the name is worth more than a fact with a wrong one.

Write each fact IN THE LANGUAGE THE PERSON SPEAKS — their diary is theirs, not yours.

Return a JSON array of strings, each a self-contained fact in the third person, clear
without the surrounding conversation. Nothing worth keeping — return []. JSON only."""


TITLE_PROMPT = """Give this conversation a title: one short line, up to seven words,
in the language the person speaks. Say what it was ABOUT, plainly — no quotes, no
punctuation at the end, no words like conversation or discussion. Title only."""


def title_for(dialog: str) -> str:
    """A title for the conversation, for the notebook's table of contents."""
    try:
        # 200 was not enough: the response's service fields eat the budget, and the
        # title came back cut down to a single word ("First", "Putting")
        return _call([{"text": dialog}], TITLE_PROMPT, temperature=0.3,
                     max_tokens=500, think=False,
                     model=TITLE_MODEL).strip().strip('"').strip()[:90]
    except Exception:
        return ""


def ask_link(a: str, b: str) -> str:
    """A question about a possible connection between two entries. An empty string
    means it is not worth asking."""
    from .links import QUESTION_PROMPT
    try:
        out = _call([{"text": QUESTION_PROMPT.format(a=a[:400], b=b[:400])}],
                    "You help someone notice patterns in their own life.",
                    temperature=0.6, max_tokens=500, think=False,
                    model=TITLE_MODEL).strip()
    except Exception:
        return ""
    return "" if (not out or out.upper().startswith("SKIP")) else out.strip('"')


def _key() -> str:
    k = os.environ.get("GEMINI_API_KEY", "").strip()
    if not k:
        raise RuntimeError("No Gemini API key. Open settings and paste one.")
    return k


TOOLS = [{"functionDeclarations": [
    {"name": "recall_diary",
     "description": "Search the diary for what was written earlier about the person's life.",
     "parameters": {"type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"]}},
]}]


def _call(parts: list[dict], system: str, temperature: float = 0.7,
          max_tokens: int = 1200, mem=None, think: bool = True,
          model: str | None = None) -> str:
    """One answer from the model. If memory is given, the model can query it itself
    through a tool. That is the only way to search by SPEECH: we do not have the text
    of the query, the transcription happens inside the model."""
    contents = [{"role": "user", "parts": parts}]
    for _ in range(3):                       # enough for a couple of trips to memory
        body = {"contents": contents,
                "systemInstruction": {"parts": [{"text": system}]},
                "generationConfig": {"temperature": temperature,
                                     "maxOutputTokens": max_tokens}}
        if not think:
            # 🔴 The model thinks BEFORE answering, and on a short task the thinking
            # eats the whole limit: a 40-token title came back empty while
            # thoughtsTokenCount was 36. For background calls there is nothing to think about.
            body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
        if mem is not None:
            body["tools"] = TOOLS
        r = httpx.post(URL.format(m=model or CHAT_MODEL), params={"key": _key()},
                       json=body, timeout=120)
        if r.status_code != 200:
            raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:200]}")
        cand = (r.json().get("candidates") or [{}])[0]
        cparts = cand.get("content", {}).get("parts", []) or []
        calls = [p["functionCall"] for p in cparts if p.get("functionCall")]
        if calls and mem is not None:
            contents.append({"role": "model", "parts": cparts})
            resp = []
            for c in calls:
                q = (c.get("args") or {}).get("query", "")
                rows = mem.recall(q, 6)
                from . import memory as _m
                resp.append({"functionResponse": {"name": c.get("name"), "response": {
                    "result": _m.format_for_prompt(rows) or "Nothing in the diary yet."}}})
            contents.append({"role": "user", "parts": resp})
            continue
        return "".join(p.get("text", "") for p in cparts).strip()
    return ""


def reply(mem: Memory, text: str, history: list[dict] | None = None,
          audio: bytes | None = None, mime: str = "audio/wav",
          extra_parts: list[dict] | None = None) -> dict:
    """One turn. `audio` is raw voice: gemini-3.8 takes it directly, no separate
    transcription needed."""
    # For text we search ourselves — it is faster. For SPEECH there is nothing to
    # search with: the transcription lives inside the model, so there it queries
    # memory through a tool.
    found = mem.recall(text, n=8) if text else []
    system = SYSTEM + _memory.format_for_prompt(found)
    try:
        from . import calendar_store
        system += f"\n\nРАСПИСАНИЕ И ДЕЛА НА СЕГОДНЯ:\n{calendar_store.get_today_summary()}\n"
    except Exception:
        pass

    parts: list[dict] = []
    if history:
        tail = "\n".join(f"{'Он' if h['role']=='user' else 'Ты'}: {h['content'][:500]}"
                         for h in history[-8:])
        parts.append({"text": f"[раньше в этом разговоре]\n{tail}\n\n[сейчас]"})
    if audio:
        import base64
        parts.append({"inlineData": {"mimeType": mime,
                                     "data": base64.b64encode(audio).decode()}})
    if extra_parts:
        parts += extra_parts            # an image, a PDF, audio, or the text of a page
    if text:
        parts.append({"text": text})

    # with an attachment the model must also be able to query memory: a conversation
    # about a document almost always touches something already written down
    answer = _call(parts, system, mem=(mem if (audio or extra_parts) else None))
    return {"answer": answer, "used": found}


def _save_facts(mem: Memory, raw: str, cap: int) -> list[str]:
    """Turning the model's answer into facts. The cap keeps the diary from becoming a dump."""
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        facts = json.loads(raw)
    except Exception:
        return []
    if not isinstance(facts, list):
        return []
    saved = []
    for f in facts[:cap]:
        f = str(f).strip()
        if len(f) < 8:
            continue
        try:
            mem.remember(f, source=SRC_USER)  # a fact ABOUT THE PERSON — from their words
            saved.append(f)
        except Exception:
            continue
    return saved


def remember(mem: Memory, user_text: str, answer: str) -> list[str]:
    """The quiet pass over a SINGLE exchange. Kept for explicit calls; the ordinary
    path of a conversation goes through remember_session — one request per session
    rather than per turn."""
    dialog = f"ЧЕЛОВЕК: {user_text}\n\nТЫ: {answer}"
    try:
        raw = _call([{"text": dialog}], EXTRACT, temperature=0.0, max_tokens=600,
                    think=False, model=EXTRACT_MODEL)
    except Exception:
        return []
    return _save_facts(mem, raw, cap=6)


def remember_session(mem: Memory, turns: list[dict]) -> list[str]:
    """Distilling a WHOLE conversation in one call.

    🔴 The quiet pass used to run after every turn: a typical day is 56 turns, which
    means 56 requests — while the free tier allows 20 a day per model (measured
    13.09.2026). Memory stopped filling up by lunchtime, and silently: the person kept
    talking, the diary kept answering, and the facts were no longer saved. Once per
    session is 3-4 requests a day, inside the limit with room to spare.

    A whole conversation is also distilled BETTER than an exchange: you can see how a
    topic started ten turns ago ended, and no half-facts are minted from the middle.
    """
    lines = []
    for t in turns:
        txt = (t.get("text") or "").strip()
        if txt:
            lines.append(f"{'ЧЕЛОВЕК' if t.get('who') == 'you' else 'ТЫ'}: {txt[:600]}")
    if not lines:
        return []
    dialog = "\n".join(lines)[:24000]
    try:
        raw = _call([{"text": dialog}], EXTRACT, temperature=0.0, max_tokens=1200,
                    think=False, model=EXTRACT_MODEL)
    except Exception:
        return []
    return _save_facts(mem, raw, cap=12)      # per session the cap is higher than per turn
