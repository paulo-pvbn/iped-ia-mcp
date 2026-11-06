from iped_mcp.core import preview, tool


def test_query_patterns_ignore_operators_filters_and_exclusions() -> None:
    pats = preview.query_patterns('compra* AND "ação rápida" ext:pdf -ruido (casa casas)@1')
    shown = {p.pattern for p in pats}
    assert any("compra" in s for s in shown) and not any("ruido" in s or "pdf" in s or s == "and" for s in shown)


def test_snippet_finds_accented_word_with_prefix_and_marks_cuts() -> None:
    text = "x" * 300 + " Ele planejou a AÇÃO na sexta-feira e depois sumiu. " + "y" * 300
    out = preview.snippet(text, preview.query_patterns("acao"), width=20)
    assert "AÇÃO" in out and out.startswith("…") and out.endswith("…") and len(out) < 80
    assert preview.snippet(text, preview.query_patterns("planej*"), width=10).count("planejou") == 1
    assert preview.snippet(text, preview.query_patterns("inexistente")) == ""


def test_fold_keeps_length() -> None:
    s = "Ação ÇÃO ñ İ ß"
    assert len(preview.fold_text(s)) == len(s)


class FakeRouter:
    def get_document(self, source_id: str, doc_id: int) -> dict:
        if doc_id == 3:
            raise ValueError("doc inexistente")
        return {"properties": {"name": [f"arq{doc_id}.txt"], "path": f"/caso/arq{doc_id}.txt", "modified": "2026-01-02", "size": 100, "contentType": "text/plain"}}


def test_previews_collect_brief_and_snippet_and_survive_errors() -> None:
    texts = {1: "o preso planejou a saída pelo túnel", 2: "nada aqui"}
    out = preview.previews("S", [1, 2, 3], "tunel", FakeRouter(), lambda s, i: texts[i])
    assert out[0]["nome"] == "arq1.txt" and "túnel" in out[0]["trecho"] and out[0]["data"] == "2026-01-02"
    assert "trecho" not in out[1] and out[1]["caminho"].endswith("arq2.txt")
    assert "erro" in out[2] and out[2]["id"] == 3


def test_search_returns_hits_and_preview_zero_disables(monkeypatch) -> None:
    class R(FakeRouter):
        def search(self, q, source_id=None, timeout=None):
            return [{"source": "S", "ids": [1, 2, 4]}]

        def get_document_text(self, source_id, doc_id):
            return ("o túnel estava pronto", 21, False)

    monkeypatch.setattr(tool, "get_router", lambda: R())
    monkeypatch.setattr(tool, "text_cache_get", lambda *_: None)
    monkeypatch.setattr(tool, "text_cache_set", lambda *_: None)
    res = tool.search("tunel", "S", limit=10, preview=2)
    assert res["total"] == 3 and len(res["hits"]) == 2 and "túnel" in res["hits"][0]["trecho"]
    assert "hits" not in tool.search("tunel", "S", limit=10, preview=0)
