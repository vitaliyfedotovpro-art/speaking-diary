"""Entry point for the packaged application.

It differs from `python -m diary.server` in that it finds its own files inside the
packed archive and opens the browser. Everything else is the same server.
"""
import os
import sys
from pathlib import Path


def _base() -> Path:
    """Where the resources are: next to the script, or inside the packed file."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _ensure_desktop_shortcut() -> None:
    """On Windows we put a shortcut on the desktop at first launch — so the diary
    opens from an icon rather than by hunting for a file. Quietly: if it fails,
    never mind."""
    if sys.platform != "win32":
        return
    try:
        import os
        exe = sys.executable if getattr(sys, "frozen", False) else None
        if not exe:
            return
        desktop = Path(os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"))
        link = desktop / "Diary.lnk"
        if link.exists() or not desktop.exists():
            return
        # the shortcut via PowerShell — no third-party libraries
        import subprocess
        ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
              f"$s.TargetPath='{exe}';$s.IconLocation='{exe},0';$s.Save()")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, timeout=15)
    except Exception:
        pass


def main() -> None:
    base = _base()
    sys.path.insert(0, str(base))

    # keys: from the environment, otherwise from the diary folder — the user enters them
    home = Path(os.environ.get("DIARY_HOME", Path.home() / ".diary"))
    for var, fname in (("GEMINI_API_KEY", "key"), ("GROQ_API_KEY", "groq_key")):
        if not os.environ.get(var):
            f = home / fname
            if f.exists():
                os.environ[var] = f.read_text(encoding="utf-8").strip()

    # Where the "knock on the door" button sends to. Set at build time: a separate
    # bot or ntfy topic, not a working one. The address is visible to anyone who
    # opens the file — so the channel must be disposable and easy to replace.
    os.environ.setdefault("DIARY_REPORT_URL", "")   # ← fill in before building

    _ensure_desktop_shortcut()

    from diary.server import serve
    port = int(os.environ.get("DIARY_PORT", "8791"))
    try:
        serve(port=port)
    except OSError as e:
        # the port is taken — almost always a second launch of the same diary
        print(f"Не удалось занять порт {port}: {e}\n"
              f"Возможно, дневник уже открыт — загляни в http://127.0.0.1:{port}/")
        input("Enter, чтобы закрыть…")


if __name__ == "__main__":
    main()
