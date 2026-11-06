"""Pré-visualização dos resultados de uma busca: nome, caminho, data e o trecho onde o termo aparece.

Poupa ao modelo uma chamada `doc_info` (e muitas vezes um `doc_text`) por resultado: tudo é buscado aqui, no
servidor, e não passa pelo contexto do modelo. Não decide o que é relevante: só mostra onde a busca casou.
"""

from __future__ import annotations

import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Any

SKIP_SNIPPET_BYTES = 3_000_000  # arquivos muito grandes: sem trecho (a leitura custaria tempo)
_OPERATORS = {"and", "or", "not"}


def fold_text(text: str) -> str:
    """Minúsculas e sem acento, SEMPRE com o mesmo comprimento do original (para achar a posição do trecho)."""
    out = []
    for ch in text:
        base = unicodedata.normalize("NFD", ch)[:1].lower()
        out.append(base if len(base) == 1 else ch)
    return "".join(out)


def query_patterns(query: str) -> list[re.Pattern[str]]:
    """Palavras da consulta como padrões (curingas * e ? respeitados; operadores e filtros de campo ignorados)."""
    patterns = []
    for raw in re.findall(r'"[^"]*"|\S+', query):
        if raw.startswith("-") or ":" in raw and not raw.startswith('"'):
            continue  # exclusões e filtros campo:valor
        for word in re.split(r"[\s\"()&|@~^]+", raw):
            word = fold_text(word).lstrip("+")
            if len(word.replace("*", "").replace("?", "")) < 3 or word in _OPERATORS:
                continue
            body = "".join(r"\w*" if c == "*" else r"\w" if c == "?" else re.escape(c) for c in word)
            patterns.append(re.compile(rf"(?<!\w){body}"))
    return patterns


def snippet(text: str, patterns: list[re.Pattern[str]], width: int = 110) -> str:
    folded = fold_text(text)
    best: re.Match[str] | None = None
    for pat in patterns:
        m = pat.search(folded)
        if m and (best is None or m.start() < best.start()):
            best = m
    if best is None:
        return ""
    a, b = max(0, best.start() - width), min(len(text), best.end() + width)
    piece = re.sub(r"\s+", " ", text[a:b]).strip()
    return ("…" if a > 0 else "") + piece + ("…" if b < len(text) else "")


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else value


def brief(source_id: str, doc_id: int, patterns: list[re.Pattern[str]], router: Any, text_of: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": doc_id}
    size = 0
    try:
        props = router.get_document(source_id, doc_id).get("properties", {})
        for key, name in (("nome", "name"), ("caminho", "path"), ("data", "modified"), ("tipo", "contentType")):
            value = _first(props.get(name))
            if value in (None, "") and name == "modified":
                value = _first(props.get("created"))
            if value not in (None, ""):
                entry[key] = str(value)[:140]
        try:
            size = int(_first(props.get("size")) or 0)
        except (TypeError, ValueError):
            size = 0
    except Exception as exc:  # noqa: BLE001
        entry["erro"] = str(exc)[:80]
        return entry
    if patterns and size <= SKIP_SNIPPET_BYTES:
        try:
            found = snippet(text_of(source_id, doc_id), patterns)
        except Exception:  # noqa: BLE001, S110  (sem texto ou erro: segue sem trecho)
            found = ""
        if found:
            entry["trecho"] = found
    return entry


def previews(source_id: str, ids: list[int], query: str, router: Any, text_of: Any, workers: int = 4) -> list[dict[str, Any]]:
    patterns = query_patterns(query)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda i: brief(source_id, i, patterns, router, text_of), ids))
