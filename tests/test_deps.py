from pathlib import Path

from iped_mcp import deps as dp


class Fake(dp.Env):
    def __init__(self, files=(), have=(), models=None, python="/usr/bin/python3", webui=False, vc=True, gpu=("RTX", 8.0), java=None, ollama=None):
        self.files, self.have, self.models, self.py = {Path(f) for f in files}, set(have), models, python
        self.webui, self.vc, self._gpu, self.java, self.ollama = webui, vc, gpu, java, ollama

    def which(self, name):
        return {"java": self.java}.get(name)

    def is_file(self, path):
        return Path(path) in self.files

    def find_files(self, root, pattern, depth=4):
        import fnmatch

        return [f for f in self.files if f.is_relative_to(Path(root)) and fnmatch.fnmatch(f.name.lower(), pattern.lower())]

    def path_dirs(self):
        return []

    def vc_redist(self):
        return self.vc

    def ollama_models(self, url):
        return self.models

    def ollama_exe(self):
        return self.ollama

    def python(self):
        return Path(self.py) if self.py else None

    def python_modules(self, py, modules):
        return self.have & set(modules)

    def python_ok_for_webui(self):
        return False

    def webui_found(self):
        return self.webui

    def gpu(self):
        return self._gpu


def by_key(deps):
    return {d.key: d for d in deps}


def test_nothing_installed_everything_required_is_offered():
    d = by_key(dp.check_all("", env=Fake(python=None, gpu=None, vc=False)))
    assert d["iped"].status == dp.MISSING and d["iped"].plan.kind == "manual"
    assert d["java"].plan.kind == "winget" and not d["java"].plan.validated
    assert d["ollama"].plan.command[-1] == "Ollama.Ollama"
    assert d["model"].status == dp.MISSING and d["model"].plan is None  # sem Ollama não dá para baixar modelo
    assert d["webui"].plan.kind == "openwebui" and d["py311"].status == dp.OPTIONAL
    assert d["vcredist"].plan.command[-1] == "Microsoft.VCRedist.2015+.x64"
    assert d["whisper"].detail == "Precisa do Python"
    keys = {x.key for x in dp.installable(list(d.values()))}
    assert {"java", "ollama", "webui"} <= keys and "iped" not in keys and "model" not in keys


def test_installed_things_are_ok_and_python_modules_use_pip_plan():
    root = "/iped"
    files = [f"{root}/iped.jar", f"{root}/jre/bin/java", f"{root}/lib/stanford-corenlp-4.jar", f"{root}/tools/tesseract/tesseract.exe", f"{root}/tools/tesseract/tessdata/eng.traineddata"]
    d = by_key(dp.check_all(root, env=Fake(files=files, have={"numpy"}, models=["qwen3:8b"], webui=True)))
    assert d["iped"].status == dp.OK and d["java"].detail.startswith("JRE empacotada")
    assert d["numpy"].status == dp.OK and d["whisper"].status == dp.OPTIONAL
    assert d["whisper"].plan.kind == "pip" and d["whisper"].plan.command[-2:] == ("install", "faster-whisper")
    assert d["corenlp"].status == dp.OK
    assert d["tesseract"].status == dp.OPTIONAL and "por" in d["tesseract"].detail  # falta o idioma português
    assert d["model"].status == dp.OK and d["webui"].status == dp.OK and dp.missing_required(list(d.values())) == []


def test_model_suggestion_follows_vram():
    assert dp.suggest_model(None) == "qwen3:4b"
    assert dp.suggest_model(8) == "qwen3:8b"
    assert dp.suggest_model(16) == "qwen3:14b"
    assert dp.suggest_model(24) == "qwen3:30b"


def test_model_pull_plan_when_ollama_runs_without_models():
    d = by_key(dp.check_all("", env=Fake(models=[], gpu=("RTX", 8.0))))
    assert d["model"].plan.kind == "pull" and d["model"].plan.command == ("qwen3:8b",)


def test_parse_pull_line():
    text, frac = dp.parse_pull_line('{"status":"pulling abc","total":4000000000,"completed":1000000000}')
    assert frac == 0.25 and "25%" in text
    assert dp.parse_pull_line('{"status":"success"}') == ("success", None)
    assert dp.parse_pull_line('{"error":"x"}')[0].startswith("Erro")


def test_run_plan_pull_and_manual():
    log, fr = [], []
    ok = dp.run_plan(dp.Plan("pull", ("m",)), log.append, fr.append, lines=lambda _m: iter(['{"status":"x","total":10,"completed":5}', '{"status":"success"}']))
    assert ok.ok and fr == [0.5] and log[-1] == "success"
    bad = dp.run_plan(dp.Plan("pull", ("m",)), log.append, lines=lambda _m: iter(['{"error":"sem espaço"}']))
    assert not bad.ok and "sem espaço" in bad.message
    assert not dp.run_plan(dp.Plan("manual", url="u"), log.append).ok


def test_run_plan_command_streams_output(tmp_path):
    import sys

    log = []
    res = dp.run_plan(dp.Plan("pip", (sys.executable, "-c", "print('olá')"), validated=False), log.append)
    assert res.ok and "olá" in log and any("não foi validado" in x for x in log)
    res = dp.run_plan(dp.Plan("winget", (sys.executable, "-c", "import sys;sys.exit(3)")), log.append)
    assert not res.ok and res.code == 3
