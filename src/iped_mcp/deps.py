"""Dependências do IPED e da IA: o que existe, o que falta e como instalar (sem interface; a janela só desenha).

Tudo o que consulta o computador passa por `Env`, que os testes trocam por uma versão falsa.
Os comandos de instalação aparecem no log antes de rodar. Os marcados `validated=False` NÃO foram confirmados
contra o IPED real: vêm da documentação pública e devem ser conferidos na sua instalação.
Baixar instaladores precisa de internet; o processamento dos casos e da IA continua local.
"""

from __future__ import annotations

import fnmatch
import json
import os
import shutil
import subprocess
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from iped_mcp import hardware as hw
from iped_mcp import webui_launcher as wl

OK, MISSING, OPTIONAL, UNKNOWN = "ok", "missing", "optional", "unknown"
IPED_WIKI = "https://github.com/sepinf-inc/IPED/wiki"
IPED_RELEASES = "https://github.com/sepinf-inc/IPED/releases"
WINGET = ["winget", "install", "-e", "--accept-package-agreements", "--accept-source-agreements", "--silent", "--id"]


@dataclass(frozen=True)
class Plan:
    kind: str  # winget | pip | openwebui | pull | manual
    command: tuple[str, ...] = ()
    note: str = ""
    url: str = ""  # página oficial (para os itens manuais)
    validated: bool = True

    @property
    def automatic(self) -> bool:
        return self.kind != "manual"


@dataclass
class Dep:
    key: str
    name: str
    group: str  # "iped" | "ia"
    status: str
    detail: str
    required: bool = False
    plan: Plan | None = None
    why: str = ""


