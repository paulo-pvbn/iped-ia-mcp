"""Busca temática: dá a modelos locais pequenos o que eles não fazem sozinhos.

O `search` do IPED casa a palavra EXATA (fuga não casa fugiu; presos não casa presídio). Um modelo pequeno
costuma tentar uma frase literal, receber 0 e concluir que "não há nada". Estas duas tools evitam isso:

- `topic_terms(topic)`: famílias de termos (português/inglês, com radicais `radical*`) para temas comuns.
  São HIPÓTESES de busca, não fatos sobre o caso.
- `topic_search(source_id, terms)`: roda CADA termo como consulta separada (com radical automático em
  palavras longas) e informa a contagem por termo e a união; zero resultado não prova ausência.
"""

from __future__ import annotations

import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Any

MAX_TERMS = 25

# tema -> (palavras que o identificam, termos). `x*` = radical (prefixo); "a b" = frase exata.
TOPICS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "fuga_presidio": (
        ("fuga", "fugir", "evasao", "evadir", "presidio", "penitenciaria", "resgate", "foragido", "prisao", "cadeia"),
        ("fug*", "foragid*", "evad*", "evasao", "resgat*", "rebeli*", "motim", "presid*", "penitenciari*", "cadeia",
         "cela", "detent*", "apenad*", "preso*", "tunel", "agente penitenciario",
         '"quebra de seguranca"', "escape", "jailbreak", "prison break", "inmate*", "escap*"),
    ),
    "drogas": (
        ("droga", "drogas", "trafico", "entorpecente", "maconha", "cocaina", "narcotrafico"),
        ("droga*", "trafic*", "entorpecent*", "maconha", "cocain*", "crack", "pasta base", "skunk", "haxixe", "lsd",
         "ecstasy", "mdma", "metanfetamina", "boca de fumo", "biqueira", "papelote*",
         "cannabis", "weed", "cocaine", "dealer", "narco*"),
    ),
    "armas": (
        ("arma", "armas", "fuzil", "pistola", "munição", "municao", "revolver"),
        ("arma*", "pistola*", "revolver*", "fuzil*", "espingarda*", "municao", "municoes", "calibre", "carregador*",
         "glock", "taurus", "ak47", "ak-47", "silenciador", "colete", "explosiv*", "granada*", "gun*", "rifle*", "ammo"),
    ),
    "criptoativos": (
        ("cripto", "bitcoin", "carteira", "wallet", "exchange", "usdt", "blockchain"),
        ("cripto*", "bitcoin", "btc", "ethereum", "eth", "usdt", "tether", "binance", "wallet", "carteira digital",
         "chave privada", "private key", "blockchain", "exchange", "mercado bitcoin", "corretora"),
    ),
    "fraude_golpe": (
        ("fraude", "golpe", "estelionato", "phishing", "laranja", "clonagem", "boleto"),
        ("fraud*", "golp*", "estelionat*", "phishing", "laranja", "conta laranja", "clon*", "boleto*", "falsific*",
         "falso*", "invasao", "chargeback", "scam", "carding", "cartao clonado"),
    ),
    "ameaca_extorsao": (
        ("ameaca", "extorsao", "coacao", "chantagem", "intimidacao", "sequestro"),
        ("ameac*", "extors*", "coac*", "chantag*", "intimid*", "sequestr*", "vou te matar", "vou acabar", "te pego",
         "resgate", "threat*", "blackmail", "ransom*", "extort*"),
    ),
    "violencia_homicidio": (
        ("homicidio", "matar", "morte", "assassinato", "violencia", "agressao", "feminicidio"),
        ("homicid*", "assassin*", "matar", "morte", "morreu", "cadaver", "facada*", "tiro*", "baleado*",
         "agress*", "feminicid*", "espanc*", "kill*", "murder*"),
    ),
    "corrupcao_lavagem": (
        ("corrupcao", "propina", "lavagem", "suborno", "caixa dois", "desvio"),
        ("corrup*", "propin*", "suborn*", "lavagem", "lavar", "caixa dois", "caixa 2", "desvi*", "superfaturad*",
         "laranja", "offshore", "doleiro*", "dolar*", "dinheiro vivo", "bribe*", "launder*", "kickback"),
    ),
}


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
    stem = word[:-1] if word[-1] in "aeos" else word  # presidio -> presidi*, foragido -> foragid*
    return f"{stem}*" if len(stem) >= 4 else word


def topic_terms(topic: str) -> dict[str, Any]:
    """Famílias de termos para um tema (português e inglês, radicais `x*`). São hipóteses de busca, não fatos.

    Use ANTES de buscar um tema: passe o resultado para topic_search. Se o tema não estiver no catálogo,
    devolve os temas disponíveis; então invente você mesmo 10 a 20 termos (sinônimos, gírias, inglês, radicais).

    Args:
        topic: tema em linguagem livre (ex.: "fuga de presídio", "tráfico de drogas", "criptoativos").
    """
    wanted = fold(topic)
    hits = [name for name, (triggers, _t) in TOPICS.items() if any(t in wanted for t in triggers)]
    if not hits:
        return {
            "matched_topics": [],
            "terms": [],
            "available_topics": sorted(TOPICS),
            "note": "Tema fora do catálogo: monte 10 a 20 termos (sinônimos, gírias, inglês, radicais com *) e use topic_search.",
        }
    terms: list[str] = []
    for name in hits:
        terms += [t for t in TOPICS[name][1] if t not in terms]
    return {
        "matched_topics": hits,
        "terms": terms[:MAX_TERMS],
        "note": "Hipóteses de busca, não fatos sobre o caso. Passe para topic_search; leia os itens (doc_info/doc_text) antes de afirmar algo.",
    }


def topic_search(
    source_id: str,
    terms: list[str],
    expand: bool = True,
    limit_per_term: int = 5,
    extra_filters: list[str] | None = None,
) -> dict[str, Any]:
    """Busca vários termos de um tema de uma vez: cada termo é uma consulta separada, com contagem por termo.

    Resolve o problema do search exato (fuga não casa fugiu): palavras longas viram radical (presidio ->
    presidi*), "duas palavras" vira frase exata e x* / x? são respeitados. Informa quais termos acharam e
    quais não, e o total da união. Zero resultado só vale para as consultas executadas; não prova ausência.
    Em seguida leia os melhores itens com iped_doc_info e iped_doc_text antes de afirmar qualquer coisa.

    Args:
        source_id: source no IPED (veja iped_list_sources).
        terms: até 25 termos (use o resultado de iped_topic_terms ou os seus próprios sinônimos).
        expand: se true, palavras de 5+ letras ganham radical automático.
        limit_per_term: IDs de amostra por termo (1 a 20).
        extra_filters: filtros de campo para todos os termos (ex.: ["ext:txt"]).
    """
    from iped_mcp.core.tool import search

    if not terms:
        raise ValueError("'terms' não pode ser vazio (use iped_topic_terms ou liste sinônimos).")
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
            res = search(query, source_id, limit=limit_per_term, extra_filters=extra_filters)
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
            union_res = search(" ".join(f"({q})" for q in good_queries), source_id, limit=20, extra_filters=extra_filters)
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
