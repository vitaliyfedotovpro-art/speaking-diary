"""Обёртка над Astrum HSAM — движком памяти, ради которого всё и затевалось.

Это НЕ векторная база с прикрученной моделью. HSAM делает две вещи, которых
векторный поиск не делает в принципе, и обе измерены на живом железе:

· КАРАНТИН САМООПИСАНИЙ. Факт, помеченный как «модель сказала о себе», исключается
  из выдачи целиком, а не понижается в ранге. Иначе через месяц дневник цитирует
  собственные догадки как биографию человека. Замер: загрязнение 66.6% → 0%.

· КАНОН ПЕРЕЖИВАЕТ ДАВЛЕНИЕ. Правила и ограничения — ровно то, что LRU выбрасывает
  первым, потому что их редко перечитывают. Канон-узлы не вытесняются никогда.
  Замер: 100% сохранности против 0% у политик по свежести и по частоте.

Векторы приходят снаружи (их считает Gemini) — HSAM их только хранит и ищет.
"""
from __future__ import annotations

import ctypes
import json
import platform
from pathlib import Path

# Провенанс: ось, по которой движок отделяет сказанное человеком от сочинённого моделью.
SRC_USER = 0        # сказал человек — высшее доверие
SRC_LLM = 1         # сочинила модель (о мире)
SRC_SELF = 2        # 🔒 модель о самой себе — В КАРАНТИНЕ, в выдачу не попадает
SRC_DOC = 3         # внешний документ
SRC_VERIFIED = 4    # проверено инструментом
SRC_LEGACY = 5      # происхождение неизвестно

CANON_NONE = 0
CANON_PROJECT = 1       # не вытесняется под давлением
CANON_FOUNDATIONAL = 2  # то же, верхний уровень


def _libname() -> str:
    return {"Darwin": "libastrum_memory.dylib",
            "Windows": "astrum_memory.dll"}.get(platform.system(), "libastrum_memory.so")


class Hsam:
    def __init__(self, snapshot: str | Path | None = None, lib_dir: str | Path | None = None):
        d = Path(lib_dir) if lib_dir else Path(__file__).parent / "lib"
        self._lib = ctypes.CDLL(str(d / _libname()))
        self._bind()
        self.snapshot = Path(snapshot) if snapshot else None
        if self.snapshot and self.snapshot.exists():
            h = self._lib.astrum_memory_load(str(self.snapshot).encode())
            self._h = h if h else self._lib.astrum_memory_create()
        else:
            self._h = self._lib.astrum_memory_create()
        if not self._h:
            raise RuntimeError("HSAM: could not start the memory engine")

    def _bind(self) -> None:
        L, P, F = self._lib, ctypes.c_void_p, ctypes.POINTER(ctypes.c_float)
        L.astrum_memory_create.restype = P
        L.astrum_memory_load.argtypes = [ctypes.c_char_p]; L.astrum_memory_load.restype = P
        L.astrum_memory_destroy.argtypes = [P]
        L.astrum_memory_node_count.argtypes = [P]; L.astrum_memory_node_count.restype = ctypes.c_size_t
        L.astrum_memory_add_node.argtypes = [P, ctypes.c_char_p, ctypes.c_char_p,
                                             ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8,
                                             F, ctypes.c_size_t]
        L.astrum_memory_add_node.restype = ctypes.c_void_p
        L.astrum_memory_search.argtypes = [P, F, ctypes.c_size_t, ctypes.c_uint8, ctypes.c_size_t]
        L.astrum_memory_search.restype = ctypes.c_void_p
        L.astrum_memory_save.argtypes = [P, ctypes.c_char_p]; L.astrum_memory_save.restype = ctypes.c_int32
        L.astrum_memory_record_feedback.argtypes = [P, ctypes.c_char_p, ctypes.c_int32]
        L.astrum_memory_record_feedback.restype = ctypes.c_int32
        L.astrum_memory_enforce_capacity.argtypes = [P, ctypes.c_size_t]
        L.astrum_memory_enforce_capacity.restype = ctypes.c_size_t
        L.astrum_memory_free_string.argtypes = [ctypes.c_void_p]
        L.astrum_memory_version.restype = ctypes.c_void_p

    def _take(self, ptr) -> str:
        """Забрать строку из движка и сразу вернуть ему память."""
        if not ptr:
            return ""
        try:
            return ctypes.cast(ptr, ctypes.c_char_p).value.decode("utf-8", "replace")
        finally:
            self._lib.astrum_memory_free_string(ptr)

    # ── запись ────────────────────────────────────────────────────────────
    def add(self, content: str, vec=None, source: int = SRC_USER,
            tags: list[str] | None = None, cell: int = 2, canon: int = CANON_NONE) -> str:
        arr = None
        n = 0
        if vec is not None:
            n = len(vec)
            arr = (ctypes.c_float * n)(*[float(x) for x in vec])
        return self._take(self._lib.astrum_memory_add_node(
            self._h, content.encode(), (",".join(tags or [])).encode(),
            ctypes.c_uint8(source), ctypes.c_uint8(cell), ctypes.c_uint8(canon),
            arr, ctypes.c_size_t(n)))

    def feedback(self, node_id: str, helpful: bool) -> int:
        """ТОЛЬКО по решению человека. Вердикт, взятый из собственного цикла
        («модель это использовала, значит хорошо»), возвращает вывод модели
        обратно как доказательство — ровно то, против чего движок и сделан."""
        return self._lib.astrum_memory_record_feedback(self._h, node_id.encode(), 1 if helpful else 0)

    # ── чтение ────────────────────────────────────────────────────────────
    def search(self, vec, top_k: int = 8, cell: int = 2) -> list[dict]:
        n = len(vec)
        arr = (ctypes.c_float * n)(*[float(x) for x in vec])
        raw = self._take(self._lib.astrum_memory_search(
            self._h, arr, ctypes.c_size_t(n), ctypes.c_uint8(cell), ctypes.c_size_t(top_k)))
        if not raw:
            return []
        try:
            out = json.loads(raw)
        except Exception:
            return []
        return out if isinstance(out, list) else out.get("results", [])

    def count(self) -> int:
        return int(self._lib.astrum_memory_node_count(self._h))

    def enforce_capacity(self, max_nodes: int) -> int:
        """Сброс под давлением. Канон переживёт, даже если узлов останется больше."""
        return int(self._lib.astrum_memory_enforce_capacity(self._h, max_nodes))

    def version(self) -> str:
        return self._take(self._lib.astrum_memory_version())

    def save(self, path: str | Path | None = None) -> bool:
        p = Path(path) if path else self.snapshot
        if not p:
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        return self._lib.astrum_memory_save(self._h, str(p).encode()) == 0

    def close(self) -> None:
        if getattr(self, "_h", None):
            self._lib.astrum_memory_destroy(self._h)
            self._h = None

    def __enter__(self): return self
    def __exit__(self, *a): self.close()
