from iped_mcp.bookmarks.tool import ai_name


def test_ai_name_without_prefix_is_unchanged(monkeypatch):
    monkeypatch.delenv("IPED_MCP_BOOKMARK_PREFIX", raising=False)
    assert ai_name("  frete  ") == "frete"


def test_ai_name_adds_prefix_once_and_converts_old_format(monkeypatch):
    monkeypatch.setenv("IPED_MCP_BOOKMARK_PREFIX", "[IA]")
    assert ai_name("frete PY - OK") == "[IA] frete PY - OK"  # nunca cai num marcador do perito
    assert ai_name("[IA] frete") == "[IA] frete"
    assert ai_name("[ia] frete") == "[ia] frete"
    assert ai_name("IA: pagamentos") == "[IA] pagamentos"
