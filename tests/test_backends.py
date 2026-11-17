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


LS = """You have 3 models, taking up 20.12 GB of disk space.

LLM                                PARAMS    ARCH     SIZE      DEVICE
qwen/qwen3.8-27b (1 variant)       27B       qwen35   17.74 GB  Local
unsloth/qwen3.8-27b                27B       qwen35   2.30 GB   Local

EMBEDDING                          PARAMS    ARCH     SIZE      DEVICE
text-embedding-nomic-embed-text-v1.5  Nomic BERT  84.11 MB  Local
"""


def test_parse_lms_ls_keeps_only_chat_models():
    assert bk.parse_lms_ls(LS) == ["qwen/qwen3.8-27b", "unsloth/qwen3.8-27b"]


def test_ensure_server_starts_it_and_waits_for_models():
    state = {"up": False}
    calls = []

    def run(cmd):
        calls.append(cmd[1:])
        if cmd[1:] == ["server", "start"]:
            state["up"] = True
        return 0, ""

    def models():
        return ["m1"] if state["up"] else None

    msgs = []
    assert bk.ensure_lmstudio_server(msgs.append, run=run, sleep=lambda s: None, models=models, exe="lms") == ["m1"]
    assert calls == [["server", "start"]]


def test_ensure_server_loads_first_chat_model_when_none_listed():
    state = {"loaded": False}

    def run(cmd):
        if cmd[1] == "ls":
            return 0, LS
        if cmd[1] == "load":
            state["loaded"] = True
        return 0, ""

    models = lambda: ["qwen/qwen3.8-27b"] if state["loaded"] else []  # noqa: E731
    out = bk.ensure_lmstudio_server(lambda m: None, run=run, sleep=lambda s: None, models=models, exe="lms")
    assert out == ["qwen/qwen3.8-27b"]


def test_ensure_server_without_cli_returns_none(monkeypatch):
    monkeypatch.setattr(bk, "lms_exe", lambda: None)
    assert bk.ensure_lmstudio_server(lambda m: None, models=lambda: None) is None


def test_finds_server_on_port_4321_and_remembers_it():
    def fetch(url):
        if ":4321/v1/models" in url:
            return {"data": [{"id": "qwen/qwen3.8-27b"}]}
        raise OSError("sem resposta")

    assert bk.lmstudio_models(fetch=fetch) == ["qwen/qwen3.8-27b"]
    assert bk.active_lmstudio_url() == "http://127.0.0.1:4321"


def test_reads_native_api_format_and_skips_embedding_type():
    def fetch(url):
        if url.endswith("/api/v1/models") and ":4321" in url:
            return {"models": [{"type": "llm", "key": "qwen/qwen3.8-27b"}, {"type": "embedding", "key": "nomic"}]}
        raise OSError("sem resposta")

    assert bk.lmstudio_models(fetch=fetch) == ["qwen/qwen3.8-27b"]
