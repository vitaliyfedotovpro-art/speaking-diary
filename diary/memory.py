"""Память дневника: HSAM + векторы Gemini.

Фасад, чтобы остальной код не знал ни про ctypes, ни про то, как считаются
эмбеддинги. Здесь же живёт правило, которое отличает дневник от чата:
что записывается человеком, а что — догадкой модели, и как это помечается
в промпте.
"""
from __future__ import annotations

from pathlib import Path

from . import embed as _embed
from .hsam import (Hsam, SRC_USER, SRC_LLM, SRC_SELF, SRC_DOC, SRC_VERIFIED,
                   SRC_LEGACY, CANON_NONE, CANON_FOUNDATIONAL)

_MARK = {SRC_USER: "⟨your words⟩", SRC_LLM: "⟨my guess⟩", SRC_SELF: "⟨about myself⟩",
         SRC_DOC: "⟨document⟩", SRC_VERIFIED: "⟨verified⟩", SRC_LEGACY: ""}

# Движок хранит провенанс ЧИСЛОМ, а в снимок кладёт ИМЕНЕМ. Имена сняты с самого
# движка прогоном, а не выведены из констант: source 0..5 → эти строки.
_SRC_NAMES = {"user_utterance": SRC_USER, "llm_generation": SRC_LLM,
              "llm_self_description": SRC_SELF, "external_doc": SRC_DOC,
              "verified_external": SRC_VERIFIED, "unknown_legacy": SRC_LEGACY}


def source_of(row: dict) -> int:
    """Провенанс в одном виде, откуда бы строка ни пришла — из снимка или из поиска."""
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
        return self._with_provenance(self.h.search(qv, top_k=n))

    def _source_map(self) -> dict[str, str]:
        """id → провенанс, из снимка. Перечитываем только когда снимок изменился:
        иначе разбор всего файла ложился бы на каждый ход разговора."""
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
        """🔴 Поиск через C-ABI отдаёт только node_id, content, cell_id, cosine и
        score — провенанса в нём НЕТ (так написано и в заголовке движка). Из-за
        этого пометки ⟨your words⟩/⟨my guess⟩ не проставлялись никогда, хотя промпт
        велит модели на них опираться: она получала голый список без источников.
        Достаём провенанс из снимка по node_id и заодно кладём id, на который
        рассчитывают связи."""
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
        mark = _MARK.get(source_of(r), "")
        out.append(f"  · {mark} {(r.get('content') or '')[:600]}")
    return "\n".join(out)
