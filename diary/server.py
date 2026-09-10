"""Локальный сервер дневника: отдаёт страницу и обслуживает её запросы.

Ничего наружу не слушает — только 127.0.0.1. Ключ Gemini берётся из окружения
или из файла настроек рядом с базой; в браузер он не уходит.
"""
from __future__ import annotations

import json
import os
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import chat as _chat
from . import tts as _tts
from . import stt as _stt
from . import backup as _backup
from . import files as _files
from .memory import Memory
from .journal import Journal
from .links import Links, QUESTION_PROMPT
from .hsam import SRC_USER, SRC_SELF, SRC_DOC, CANON_FOUNDATIONAL

HOME = Path(os.environ.get("DIARY_HOME", Path.home() / ".diary"))
WEB = Path(__file__).parent / "web"
HISTORY: list[dict] = []


_RT_CACHE: dict = {"ok": None}


def _record(handler, user_text: str, answer: str, voice: bool = False) -> None:
    """Разговор — в журнал, факты — в память, заголовок — когда есть о чём.
    Всё в фоне: человек не должен ждать бухгалтерию ради ответа."""
    jr, mem = handler.jr, handler.mem
    handler.lk.tick()                       # ход прошёл — опросник ближе к вопросу
    jr.add_turn("you", user_text, voice=voice)
    jr.add_turn("diary", answer, voice=voice)
    try:
        facts = _chat.remember(mem, user_text, answer)
        if facts:
            jr.add_takeaways(facts)
    except Exception as e:
        print(f"[journal] выводы не записались: {type(e).__name__}: {e}")
    if jr.needs_title():
        try:
            jr.set_title(_chat.title_for(jr.current_dialog()))
        except Exception:
            pass


def _realtime_available() -> bool:
    """Пускает ли ключ в Live API. Считается один раз за запуск: соединение
    небесплатное по времени, а тариф посреди сессии не меняется."""
    if _RT_CACHE["ok"] is not None:
        return _RT_CACHE["ok"]
    import asyncio as _a
    import json as _j
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        _RT_CACHE["ok"] = False
        return False

    async def probe() -> bool:
        try:
            import websockets
            from .voice import LIVE_URL, MODEL as _M
            async with websockets.connect(f"{LIVE_URL}?key={key}", open_timeout=15) as ws:
                await ws.send(_j.dumps({"setup": {"model": f"models/{_M}",
                                                  "generationConfig": {"responseModalities": ["AUDIO"]}}}))
                m = await _a.wait_for(ws.recv(), timeout=15)
                d = _j.loads(m if isinstance(m, str) else m.decode())
                return "setupComplete" in d
        except Exception:
            return False

    try:
        _RT_CACHE["ok"] = _a.run(probe())
    except Exception:
        _RT_CACHE["ok"] = False
    print(f"  realtime: {'доступен (платный тариф)' if _RT_CACHE['ok'] else 'НЕТ — рация на бесплатном'}")
    return _RT_CACHE["ok"]


