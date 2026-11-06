from iped_mcp.core import topics, tool


def test_to_query_rules() -> None:
    q = topics.to_query
    assert q("presídio") == "presidi*" and q("foragido") == "foragid*"
    assert q("fuga") == "fuga"  # curta: sem radical automático
    assert q("fug*") == "fug*" and q("Fugi?") == "fugi?"
    assert q("quebra de segurança") == '"quebra de seguranca"'
    assert q('"fuga de presos"') == '"fuga de presos"'
    assert q("presídio", expand=False) == "presidio" and q("  ") == ""


def test_topic_search_per_term_and_union(monkeypatch) -> None:
    calls: list[str] = []

    def fake(query: str, source_id: str, limit: int = 50, extra_filters=None) -> dict:
        calls.append(query)
        hits = {"fug*": [1, 2], "presidi*": [2, 3]}
        if query in hits:
            return {"total": len(hits[query]), "by_source": {source_id: len(hits[query])}, "ids": hits[query], "truncated": False}
        if query.startswith("(") and "fug*" in query:  # consulta da união
            return {"total": 3, "by_source": {source_id: 3}, "ids": [1, 2, 3], "truncated": False}
        if query == "boom":
            raise RuntimeError("falhou")
        return {"total": 0, "by_source": {}, "ids": [], "truncated": False}

    monkeypatch.setattr(tool, "search", fake)
    res = topics.topic_search("S1", ["fug*", "presídio", "tunel", "boom", "fug*"])
    assert res["terms_with_hits"] == ["fug*", "presídio"]
    assert res["terms_without_hits"] == ["tunel"]
    assert res["terms_failed"] == ["boom"]
    assert res["union"]["total"] == 3 and res["union"]["ids"] == [1, 2, 3]
    assert [r["query"] for r in res["queries"]].count("fug*") == 1  # duplicado removido
    assert "ausência" in res["note"]
