"""The diary's memory: HSAM plus Gemini vectors.

A facade, so the rest of the code knows nothing about ctypes or about how the
embeddings are computed. Here too lives the rule that separates a diary from a chat:
what was written by the person and what was a guess by the model, and how that is
marked in the prompt.
"""
from __future__ import annotations

from pathlib import Path

from . import embed as _embed
from .hsam import (Hsam, SRC_USER, SRC_LLM, SRC_SELF, SRC_DOC, SRC_VERIFIED,
                   SRC_LEGACY, CANON_NONE, CANON_FOUNDATIONAL)

_MARK = {SRC_USER: "⟨your words⟩", SRC_LLM: "⟨my guess⟩", SRC_SELF: "⟨about myself⟩",
         SRC_DOC: "⟨document⟩", SRC_VERIFIED: "⟨verified⟩", SRC_LEGACY: ""}

# The engine keeps provenance as a NUMBER, but writes it into the snapshot as a NAME.
# The names were taken from the engine by running it, not inferred from the
# constants: source 0..5 → these strings.
_SRC_NAMES = {"user_utterance": SRC_USER, "llm_generation": SRC_LLM,
              "llm_self_description": SRC_SELF, "external_doc": SRC_DOC,
              "verified_external": SRC_VERIFIED, "unknown_legacy": SRC_LEGACY}


def source_of(row: dict) -> int:
    """Provenance in one shape, wherever the row came from — the snapshot or the search."""
    v = row.get("source_type")
    if isinstance(v, str):
        return _SRC_NAMES.get(v, SRC_LEGACY)
    try:
        return int(v)
    except (TypeError, ValueError):
        return SRC_LEGACY


class Memory:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.h = Hsam(snapshot=self.path)
        self._src_cache: dict[str, str] = {}
        self._src_mtime: float = -1.0

    def remember(self, text: str, source: int = SRC_USER,
                 tags: list[str] | None = None, canon: int = CANON_NONE) -> str:
        """Write down a fact. `source` decides whether it will ever surface in recall:
        SRC_SELF goes into quarantine for good — that is the protection against the
        diary retelling a person their own life in its own edit."""
        text = (text or "").strip()
        if not text:
            return ""
        # Rubbish from speech recognition: two-word fragments without a verb became
        # "facts" — the diary once acquired "Ken is a town in the Alberta mountains".
        if len(text) < 15 or len(text.split()) < 3:
            return ""
        try:
            vec = _embed.embed(text)
        except Exception:
            vec = None                      # without a vector the fact is stored but not searchable
        # A duplicate by meaning: the same event arrives in different wordings from
        # different turns, and memory fills up with retellings of itself.
        if vec is not None and source == SRC_USER:
            try:
                near = self.h.search(vec, top_k=3)
                for r in near:
                    if float(r.get("score", 0) or 0) >= 0.93:
                        return ""           # we know this already, do not write it twice
            except Exception:
                pass
        nid = self.h.add(text, vec, source=source, tags=tags, canon=canon)
        self.h.save()
        return nid

    def recall(self, query: str, n: int = 8) -> list[dict]:
        if not query.strip() or self.h.count() == 0:
            return []
        try:
            qv = _embed.embed_query(query)
        except Exception:
            return []
        return self._with_provenance(self.h.search(qv, top_k=n))

    def _source_map(self) -> dict[str, str]:
        """id → provenance, from the snapshot. Re-read only when the snapshot has
        changed: otherwise parsing the whole file would fall on every turn of the
        conversation."""
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return {}
        if mtime == self._src_mtime:
            return self._src_cache
        import json as _j
        try:
            snap = _j.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return self._src_cache
        raw = (snap.get("nexus") or {}).get("nodes") or []
        nodes = list(raw.values()) if isinstance(raw, dict) else raw
        self._src_cache = {n["id"]: n.get("source_type") for n in nodes
                           if isinstance(n, dict) and n.get("id")}
        self._src_mtime = mtime
        return self._src_cache

    def _with_provenance(self, rows: list[dict]) -> list[dict]:
        """🔴 Search through the C-ABI returns only node_id, content, cell_id, cosine
        and score — provenance is NOT in it (the engine's own header says so). Because
        of that the ⟨your words⟩ / ⟨my guess⟩ marks were never emitted, even though the
        prompt instructs the model to rely on them: it received a bare list with no
        sources. We take provenance from the snapshot by node_id, and set the id that
        the links code expects while we are here."""
        if not rows:
            return rows
        src = self._source_map()
        for r in rows:
            nid = r.get("node_id") or r.get("id")
            if not nid:
                continue
            r.setdefault("id", nid)
            if nid in src:
                r["source_type"] = src[nid]
        return rows

    def confirm(self, node_id: str, helpful: bool) -> None:
        """A HUMAN's verdict on whether a memory surfaced to the point. It affects how
        long a fact survives under pressure, and never the order of results."""
        self.h.feedback(node_id, helpful)
        self.h.save()

    def count(self) -> int:
        return self.h.count()

    def nodes_with_vectors(self) -> tuple[list[dict], dict[str, list[float]]]:
        """Everything in memory, for the questionnaire: the nodes and their vectors.

        Read from the snapshot — the C-ABI cannot hand over the whole list, and search
        requires a query vector and returns nothing for a zero one.
        """
        import json as _j
        try:
            self.h.save()
            snap = _j.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return [], {}
        nx = snap.get("nexus") or {}
        raw = nx.get("nodes") or []
        nodes = list(raw.values()) if isinstance(raw, dict) else raw
        nodes = [n for n in nodes if isinstance(n, dict) and n.get("content")]
        # index.vectors is a list of [id, vector] pairs; that is how the engine writes it
        vecs: dict[str, list[float]] = {}
        box = (snap.get("index") or {}).get("vectors") or []
        if isinstance(box, dict):
            vecs = {k: v for k, v in box.items() if isinstance(v, list)}
        else:
            for it in box:
                if isinstance(it, (list, tuple)) and len(it) == 2 and isinstance(it[1], list):
                    vecs[str(it[0])] = it[1]
                elif isinstance(it, dict) and isinstance(it.get("vector"), list):
                    vecs[str(it.get("id"))] = it["vector"]
        return nodes, vecs

    def linked_context(self, rows: list[dict], links) -> list[dict]:
        """Facts connected to the ones found — through links a human confirmed.
        A vector will not raise these: the connected is often NOT similar."""
        if not rows or links is None:
            return []
        nodes, _ = self.nodes_with_vectors()
        by_id = {n.get("id"): n for n in nodes}
        seen = {r.get("id") for r in rows}
        out = []
        for r in rows:
            for l in links.links_of(r.get("id", "")):
                other = l["b"] if l["a"] == r.get("id") else l["a"]
                if other in seen or other not in by_id:
                    continue
                seen.add(other)
                n = dict(by_id[other])
                n["via_link"] = l.get("note", "")
                out.append(n)
        return out[:4]

    def close(self) -> None:
        self.h.save()
        self.h.close()


def format_for_prompt(rows: list[dict]) -> str:
    if not rows:
        return ""
    out = ["\n\nFROM THE DIARY (written earlier):"]
    for r in rows:
        mark = _MARK.get(source_of(r), "")
        out.append(f"  · {mark} {(r.get('content') or '')[:600]}")
    return "\n".join(out)