class Handler(BaseHTTPRequestHandler):
    mem: Memory = None                      # выставляется при старте
    jr: Journal = None
    lk: Links = None
    voice_port: int = 8792

    def log_message(self, *a):              # молчим: свой лог печатаем сами
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            f = WEB / "index.html"
            if not f.exists():
                return self._send(404, b"index.html not found", "text/plain")
            return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
        if self.path.startswith("/api/backup/targets"):
            cur = os.environ.get("DIARY_BACKUP_DIR", "")
            return self._json({"targets": _backup.targets(), "current": cur,
                               "last": _backup.last(cur) if cur else None})
        if self.path.startswith("/api/quiz"):
            # Вопрос о неочевидной связи. Не чаще раза в несколько ходов и только
            # когда есть подходящая пара — иначе опросник превращается в допрос.
            if not self.lk.due():
                return self._json({"question": None,
                                   "turns": self.lk.turns_since_ask,
                                   "need": 6})
            nodes, vecs = self.mem.nodes_with_vectors()
            c = self.lk.candidate(nodes, vecs)
            if not c:
                return self._json({"question": None})
            try:
                q = _chat.ask_link(c["a_text"], c["b_text"])
            except Exception as e:
                print(f"[quiz] вопрос не составился: {e}")
                return self._json({"question": None})
            if not q:                       # модель сочла связь очевидной или бестактной
                self.lk.save_answer(c["a"], c["b"], False, note="skipped by model",
                                    a_text=c["a_text"], b_text=c["b_text"])
                return self._json({"question": None})
            self.lk.reset()
            return self._json({"question": q, "pair": c})
        if self.path.startswith("/api/export"):
            # Выгружаем ВСЁ: и разговоры, и факты. Прежний экспорт собирал карточки
            # со страницы и после переделки интерфейса отдавал пустой файл.
            # Узлы берём из снимка, а не поиском: поиск требует вектор запроса и
            # по нулевому не возвращает ничего. Снимок — полный список как есть.
            facts, edges = [], []
            try:
                self.mem.h.save()
                snap = json.loads((HOME / "diary.hsam.json").read_text(encoding="utf-8"))
                nx = snap.get("nexus") or {}
                raw_nodes = nx.get("nodes") or []
                # в снимке nodes — СЛОВАРЬ id→узел, а не список
                if isinstance(raw_nodes, dict):
                    raw_nodes = list(raw_nodes.values())
                for n in raw_nodes:
                    if not isinstance(n, dict):
                        continue
                    facts.append({"content": n.get("content", ""),
                                  "source_type": n.get("source_type"),
                                  "importance": n.get("importance"),
                                  "canon": n.get("canon_level"),
                                  "tags": n.get("tags")})
                raw_edges = nx.get("edges") or []
                edges = list(raw_edges.values()) if isinstance(raw_edges, dict) else raw_edges
            except Exception as e:
                print(f"[export] снимок не прочитан: {e}")
            body = json.dumps({"exported": time.strftime("%Y-%m-%dT%H:%M:%S"),
                               "days": self.jr.days(),
                               "memory_entries": self.mem.count(),
                               "facts": facts, "edges": edges}, ensure_ascii=False, indent=1).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="diary_export.json"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if self.path.startswith("/api/days"):
            return self._json({"days": self.jr.days()})
        if self.path.startswith("/api/capabilities"):
            # Live API нет на бесплатном тарифе. Проверяем не по документации,
            # а попыткой соединения — тариф виден только так.
            return self._json({"stt": _stt.available(),
                               "realtime": _realtime_available(),
                               "why": "" if _realtime_available()
                                      else "Realtime voice needs a paid Gemini plan. "
                                           "Walkie-talkie mode works on the free tier."})
        if self.path.startswith("/api/settings"):
            # 🔴 Ключ в страницу отдаётся ТОЛЬКО в режиме разработки (DIARY_DEV=1),
            # чтобы поле настроек показывало реальное значение на своей машине.
            # В обычной сборке ключи вводит пользователь и живут они у него.
            dev = os.environ.get("DIARY_DEV", "") == "1"
            return self._json({"apiKey": os.environ.get("GEMINI_API_KEY", "") if dev else "",
                               "hasKey": bool(os.environ.get("GEMINI_API_KEY")),
                               "hasStt": bool(os.environ.get("GROQ_API_KEY")),
                               "voice": os.environ.get("DIARY_LIVE_VOICE", "Kore"),
                               "liveModel": os.environ.get("DIARY_LIVE_MODEL",
                                                           "gemini-2.5-flash-native-audio-latest")})
        if self.path.startswith("/api/status"):
            return self._json({"entries": self.mem.count(),
                               "db": str(self.mem.path),
                               "key": bool(os.environ.get("GEMINI_API_KEY")),
                               "voice_ws": f"ws://127.0.0.1:{getattr(self, 'voice_port', 8792)}"})
        name = self.path.lstrip("/").split("?")[0]
        f = WEB / name
        if f.exists() and f.is_file() and WEB in f.resolve().parents:
            ct = ("image/jpeg" if f.suffix in (".jpg", ".jpeg") else
                  "image/png" if f.suffix == ".png" else "text/plain")
            return self._send(200, f.read_bytes(), ct)
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json({"error": "bad request"}, 400)

        if self.path == "/api/chat":
            text = (body.get("text") or "").strip()
            if not text:
                return self._json({"error": "empty"}, 400)
            try:
                r = _chat.reply(self.mem, text, HISTORY)
            except Exception as e:
                return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            answer = r["answer"]
            HISTORY.append({"role": "user", "content": text})
            HISTORY.append({"role": "assistant", "content": answer})
            del HISTORY[:-16]
            # запись фактов — в фоне: человек не должен ждать её ради ответа
            threading.Thread(target=_record, args=(self, text, answer, False),
                             daemon=True).start()
            return self._json({"answer": answer,
                               "recalled": [{"content": x.get("content", ""),
                                             "source": x.get("source_type")} for x in r["used"]]})

        if self.path == "/api/voice":
            """Рация: пришёл голос — вернулись текст и озвученный ответ.
            Работает на бесплатном ключе, в отличие от Live API."""
            import base64 as _b64
            audio_b64 = body.get("audio") or ""
            voice = body.get("voice") or "Kore"
            if not audio_b64:
                return self._json({"error": "no audio"}, 400)
            try:
                raw = _b64.b64decode(audio_b64)
                # Копия последней записи: без неё «она меня не слышит» не отличить
                # от «микрофон пишет тишину». Файл один, перезаписывается.
                try:
                    (HOME / "last_voice.wav").write_bytes(raw)
                except Exception:
                    pass
                # Сначала СЛОВА, потом разговор. Gemini умеет слушать аудио сам, но
                # расшифровку наружу не отдаёт: в дневнике оставалась заглушка вместо
                # реплики, память искалась вслепую, а «не расслышала» было не отличить
                # от «записалась тишина». Whisper возвращает текст — дальше всё идёт
                # обычным текстовым путём.
                said = ""
                try:
                    said = _stt.transcribe(raw, language=body.get("language") or None)
                except _stt.SttUnavailable as e:
                    print(f"[stt] недоступен ({e}) — отдаю аудио напрямую в модель")
                if said:
                    r = _chat.reply(self.mem, said, HISTORY)
                else:
                    r = _chat.reply(self.mem, body.get("hint") or "",
                                    HISTORY, audio=raw, mime="audio/wav")
            except Exception as e:
                return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            answer = r["answer"]
            HISTORY.append({"role": "user", "content": said or "[voice]"})
            HISTORY.append({"role": "assistant", "content": answer})
            del HISTORY[:-16]
            threading.Thread(target=_record, args=(self, said or "🎙 (spoken)", answer, True),
                             daemon=True).start()
            wav = _tts.speak(answer, voice=voice)
            return self._json({"answer": answer,
                               "transcript": said,          # то, что услышал Whisper
                               "audio": _b64.b64encode(wav).decode() if wav else "",
                               "recalled": len(r["used"])})

        if self.path == "/api/backup/run":
            dest = (body.get("dest") or os.environ.get("DIARY_BACKUP_DIR") or "").strip()
            if not dest:
                return self._json({"ok": False, "error": "choose a folder first"}, 400)
            self.mem.h.save()                      # сначала сбросить память на диск
            r = _backup.make(HOME, dest)
            if r.get("ok"):
                os.environ["DIARY_BACKUP_DIR"] = dest
                try:
                    (HOME / "backup_dir").write_text(dest, encoding="utf-8")
                except Exception:
                    pass
            return self._json(r)

        if self.path == "/api/backup/restore":
            return self._json(_backup.restore(body.get("file") or "", HOME))

        if self.path == "/api/keys":
            """Ключи, введённые пользователем. Держим только в памяти процесса и
            в файле рядом с дневником — наружу они не уходят никуда, кроме
            самих Google и Groq."""
            saved = []
            for field, var, fname in (("gemini", "GEMINI_API_KEY", "key"),
                                      ("groq", "GROQ_API_KEY", "groq_key")):
                v = (body.get(field) or "").strip()
                if not v:
                    continue
                os.environ[var] = v
                try:
                    f = HOME / fname
                    f.write_text(v, encoding="utf-8")
                    os.chmod(f, 0o600)
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)}, 500)
                saved.append(field)
            _RT_CACHE["ok"] = None            # тариф мог измениться — проверим заново
            return self._json({"ok": True, "saved": saved})

        if self.path == "/api/attach":
            """Файл или ссылка в разговор. Оригинал кладём рядом, в память идёт
            смысл, а не байты."""
            import base64 as _b64
            note = (body.get("text") or "").strip()
            url = (body.get("url") or "").strip()
            parts, label = [], ""
            try:
                if url:
                    got = _files.fetch_link(url)
                    if got["kind"] == "file":
                        p = _files.store(got["data"], got["name"], HOME / "attachments")
                        parts = [_files.as_part(got["data"], got["mime"], got["name"])]
                        label = f"[ссылка: {url}]"
                    else:
                        label = f"[страница: {got.get('title') or url}]"
                        parts = [{"text": f"{label}\n{got['url']}\n\n{got['text']}"}]
                else:
                    name = body.get("name") or "file"
                    data = _b64.b64decode(body.get("data") or "")
                    if not data:
                        return self._json({"error": "empty file"}, 400)
                    mime = body.get("mime") or _files.guess_mime(name, data)
                    p = _files.store(data, name, HOME / "attachments")
                    parts = [_files.as_part(data, mime, name)]
                    label = f"[файл: {name}]"
            except Exception as e:
                return self._json({"error": f"{type(e).__name__}: {e}"}, 400)

            ask = note or ("Look at this and tell me what matters here. "
                           "Then ask me one question about it.")
            try:
                r = _chat.reply(self.mem, ask, HISTORY, extra_parts=parts)
            except Exception as e:
                return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            answer = r["answer"]
            HISTORY.append({"role": "user", "content": f"{label} {note}".strip()})
            HISTORY.append({"role": "assistant", "content": answer})
            del HISTORY[:-16]
            threading.Thread(target=_record,
                             args=(self, f"{label} {note}".strip(), answer, False),
                             daemon=True).start()
            return self._json({"answer": answer, "label": label,
                               "recalled": len(r["used"])})

        if self.path == "/api/quiz/answer":
            pair = body.get("pair") or {}
            linked = bool(body.get("linked"))
            self.lk.save_answer(pair.get("a", ""), pair.get("b", ""), linked,
                                note=body.get("note", ""),
                                a_text=pair.get("a_text", ""), b_text=pair.get("b_text", ""))
            # подтверждённая связь — сама по себе факт о жизни, её стоит помнить
            if linked and body.get("note"):
                self.mem.remember(body["note"].strip()[:300], source=SRC_USER)
            return self._json({"ok": True})

        if self.path == "/api/remember":            # явная запись «запомни это»
            t = (body.get("text") or "").strip()
            canon = CANON_FOUNDATIONAL if body.get("canon") else 0
            return self._json({"id": self.mem.remember(t, source=SRC_USER, canon=canon)})

        if self.path == "/api/session/edit":
            session_id = (body.get("id") or "").strip()
            if not session_id:
                return self._json({"error": "missing id"}, 400)
            ok = self.jr.update_session(
                session_id=session_id,
                title=body.get("title"),
                turns=body.get("turns"),
                takeaways=body.get("takeaways"),
            )
            return self._json({"ok": ok})

        if self.path == "/api/session/delete":
            session_id = (body.get("id") or "").strip()
            if not session_id:
                return self._json({"error": "missing id"}, 400)
            ok = self.jr.delete_session(session_id)
            return self._json({"ok": ok})

        self._json({"error": "unknown endpoint"}, 404)


