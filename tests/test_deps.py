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


class Ready(dp.ins.Ops):
    """Ollama instalado e respondendo; sem rede nem espera."""

    def __init__(self):
        super().__init__(lambda _t: None)

    def say(self, text):
        pass

    def ollama_up(self):
        return True

    def sleep(self, secs):
        pass


def by_key(deps):
    return {d.key: d for d in deps}


def test_nothing_installed_everything_required_is_offered():
    d = by_key(dp.check_all("", env=Fake(python=None, gpu=None, vc=False)))
    assert d["iped"].status == dp.MISSING and d["iped"].plan.kind == "manual"
    assert d["java"].status == dp.OPTIONAL  # sem pasta do IPED não há onde procurar o JRE
    assert d["ollama"].plan.kind == "ollama" and d["ollama"].plan.command[-1] == "Ollama.Ollama"
    assert d["model"].status == dp.MISSING and d["model"].plan.kind == "pull"  # o plano inicia o Ollama antes de baixar
    assert d["webui"].plan.kind == "openwebui" and d["py311"].status == dp.OPTIONAL
    assert d["vcredist"].plan.kind == "vcredist" and d["vcredist"].plan.command[-1] == "Microsoft.VCRedist.2015+.x64"
    assert d["whisper"].detail == "Precisa do Python"
    keys = {x.key for x in dp.installable(list(d.values()))}
    order = [x.key for x in dp.installable(list(d.values()))]
    assert {"ollama", "model", "webui"} <= keys and "iped" not in keys
    assert order.index("ollama") < order.index("model")  # o Ollama precisa existir antes do modelo


def test_installed_things_are_ok_and_python_modules_use_pip_plan():
    root = "/iped"
    files = [f"{root}/iped.jar", f"{root}/jre/bin/java", f"{root}/lib/stanford-corenlp-4.jar", f"{root}/tools/tesseract/tesseract.exe", f"{root}/tools/tesseract/tessdata/eng.traineddata"]
    d = by_key(dp.check_all(root, env=Fake(files=files, have={"numpy"}, models=["qwen3:8b"], webui=True)))
    assert d["iped"].status == dp.OK and d["java"].status == dp.OK
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
    ready = Ready()
    ok = dp.run_plan(dp.Plan("pull", ("m",)), log.append, fr.append, lines=lambda _m: iter(['{"status":"x","total":10,"completed":5}', '{"status":"success"}']), ops=ready)
    assert ok.ok and fr == [0.5] and log[-1] == "success"
    bad = dp.run_plan(dp.Plan("pull", ("m",)), log.append, lines=lambda _m: iter(['{"error":"sem espaço"}']), ops=ready)
    assert not bad.ok and "sem espaço" in bad.message
    assert not dp.run_plan(dp.Plan("manual", url="u"), log.append).ok


def test_run_plan_command_streams_output(tmp_path):
    import sys

    log = []
    res = dp.run_plan(dp.Plan("pip", (sys.executable, "-c", "print('olá')"), validated=False), log.append, ops=dp.ins.Ops(log.append))
    assert res.ok and "olá" in log and any("não foi validado" in x for x in log)
    res = dp.run_plan(dp.Plan("winget", (sys.executable, "-c", "import sys;sys.exit(3)")), log.append, ops=dp.ins.Ops(log.append))
    assert not res.ok and res.code == 3


def test_missing_jre_inside_iped_folder_plans_extraction_there():
    d = by_key(dp.check_all("/iped", env=Fake(files=["/iped/iped.jar"])))
    assert d["java"].status == dp.MISSING and d["java"].required and d["java"].plan.kind == "java"
    assert d["java"].plan.command == (str(Path("/iped") / "jre"),)


def _by_key(deps, key):
    return next(d for d in deps if d.key == key)


def test_local_calls_ignore_system_proxy(monkeypatch):
    import http.server
    import threading

    from iped_mcp import localnet

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")  # proxy inexistente: se fosse usado, a chamada falharia
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    with localnet.open_local(f"http://127.0.0.1:{srv.server_address[1]}/", 3) as resp:
        assert resp.read() == b"ok"
    srv.shutdown()


def test_presets_follow_vram_and_match_suggestion():
    assert dp.recommended_preset(None).tag == "qwen3:4b"
    assert dp.recommended_preset(4).label == "Leve"
    assert dp.recommended_preset(8).tag == "qwen3:8b"
    assert dp.recommended_preset(12).tag == "qwen3:14b"
    assert dp.recommended_preset(24).tag == "qwen3:30b"
    for vram in (None, 4, 8, 12, 24):
        assert dp.suggest_model(vram) == dp.recommended_preset(vram).tag
    assert [x.tag for x in dp.MODEL_PRESETS] == sorted({x.tag for x in dp.MODEL_PRESETS}, key=lambda t: float(t.split(":")[1][:-1]))


def test_manual_guides_name_the_page_and_the_command():
    ollama = dp.manual_guide("ollama")
    assert dp.OLLAMA_DOWNLOAD_URL in ollama and "OllamaSetup.exe" in ollama and "Verificar de novo" in ollama
    model = dp.manual_guide("model", "qwen3:14b")
    assert "ollama pull qwen3:14b" in model and "success" in model


def test_ollama_is_required_and_offers_automatic_install_with_manual_fallback():
    deps = dp.check_all("", env=Fake(models=None, ollama=None))
    ollama = _by_key(deps, "ollama")
    assert ollama.required and ollama.plan.kind == "ollama"
    assert "passo a passo" in ollama.why
    model = _by_key(deps, "model")
    assert model.plan.kind == "pull" and model.plan.command[0] == dp.suggest_model(8.0)