# ------------------------------------------------------------------------------------------ ambiente
class Env:
    """Consultas ao computador. Os testes herdam desta classe e devolvem respostas prontas."""

    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def run(self, cmd: list[str], timeout: int = 30) -> tuple[int, str]:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace", check=False, **wl.hidden_kwargs())  # noqa: S603
            return out.returncode, out.stdout
        except (OSError, subprocess.SubprocessError):
            return 1, ""

    def is_file(self, path: Path) -> bool:
        return path.is_file()

    def find_files(self, root: Path | str, pattern: str, depth: int = 4) -> list[Path]:
        """Arquivos cujo nome combina com `pattern`, até `depth` níveis abaixo de `root` (sem seguir atalhos)."""
        base = Path(root)
        if not base.is_dir():
            return []
        found: list[Path] = []
        base_depth = len(base.parts)
        for dirpath, dirs, files in os.walk(base):
            if len(Path(dirpath).parts) - base_depth >= depth:
                dirs[:] = []
            found += [Path(dirpath) / f for f in files if fnmatch.fnmatch(f.lower(), pattern.lower())]
            if len(found) > 50:
                break
        return found

    def path_dirs(self) -> list[Path]:
        dirs = [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
        for var in ("CUDA_PATH", "CUDNN_PATH"):
            if os.environ.get(var):
                base = Path(os.environ[var])
                dirs += [base, base / "bin"]
        return dirs

    def vc_redist(self) -> bool | None:
        """Visual C++ 2015-2022 (x64) instalado? None fora do Windows."""
        if os.name != "nt":
            return None
        try:
            import winreg  # type: ignore[import-not-found]

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64") as key:  # type: ignore[attr-defined]
                return bool(winreg.QueryValueEx(key, "Installed")[0])  # type: ignore[attr-defined]
        except OSError:
            return False

    def ollama_models(self, url: str) -> list[str] | None:
        """Nomes dos modelos baixados, ou None se o Ollama não respondeu."""
        try:
            with urllib.request.urlopen(f"{url}/api/tags", timeout=3) as resp:  # noqa: S310
                return [m.get("name", "") for m in json.load(resp).get("models", [])]
        except Exception:  # noqa: BLE001
            return None

    def ollama_exe(self) -> str | None:
        found = self.which("ollama")
        if found:
            return found
        local = os.environ.get("LOCALAPPDATA")
        if local and (Path(local) / "Programs" / "Ollama" / "ollama.exe").is_file():
            return str(Path(local) / "Programs" / "Ollama" / "ollama.exe")
        return None

    def python(self) -> Path | None:
        found = wl.python_candidates()
        return found[0] if found else None

    def python_modules(self, py: Path, modules: list[str]) -> set[str]:
        code = "import importlib.util as u,sys;print(','.join(m for m in sys.argv[1:] if u.find_spec(m))) "
        rc, out = self.run([str(py), "-c", code, *modules], timeout=40)
        return set(out.strip().split(",")) - {""} if rc == 0 else set()

    def python_ok_for_webui(self) -> bool:
        return bool(wl.find_python_311() or shutil.which("uv"))

    def webui_found(self) -> bool:
        return bool(wl.running_open_webui(3737) or wl.find_open_webui_command())

    def gpu(self) -> tuple[str, float | None] | None:
        try:
            found = hw.detect()
        except Exception:  # noqa: BLE001
            return None
        if not found.gpus:
            return None
        return found.gpus[0].name, found.gpus[0].vram_gb


# ------------------------------------------------------------------------------------------ verificação
def suggest_model(vram_gb: float | None) -> str:
    """Modelo sugerido para baixar, conforme a memória da placa de vídeo (é só uma sugestão)."""
    if vram_gb is None or vram_gb < 6:
        return "qwen3:4b"
    if vram_gb < 12:
        return "qwen3:8b"
    if vram_gb < 20:
        return "qwen3:14b"
    return "qwen3:30b"


def _winget(package: str, note: str = "", validated: bool = True) -> Plan:
    return Plan("winget", (*WINGET, package), note or "Instala pelo winget (vem com o Windows 10/11).", validated=validated)


def check_all(iped_root: str = "", ollama_url: str = "http://127.0.0.1:11434", env: Env | None = None) -> list[Dep]:  # noqa: C901
    env = env or Env()
    root = Path(iped_root) if iped_root else None
    out: list[Dep] = []

    # -- IPED
    jar = root / "iped.jar" if root else None
    if jar and env.is_file(jar):
        out.append(Dep("iped", "IPED", "iped", OK, f"iped.jar em {root}", required=True))
    else:
        out.append(Dep("iped", "IPED", "iped", MISSING, "iped.jar não encontrado" if root else "Pasta do IPED ainda não informada", required=True,
                       plan=Plan("manual", note="Baixe e descompacte o IPED; depois escolha a pasta no assistente.", url=IPED_RELEASES)))  # fmt: skip
    bundled = [root / "jre" / "bin" / n for n in ("java.exe", "java")] if root else []
    if any(env.is_file(p) for p in bundled):
        out.append(Dep("java", "Java (JRE)", "iped", OK, "JRE empacotada na pasta do IPED", required=True))
    elif env.which("java"):
        out.append(Dep("java", "Java (JRE)", "iped", OK, "Java do sistema", required=True))
    else:
        out.append(Dep("java", "Java (JRE)", "iped", MISSING, "Nenhum Java encontrado", required=True,
                       plan=_winget("EclipseAdoptium.Temurin.17.JRE", "O IPED costuma trazer um JRE na pasta jre: confira antes. Versão exigida: veja a documentação da sua versão do IPED.", validated=False)))  # fmt: skip

    py = env.python()
    if py:
        out.append(Dep("python", "Python (módulos opcionais do IPED)", "iped", OK, f"{py}", why="Transcrição, reconhecimento facial e outros módulos do IPED usam Python."))
    else:
        out.append(Dep("python", "Python (módulos opcionais do IPED)", "iped", OPTIONAL, "Nenhum Python encontrado",
                       plan=_winget("Python.Python.3.11"), why="Só é preciso para os módulos opcionais do IPED que usam Python."))  # fmt: skip

    def pip_dep(key: str, name: str, module: str, packages: list[str], why: str, note: str = "") -> Dep:
        if not py:
            return Dep(key, name, "iped", OPTIONAL, "Precisa do Python", why=why)
        if module in have:
            return Dep(key, name, "iped", OK, "Instalado", why=why)
        cmd = (str(py), "-m", "pip", "install", *packages)
        return Dep(key, name, "iped", OPTIONAL, "Não instalado", why=why, plan=Plan("pip", cmd, note or "Instala com o pip no Python acima. Confirme que o IPED usa esse mesmo Python.", validated=False))

    mods = ["jep", "numpy", "faster_whisper", "face_recognition"]
    have = env.python_modules(py, mods) if py else set()
    out.append(pip_dep("jep", "JEP (ponte Java-Python)", "jep", ["jep"], "O IPED chama os módulos Python por esta ponte.", "No Windows pode exigir o JDK e as ferramentas de compilação."))
    out.append(pip_dep("numpy", "Whisper: numpy", "numpy", ["numpy"], "Biblioteca numérica da transcrição de áudio."))
    out.append(pip_dep("whisper", "Whisper: faster-whisper", "faster_whisper", ["faster-whisper"], "Transcrição de áudio e vídeo.",))
    out.append(pip_dep("face", "Reconhecimento facial (face_recognition)", "face_recognition", ["face_recognition"], "Agrupa e procura rostos.", "Depende do dlib, que pode exigir compilador C++."))
    out.append(Dep("age", "Estimativa de idade (módulos)", "iped", UNKNOWN, "Não consigo verificar automaticamente", plan=Plan("manual", note="Veja os requisitos do módulo na documentação do IPED.", url=IPED_WIKI), why="Módulo opcional de estimativa de idade em imagens."))  # fmt: skip

    gpu = env.gpu()
    dirs = env.path_dirs()
    cudnn = any(env.find_files(d, "cudnn*.dll", 1) for d in dirs)
    cublas = any(env.find_files(d, "cublas64_*.dll", 1) for d in dirs)
    if cudnn and cublas:
        out.append(Dep("cuda", "CUDA: cuDNN e cuBLAS", "iped", OK, "Encontrados no PATH", why="Aceleram a transcrição e outros módulos na placa NVIDIA."))
    else:
        missing = " e ".join(n for n, f in (("cuDNN", cudnn), ("cuBLAS", cublas)) if not f)
        out.append(Dep("cuda", "CUDA: cuDNN e cuBLAS", "iped", OPTIONAL, f"Não encontrei {missing}" + ("" if gpu else " (nenhuma GPU detectada)"),
                       why="Só adianta com placa NVIDIA; sem eles o processamento usa a CPU.", plan=Plan("manual", note="Baixe do site da NVIDIA a versão compatível com o seu IPED.", url="https://developer.nvidia.com/cuda-downloads")))  # fmt: skip

    def file_dep(key: str, name: str, patterns: list[str], why: str, url: str = IPED_WIKI) -> Dep:
        for pat in patterns:
            hit = env.find_files(root, pat, 5) if root else []
            if hit:
                return Dep(key, name, "iped", OK, str(hit[0].name), why=why)
        return Dep(key, name, "iped", OPTIONAL, "Não encontrado na pasta do IPED", why=why, plan=Plan("manual", note="Componente opcional: veja onde obtê-lo na documentação do IPED.", url=url))

    out.append(file_dep("corenlp", "Stanford CoreNLP", ["*corenlp*.jar"], "Reconhecimento de nomes, locais e organizações nos textos."))
    out.append(file_dep("jpx", "Plugin JPEG2000 (JPX)", ["*jpeg2000*.jar", "*jai*jpeg*.jar"], "Abre imagens JPEG2000."))
    out.append(file_dep("mplayer", "MPlayer (vídeos)", ["mplayer*.exe", "mplayer"], "Gera miniaturas e cenas de vídeos."))
    vc = env.vc_redist()
    if vc is None:
        pass
    elif vc:
        out.append(Dep("vcredist", "Visual C++ 2015-2022 (x64)", "iped", OK, "Instalado", why="Necessário para o MPlayer e outras ferramentas nativas."))
    else:
        out.append(Dep("vcredist", "Visual C++ 2015-2022 (x64)", "iped", MISSING, "Não encontrado", why="Necessário para o MPlayer e outras ferramentas nativas.", plan=_winget("Microsoft.VCRedist.2015+.x64")))  # fmt: skip
    tess = file_dep("tesseract", "Tesseract OCR", ["tesseract.exe", "tesseract"], "Lê texto de imagens e PDFs digitalizados.")
    if tess.status == OK and root:
        langs = sorted({p.stem for p in env.find_files(root, "*.traineddata", 6)})
        tess.detail += f"; idiomas: {', '.join(langs) if langs else 'nenhum dicionário encontrado'}"
        if langs and "por" not in langs:
            tess.status, tess.detail = OPTIONAL, tess.detail + " (falta o português: por)"
            tess.plan = Plan("manual", note="Coloque por.traineddata na pasta tessdata do Tesseract.", url="https://github.com/tesseract-ocr/tessdata")
    out.append(tess)

    # -- IA
    out.append(Dep("mcp", "Servidor MCP", "ia", OK, "Embutido neste programa (nada a instalar)", required=True))
    models = env.ollama_models(ollama_url)
    exe = env.ollama_exe()
    if models is not None or exe:
        out.append(Dep("ollama", "Ollama", "ia", OK, "Em execução" if models is not None else "Instalado (abra o Ollama)", required=True))
    else:
        out.append(Dep("ollama", "Ollama", "ia", MISSING, "Não instalado", required=True, plan=_winget("Ollama.Ollama"), why="Roda o modelo de IA neste computador."))
    sugg = suggest_model(gpu[1] if gpu else None)
    if models:
        out.append(Dep("model", "Modelo de IA", "ia", OK, f"{len(models)} baixado(s): {', '.join(models[:3])}{'…' if len(models) > 3 else ''}", required=True))
    else:
        out.append(Dep("model", "Modelo de IA", "ia", MISSING, "Nenhum modelo baixado", required=True, why=f"Sugestão para a sua placa: {sugg} (baixa alguns GB).",
                       plan=Plan("pull", (sugg,), f"Baixa {sugg} pelo Ollama (precisa dele aberto).") if models is not None or exe else None))  # fmt: skip
    if env.python_ok_for_webui():
        out.append(Dep("py311", "Python 3.11 para o Open WebUI", "ia", OK, "Disponível"))
    else:
        out.append(Dep("py311", "Python 3.11 para o Open WebUI", "ia", OPTIONAL, "Será baixado sozinho na instalação do Open WebUI", why="Usa o gerenciador uv; não precisa instalar nada antes."))
    if env.webui_found():
        out.append(Dep("webui", "Open WebUI (chat)", "ia", OK, "Encontrado", required=True))
    else:
        out.append(Dep("webui", "Open WebUI (chat)", "ia", MISSING, "Não encontrado", required=True, why="A tela de conversa. Instala em ambiente próprio (centenas de MB).",
                       plan=Plan("openwebui", tuple(wl.self_command("instalar-webui")), "Cria um ambiente próprio e instala o Open WebUI; baixa o Python 3.11 se preciso.")))  # fmt: skip
    out.append(Dep("gpu", "Placa de vídeo", "ia", OK if gpu else OPTIONAL, f"{gpu[0]}" + (f" ({gpu[1]:g} GB)" if gpu and gpu[1] else "") if gpu else "Nenhuma GPU detectada: o Ollama usará a CPU (lento)"))
    return out


def missing_required(deps: list[Dep]) -> list[Dep]:
    return [d for d in deps if d.required and d.status != OK]


def installable(deps: list[Dep]) -> list[Dep]:
    """O que o botão "Instalar o que falta" cobre: obrigatórios ausentes com instalação automática."""
    return [d for d in missing_required(deps) if d.plan and d.plan.automatic]


# ------------------------------------------------------------------------------------------ execução
def parse_pull_line(line: str) -> tuple[str, float | None]:
    """Linha JSON do `POST /api/pull` do Ollama -> (texto, fração 0-1 ou None)."""
    try:
        data = json.loads(line)
    except ValueError:
        return line.strip(), None
    if data.get("error"):
        return f"Erro: {data['error']}", None
    total, done = data.get("total"), data.get("completed")
    frac = min(1.0, done / total) if total and done is not None else None
    text = str(data.get("status", ""))
    if frac is not None:
        text += f" {frac * 100:.0f}% ({done / 1e9:.1f}/{total / 1e9:.1f} GB)"
    return text, frac


def pull_lines(model: str, url: str = "http://127.0.0.1:11434") -> Iterator[str]:  # pragma: no cover - rede
    req = urllib.request.Request(f"{url}/api/pull", data=json.dumps({"model": model}).encode(), headers={"Content-Type": "application/json"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=3600) as resp:  # noqa: S310
        for raw in resp:
            yield raw.decode("utf-8", "replace")


@dataclass
class Result:
    ok: bool
    code: int = 0
    message: str = ""
    log: list[str] = field(default_factory=list)


def run_plan(plan: Plan, say: Callable[[str], None], progress: Callable[[float | None], None] | None = None, *, lines: Callable[[str], Iterator[str]] | None = None) -> Result:
    """Executa um plano automático, narrando no `say`. Nunca abre janela de console."""
    progress = progress or (lambda _f: None)
    if plan.kind == "manual":
        return Result(False, 2, f"Instalação manual: {plan.url or plan.note}")
    if plan.kind == "pull":
        model = plan.command[0]
        say(f"$ ollama pull {model}  (via API do Ollama)")
        err = ""
        try:
            for line in (lines or pull_lines)(model):
                text, frac = parse_pull_line(line)
                if text:
                    say(text)
                if frac is not None:
                    progress(frac)
                if text.startswith("Erro"):
                    err = text
        except Exception as exc:  # noqa: BLE001
            return Result(False, 1, f"Não consegui falar com o Ollama: {exc}")
        return Result(not err, 1 if err else 0, err)
    if not plan.validated:
        say("AVISO: este comando não foi validado contra o seu IPED; confira o resultado depois.")
    say("$ " + " ".join(f'"{c}"' if " " in c else c for c in plan.command))
    progress(None)
    try:
        proc = subprocess.Popen(  # noqa: S603
            list(plan.command), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"), **wl.hidden_kwargs(),
        )  # fmt: skip
    except OSError as exc:
        return Result(False, 1, f"Não consegui executar: {exc}")
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.strip():
            say(line.rstrip())
    code = proc.wait()
    return Result(code == 0, code, "" if code == 0 else f"O instalador terminou com erro (código {code}).")
