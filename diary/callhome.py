"""The "knock on the door" button: the report goes to whoever installed this.

Why not email and not GitHub: you cannot hand a task to a person who already has a
problem. They press one button, write a single line about what happened, and it
leaves on its own. Any request to "copy a file and send it over" loses half the
reports.

Where exactly is set at build time through DIARY_REPORT_URL:

  Discord    https://discord.com/api/webhooks/<id>/<token>   ← familiar anywhere
  Slack      https://hooks.slack.com/services/<...>
  ntfy       https://ntfy.sh/<long-random-topic>              ← nothing to set up
  Telegram   https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<ID>
  own hook   any URL that accepts a POST

When choosing a channel, remember the address is visible to anyone who opens the
build. In North America a Telegram link makes many people suspicious on its own —
Discord, Slack and ntfy make nobody suspicious.

⚠️ The address is visible to anyone who opens the build. So the bot must be a
SEPARATE one, not a working account: if the address leaks, the worst case is spam
in a single chat, and the channel is changed by replacing one string. Keys and
diary content never reach this place — what leaves is the same report the person
sees on screen before sending.
"""
from __future__ import annotations

import os
import platform

import httpx

MAX = 3800          # Telegram cuts long messages; the report is shorter, with room to spare


def target() -> str:
    return os.environ.get("DIARY_REPORT_URL", "").strip()


def configured() -> bool:
    return bool(target())


def send(report_text: str, complaint: str = "", who: str = "") -> dict:
    """→ {'ok': bool, 'why': str}. The error comes back in words, not as a code:
    a person will read it, not a developer."""
    url = target()
    if not url:
        return {"ok": False, "why": "no report channel configured in this build"}

    head = "🔴 DIARY REPORT"
    if who:
        head += f" · from {who[:40]}"
    body = (f"{head}\n"
            f"{platform.system()} {platform.release()}\n\n"
            f"WHAT HAPPENED:\n{(complaint or '(not described)')[:600]}\n\n"
            f"{report_text}")[:MAX]

    try:
        if "discord.com/api/webhooks" in url or "discordapp.com/api/webhooks" in url:
            # Discord caps messages at 2000 characters and dislikes empty fields
            r = httpx.post(url, timeout=30,
                           json={"content": f"```\n{body[:1900]}\n```"})
        elif "hooks.slack.com" in url:
            r = httpx.post(url, timeout=30, json={"text": f"```{body[:3500]}```"})
        elif "api.telegram.org" in url:
            r = httpx.post(url, timeout=30,
                           data={"text": body, "disable_web_page_preview": "true"})
        elif "ntfy.sh" in url:
            r = httpx.post(url, timeout=30, content=body.encode("utf-8"),
                           headers={"Title": "Diary report", "Priority": "high"})
        else:
            r = httpx.post(url, timeout=30,
                           json={"text": body, "complaint": complaint, "who": who})
        if r.status_code // 100 == 2:
            return {"ok": True, "why": ""}
        return {"ok": False, "why": f"the channel answered {r.status_code}"}
    except Exception as e:
        return {"ok": False, "why": f"could not reach the channel ({type(e).__name__})"}
