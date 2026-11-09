from __future__ import annotations

import sys
from pathlib import Path

from iped_mcp import webui_launcher as launcher

OUTPUT = """[INICIANDO] source=src1 porta=1234
[INICIANDO] source=caso dois porta=1235

=== SERVIDORES IPED INICIADOS ===
- src1: http://127.0.0.1:1234/  (PID 4321)
- caso dois: http://127.0.0.1:1235/  (PID 4322)

Pressione Ctrl+C para encerrar todos os servidores.
"""


def test_parse_endpoints_reads_launcher_output():
    found = launcher.parse_endpoints(OUTPUT.splitlines())
    assert found == {"src1": "http://127.0.0.1:1234", "caso dois": "http://127.0.0.1:1235"}
    assert launcher.endpoints_env(found) == "ep1=http://127.0.0.1:1234,ep2=http://127.0.0.1:1235"


def test_parse_ignores_noise():
    assert launcher.parse_endpoints(["ERRO: algo", "- sem url"]) == {}


def test_sources_from_cases_dir(tmp_path):
    (tmp_path / "Caso A" / "iped").mkdir(parents=True)
    (tmp_path / "caso-b").mkdir()
    (tmp_path / "caso-b" / "IPED-SearchApp.exe").write_bytes(b"")
    (tmp_path / "Caso_A").mkdir()  # nome parecido, mas não é caso
    (tmp_path / "docs").mkdir()
    sources = launcher.sources_from_cases_dir(tmp_path)
    assert [s["id"] for s in sources] == ["Caso_A", "caso-b"]
    assert all(Path(s["path"]).is_dir() for s in sources)
    # a própria pasta pode ser um caso
    assert launcher.sources_from_cases_dir(tmp_path / "caso-b")[0]["id"] == "caso-b"


def test_unique_ids_when_names_collide(tmp_path):
    for name in ("a b", "a_b"):
        (tmp_path / name / "iped").mkdir(parents=True)
    assert [s["id"] for s in launcher.sources_from_cases_dir(tmp_path)] == ["a_b", "a_b_2"]


def test_self_command_uses_script_when_not_frozen():
    cmd = launcher.self_command("mcp")
    assert cmd[0] == sys.executable and cmd[-2:] == ["--modo", "mcp"] and cmd[1].endswith("entry.py")


def test_source_json_is_ascii_even_with_accents(tmp_path):
    """O Java 11/Windows lê o arquivo em cp1252: o caminho com acento tem de ir como \\uXXXX."""
    import json

    text = json.dumps([{"id": "x", "path": "D:\\Pasta Exemplo\\Relatório"}], ensure_ascii=True)
    assert text.isascii() and "\\u00f3" in text
    assert json.loads(text)[0]["path"] == "D:\\Pasta Exemplo\\Relatório"
    src = Path(__file__).resolve().parent.parent / "web-api-launcher.py"
    assert "ensure_ascii=True" in src.read_text(encoding="utf-8")


def test_cases_found_up_to_three_levels_and_ids_without_accents(tmp_path):
    (tmp_path / "Pasta Exemplo" / "Caso Ação" / "iped").mkdir(parents=True)
    (tmp_path / "outro" / "iped").mkdir(parents=True)
    ids = [s["id"] for s in launcher.sources_from_cases_dir(tmp_path)]
    assert ids == ["outro", "Caso_Acao"] or ids == ["Caso_Acao", "outro"]
    assert all(i.isascii() for i in ids)


def test_host_port_and_say_writes_log(tmp_path, monkeypatch, capsys):
    assert launcher._host_port("http://127.0.0.1:1235/") == ("127.0.0.1", 1235)
    monkeypatch.setattr(launcher, "LOG_PATH", tmp_path / "log" / "ultimo-log.txt")
    monkeypatch.setattr(launcher, "_LOG_FILE", None)
    launcher.say("olá, ação")
    assert "olá, ação" in capsys.readouterr().out
    assert "olá, ação" in (tmp_path / "log" / "ultimo-log.txt").read_text(encoding="utf-8")


def test_find_open_webui_prefers_managed_venv(tmp_path, monkeypatch):
    exe = tmp_path / "open-webui"
    exe.write_text("x")
    monkeypatch.setattr(launcher, "OPENWEBUI_EXE", exe)
    assert launcher.find_open_webui() == str(exe)
    monkeypatch.setattr(launcher, "OPENWEBUI_EXE", tmp_path / "nao-existe")
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    monkeypatch.setattr(launcher, "load_config", lambda: {})
    monkeypatch.setattr(launcher, "_known_webui_exes", lambda: [])
    assert launcher.find_open_webui() is None


