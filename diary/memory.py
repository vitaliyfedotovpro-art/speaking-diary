"""Память дневника: HSAM + векторы Gemini.

Фасад, чтобы остальной код не знал ни про ctypes, ни про то, как считаются
эмбеддинги. Здесь же живёт правило, которое отличает дневник от чата:
что записывается человеком, а что — догадкой модели, и как это помечается
в промпте.
"""
from __future__ import annotations

from pathlib import Path

from . import embed as _embed
from .hsam import (Hsam, SRC_USER, SRC_LLM, SRC_SELF, SRC_VERIFIED,
                   CANON_NONE, CANON_FOUNDATIONAL)

_MARK = {0: "⟨your words⟩", 1: "⟨my guess⟩", 2: "⟨about myself⟩",
         3: "⟨document⟩", 4: "⟨verified⟩", 5: ""}


class Memory:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.h = Hsam(snapshot=self.path)

    def remember(self, text: str, source: int = SRC_USER,
                 tags: list[str] | None = None, canon: int = CANON_NONE) -> str:
        """Записать факт. source решает, попадёт ли он когда-нибудь в выдачу:
        SRC_SELF уходит в карантин навсегда — это защита от того, чтобы дневник
        начал пересказывать человеку его жизнь в собственной редакции."""
        text = (text or "").strip()
        if not text:
            return ""
        # Мусор от распознавания: обрывки в два слова без глагола становились
        # «фактами» — в дневнике завёлся «Кен — город в горах Альберты».
        if len(text) < 15 or len(text.split()) < 3:
            return ""
        try:
            vec = _embed.embed(text)
        except Exception:
            vec = None                      # без вектора факт хранится, но не ищется
        # Дубль по смыслу: одно и то же событие приходит разными формулировками
        # из разных ходов, и память заполняется пересказами самой себя.
        if vec is not None and source == SRC_USER:
            try:
                near = self.h.search(vec, top_k=3)
                for r in near:
                    if float(r.get("score", 0) or 0) >= 0.93:
                        return ""           # уже знаем это, второй раз не пишем
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
        return self.h.search(qv, top_k=n)

    def confirm(self, node_id: str, helpful: bool) -> None:
        """Вердикт ЧЕЛОВЕКА о том, к месту ли всплыло воспоминание. Влияет на то,
        как долго факт живёт под давлением, и никогда — на порядок выдачи."""
        self.h.feedback(node_id, helpful)
        self.h.save()

    def count(self) -> int:
        return self.h.count()

    def nodes_with_vectors(self) -> tuple[list[dict], dict[str, list[float]]]:
        """Всё содержимое памяти для опросника: узлы и их векторы.

        Читаем из снимка — C-ABI не умеет отдавать список целиком, а поиск
        требует вектор запроса и по нулевому не возвращает ничего.
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
        # index.vectors — список пар [id, вектор]; так его пишет движок
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
        """Факты, связанные с найденными — по подтверждённым человеком связям.
        Вектор такого не поднимет: связанное часто НЕ похоже."""
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
        mark = _MARK.get(int(r.get("source_type", 5) or 5), "")
        out.append(f"  · {mark} {(r.get('content') or '')[:600]}")
    return "\n".join(out)
