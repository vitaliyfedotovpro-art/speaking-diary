"""Backups of the diary.

The diary is two files: the HSAM memory snapshot and the journal of conversations.
Both are text, both are small (a thousand entries ≈ a few megabytes), so a copy is
made simply and whole, without incremental cleverness.

Where to: any folder on disk. An external SSD is an ordinary folder under /Volumes
(macOS) or on a drive letter (Windows). The cloud is a sync folder that iCloud,
Dropbox or Google Drive already keeps: writing a file into it is more reliable than
calling somebody's API, and needs neither keys nor trust in one more service.

⚠️ A backup is an ordinary file with personal entries. There is no encryption here:
if the folder is in the cloud, whoever has access to the cloud sees the contents.
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

KEEP = 20          # how many copies to keep; older ones delete themselves


def _candidates() -> list[dict]:
    """Where to offer writing — only what actually exists on this machine."""
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
            # on macOS Google Drive lives inside CloudStorage as its own folder
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
    """One copy: a zip with the date in its name. Returns what came of it."""
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
        tmp.replace(dest / name)              # atomic: an interruption leaves no broken archive
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
    """Bring the diary back from a copy. Current files are moved aside, not overwritten."""
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