def test_find_open_webui_uses_chosen_and_known_locations(tmp_path, monkeypatch):
    chosen = tmp_path / "meu-open-webui.exe"
    chosen.write_text("x")
    monkeypatch.setattr(launcher, "OPENWEBUI_EXE", tmp_path / "nao-existe")
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    monkeypatch.setattr(launcher, "load_config", lambda: {"openwebui_exe": str(chosen)})
    assert launcher.find_open_webui() == str(chosen)
    known = tmp_path / "Python311" / "open-webui.exe"
    known.parent.mkdir()
    known.write_text("x")
    monkeypatch.setattr(launcher, "load_config", lambda: {})
    monkeypatch.setattr(launcher, "_known_webui_exes", lambda: [tmp_path / "x" / "open-webui.exe", known])
    assert launcher.find_open_webui() == str(known)


def test_free_port_skips_busy_port():
    import socket

    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        busy = srv.getsockname()[1]
        assert launcher.free_port(busy) != busy


def test_find_open_webui_command_next_to_python(tmp_path, monkeypatch):
    scripts = tmp_path / "Python311" / "Scripts"
    scripts.mkdir(parents=True)
    exe = scripts / "open-webui.exe"
    exe.write_text("x")
    py = tmp_path / "Python311" / "python.exe"
    py.write_text("x")
    monkeypatch.setattr(launcher, "find_open_webui", lambda: None)
    monkeypatch.setattr(launcher, "python_candidates", lambda: [py])
    assert launcher.find_open_webui_command() == [str(exe)]  # Scripts/ ao lado do python.exe


def test_running_open_webui_detects_page(monkeypatch):
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<title>Open WebUI</title>")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    assert launcher.running_open_webui(port) == f"http://127.0.0.1:{port}/"
    srv.shutdown()
    assert launcher.running_open_webui(port) is None


def test_python_candidates_expand_conda_envs(tmp_path, monkeypatch):
    name = "python.exe" if launcher.os.name == "nt" else "bin/python"
    base = tmp_path / "Conda" / name
    env = tmp_path / "Conda" / "envs" / "webui" / name
    for f in (base, env):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    monkeypatch.setattr(launcher.shutil, "which", lambda n: str(base) if n == "python" else None)
    found = launcher.python_candidates()
    assert base in found
    if launcher.os.name == "nt":  # em Linux o python do conda fica em bin/: o envs/ está um nível acima
        assert env in found


