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

    text = json.dumps([{"id": "x", "path": "D:\\Polícia Federal\\Exercício"}], ensure_ascii=True)
    assert text.isascii() and "\\u00ed" in text
    assert json.loads(text)[0]["path"] == "D:\\Polícia Federal\\Exercício"
    src = Path(__file__).resolve().parent.parent / "web-api-launcher.py"
    assert "ensure_ascii=True" in src.read_text(encoding="utf-8")


def test_cases_found_up_to_three_levels_and_ids_without_accents(tmp_path):
    (tmp_path / "Polícia Federal" / "Caso Ação" / "iped").mkdir(parents=True)
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
