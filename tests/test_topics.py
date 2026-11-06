from iped_mcp.core import topics, tool


def test_to_query_rules() -> None:
    q = topics.to_query
    assert q("relatório") == "relatori*" and q("entregue") == "entregu*"
    assert q("casa") == "casa"  # curta: sem radical automático
    assert q("cas*") == "cas*" and q("Cas?") == "fugi?"
    assert q("quebra de segurança") == '"quebra de seguranca"'
    assert q('"pasta compartilhada"') == '"pasta compartilhada"'
    assert q("relatório", expand=False) == "relatorio" and q("  ") == ""


def test_topic_terms_matches_theme() -> None:
    res = topics.topic_terms("reunião")
    assert "casa_relatorio" in res["matched_topics"] and "cas*" in res["terms"] and "relat*" in res["terms"]
    other = topics.topic_terms("assunto desconhecido xyz")
    assert other["terms"] == [] and "drogas" in other["available_topics"]
    assert len(topics.topic_terms("reunião e drogas")["terms"]) <= topics.MAX_TERMS


def test_topic_search_per_term_and_union(monkeypatch) -> None:
    calls: list[str] = []

    def fake(query: str, source_id: str, limit: int = 50, extra_filters=None) -> dict:
        calls.append(query)
        hits = {"cas*": [1, 2], "relatori*": [2, 3]}
        if query in hits:
            return {"total": len(hits[query]), "by_source": {source_id: len(hits[query])}, "ids": hits[query], "truncated": False}
        if query.startswith("(") and "cas*" in query:  # consulta da união
            return {"total": 3, "by_source": {source_id: 3}, "ids": [1, 2, 3], "truncated": False}
        if query == "boom":
            raise RuntimeError("falhou")
        return {"total": 0, "by_source": {}, "ids": [], "truncated": False}

    monkeypatch.setattr(tool, "search", fake)
    res = topics.topic_search("S1", ["cas*", "relatório", "tunel", "boom", "cas*"])
    assert res["terms_with_hits"] == ["cas*", "relatório"]
    assert res["terms_without_hits"] == ["tunel"]
    assert res["terms_failed"] == ["boom"]
    assert res["union"]["total"] == 3 and res["union"]["ids"] == [1, 2, 3]
    assert [r["query"] for r in res["queries"]].count("cas*") == 1  # duplicado removido
    assert "ausência" in res["note"]