def test_provision_open_webui_against_fake_server():
    import http.server
    import json as _json
    import threading

    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def _send(self, obj, code=200):
            body = _json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            seen.append(("GET", self.path))
            if self.path == "/ollama/config":
                self._send({"ENABLE_OLLAMA_API": True, "OLLAMA_BASE_URLS": ["http://127.0.0.1:11434"], "OLLAMA_API_CONFIGS": {"0": {"key": "k"}}})
            elif self.path == "/api/models":
                self._send({"data": [{"id": "qwen:7b", "owned_by": "ollama"}, {"id": "outro", "owned_by": "openai"}]})
            else:
                self._send({"status": True})

        def do_POST(self):
            body = _json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            seen.append(("POST", self.path, body, self.headers.get("Authorization")))
            self._send({"token": "T"} if self.path.endswith("/signin") else body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    ok, msg = launcher.provision_open_webui(base, "http://127.0.0.1:8000/mcp", "tok", "PROMPT", ollama_url="http://127.0.0.1:11435")
    srv.shutdown()
    assert ok, msg
    posts = {p[1]: p for p in seen if p[0] == "POST"}
    conn = posts["/api/v1/configs/tool_servers"][2]["TOOL_SERVER_CONNECTIONS"][0]
    assert conn["type"] == "mcp" and conn["auth_type"] == "bearer" and conn["key"] == "tok" and conn["info"]["id"] == "iped"
    assert posts["/api/v1/configs/tool_servers"][3] == "Bearer T"
    model = posts["/api/v1/models/create"][2]
    assert model["base_model_id"] == "qwen:7b" and model["meta"]["toolIds"] == ["server:mcp:iped"]
    assert model["params"]["system"] == "PROMPT" and model["params"]["function_calling"] == "native"
    assert model["params"]["num_ctx"] >= 16384
    assert model["params"]["temperature"] == 0.5 and model["params"]["top_p"] == 0.6 and model["params"]["keep_alive"] == "30m"
    assert model["meta"]["capabilities"]["builtin_tools"] is False
    flt = conn["config"]["function_name_filter_list"].split(",")
    assert "list_sources" in flt and "!bookmarks_save_search" in flt
    assert not any("remove" in f or "rename" in f or "delete" in f for f in flt)
    assert posts["/api/v1/configs/models"][2]["DEFAULT_MODELS"] == "analista-iped"
    oll = posts["/ollama/config/update"][2]  # Open WebUI passa a falar com o Ollama pela guarda de contexto
    assert oll["OLLAMA_BASE_URLS"] == ["http://127.0.0.1:11435"] and oll["OLLAMA_API_CONFIGS"] == {"0": {"key": "k"}}
    order = [p[1] for p in seen if p[0] == "POST"]
    assert order.index("/ollama/config/update") < order.index("/api/v1/models/create")


def _server_names() -> list[str]:
    import asyncio
    import sys

    root = str(Path(__file__).resolve().parent.parent)
    sys.path.insert(0, root)
    try:
        import main as server  # noqa: PLC0415
    finally:
        sys.path.remove(root)
    return [t.name for t in asyncio.run(server.mcp.list_tools())]


def _effective(filter_text: str, names: list[str]) -> set[str]:
    """Mesma regra do Open WebUI (sufixo; '!' bloqueia)."""
    items = filter_text.split(",")
    allow = [i for i in items if not i.startswith("!")]
    block = [i[1:] for i in items if i.startswith("!")]
    return {n for n in names if any(n.endswith(a) for a in allow) and not any(n.endswith(b) for b in block)}


def test_catalog_matches_server_and_filter_is_exact() -> None:
    import random

    from iped_mcp import tools_catalog as tc

    names = _server_names()
    assert sorted(names) == sorted(tc.NAMES)  # catálogo sincronizado com o servidor
    assert set(tc.DEFAULT) <= set(names)
    assert not any(x in n for n in names for x in ("remove", "rename", "delete"))
    assert _effective(tc.build_filter(tc.DEFAULT), names) == set(tc.DEFAULT)
    assert _effective(tc.build_filter(tc.NAMES), names) == set(names)
    rnd = random.Random(7)
    for _ in range(60):
        pick = rnd.sample(names, rnd.randint(1, len(names)))
        assert _effective(tc.build_filter(pick, names), names) == set(pick)
    assert _effective(tc.build_filter([], names), names) == set()
    assert tc.tokens(tc.NAMES) > tc.tokens(tc.DEFAULT) > 0
    assert tc.normalize("search, doc_info ,inexistente,search") == ["search", "doc_info"]


def test_finish_open_webui_waits_for_existing_instance(monkeypatch) -> None:
    """Se o nosso Open WebUI sai porque a porta já tem outro, espera o antigo ficar pronto (e abre o navegador)."""
    import threading
    import time

    from iped_mcp import webui_launcher as wl

    class Dead:
        returncode = 1

        def poll(self) -> int:
            return 1

    state = {"calls": 0}
    opened = threading.Event()

    def healthy(_url: str) -> bool:
        state["calls"] += 1
        return state["calls"] >= 3

    monkeypatch.setattr(wl, "_healthy", healthy)
    monkeypatch.setattr(wl, "port_open", lambda *_a: True)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    monkeypatch.setattr("webbrowser.open", lambda _u: opened.set())
    wl.finish_open_webui("http://127.0.0.1:3737", 3737, None, Dead())
    assert opened.wait(5), "não chegou a abrir o navegador (a thread quebrou?)"


def test_text_chars_scale_with_context(monkeypatch) -> None:
    import importlib

    from iped_mcp import webui_launcher as wl

    assert wl.text_chars_for(16384) == 3000 and wl.text_chars_for(32768) == 6500
    assert wl.text_chars_for(1024) == 1500 and wl.text_chars_for(1_000_000) == 20000
    monkeypatch.setenv("IPED_MCP_TEXT_CHARS", "3000")
    from iped_mcp.core import tool

    importlib.reload(tool)
    try:
        import inspect

        assert inspect.signature(tool.doc_text).parameters["max_chars"].default == 3000
    finally:
        monkeypatch.delenv("IPED_MCP_TEXT_CHARS")
        importlib.reload(tool)
    assert inspect.signature(tool.doc_text).parameters["max_chars"].default == 20000


def test_user_prompt_file_has_priority(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "CONFIG_PATH", tmp_path / "config.json")
    text, where = launcher.load_prompt()
    assert text and where is not None and where.name == "analista.md"  # padrão do pacote
    (tmp_path / "prompt.md").write_text("minhas instruções", encoding="utf-8")
    text, where = launcher.load_prompt()
    assert text == "minhas instruções" and where == tmp_path / "prompt.md"


def test_quiet_background_tasks_only_turns_off_enabled_keys(monkeypatch):
    sent = []
    cfg = {"ENABLE_TITLE_GENERATION": True, "ENABLE_TAGS_GENERATION": False, "TASK_MODEL": "", "ENABLE_AUTOCOMPLETE_GENERATION": True}

    def fake_api(method, url, body=None, token=None, timeout=60):
        if method == "GET":
            return cfg
        sent.append(body)
        return {}

    monkeypatch.setattr(launcher, "_api", fake_api)
    assert launcher.quiet_background_tasks("http://x", "t") == ["ENABLE_TITLE_GENERATION", "ENABLE_AUTOCOMPLETE_GENERATION"]
    assert sent[0]["ENABLE_TITLE_GENERATION"] is False and sent[0]["TASK_MODEL"] == "" and "ENABLE_FOLLOW_UP_GENERATION" not in sent[0]
    sent.clear()
    cfg.update(ENABLE_TITLE_GENERATION=False, ENABLE_AUTOCOMPLETE_GENERATION=False)
    assert launcher.quiet_background_tasks("http://x", "t") == [] and sent == []
    monkeypatch.setattr(launcher, "_api", lambda *a, **k: None)
    assert launcher.quiet_background_tasks("http://x", "t") == []
