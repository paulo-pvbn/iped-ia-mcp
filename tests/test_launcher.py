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
