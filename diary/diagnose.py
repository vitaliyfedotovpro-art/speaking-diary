"""Отчёт о состоянии дневника — чтобы «у меня не работает» стало разбираемым.

Человеку на другой машине не объяснить по переписке, что именно сломалось: он
видит молчащую кнопку, а причина лежит на три слоя ниже. Отчёт собирает всё, что
нужно для разбора, и НИЧЕГО из того, что нельзя показывать.

⛔ Что сюда не попадает никогда: сами ключи, тексты записей, содержимое разговоров,
имена файлов вложений. Только факты о работоспособности.
"""
from __future__ import annotations

import json
import os
import platform
import sys
import time
from pathlib import Path

import httpx


def _probe_gemini(key: str) -> dict:
    if not key:
        return {"ok": False, "why": "no key"}
    try:
        r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models",
                      params={"key": key}, timeout=20)
        if r.status_code != 200:
            return {"ok": False, "http": r.status_code, "why": r.text[:200]}
        models = [m["name"].replace("models/", "") for m in r.json().get("models", [])]
        return {"ok": True, "models": len(models),
                "has_chat": any("gemini-3.8" in m for m in models),
                "has_tts": any("tts" in m for m in models),
                "has_embed": any("embedding" in m for m in models)}
    except Exception as e:
        return {"ok": False, "why": f"{type(e).__name__}: {e}"}


def _probe_groq(key: str) -> dict:
    if not key:
        return {"ok": False, "why": "no key"}
    try:
        r = httpx.get("https://api.groq.com/openai/v1/models",
                      headers={"Authorization": f"Bearer {key}"}, timeout=20)
        if r.status_code != 200:
            return {"ok": False, "http": r.status_code, "why": r.text[:200]}
        ids = [m["id"] for m in r.json().get("data", [])]
        return {"ok": True, "has_whisper": any("whisper" in i for i in ids)}
    except Exception as e:
        return {"ok": False, "why": f"{type(e).__name__}: {e}"}


def collect(home: Path, mem=None, journal=None, extra: dict | None = None) -> dict:
    gem = os.environ.get("GEMINI_API_KEY", "")
    grq = os.environ.get("GROQ_API_KEY", "")

    files = {}
    for name in ("diary.hsam.json", "journal.jsonl", "links.jsonl"):
        p = home / name
        files[name] = {"exists": p.exists(),
                       "size": p.stat().st_size if p.exists() else 0}
    att = home / "attachments"
    files["attachments"] = {"exists": att.exists(),
                            "count": len(list(att.glob("*"))) if att.exists() else 0}

    engine = {"loaded": False}
    try:
        from .hsam import Hsam
        h = Hsam()
        engine = {"loaded": True, "version": h.version()}
        h.close()
    except Exception as e:
        engine = {"loaded": False, "why": f"{type(e).__name__}: {e}"}

    return {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "app": {"frozen": bool(getattr(sys, "frozen", False)),
                "python": sys.version.split()[0]},
        "system": {"os": platform.system(), "release": platform.release(),
                   "machine": platform.machine()},
        "keys": {"gemini_present": bool(gem), "groq_present": bool(grq),
                 # длина и первые символы помогают поймать обрезанный или
                 # склеенный ключ, но сам ключ не раскрывают
                 "gemini_len": len(gem), "gemini_prefix": gem[:4],
                 "groq_len": len(grq), "groq_prefix": grq[:4]},
        "gemini": _probe_gemini(gem),
        "groq": _probe_groq(grq),
        "memory": {"entries": (mem.count() if mem else None), **engine},
        "journal": {"days": len(journal.days()) if journal else None},
        "files": files,
        "home": str(home),
        **(extra or {}),
    }


def as_text(d: dict) -> str:
    """Человекочитаемо: этот текст можно просто вставить в сообщение."""
    g, q = d.get("gemini", {}), d.get("groq", {})
    k = d.get("keys", {})
    lines = [
        "DIARY DIAGNOSTIC REPORT",
        f"generated: {d.get('generated')}",
        f"system:    {d['system']['os']} {d['system']['release']} ({d['system']['machine']})",
        f"build:     {'packaged app' if d['app']['frozen'] else 'from source'}, python {d['app']['python']}",
        "",
        f"gemini key: {'present' if k.get('gemini_present') else 'MISSING'}"
        f" (len {k.get('gemini_len')}, starts '{k.get('gemini_prefix')}')",
        f"  -> {'works, ' + str(g.get('models')) + ' models' if g.get('ok') else 'FAILS: ' + str(g.get('why') or g.get('http'))}",
        f"  chat model available: {g.get('has_chat')}   tts: {g.get('has_tts')}   embeddings: {g.get('has_embed')}",
        "",
        f"groq key:   {'present' if k.get('groq_present') else 'missing (speech will not be transcribed)'}"
        f" (len {k.get('groq_len')}, starts '{k.get('groq_prefix')}')",
        f"  -> {'works, whisper: ' + str(q.get('has_whisper')) if q.get('ok') else 'fails: ' + str(q.get('why') or q.get('http'))}",
        "",
        f"memory engine: {'loaded, ' + str(d['memory'].get('version')) if d['memory'].get('loaded') else 'NOT LOADED: ' + str(d['memory'].get('why'))}",
        f"entries: {d['memory'].get('entries')}   days in journal: {d.get('journal', {}).get('days')}",
        "",
        "files in " + str(d.get("home")) + ":",
    ]
    for name, info in (d.get("files") or {}).items():
        if "count" in info:
            lines.append(f"  {name}: {'yes' if info['exists'] else 'no'}, {info['count']} items")
        else:
            lines.append(f"  {name}: {'yes' if info['exists'] else 'no'}, {info['size']} bytes")
    if d.get("client"):
        lines += ["", "browser side:"]
        for kk, vv in d["client"].items():
            lines.append(f"  {kk}: {vv}")
    lines += ["", "No diary content, conversations or keys are included in this report."]
    return "\n".join(lines)
