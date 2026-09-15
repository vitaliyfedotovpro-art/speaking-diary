#!/usr/bin/env python3
"""Support console — for whoever installed the diary, not for the person using it.

The person opens a session and reads out a short code. You type the code here and
run checks on their machine: the request goes into the channel, the application
answers, and the answer is printed here.

Only the checks from the list are run — there are no arbitrary commands on their
side or on yours. The diary itself is not reachable.

  python3 support_console.py <code>
"""
import sys, time, httpx

CHANNEL = "PASTE_SUPPORT_CHANNEL_URL"   # the same address that is baked into the build

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
        # put the task into the channel and wait for the answer
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
