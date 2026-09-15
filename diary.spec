# PyInstaller: a single executable with the diary inside.
#
# What goes in: the code, the page with its styling and background, and the HSAM
# library for this platform. Torch and models are not needed — Gemini computes the
# vectors, and the memory engine is Rust.
import platform
from pathlib import Path

LIB = {"Darwin": "libastrum_memory.dylib",
       "Windows": "astrum_memory.dll"}.get(platform.system(), "libastrum_memory.so")

datas = [("diary/web", "diary/web")]
libpath = Path("diary/lib") / LIB
if libpath.exists():
    datas.append((str(libpath), "diary/lib"))

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    datas=datas,
    hiddenimports=["httpx", "numpy", "websockets", "websockets.asyncio.server"],
    excludes=["torch", "tensorflow", "matplotlib", "PIL", "scipy", "pandas",
              "sentence_transformers", "transformers", "chromadb"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="Diary",
    console=False,           # no console window: this is a personal thing, not a utility
    onefile=True,
    disable_windowed_traceback=False,
    icon="diary/lib/diary.ico",
)
