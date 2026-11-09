from pathlib import Path

from iped_mcp import deps as dp
from iped_mcp import installers as ins


class FakeOps(ins.Ops):
    """Ops sem rede nem processos: `script` diz o que cada passo faz."""

    def __init__(self, winget=True, winget_code=0, installed_after=(), up_after=(), download_ok=True):
        super().__init__(lambda _t: None)
        self.log, self.winget, self.winget_code = [], winget, winget_code
        self.installed, self.up = False, False
        self.installed_after, self.up_after = set(installed_after), set(up_after)  # nomes de passos que produzem o efeito
        self.download_ok = download_ok

    def say(self, text):
        self.log.append(text)

    def has_winget(self):
        return self.winget

    def stream(self, cmd, elevate=False):
        step = "winget" if cmd[0] == "winget" else "setup"
        self.log.append(f"RUN {step}")
        if step in self.installed_after:
            self.installed = True
        return self.winget_code if step == "winget" else 0

    def download(self, url, name, progress):
        self.log.append(f"DL {name}")
        return Path(name) if self.download_ok else None

    def ollama_exe(self):
        return "ollama" if self.installed else None

    def ollama_up(self):
        return self.up

    def start_ollama(self):
        self.log.append("START")
        if "start" in self.up_after:
            self.up = True

    def sleep(self, secs):
        pass


def noprog(_f):
    pass


def test_ollama_winget_works_and_is_started():
    ops = FakeOps(installed_after={"winget"}, up_after={"start"})
    res = ins.ensure_ollama(ops, noprog)
    assert res.ok and "DL OllamaSetup.exe" not in ops.log and "START" in ops.log


def test_ollama_falls_back_to_official_installer_when_winget_missing_or_useless():
    for ops in (FakeOps(winget=False, installed_after={"setup"}, up_after={"start"}), FakeOps(winget_code=1, installed_after={"setup"}, up_after={"start"})):
        res = ins.ensure_ollama(ops, noprog)
        assert res.ok and "DL OllamaSetup.exe" in ops.log


def test_ollama_exit_code_is_not_proof():
    # winget "sucesso" (código 0), mas nada foi instalado e o download falha: tem que falhar com orientação
    ops = FakeOps(download_ok=False)
    res = ins.ensure_ollama(ops, noprog)
    assert not res.ok and "ollama.com/download" in res.message
    # winget "falhou" (código 1) mas o programa ficou instalado: segue em frente
    ops = FakeOps(winget_code=1, installed_after={"winget"}, up_after={"start"})
    assert ins.ensure_ollama(ops, noprog).ok


def test_ollama_installed_but_never_answers_reports_it():
    ops = FakeOps(installed_after={"winget"})
    res = ins.ensure_ollama(ops, noprog)
    assert not res.ok and "não respondeu" in res.message


def test_pull_starts_ollama_first_and_retries(monkeypatch):
    ops = FakeOps(up_after={"start"})
    ops.installed = True
    calls = []

    def lines(_m):
        calls.append(1)
        if len(calls) < 3:
            raise OSError("rede caiu")
        yield '{"status":"success"}'

    res = dp.run_plan(dp.Plan("pull", ("m",)), lambda _t: None, lines=lines, ops=ops)
    assert res.ok and len(calls) == 3 and "START" in ops.log


def test_pull_without_ollama_installed_says_so():
    res = dp.run_plan(dp.Plan("pull", ("m",)), lambda _t: None, lines=lambda _m: iter(()), ops=FakeOps())
    assert not res.ok and "não está instalado" in res.message


def test_jre_never_overwrites_existing_folder(tmp_path):
    (tmp_path / "jre").mkdir()
    (tmp_path / "jre" / "algo.txt").write_text("x")
    res = ins.ensure_jre(FakeOps(), tmp_path / "jre", noprog)
    assert not res.ok and "não vou mexer" in res.message


def test_jre_extracts_zip_into_target(tmp_path, monkeypatch):
    import zipfile

    z = tmp_path / "jre.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("jdk-17-jre/bin/java.exe", "x")
        f.writestr("jdk-17-jre/bin/java", "x")
    ops = FakeOps()
    monkeypatch.setattr(ops, "download", lambda url, name, progress: z)
    res = ins.ensure_jre(ops, tmp_path / "iped" / "jre", noprog)
    assert res.ok and ((tmp_path / "iped" / "jre" / "bin" / "java.exe").is_file() or (tmp_path / "iped" / "jre" / "bin" / "java").is_file())


def test_winget_command_uses_winget_source_and_agreements():
    cmd = ins.winget_cmd("Ollama.Ollama")
    assert cmd[:2] == ["winget", "install"] and "--source" in cmd and "winget" in cmd and "--accept-package-agreements" in cmd and cmd[-1] == "Ollama.Ollama"
