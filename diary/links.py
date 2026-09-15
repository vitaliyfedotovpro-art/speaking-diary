"""Connections between facts, and the questionnaire that finds them.

Why. Memory accumulates facts one at a time, and half a year later it is a heap of
unconnected entries: "fell out with my brother", "took extra work", "sleeping badly"
— each on its own, yet together they are a story. Vector search will not find such
links: it looks for the similar, and the connected is often NOT similar.

Building edges automatically is not an option — the model would invent connections
that do not exist, and they would become "facts". So a human confirms the link: the
diary notices pairs that MIGHT be connected, and asks. A "no" is valuable too — the
same pair is not asked about twice.

⚠️ The engine's C-ABI only knows nodes; edges cannot be added from outside. So links
live in their own file next to the snapshot and are mixed in during recall.
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path
from threading import Lock

# Candidates are looked for in the MIDDLE band of similarity: pairs that are too close
# are connected obviously ("booked a doctor" / "went to the doctor") — asking about
# them is silly. Pairs too far apart are not connected at all. The interesting ones
# lie in between.
NEAR = 0.72     # above this it is obvious, we do not ask
FAR = 0.38      # below this a connection is unlikely
ASK_EVERY = 6   # no more often than once in this many turns: the questionnaire must not nag


class Links:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self.turns_since_ask = 0

    def _rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
        return out

    def _append(self, row: dict) -> None:
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def asked_pairs(self) -> set[tuple[str, str]]:
        """What has already been asked about — including the answers "not connected"."""
        return {tuple(sorted((r["a"], r["b"]))) for r in self._rows()}

    def links_of(self, node_id: str) -> list[dict]:
        return [r for r in self._rows()
                if r.get("kind") == "linked" and node_id in (r.get("a"), r.get("b"))]

    def save_answer(self, a: str, b: str, linked: bool, note: str = "",
                    a_text: str = "", b_text: str = "") -> None:
        self._append({"a": a, "b": b, "kind": "linked" if linked else "unrelated",
                      "note": note.strip()[:300], "a_text": a_text[:200],
                      "b_text": b_text[:200], "ts": time.time()})

    # ── finding a pair worth asking about ─────────────────────────────────
    def candidate(self, nodes: list[dict], vectors: dict[str, list[float]]) -> dict | None:
        """A pair of facts from the middle band of similarity, not yet asked about.

        nodes: [{id, content, ...}], vectors: id → vector (normalised).
        """
        import numpy as np

        ids = [n["id"] for n in nodes if n.get("id") in vectors and n.get("content")]
        if len(ids) < 4:
            return None
        asked = self.asked_pairs()
        by_id = {n["id"]: n for n in nodes}
        mat = np.vstack([np.asarray(vectors[i], dtype=np.float32) for i in ids])
        sims = mat @ mat.T

        pool = []
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                s = float(sims[i][j])
                if not (FAR < s < NEAR):
                    continue
                if tuple(sorted((ids[i], ids[j]))) in asked:
                    continue
                pool.append((s, ids[i], ids[j]))
        if not pool:
            return None
        # closer to the upper bound means a meaningful link is likelier, but we pick
        # with some spread so the questionnaire does not hammer the same theme
        pool.sort(key=lambda t: -t[0])
        s, a, b = random.choice(pool[:max(3, len(pool) // 4)])
        return {"a": a, "b": b, "similarity": round(s, 3),
                "a_text": by_id[a]["content"], "b_text": by_id[b]["content"]}

    def due(self) -> bool:
        return self.turns_since_ask >= ASK_EVERY

    def tick(self) -> None:
        self.turns_since_ask += 1

    def reset(self) -> None:
        self.turns_since_ask = 0


QUESTION_PROMPT = """Two things from a person's diary:

A: {a}
B: {b}

Ask them ONE short question about whether these two are connected.

LANGUAGE: write the question in the SAME language as entries A and B above. If they
are in Russian, the question must be in Russian. If in English — in English. Do not
translate, do not switch. This is their diary, not yours.

Not "are A and B related?" — ask like someone who noticed something and got curious.
One sentence, no preamble, question only.

If the connection would be obvious to anyone, or asking would be intrusive or
tactless, answer exactly: SKIP"""
