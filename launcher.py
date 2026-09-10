"""Точка входа для собранного приложения.

Отличается от `python -m diary.server` тем, что сама находит свои файлы внутри
упакованного архива и открывает браузер. Всё остальное — тот же сервер.
"""
import os
import sys
from pathlib import Path


def _base() -> Path:
    """Где лежат ресурсы: рядом со скриптом или внутри собранного файла."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def main() -> None:
    base = _base()
    sys.path.insert(0, str(base))

    # ключи: из окружения, иначе из папки дневника — их вводит сам пользователь
    home = Path(os.environ.get("DIARY_HOME", Path.home() / ".diary"))
    for var, fname in (("GEMINI_API_KEY", "key"), ("GROQ_API_KEY", "groq_key")):
        if not os.environ.get(var):
            f = home / fname
            if f.exists():
                os.environ[var] = f.read_text(encoding="utf-8").strip()

    # Куда уходит кнопка «постучаться». Задаётся при сборке: отдельный бот или
    # тема ntfy, не рабочие. Адрес виден любому, кто вскроет файл, — поэтому
    # канал должен быть одноразовым и легко заменяемым.
    os.environ.setdefault("DIARY_REPORT_URL", "")   # ← вписать перед сборкой

    from diary.server import serve
    port = int(os.environ.get("DIARY_PORT", "8791"))
    try:
        serve(port=port)
    except OSError as e:
        # порт занят — почти всегда это второй запуск того же дневника
        print(f"Не удалось занять порт {port}: {e}\n"
              f"Возможно, дневник уже открыт — загляни в http://127.0.0.1:{port}/")
        input("Enter, чтобы закрыть…")


if __name__ == "__main__":
    main()
