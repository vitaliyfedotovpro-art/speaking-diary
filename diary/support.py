"""Режим поддержки: временный канал для разбора неполадок.

Как это работает. Человек нажимает «Allow support session» и получает короткий
код. Пока сессия открыта, приложение раз в несколько секунд спрашивает у канала,
нет ли для него задания, выполняет ОДНУ из разрешённых проверок и отправляет
результат. Сессия гаснет сама через час и по кнопке — в любой момент.

⛔ ЧЕГО ЗДЕСЬ НЕТ И НЕ БУДЕТ

· Произвольных команд. Выполняются только проверки из списка ниже — это не
  «выполни что пришлют», а «ответь на один из заранее известных вопросов».
  Список закрыт: чего в нём нет, того сделать нельзя, даже если попросят.

· Доступа к дневнику. Содержимое памяти, тексты разговоров, вложения и ключи
  недоступны ни одной проверке. Смотреть можно на то, ЧТО СЛОМАЛОСЬ, а не на то,
  ЧТО ЧЕЛОВЕК НАПИСАЛ. Это не настройка, которую можно поменять, — таких
  проверок просто не существует.

· Постоянного включения. Без нажатия кнопки канал не открывается вообще.
"""
from __future__ import annotations

import json
import os
import platform
import secrets
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import httpx

TTL = 3600          # сессия живёт час и гаснет сама
POLL = 6            # как часто спрашивать задание


class Support:
    def __init__(self, home: Path, mem=None, journal=None):
        self.home, self.mem, self.jr = home, mem, journal
        self.code = ""
        self.until = 0.0
        self.log: list[str] = []
        self._stop = threading.Event()

    # ── что вообще можно спросить ────────────────────────────────────────
    def actions(self) -> dict:
        return {
            "status":     self._status,
            "diagnose":   self._diagnose,
            "logtail":    self._logtail,
            "files":      self._files,
            "engine":     self._engine,
            "net":        self._net,
            "versions":   self._versions,
        }

    def _status(self) -> str:
        return json.dumps({
            "entries": self.mem.count() if self.mem else None,
            "days": len(self.jr.days()) if self.jr else None,
            "uptime_s": int(time.time() - _START),
            "session_left_s": max(0, int(self.until - time.time())),
        }, ensure_ascii=False)

    def _diagnose(self) -> str:
        from . import diagnose as d
        return d.as_text(d.collect(self.home, self.mem, self.jr))

    def _logtail(self) -> str:
        """Последние строки лога. Реплики туда не пишутся — только события."""
        p = self.home / "app.log"
        if not p.exists():
            return "no log file"
        try:
            return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                             .splitlines()[-80:])
        except Exception as e:
            return f"cannot read log: {e}"

    def _files(self) -> str:
        """Имена и размеры — без содержимого. Вложения только считаем."""
        out = []
        for name in ("diary.hsam.json", "journal.jsonl", "links.jsonl",
                     "key", "groq_key", "backup_dir"):
            p = self.home / name
            out.append(f"{name}: {'yes' if p.exists() else 'no'}"
                       f"{', ' + str(p.stat().st_size) + ' B' if p.exists() else ''}")
        att = self.home / "attachments"
        out.append(f"attachments: {len(list(att.glob('*'))) if att.exists() else 0} files")
        return "\n".join(out)

    def _engine(self) -> str:
        try:
            from .hsam import Hsam
            h = Hsam()
            v = h.version()
            n = h.count()
            h.close()
            return f"engine loaded: {v}\nnodes: {n}"
        except Exception:
            return "engine FAILED to load:\n" + traceback.format_exc()[-900:]

    def _net(self) -> str:
        out = []
        for name, url, hdr in (
            ("gemini", "https://generativelanguage.googleapis.com/v1beta/models", None),
            ("groq", "https://api.groq.com/openai/v1/models",
             {"Authorization": f"Bearer {os.environ.get('GROQ_API_KEY', '')}"}),
        ):
            try:
                params = {"key": os.environ.get("GEMINI_API_KEY", "")} if name == "gemini" else None
                t = time.time()
                r = httpx.get(url, params=params, headers=hdr, timeout=15)
                out.append(f"{name}: HTTP {r.status_code} in {int((time.time()-t)*1000)} ms")
            except Exception as e:
                out.append(f"{name}: {type(e).__name__}")
        return "\n".join(out)

    def _versions(self) -> str:
        mods = []
        for m in ("httpx", "numpy", "websockets"):
            try:
                mods.append(f"{m} {__import__(m).__version__}")
            except Exception:
                mods.append(f"{m} MISSING")
        return (f"python {sys.version.split()[0]}\n"
                f"{platform.system()} {platform.release()} {platform.machine()}\n"
                f"packaged: {bool(getattr(sys, 'frozen', False))}\n" + "\n".join(mods))

    # ── жизнь сессии ─────────────────────────────────────────────────────
    def open(self) -> dict:
        if not os.environ.get("DIARY_SUPPORT_URL", "").strip():
            return {"ok": False, "why": "no support channel configured in this build"}
        self.code = secrets.token_hex(3).upper()
        self.until = time.time() + TTL
        self._stop.clear()
        threading.Thread(target=self._loop, daemon=True).start()
        return {"ok": True, "code": self.code, "minutes": TTL // 60}

    def close(self) -> dict:
        self._stop.set()
        self.until = 0.0
        return {"ok": True}

    def state(self) -> dict:
        left = max(0, int(self.until - time.time()))
        return {"open": left > 0, "code": self.code if left else "", "left_s": left,
                "configured": bool(os.environ.get("DIARY_SUPPORT_URL", "").strip()),
                "actions": sorted(self.actions()), "log": self.log[-40:]}


    def run(self, name: str) -> str:
        fn = self.actions().get(name)
        if not fn:
            return f"unknown check: {name}. available: {', '.join(sorted(self.actions()))}"
        try:
            return fn()
        except Exception:
            return "check failed:\n" + traceback.format_exc()[-800:]

    def _loop(self) -> None:
        """Спрашиваем канал, нет ли задания. Приложение ходит НАРУЖУ само —
        порты для входящих соединений не открываются, файрвол не трогаем."""
        url = os.environ.get("DIARY_SUPPORT_URL", "").strip().rstrip("/")
        while not self._stop.is_set() and time.time() < self.until:
            try:
                r = httpx.get(f"{url}/task", params={"code": self.code}, timeout=20)
                if r.status_code == 200:
                    check = (r.json() or {}).get("check", "").strip()
                    if check:
                        httpx.post(f"{url}/result", timeout=30,
                                   json={"code": self.code, "check": check,
                                         "output": self.run(check)[:12000]})
            except Exception:
                pass
            self._stop.wait(POLL)
        self.until = 0.0


_START = time.time()
