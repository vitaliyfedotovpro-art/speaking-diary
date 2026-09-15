"""A wrapper around Astrum HSAM — the memory engine this was all built for.

This is NOT a vector store with a model bolted on. HSAM does two things a vector
search does not do at all, and both were measured on real hardware:

· QUARANTINE OF SELF-DESCRIPTIONS. A fact marked "the model said this about itself"
  is excluded from recall entirely, not merely down-ranked. Otherwise, a month later
  the diary quotes its own guesses back as a person's biography. Measured:
  contamination 66.6% → 0%.

· CANON SURVIVES PRESSURE. Rules and constraints are exactly what an LRU policy
  throws out first, because they are rarely re-read. Canon nodes are never evicted.
  Measured: 100% retention against 0% for recency- and frequency-based policies.

Vectors come from outside (Gemini computes them) — HSAM only stores and searches them.
"""
from __future__ import annotations

import ctypes
import json
import platform
from pathlib import Path

# Provenance: the axis along which the engine separates what a person said from what
# a model invented.
SRC_USER = 0        # a person said it — highest trust
SRC_LLM = 1         # a model produced it (about the world)
SRC_SELF = 2        # 🔒 a model about itself — QUARANTINED, never reaches recall
SRC_DOC = 3         # an external document
SRC_VERIFIED = 4    # verified by a tool
SRC_LEGACY = 5      # origin unknown

CANON_NONE = 0
CANON_PROJECT = 1       # not evicted under pressure
CANON_FOUNDATIONAL = 2  # the same, top level


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
        """Take a string from the engine and hand the memory straight back."""
        if not ptr:
            return ""
        try:
            return ctypes.cast(ptr, ctypes.c_char_p).value.decode("utf-8", "replace")
        finally:
            self._lib.astrum_memory_free_string(ptr)

    # ── writing ───────────────────────────────────────────────────────────
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
        """ONLY on a human decision. A verdict taken from the model's own loop
        ("the model used it, so it was good") feeds the model's output back in as
        evidence — the very thing this engine exists to prevent."""
        return self._lib.astrum_memory_record_feedback(self._h, node_id.encode(), 1 if helpful else 0)

    # ── reading ───────────────────────────────────────────────────────────
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
        """Shedding under pressure. Canon survives even if more nodes remain than asked."""
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
