#!/usr/bin/env python3
"""Пульт поддержки — для тебя, не для Адди.

Адди открыл сессию и назвал код. Ты вводишь код здесь и гоняешь проверки на его
машине: они уходят в канал, приложение отвечает, ответ печатается тут.

Выполняются только проверки из списка — произвольных команд нет ни на его
стороне, ни на твоей. Дневник недоступен.

  python3 support_console.py <код>
"""
import sys, time, httpx

CHANNEL = "PASTE_SUPPORT_CHANNEL_URL"   # тот же адрес, что зашит в сборку

CHECKS = ["status", "diagnose", "net", "engine", "files", "logtail", "versions"]

def main():
    if len(sys.argv) < 2:
        print("укажи код сессии, который назвал человек"); return
    code = sys.argv[1].strip().upper()
    url = CHANNEL.rstrip("/")
    print(f"сессия {code}. проверки: {', '.join(CHECKS)}  (пусто — выйти)")
    while True:
        try:
            c = input(f"[{code}] проверка> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not c:
            break
        if c not in CHECKS:
            print("нет такой. доступно:", ", ".join(CHECKS)); continue
        # положить задание в канал и ждать ответ
        httpx.post(f"{url}/task", json={"code": code, "check": c}, timeout=20)
        print("… ждём машину человека")
        for _ in range(20):
            time.sleep(3)
            r = httpx.get(f"{url}/result", params={"code": code, "check": c}, timeout=20)
            if r.status_code == 200 and (r.json() or {}).get("output"):
                print("─" * 60)
                print(r.json()["output"])
                print("─" * 60)
                break
        else:
            print("ответа нет — сессия закрыта или машина офлайн")

if __name__ == "__main__":
    main()
