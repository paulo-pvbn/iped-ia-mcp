from iped_mcp import backends as bk


def test_lmstudio_models_lists_chat_models_and_skips_embeddings():
    data = {"data": [{"id": "qwen3-8b"}, {"id": "text-embedding-nomic-embed-text-v1.5"}, {"id": "gemma-3-12b"}]}
    assert bk.lmstudio_models(fetch=lambda url: data) == ["gemma-3-12b", "qwen3-8b"]


def test_lmstudio_server_off_is_none_not_empty():
    def boom(url):
        raise OSError("recusou")

    assert bk.lmstudio_models(fetch=boom) is None
    assert bk.lmstudio_models(fetch=lambda url: {"data": []}) == []


def test_choose_backend():
    assert bk.choose_backend("auto", "", ["a"], ["b"]) == "ollama"
    assert bk.choose_backend("auto", "", [], ["b"]) == "lmstudio"
    assert bk.choose_backend("auto", "b", ["a"], ["b"]) == "lmstudio"  # o modelo escolhido manda
    assert bk.choose_backend("lmstudio", "", ["a"], None) == "lmstudio"
    assert bk.choose_backend("auto", "", None, None) == "ollama"