def _title_sweep(handler) -> None:
    """Безымянным сессиям раздаём названия. Живой разговор идёт мимо _record,
    поэтому его сессии оставались «Untitled» — заголовок им нужен так же."""
    def loop():
        while True:
            time.sleep(90)
            try:
                if handler.jr.needs_title():
                    handler.jr.set_title(_chat.title_for(handler.jr.current_dialog()))
            except Exception:
                pass
    threading.Thread(target=loop, daemon=True).start()


def _start_autobackup(handler) -> None:
    """Копия раз в час, если папка выбрана. Дневник копится молча, и человек
    вспоминает о копии ровно тогда, когда она уже нужна."""
    def loop():
        while True:
            time.sleep(3600)
            dest = os.environ.get("DIARY_BACKUP_DIR", "")
            if not dest:
                continue
            try:
                handler.mem.h.save()
                r = _backup.make(HOME, dest)
                if not r.get("ok"):
                    print(f"[backup] не вышло: {r.get('error')}")
            except Exception as e:
                print(f"[backup] сбой: {type(e).__name__}: {e}")
    threading.Thread(target=loop, daemon=True).start()


def _start_voice(mem, port: int, jr=None) -> None:
    """Голосовой мост живёт в своём потоке со своим циклом: http.server —
    синхронный, а Live API асинхронный, и мешать их в одном цикле незачем."""
    import asyncio as _a
    from . import voice as _voice

    def runner():
        try:
            _a.run(_voice.run(mem, port, jr))
        except Exception as e:
            print(f"[voice] мост не поднялся: {type(e).__name__}: {e}")
    threading.Thread(target=runner, daemon=True).start()


def serve(port: int = 8791, open_browser: bool = True, voice_port: int = 8792) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    Handler.mem = Memory(HOME / "diary.hsam.json")
    Handler.jr = Journal(HOME / "journal.jsonl")
    Handler.lk = Links(HOME / "links.jsonl")
    # куда писать копии — помним между запусками
    if not os.environ.get("DIARY_BACKUP_DIR") and (HOME / "backup_dir").exists():
        os.environ["DIARY_BACKUP_DIR"] = (HOME / "backup_dir").read_text(encoding="utf-8").strip()
    _start_autobackup(Handler)
    _title_sweep(Handler)
    Handler.voice_port = voice_port
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"дневник: {url}\n  память: {Handler.mem.path}  ({Handler.mem.count()} записей)")
    _start_voice(Handler.mem, voice_port, Handler.jr)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nостановлен")
    finally:
        Handler.mem.close()


if __name__ == "__main__":
    import sys
    serve(port=int(sys.argv[1]) if len(sys.argv) > 1 else 8791)
