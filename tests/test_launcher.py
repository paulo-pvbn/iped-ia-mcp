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
            if self.path == "/api/models":
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
    ok, msg = launcher.provision_open_webui(base, "http://127.0.0.1:8000/mcp", "tok", "PROMPT")
    srv.shutdown()
    assert ok, msg
    posts = {p[1]: p for p in seen if p[0] == "POST"}
    conn = posts["/api/v1/configs/tool_servers"][2]["TOOL_SERVER_CONNECTIONS"][0]
    assert conn["type"] == "mcp" and conn["auth_type"] == "bearer" and conn["key"] == "tok" and conn["info"]["id"] == "iped"
    assert posts["/api/v1/configs/tool_servers"][3] == "Bearer T"
    model = posts["/api/v1/models/create"][2]
    assert model["base_model_id"] == "qwen:7b" and model["meta"]["toolIds"] == ["server:mcp:iped"]
    assert model["params"]["system"] == "PROMPT" and model["params"]["function_calling"] == "native"
    assert posts["/api/v1/configs/models"][2]["DEFAULT_MODELS"] == "analista-iped"
