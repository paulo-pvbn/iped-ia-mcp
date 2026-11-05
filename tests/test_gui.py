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


def test_find_case_app_subfolders_and_parent(tmp_path):
    assert gui.find_case_app(tmp_path / "nao-existe") is None
    assert gui.find_case_app(tmp_path) is None
    caso = tmp_path / "Caso 1"
    (caso / "iped").mkdir(parents=True)
    exe = caso / "IPED-SearchApp.exe"
    exe.write_text("x")
    assert gui.find_case_app(caso) == exe  # pasta do caso
    assert gui.find_case_app(tmp_path) == exe  # pasta acima
    assert gui.find_case_app(caso / "iped") == exe  # subpasta iped -> pasta-mãe


def test_chat_command_ferramentas_e_aviso_de_modelo_grande() -> None:
    cmd = gui.chat_command("C:/iped", "C:/casos", sem_destrutivas=True, modelo="m", ferramentas="search,doc_info,xx")
    assert cmd[cmd.index("--ferramentas") + 1] == "search,doc_info"
    assert "--ferramentas" not in gui.chat_command("C:/iped", "C:/casos")
    assert "8 GB" in gui.big_model_hint("qwen3:27b-modelo8k")
    assert gui.big_model_hint("qwen3:8b") == "" and gui.big_model_hint("llama3") == ""
