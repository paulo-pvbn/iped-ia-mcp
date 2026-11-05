from pathlib import Path

from iped_mcp import gui


def test_build_index_command_uses_iped_jre_when_present(tmp_path):
    (tmp_path / "jre" / "bin").mkdir(parents=True)
    java = tmp_path / "jre" / "bin" / "java.exe"
    java.write_bytes(b"")
    (tmp_path / "iped.jar").write_bytes(b"")
    cmd = gui.build_index_command(tmp_path, Path("D:/img/disco.E01"), Path("D:/casos/c1"))
    assert cmd[0] == str(java) and cmd[1:3] == ["-jar", str(tmp_path / "iped.jar")]
    assert cmd[3:] == ["-d", str(Path("D:/img/disco.E01")), "-o", str(Path("D:/casos/c1"))]


def test_find_java_falls_back_to_system(tmp_path):
    assert gui.find_java(tmp_path) == "java"


def test_check_index_inputs(tmp_path):
    (tmp_path / "iped.jar").write_bytes(b"")
    origem = tmp_path / "ev"
    origem.mkdir()
    assert "pasta do IPED" in gui.check_index_inputs("", str(origem), "x")
    assert "ORIGEM" in gui.check_index_inputs(str(tmp_path), str(tmp_path / "nao"), "x")
    assert "DESTINO" in gui.check_index_inputs(str(tmp_path), str(origem), "")
    assert gui.check_index_inputs(str(tmp_path), str(origem), str(tmp_path / "caso")) == ""


def test_case_app_and_chat_command(tmp_path):
    assert gui.case_app(tmp_path) is None
    (tmp_path / "IPED-SearchApp.exe").write_bytes(b"")
    assert gui.case_app(tmp_path) == tmp_path / "IPED-SearchApp.exe"
    cmd = gui.chat_command("C:/iped", "D:/casos", sem_destrutivas=True)
    assert "--cases-dir" in cmd and cmd[-1] == "--sem-destrutivas" and "iniciar" in cmd
