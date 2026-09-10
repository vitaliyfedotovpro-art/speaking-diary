"""Кнопка «постучаться»: отчёт уходит тому, кто это поставил.

Зачем не почта и не GitHub: человеку с проблемой нельзя давать задание. Он жмёт
одну кнопку, пишет строчку «что случилось» — и всё уходит само. Любая просьба
«скопируйте файл и пришлите» теряет половину обращений.

Куда именно — задаётся при сборке через DIARY_REPORT_URL:

  Discord    https://discord.com/api/webhooks/<id>/<token>   ← привычен где угодно
  Slack      https://hooks.slack.com/services/<...>
  ntfy       https://ntfy.sh/<длинная-случайная-тема>          ← ничего не заводить
  Telegram   https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<ID>
  свой хук   любой URL, принимающий POST

Выбирая канал, помни: адрес виден тому, кто вскроет сборку. В Северной Америке
ссылка на Telegram у многих вызывает подозрение сама по себе — Discord, Slack
или ntfy не вызывают ни у кого.

⚠️ Адрес виден любому, кто вскроет сборку. Поэтому бот заводится ОТДЕЛЬНЫЙ, не
рабочий: если адрес утечёт, худшее — спам в один чат, и канал меняется заменой
строки. Ключи и содержимое дневника сюда не попадают никогда — уходит тот же
отчёт, что человек видит на экране перед отправкой.
"""
from __future__ import annotations

import os
import platform

import httpx

MAX = 3800          # телеграм рвёт длинные сообщения; отчёт короче, но с запасом


def target() -> str:
    return os.environ.get("DIARY_REPORT_URL", "").strip()


def configured() -> bool:
    return bool(target())


def send(report_text: str, complaint: str = "", who: str = "") -> dict:
    """→ {'ok': bool, 'why': str}. Ошибку возвращаем словами, а не кодом:
    её увидит человек, а не разработчик."""
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
            # Discord режет сообщения на 2000 символов и не любит пустые поля
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
