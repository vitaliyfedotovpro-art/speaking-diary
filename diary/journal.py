"""Журнал разговоров: то, что человек листает в блокноте.

Отдельно от HSAM намеренно. HSAM хранит ФАКТЫ — выжимку, которая переживёт годы
и по которой идёт поиск. Журнал хранит РАЗГОВОРЫ как они были, по дням, чтобы их
можно было перечитать. Смешивать нельзя: сырые реплики в памяти утопят факты,
а факты без разговора теряют то, как это было сказано.

Сессия рвётся по паузе: молчал больше получаса — начался новый разговор.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from threading import Lock

GAP = 30 * 60          # пауза, после которой разговор считается новым


class Journal:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._cur: dict | None = None
        self._load_tail()

    def _load_tail(self) -> None:
        """Последняя сессия дня продолжается, если пауза была короткой."""
        last = None
        for row in self._rows():
            last = row
        if last and (time.time() - last.get("ts_last", 0)) < GAP:
            self._cur = last

    def _rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue                      # битая строка не должна ронять журнал
        return out

    def _flush(self, rows: list[dict]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                       encoding="utf-8")
        tmp.replace(self.path)                # атомарно: обрыв не оставит полфайла

    def add_turn(self, who: str, text: str, voice: bool = False) -> str:
        with self._lock:
            now = time.time()
            if not self._cur or (now - self._cur.get("ts_last", 0)) > GAP:
                self._cur = {"id": uuid.uuid4().hex[:12],
                             "date": datetime.now().strftime("%Y-%m-%d"),
                             "started": datetime.now().strftime("%H:%M"),
                             "ts": now, "ts_last": now,
                             "title": "", "turns": [], "takeaways": [],
                             "extracted": 0}   # сколько ходов уже разобрано на факты
                rows = self._rows() + [self._cur]
            else:
                rows = [r for r in self._rows() if r["id"] != self._cur["id"]] + [self._cur]
            self._cur["turns"].append({"who": who, "text": text,
                                       "at": datetime.now().strftime("%H:%M"),
                                       "voice": bool(voice)})
            self._cur["ts_last"] = now
            self._flush(rows)
            return self._cur["id"]

    def add_takeaways(self, facts: list[str]) -> None:
        """Осадок разговора — то, что дневник запомнил. Показывается сноской."""
        if not facts or not self._cur:
            return
        with self._lock:
            self._cur["takeaways"] = (self._cur.get("takeaways") or []) + list(facts)
            rows = [r for r in self._rows() if r["id"] != self._cur["id"]] + [self._cur]
            self._flush(rows)

    def backfill_extracted(self) -> int:
        """Пометить прежние разговоры как уже разобранные.

        🔴 До перехода на разбор раз в сессию каждый ход разбирался сразу — всё
        записанное раньше УЖЕ лежит в памяти. Без этой отметки первый же проход
        принял бы всю историю за неразобранную и утащил её в модель: у человека
        с месяцами записей это десятки запросов разом и выбранная за минуту
        суточная квота. Ставится один раз, при первом запуске новой версии.
        """
        with self._lock:
            rows = self._rows()
            n = 0
            for r in rows:
                if "extracted" not in r:
                    r["extracted"] = len(r.get("turns") or [])
                    n += 1
            if self._cur is not None and "extracted" not in self._cur:
                # _cur живёт отдельным объектом и при следующей записи перекроет
                # строку на диске — без этого отметка бы потерялась
                self._cur["extracted"] = len(self._cur.get("turns") or [])
            if n:
                self._flush(rows)
            return n

    def pending(self, idle_sec: int = 180, max_pending: int = 12) -> list[dict]:
        """Разговоры, которые пора разобрать на факты.

        Ждём либо тишины (человек ушёл — разговор закончен), либо накопления
        дюжины неразобранных ходов: иначе долгая беседа весь день оставалась бы
        неразобранной, а к вечеру её пришлось бы глотать одним куском.
        """
        now = time.time()
        out = []
        for r in self._rows():
            turns = r.get("turns") or []
            done = int(r.get("extracted") or 0)
            if len(turns) <= done:
                continue
            idle = now - float(r.get("ts_last") or 0)
            if idle >= idle_sec or (len(turns) - done) >= max_pending:
                out.append(r)
        return out

    def settle_session(self, session_id: str, facts: list[str], upto: int) -> None:
        """Итог разбора: выводы в сессию, отметка «разобрано до хода N».
        Одной записью на диск — две отдельные ломали бы файл на полпути."""
        with self._lock:
            rows = self._rows()
            for r in rows:
                if r.get("id") != session_id:
                    continue
                if facts:
                    r["takeaways"] = (r.get("takeaways") or []) + list(facts)
                r["extracted"] = upto
                break
            else:
                return
            if self._cur and self._cur.get("id") == session_id:
                if facts:
                    self._cur["takeaways"] = (self._cur.get("takeaways") or []) + list(facts)
                self._cur["extracted"] = upto
            self._flush(rows)

    def set_title(self, title: str) -> None:
        if not title or not self._cur:
            return
        with self._lock:
            self._cur["title"] = title.strip()[:90]
            rows = [r for r in self._rows() if r["id"] != self._cur["id"]] + [self._cur]
            self._flush(rows)

    def needs_title(self) -> bool:
        return bool(self._cur) and not self._cur.get("title") and len(self._cur["turns"]) >= 2

    def current_dialog(self) -> str:
        if not self._cur:
            return ""
        return "\n".join(f"{'PERSON' if t['who'] == 'you' else 'DIARY'}: {t['text'][:400]}"
                         for t in self._cur["turns"][:8])

    def days(self) -> list[dict]:
        """Всё для блокнота: дни от новых к старым, внутри — сессии по времени."""
        by: dict[str, list[dict]] = {}
        for r in self._rows():
            by.setdefault(r.get("date", "?"), []).append(r)
        out = []
        for date in sorted(by, reverse=True):
            ses = sorted(by[date], key=lambda r: r.get("ts", 0))
            out.append({"date": date, "sessions": [
                {"id": s["id"], "started": s.get("started", ""),
                 "title": s.get("title") or "Untitled conversation",
                 "turns": s.get("turns", []),
                 "takeaways": s.get("takeaways", [])} for s in ses]})
        return out

    def update_session(self, session_id: str, title: str | None = None,
                       turns: list[dict] | None = None,
                       takeaways: list[str] | None = None) -> bool:
        with self._lock:
            rows = self._rows()
            found = False
            for r in rows:
                if r.get("id") == session_id:
                    found = True
                    if title is not None:
                        r["title"] = str(title).strip()[:90]
                    if turns is not None:
                        r["turns"] = turns
                    if takeaways is not None:
                        r["takeaways"] = takeaways
                    break
            if not found:
                return False
            if self._cur and self._cur.get("id") == session_id:
                if title is not None:
                    self._cur["title"] = str(title).strip()[:90]
                if turns is not None:
                    self._cur["turns"] = turns
                if takeaways is not None:
                    self._cur["takeaways"] = takeaways
            self._flush(rows)
            return True

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            rows = self._rows()
            new_rows = [r for r in rows if r.get("id") != session_id]
            if len(new_rows) == len(rows):
                return False
            if self._cur and self._cur.get("id") == session_id:
                self._cur = None
            self._flush(new_rows)
            return True

