"""Резервные копии дневника.

Дневник — это два файла: снимок памяти HSAM и журнал разговоров. Оба текстовые,
оба маленькие (тысяча записей ≈ единицы мегабайт), поэтому копия делается просто
и целиком, без хитростей с инкрементами.

Куда: любая папка на диске. Внешний SSD — обычная папка в /Volumes (macOS) или на
букве диска (Windows). Облако — папка синхронизации, которую уже держит iCloud,
Dropbox или Google Drive: писать в неё файлом надёжнее, чем ходить в чужое API,
и не требует ни ключей, ни доверия к ещё одному сервису.

⚠️ Копия — обычный файл с личными записями. Шифрования здесь нет: если папка
облачная, содержимое увидит тот, у кого доступ к облаку.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import time
import zipfile
from datetime import datetime
from pathlib import Path

KEEP = 20          # сколько копий храним; старые удаляются сами


def _candidates() -> list[dict]:
    """Куда предложить писать — только то, что реально существует на машине."""
    home = Path.home()
    out: list[dict] = []
    sysname = platform.system()

    if sysname == "Darwin":
        for p in Path("/Volumes").glob("*"):
            if p.is_dir() and not p.is_symlink() and p.name != "Macintosh HD":
                out.append({"kind": "drive", "label": f"External drive · {p.name}",
                            "path": str(p / "DiaryBackup")})
        clouds = [("iCloud Drive", home / "Library/Mobile Documents/com~apple~CloudDocs"),
                  ("Dropbox", home / "Dropbox"),
                  ("Google Drive", home / "Library/CloudStorage")]
    elif sysname == "Windows":
        for letter in "DEFGH":
            p = Path(f"{letter}:/")
            if p.exists():
                out.append({"kind": "drive", "label": f"External drive · {letter}:",
                            "path": str(p / "DiaryBackup")})
        clouds = [("OneDrive", home / "OneDrive"),
                  ("Dropbox", home / "Dropbox"),
                  ("Google Drive", home / "Google Drive")]
    else:
        clouds = [("Dropbox", home / "Dropbox"), ("Nextcloud", home / "Nextcloud")]

    for label, p in clouds:
        if p.exists():
            # Google Drive на маке лежит внутри CloudStorage отдельной папкой
            if p.name == "CloudStorage":
                for sub in p.glob("GoogleDrive-*"):
                    out.append({"kind": "cloud", "label": f"Google Drive · {sub.name.split('-')[-1]}",
                                "path": str(sub / "My Drive/DiaryBackup")})
                continue
            out.append({"kind": "cloud", "label": label, "path": str(p / "DiaryBackup")})

    out.append({"kind": "local", "label": "This machine · Documents",
                "path": str(home / "Documents/DiaryBackup")})
    return out


def targets() -> list[dict]:
    return _candidates()


def make(src_dir: Path, dest_dir: str | Path) -> dict:
    """Одна копия: zip с датой в имени. Возвращает, что получилось."""
    src_dir = Path(src_dir)
    dest = Path(os.path.expanduser(str(dest_dir)))
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"ok": False, "error": f"cannot write to that folder: {e}"}

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    name = f"diary_{stamp}.zip"
    tmp = dest / (name + ".part")
    files = [p for p in (src_dir / "diary.hsam.json", src_dir / "journal.jsonl") if p.exists()]
    if not files:
        return {"ok": False, "error": "diary is empty — nothing to back up"}
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                z.write(f, f.name)
            z.writestr("about.txt",
                       "Personal diary backup\n"
                       f"created: {datetime.now().isoformat(timespec='seconds')}\n"
                       f"files: {', '.join(f.name for f in files)}\n\n"
                       "diary.hsam.json — memory engine snapshot (facts, provenance, vectors)\n"
                       "journal.jsonl   — conversations by day\n\n"
                       "Restore: put both files back into the diary folder and restart.\n"
                       "NOT ENCRYPTED — treat this file as the diary itself.\n")
        tmp.replace(dest / name)              # атомарно: обрыв не оставит битый архив
    except Exception as e:
        try: tmp.unlink()
        except Exception: pass
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    old = sorted(dest.glob("diary_*.zip"))
    for p in old[:-KEEP]:
        try: p.unlink()
        except Exception: pass

    size = (dest / name).stat().st_size
    return {"ok": True, "file": str(dest / name), "size": size,
            "kept": len(sorted(dest.glob("diary_*.zip")))}


def last(dest_dir: str | Path) -> dict | None:
    dest = Path(os.path.expanduser(str(dest_dir)))
    if not dest.exists():
        return None
    files = sorted(dest.glob("diary_*.zip"))
    if not files:
        return None
    f = files[-1]
    return {"file": str(f), "size": f.stat().st_size,
            "when": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)),
            "count": len(files)}


def restore(zip_path: str | Path, dest_dir: Path) -> dict:
    """Вернуть дневник из копии. Текущие файлы отодвигаются, а не затираются."""
    z = Path(os.path.expanduser(str(zip_path)))
    dest_dir = Path(dest_dir)
    if not z.exists():
        return {"ok": False, "error": "backup file not found"}
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        for name in ("diary.hsam.json", "journal.jsonl"):
            cur = dest_dir / name
            if cur.exists():
                shutil.move(str(cur), str(dest_dir / f"{name}.before_restore_{stamp}"))
        with zipfile.ZipFile(z) as arc:
            for name in ("diary.hsam.json", "journal.jsonl"):
                if name in arc.namelist():
                    arc.extract(name, dest_dir)
        return {"ok": True, "note": "restart the diary so the memory is reloaded"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
