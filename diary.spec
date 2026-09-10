# PyInstaller: один исполняемый файл с дневником внутри.
#
# Что кладём: код, страницу с оформлением и фоном, и библиотеку HSAM под нужную
# платформу. Torch и модели не нужны — векторы считает Gemini, память считает
# движок на Rust.
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
    console=False,           # окна консоли быть не должно: это личная вещь, не утилита
    onefile=True,
    disable_windowed_traceback=False,
    icon="diary/lib/diary.ico",
)
