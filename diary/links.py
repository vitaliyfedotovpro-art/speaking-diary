"""Связи между фактами и опросник, который их выясняет.

Зачем. Память копит факты по одному, и через полгода это груда несвязанных
записей: «поссорился с братом», «взял подработку», «плохо сплю» — каждая сама по
себе, а вместе они история. Векторный поиск таких связей не найдёт: он ищет
похожее, а связанное часто НЕ похоже.

Автоматически строить рёбра нельзя — модель напридумывает связей, которых нет,
и они станут «фактами». Поэтому связь подтверждает человек: дневник замечает
пары, которые МОГУТ быть связаны, и спрашивает. Ответ «нет» тоже ценен —
второй раз про эту пару не спросят.

⚠️ C-ABI движка умеет только узлы, рёбра снаружи не добавить. Поэтому связи
живут своим файлом рядом со снимком и подмешиваются при recall.
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path
from threading import Lock

# Кандидаты ищем в СРЕДНЕЙ зоне похожести: слишком близкие пары связаны очевидно
# («записался к врачу» / «сходил к врачу») — спрашивать про них глупо. Слишком
# далёкие не связаны никак. Интересное лежит между.
NEAR = 0.72     # выше — очевидно, не спрашиваем
FAR = 0.38      # ниже — вряд ли связано
ASK_EVERY = 6   # не чаще, чем раз в столько ходов: опросник не должен надоедать


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
        """Про что уже спрашивали — включая ответы «не связано»."""
        return {tuple(sorted((r["a"], r["b"]))) for r in self._rows()}

    def links_of(self, node_id: str) -> list[dict]:
        return [r for r in self._rows()
                if r.get("kind") == "linked" and node_id in (r.get("a"), r.get("b"))]

    def save_answer(self, a: str, b: str, linked: bool, note: str = "",
                    a_text: str = "", b_text: str = "") -> None:
        self._append({"a": a, "b": b, "kind": "linked" if linked else "unrelated",
                      "note": note.strip()[:300], "a_text": a_text[:200],
                      "b_text": b_text[:200], "ts": time.time()})

    # ── поиск пары, о которой стоит спросить ──────────────────────────────
    def candidate(self, nodes: list[dict], vectors: dict[str, list[float]]) -> dict | None:
        """Пара фактов из средней зоны похожести, о которой ещё не спрашивали.

        nodes: [{id, content, ...}], vectors: id → вектор (нормированный).
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
        # ближе к верхней границе — вероятнее осмысленная связь, но берём с разбросом,
        # чтобы опросник не долбил одну и ту же тему
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
