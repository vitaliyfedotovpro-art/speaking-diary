"""Управление календарем дел, расписанием и экспресс-стикерами."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, date, timedelta
from pathlib import Path

def get_calendar_file(home: Path | None = None) -> Path:
    if home is None:
        home = Path(os.environ.get("DIARY_HOME", Path.home() / ".diary"))
    home.mkdir(parents=True, exist_ok=True)
    return home / "calendar.json"

def load_data(home: Path | None = None) -> dict:
    f = get_calendar_file(home)
    if not f.exists():
        return {"events": [], "stickers": []}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
        if "events" not in data:
            data["events"] = []
        if "stickers" not in data:
            data["stickers"] = []
        return data
    except Exception:
        return {"events": [], "stickers": []}

def save_data(data: dict, home: Path | None = None) -> None:
    f = get_calendar_file(home)
    try:
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[calendar] save error: {e}")

def parse_relative_date(val: str) -> str:
    s = (val or "").strip().lower()
    today = date.today()
    if not s or s in ("сегодня", "today"):
        return today.isoformat()
    if s in ("завтра", "tomorrow"):
        return (today + timedelta(days=1)).isoformat()
    if s in ("послезавтра",):
        return (today + timedelta(days=2)).isoformat()
    try:
        d = datetime.strptime(s, "%Y-%m-%d").date()
        return d.isoformat()
    except Exception:
        return today.isoformat()

def add_event(home: Path | None, title: str, date_str: str = "today",
              time_str: str = "all-day", notes: str = "", tag: str = "general") -> dict:
    data = load_data(home)
    actual_date = parse_relative_date(date_str)
    evt = {
        "id": f"evt_{int(time.time() * 1000)}",
        "title": title.strip(),
        "date": actual_date,
        "time": time_str.strip() or "all-day",
        "notes": notes.strip(),
        "tag": tag.strip() or "general",
        "created": datetime.now().isoformat()
    }
    data["events"].append(evt)
    save_data(data, home)
    return evt

def add_sticker(home: Path | None, text: str, color: str = "kraft") -> dict:
    data = load_data(home)
    stk = {
        "id": f"stk_{int(time.time() * 1000)}",
        "text": text.strip(),
        "completed": False,
        "color": color.strip() or "kraft",
        "created": datetime.now().isoformat()
    }
    data["stickers"].append(stk)
    save_data(data, home)
    return stk

def complete_sticker(home: Path | None, sticker_id: str, completed: bool = True) -> bool:
    data = load_data(home)
    found = False
    for s in data.get("stickers", []):
        if s.get("id") == sticker_id:
            s["completed"] = completed
            found = True
            break
    if found:
        save_data(data, home)
    return found

def delete_event(home: Path | None, event_id: str) -> bool:
    data = load_data(home)
    events = data.get("events", [])
    new_events = [e for e in events if e.get("id") != event_id]
    if len(new_events) != len(events):
        data["events"] = new_events
        save_data(data, home)
        return True
    return False

def delete_sticker(home: Path | None, sticker_id: str) -> bool:
    data = load_data(home)
    stickers = data.get("stickers", [])
    new_stickers = [s for s in stickers if s.get("id") != sticker_id]
    if len(new_stickers) != len(stickers):
        data["stickers"] = new_stickers
        save_data(data, home)
        return True
    return False

def update_sticker(home: Path | None, sticker_id: str, text: str | None = None, color: str | None = None) -> bool:
    data = load_data(home)
    for s in data.get("stickers", []):
        if s.get("id") == sticker_id:
            if text is not None:
                s["text"] = text.strip()
            if color is not None:
                s["color"] = color.strip()
            save_data(data, home)
            return True
    return False

def get_today_summary(home: Path | None = None) -> str:
    data = load_data(home)
    today_iso = date.today().isoformat()
    today_events = [e for e in data.get("events", []) if e.get("date") == today_iso]
    pending_stickers = [s for s in data.get("stickers", []) if not s.get("completed")]

    lines = []
    weekday_name = datetime.now().strftime('%A')
    lines.append(f"СЕГОДНЯ: {today_iso} ({weekday_name}).")
    if today_events:
        lines.append("ПЛАНЫ И ВСТРЕЧИ НА СЕГОДНЯ:")
        for e in today_events:
            t = e.get("time", "all-day")
            time_part = f" в {t}" if t and t != "all-day" else ""
            notes = f" ({e['notes']})" if e.get("notes") else ""
            title = e.get("title", "Без названия")
            lines.append(f"  • {title}{time_part}{notes}")
    else:
        lines.append("На сегодня запланированных встреч в календаре нет.")

    if pending_stickers:
        lines.append("ЭКСПРЕСС-ЗАДАЧИ И СТИКЕРЫ:")
        for s in pending_stickers[:5]:
            stk_text = s.get("text", "")
            lines.append(f"  • [ ] {stk_text}")

    return "\n".join(lines)
