from __future__ import annotations

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("iniciar_webui", Path(__file__).resolve().parent.parent / "iniciar-webui.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)

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
