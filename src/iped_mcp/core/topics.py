"""Busca de vários termos de uma vez, sem vocabulário pré-definido.

O `search` do IPED casa a palavra EXATA (comprar não casa comprou). Um modelo pequeno
costuma tentar uma frase literal, receber 0 e concluir que "não há nada". `topic_search` roda CADA termo que o
próprio modelo escolheu como consulta separada (com radical automático em palavras longas) e informa a contagem
por termo e a união. Não há lista de temas nem de sinônimos: quem decide o que procurar é o modelo/analista.
"""

from __future__ import annotations

import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Any

MAX_TERMS = 25

def fold(text: str) -> str:
    """Minúsculas e sem acentos (o IPED indexa sem acentos; curingas não passam pelo analisador)."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def to_query(term: str, expand: bool = True) -> str:
    """Um termo -> consulta Lucene: frase entre aspas, curingas preservados, palavra longa ganha radical."""
    raw = term.strip()
    if not raw:
        return ""
    if raw.startswith('"') and raw.endswith('"') and len(raw) > 2:
        return '"' + fold(raw.strip('"')) + '"'
    if " " in raw:
        return '"' + fold(raw) + '"'
    word = fold(raw)
    if "*" in word or "?" in word or "~" in word:
        return word
    if not expand or not re.fullmatch(r"[a-z0-9]+", word) or len(word) < 5:
        return word
    stem = word[:-1] if word[-1] in "aeos" else word  # ex.: cidade -> cidad*, ferramentas -> ferramenta*
    return f"{stem}*" if len(stem) >= 4 else word


def topic_search(
    source_id: str,
    terms: list[str],
    expand: bool = True,
    limit_per_term: int = 5,
    extra_filters: list[str] | None = None,
) -> dict[str, Any]:
    """Busca vários termos de um tema de uma vez: cada termo é uma consulta separada, com contagem por termo.

    Resolve o problema do search exato (comprar não casa comprou): palavras longas viram radical (comprou ->
    compro*), "duas palavras" vira frase exata e x* / x? são respeitados. Informa quais termos acharam e
    quais não, e o total da união. Zero resultado só vale para as consultas executadas; não prova ausência.
    Escolha o vocabulário você mesmo, pensando como um investigador; depois leia os melhores itens com
    iped_doc_info e iped_doc_text e siga as pistas (nomes, números, lugares, datas) antes de afirmar algo.

    Args:
        source_id: source no IPED (veja iped_list_sources).
        terms: até 25 termos que VOCÊ escolheu (flexões, sinônimos, gírias, inglês, radicais com *).
        expand: se true, palavras de 5+ letras ganham radical automático.
        limit_per_term: IDs de amostra por termo (1 a 20).
        extra_filters: filtros de campo para todos os termos (ex.: ["ext:txt"]).
    """
    from iped_mcp.core.tool import search

    if not terms:
        raise ValueError("'terms' não pode ser vazio (liste os termos que você escolheu: flexões, sinônimos, gírias, inglês).")
    if not 1 <= limit_per_term <= 20:
        raise ValueError("limit_per_term deve estar entre 1 e 20.")
    pairs: list[tuple[str, str]] = []
    for term in terms[:MAX_TERMS]:
        query = to_query(term, expand)
        if query and query not in (q for _t, q in pairs):
            pairs.append((term, query))

    def run(pair: tuple[str, str]) -> dict[str, Any]:
        term, query = pair
        try:
            res = search(query, source_id, limit=limit_per_term, extra_filters=extra_filters, preview=0)
            return {"term": term, "query": query, "status": "ok", "total": res["total"], "ids": res["ids"]}
        except Exception as exc:  # noqa: BLE001
            return {"term": term, "query": query, "status": "erro", "error": str(exc)[:200]}

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(run, pairs))
    ok = [r for r in rows if r["status"] == "ok"]
    union: dict[str, Any] = {"total": 0, "ids": [], "by_source": {}}
    good_queries = [r["query"] for r in ok if r["total"]]
    if good_queries:
        try:
            union_res = search(" ".join(f"({q})" for q in good_queries), source_id, limit=20, extra_filters=extra_filters, preview=0)
            union = {"total": union_res["total"], "ids": union_res["ids"], "by_source": union_res["by_source"]}
        except Exception as exc:  # noqa: BLE001
            union = {"error": str(exc)[:200]}
    return {
        "source_id": source_id,
        "queries": rows,
        "terms_with_hits": [r["term"] for r in ok if r["total"]],
        "terms_without_hits": [r["term"] for r in ok if not r["total"]],
        "terms_failed": [r["term"] for r in rows if r["status"] != "ok"],
        "union": union,
        "note": "Zero resultado vale só para as consultas executadas e não prova ausência. Leia os itens (iped_doc_info/iped_doc_text) antes de concluir.",
    }
