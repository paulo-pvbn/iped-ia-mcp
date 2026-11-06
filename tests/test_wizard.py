from pathlib import Path

from iped_mcp import wizard as wz


def make_iped(tmp_path: Path) -> Path:
    root = tmp_path / "iped"
    (root / "profiles" / "forensic").mkdir(parents=True)
    (root / "profiles" / "blind").mkdir()
    (root / "profiles" / ".oculta").mkdir()
    (root / "iped.jar").write_bytes(b"")
    return root


def test_steps_per_task() -> None:
    assert [s.key for s in wz.State(task=wz.NOVO).steps()] == ["tarefa", "iped", "origem", "destino", "proc", "ia", "revisar", "executar"]
    assert len(wz.State(task=wz.NOVO).steps()) == 8
    assert [s.key for s in wz.State(task=wz.ABRIR).steps()] == ["tarefa", "destino", "revisar", "executar"]
    assert "iped" in [s.key for s in wz.State(task=wz.CHAT).steps()] and "origem" not in [s.key for s in wz.State(task=wz.MULTI).steps()]
    assert set(wz.TASKS.values()) == set(wz.ORDER) == set(wz.TASK_HELP)


def test_list_profiles(tmp_path) -> None:
    root = make_iped(tmp_path)
    assert wz.list_profiles(str(root)) == ["blind", "forensic"]
    assert wz.list_profiles("") == [] and wz.list_profiles(str(tmp_path / "nada")) == []


def test_validate_each_step(tmp_path) -> None:
    root = make_iped(tmp_path)
    ev = tmp_path / "ev"
    ev.mkdir()
    s = wz.State(task=wz.NOVO)
    assert "iped.jar" in wz.validate("iped", s)
    s.iped = str(root)
    assert wz.validate("iped", s) == ""
    assert "evidências" in wz.validate("origem", s)
    s.origem = str(ev)
    assert wz.validate("origem", s) == ""
    assert wz.validate("destino", s) == "Escolha a pasta."
    s.destino = str(ev / "caso")
    assert "dentro da origem" in wz.validate("destino", s)
    s.destino = str(tmp_path / "caso")
    assert wz.validate("destino", s) == ""
    s.extra = "--nogui -o x"
    assert "-o" in wz.validate("proc", s)
    s.extra = "--nogui"
    assert wz.validate("proc", s) == ""
    assert "modelo" in wz.validate("ia", s)
    s.modelo = "qwen3:8b"
    assert wz.validate("ia", s) == ""


def test_destino_beside_image_file_is_allowed(tmp_path) -> None:
    image = tmp_path / "disco.E01"
    image.write_bytes(b"")
    s = wz.State(task=wz.NOVO, origem=str(image), destino=str(tmp_path / "caso"))
    assert wz.validate("destino", s) == ""  # ao lado do arquivo de imagem
    s.destino = str(image)
    assert "precisa ser uma pasta" in wz.validate("destino", s)
    folder = tmp_path / "ev"
    (folder / "sub").mkdir(parents=True)
    s = wz.State(task=wz.NOVO, origem=str(folder), destino=str(folder / "sub" / "caso"))
    assert "dentro da origem" in wz.validate("destino", s)


def test_validate_open_and_chat(tmp_path) -> None:
    s = wz.State(task=wz.ABRIR, destino=str(tmp_path))
    assert "IPED-SearchApp" in wz.validate("destino", s)
    (tmp_path / "IPED-SearchApp.exe").write_bytes(b"")
    assert wz.validate("destino", s) == ""
    c = wz.State(task=wz.CHAT, destino=str(tmp_path / "nao"))
    assert "não existe" in wz.validate("destino", c)
    c.destino = str(tmp_path)
    assert wz.validate("destino", c) == ""


def test_build_command_with_profile_and_extra(tmp_path) -> None:
    root = make_iped(tmp_path)
    s = wz.State(iped=str(root), origem="D:/ev/disco.E01", destino="D:/casos/c1", profile="forensic", extra="--nogui --portable")
    cmd = wz.build_command(s)
    assert cmd[1:3] == ["-jar", str(root / "iped.jar")]
    assert cmd[3:7] == ["-d", str(Path("D:/ev/disco.E01")), "-o", str(Path("D:/casos/c1"))]
    assert cmd[7:] == ["-profile", "forensic", "--nogui", "--portable"]
    assert wz.build_command(wz.State(iped=str(root), origem="a", destino="b"))[-2:] == ["-o", str(Path("b"))]


def test_review_rows(tmp_path) -> None:
    rows = dict(wz.review(wz.State(task=wz.NOVO, iped="I", origem="O", destino="D", modelo="m")))
    assert rows["Evidências"] == "O" and rows["Perfil"] == "padrão do IPED" and rows["Pasta do caso"] == "D" and rows["Modelo de IA"] == "m"
    assert "Pasta do IPED" not in dict(wz.review(wz.State(task=wz.ABRIR, destino="D")))


def test_dependencies_report(tmp_path, monkeypatch) -> None:
    root = make_iped(tmp_path)
    monkeypatch.setattr(wz.gui, "ollama_info", lambda url="": ([{"name": "m"}], None))
    monkeypatch.setattr(wz.hw, "detect", lambda: wz.hw.Hardware(gpus=[wz.hw.Gpu("RTX", 8.0)], ram_gb=16))
    got = {c.name: c for c in wz.dependencies(str(root), webui_found=True)}
    assert got["IPED"].status == "ok" and got["Ollama"].status == "ok" and got["Open WebUI"].status == "ok"
    assert "8 GB" in got["Placa de vídeo"].detail
    empty = {c.name: c for c in wz.dependencies("")}
    assert empty["IPED"].status == "warn" and empty["Open WebUI"].hint
