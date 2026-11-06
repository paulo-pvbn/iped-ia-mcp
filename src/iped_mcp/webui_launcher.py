"""Sobe tudo para conversar com os casos do IPED no Open WebUI: Web APIs do IPED + MCP + Open WebUI.

Ordem: (1) Web API do IPED (uma por caso, só em 127.0.0.1), (2) o MCP apontando para elas, com token,
(3) o Open WebUI, se estiver instalado (``pip install open-webui``; Python 3.11). Ctrl+C encerra tudo.

Funciona como script e dentro do executável (PyInstaller): os filhos são o próprio programa com ``--modo``.
Nada aqui foi validado contra o Open WebUI/IPED reais: é um roteiro automatizado dos passos manuais.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import unicodedata
import threading
import time
from pathlib import Path

from iped_mcp.ollama_guard import OllamaGuard
from iped_mcp.tools_catalog import DEFAULT, build_filter, normalize

ENDPOINT_RE = re.compile(r"^-\s+(?P<id>.+?):\s+(?P<url>https?://\S+?)/?\s+\(PID\s+\d+\)")
READY_MARK = "SERVIDORES IPED INICIADOS"
WEBAPI_PORT = 1234  # primeira porta tentada pelo web-api-launcher.py (padrão dele)
FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))  # onde estão main.py e prompts/
LOG_PATH = Path(os.environ.get("APPDATA") or Path.home()) / "iped-ia-mcp" / "ultimo-log.txt"
CONFIG_PATH = Path(os.environ.get("APPDATA") or Path.home()) / "iped-ia-mcp" / "config.json"


def parse_endpoints(lines: list[str]) -> dict[str, str]:
    """Extrai ``{id_da_source: url}`` das linhas impressas pelo web-api-launcher.py."""
    found: dict[str, str] = {}
    for line in lines:
        m = ENDPOINT_RE.match(line.strip())
        if m:
            found[m["id"]] = m["url"]
    return found


def endpoints_env(endpoints: dict[str, str]) -> str:
    """Valor de IPED_ENDPOINTS (ids de endpoint sem espaços e únicos)."""
    return ",".join(f"ep{i}={url}" for i, url in enumerate(endpoints.values(), start=1))


def looks_like_case(folder: Path) -> bool:
    return (folder / "iped").is_dir() or any(folder.glob("IPED-SearchApp*"))


def find_cases(folder: Path, depth: int = 3) -> list[Path]:
    """Pastas de caso do IPED dentro de ``folder`` (ou ela mesma), procurando até ``depth`` níveis abaixo."""
    if looks_like_case(folder):
        return [folder]
    found: list[Path] = []
    if depth <= 0:
        return found
    try:
        children = sorted(p for p in folder.iterdir() if p.is_dir())
    except OSError:
        return found
    for child in children:
        found.extend(find_cases(child, depth - 1))
    return found


def sources_from_cases_dir(cases_dir: Path) -> list[dict[str, str]]:
    """Fontes ``[{"id","path"}]`` para uma pasta que é um caso ou que contém vários casos."""
    used: set[str] = set()
    out = []
    for case in find_cases(cases_dir):
        plain = unicodedata.normalize("NFKD", case.name).encode("ascii", "ignore").decode()
        base = re.sub(r"[^A-Za-z0-9_-]+", "_", plain).strip("_") or "caso"
        sid, n = base, 2
        while sid in used:
            sid, n = f"{base}_{n}", n + 1
        used.add(sid)
        out.append({"id": sid, "path": str(case)})
    return out


def free_port(preferred: int, host: str = "127.0.0.1", tries: int = 50) -> int:
    """Primeira porta livre a partir de `preferred` (sobra de execução anterior ocupa a porta padrão)."""
    for port in range(preferred, preferred + tries):
        if not port_open(host, port):
            return port
    return preferred


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def self_command(modo: str) -> list[str]:
    """Comando que reexecuta este programa em outro modo (executável ou script)."""
    if FROZEN:
        return [sys.executable, "--modo", modo]
    return [sys.executable, str(ROOT / "scripts" / "entry.py"), "--modo", modo]


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":  # encerra a árvore (Java/Python filhos)
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
        else:
            proc.terminate()
        proc.wait(timeout=15)
    except Exception:  # noqa: BLE001
        proc.kill()


# ----------------------------------------------------------------------------------------- perguntas
OPENWEBUI_HOME = CONFIG_PATH.parent / "openwebui"  # venv gerenciada + dados do Open WebUI
OPENWEBUI_EXE = OPENWEBUI_HOME / "venv" / ("Scripts/open-webui.exe" if os.name == "nt" else "bin/open-webui")


def _known_webui_exes() -> list[Path]:
    """Locais comuns do open-webui(.exe): Python do usuário, pip --user, conda e venvs na pasta pessoal."""
    sub = "Scripts/open-webui.exe" if os.name == "nt" else "bin/open-webui"
    home = Path.home()
    roots = [
        Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local")) / "Programs/Python",
        Path(os.environ.get("APPDATA", home / "AppData/Roaming")) / "Python",
    ]
    found: list[Path] = []
    for root in roots:
        found += sorted(root.glob(f"Python3*/{sub}"))
    for conda in ("anaconda3", "miniconda3", "miniforge3", "mambaforge"):
        found.append(home / conda / sub)
        found += sorted((home / conda / "envs").glob(f"*/{sub}"))
    for venv in (".venv", "venv", "open-webui", "openwebui", "env"):
        found.append(home / venv / sub)
    return found


def find_open_webui() -> str | None:
    """Executável do Open WebUI: escolhido pelo usuário, instalado pelo botão, no PATH ou em locais comuns."""
    chosen = load_config().get("openwebui_exe", "")
    if chosen and Path(chosen).is_file():
        return chosen
    if OPENWEBUI_EXE.is_file():
        return str(OPENWEBUI_EXE)
    on_path = shutil.which("open-webui")
    if on_path:
        return on_path
    for candidate in _known_webui_exes():
        if candidate.is_file():
            return str(candidate)
    return None


def python_candidates() -> list[Path]:
    """Pythons do computador: PATH, launcher `py`, registro do Windows e pastas comuns (sem alias da Microsoft Store)."""
    found: list[Path] = []

    def add(path: str | Path | None) -> None:
        if path and Path(path).is_file() and "WindowsApps" not in str(path):
            found.append(Path(path))

    for name in ("python", "python3", "python3.11", "python3.12"):
        add(shutil.which(name))
    if shutil.which("py"):
        try:
            out = subprocess.run(["py", "-0p"], capture_output=True, text=True, timeout=20).stdout  # noqa: S603,S607
            for line in out.splitlines():
                m = re.search(r"([A-Za-z]:\\.*?python[w]?\.exe)\s*$", line.strip(), re.IGNORECASE)
                if m:
                    add(m.group(1))
        except (OSError, subprocess.SubprocessError):
            pass
    if os.name == "nt":
        try:
            import winreg

            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    core = winreg.OpenKey(hive, r"SOFTWARE\Python\PythonCore")
                except OSError:
                    continue
                for i in range(winreg.QueryInfoKey(core)[0]):
                    try:
                        with winreg.OpenKey(core, winreg.EnumKey(core, i) + r"\InstallPath") as key:
                            add(Path(winreg.QueryValueEx(key, "")[0]) / "python.exe")
                    except OSError:
                        continue
        except ImportError:  # pragma: no cover
            pass
    home = Path.home()
    exe = "python.exe" if os.name == "nt" else "bin/python"
    for root in (
        Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local")) / "Programs/Python",
        Path(os.environ.get("ProgramFiles", "C:/Program Files")),
        Path("C:/"),
    ):
        for p in sorted(root.glob("Python3*")):
            add(p / exe)
    for conda in ("anaconda3", "miniconda3", "miniforge3", "mambaforge"):
        add(home / conda / exe)
        for env in sorted((home / conda / "envs").glob("*")):
            add(env / exe)
    for base in list(found):  # Conda instalado fora da pasta pessoal (ex.: D:\\Python\\Conda): ambientes em envs/
        for env in sorted((base.parent / "envs").glob("*")):
            add(env / exe)
    unique: dict[str, Path] = {}
    for item in found:
        unique.setdefault(str(item).lower(), item)
    return list(unique.values())


def python_version(py: Path) -> tuple[int, int] | None:
    try:
        out = subprocess.run(  # noqa: S603
            [str(py), "-c", "import sys;print('%d.%d' % sys.version_info[:2])"], capture_output=True, text=True, timeout=20
        ).stdout.strip()
        major, minor = out.split(".")
        return int(major), int(minor)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def find_open_webui_command() -> list[str] | None:
    """Comando para subir o Open WebUI: o executável ou `python -m open_webui` em qualquer Python do computador."""
    exe = find_open_webui()
    if exe:
        return [exe]
    pythons = python_candidates()
    for py in pythons:  # open-webui.exe ao lado do Python (venv) ou em Scripts/ (instalação base)
        for rel in ("open-webui.exe", "Scripts/open-webui.exe", "bin/open-webui"):
            if (py.parent / rel).is_file():
                return [str(py.parent / rel)]
    probe = "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('open_webui') else 1)"
    for py in pythons:
        try:
            if subprocess.run([str(py), "-c", probe], capture_output=True, timeout=30).returncode == 0:  # noqa: S603
                return [str(py), "-m", "open_webui"]
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def find_python_311() -> list[str] | None:
    """Python 3.11/3.12 (o Open WebUI exige >=3.11,<3.13); None se não houver."""
    for py in python_candidates():
        if python_version(py) in ((3, 11), (3, 12)):
            return [str(py)]
    return None


ADMIN_EMAIL, ADMIN_PASSWORD = "admin@localhost", "admin"  # conta do modo sem login (WEBUI_AUTH=False) do Open WebUI
PRESET_ID, MCP_ID = "analista-iped", "iped"


def _api(method: str, url: str, body: object = None, token: str | None = None, timeout: int = 60):
    import urllib.request

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)  # noqa: S310
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        raw = resp.read()
    return json.loads(raw) if raw else None


# As 33 ferramentas + prompt + resultado estouram 8k: o Ollama corta o começo da conversa
# (inclusive a pergunta) e o template Qwen falha com "No user query found in messages".
NUM_CTX = 16384
GUARD_PORT = 11435  # guarda de contexto entre o Open WebUI e o Ollama (11434)


def text_chars_for(num_ctx: int) -> int:
    """Caracteres por leitura de documento: ~20% do contexto (1 token ~ 3 caracteres, e o resto fica para a conversa)."""
    return max(1500, min(20000, int(num_ctx * 0.2) // 500 * 500))


def provision_open_webui(
    base_url: str, mcp_url: str, mcp_token: str, system_prompt: str, model: str = "", tools: object = None, num_ctx: int = 0,
    ollama_url: str = "",
) -> tuple[bool, str]:
    """Deixa o Open WebUI pronto: servidor MCP do IPED cadastrado e o modelo "Analista IPED" (prompt + ferramenta ligada).

    Só funciona em Open WebUI iniciado sem login por este programa. Devolve (ok, mensagem).
    """
    import urllib.error

    base = base_url.rstrip("/")
    try:
        token = _api("POST", f"{base}/api/v1/auths/signin", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})["token"]
    except Exception as exc:  # noqa: BLE001
        return False, f"não consegui entrar no Open WebUI sem login ({exc}); ele já tinha contas?"
    try:
        if ollama_url:  # o Open WebUI passa a falar com o Ollama pela guarda de contexto (a configuração salva prevalece sobre a variável de ambiente)
            current = _api("GET", f"{base}/ollama/config", None, token) or {}
            _api("POST", f"{base}/ollama/config/update",
                 {"ENABLE_OLLAMA_API": True, "OLLAMA_BASE_URLS": [ollama_url], "OLLAMA_API_CONFIGS": {"0": (current.get("OLLAMA_API_CONFIGS") or {}).get("0", {})}}, token)  # fmt: skip
        connection = {
            "url": mcp_url,
            "path": "",
            "type": "mcp",
            "auth_type": "bearer" if mcp_token else "none",
            "key": mcp_token or "",
            "config": {"enable": True, "access_control": None, "function_name_filter_list": build_filter(tools or DEFAULT)},
            "info": {"id": MCP_ID, "name": "IPED", "description": "Casos do IPED (somente leitura)"},
        }
        _api("POST", f"{base}/api/v1/configs/tool_servers", {"TOOL_SERVER_CONNECTIONS": [connection]}, token)
        listed = _api("GET", f"{base}/api/models", None, token)
        items = listed.get("data", []) if isinstance(listed, dict) else (listed or [])
        bases = [m for m in items if not m.get("preset") and m.get("id") != PRESET_ID]
        chosen = next((m for m in bases if model and m.get("id") == model), None)
        chosen = chosen or next((m for m in bases if m.get("owned_by") == "ollama"), None) or (bases[0] if bases else None)
        if chosen is None:
            return False, "o Open WebUI não lista nenhum modelo (o Ollama está aberto e tem modelo baixado?)"
        form = {
            "id": PRESET_ID,
            "base_model_id": chosen["id"],
            "name": "Analista IPED",
            "meta": {
                "description": "Analista forense com as ferramentas do IPED",
                "toolIds": [f"server:mcp:{MCP_ID}"],
                "capabilities": {"builtin_tools": False},  # sem search_knowledge_files etc.: só as do IPED
            },
            "params": {"system": system_prompt, "function_calling": "native", "num_ctx": max(4096, int(num_ctx or NUM_CTX))},
            "is_active": True,
        }
        try:
            _api("POST", f"{base}/api/v1/models/create", form, token)
        except urllib.error.HTTPError:  # já existe: atualiza (token e porta mudam a cada execução)
            _api("POST", f"{base}/api/v1/models/model/update", form, token)
        try:
            _api("POST", f"{base}/api/v1/configs/models", {"DEFAULT_MODELS": PRESET_ID, "DEFAULT_PINNED_MODELS": None, "MODEL_ORDER_LIST": None}, token)
        except Exception:  # noqa: BLE001, S110  (só deixa como modelo inicial; não é essencial)
            pass
        return True, f"modelo 'Analista IPED' criado sobre {chosen['id']}"
    except Exception as exc:  # noqa: BLE001
        return False, f"falha ao configurar o Open WebUI ({exc})"


def finish_open_webui(url: str, port: int | None, setup: dict | None, proc: subprocess.Popen | None = None) -> None:
    """Em segundo plano: espera o Open WebUI responder, configura o IPED nele e abre o navegador."""

    def work() -> None:
        nonlocal proc
        start = time.monotonic()
        deadline = start + 900
        last = start
        while port is not None and not _healthy(url):
            if proc is not None and proc.poll() is not None:
                if port_open("127.0.0.1", port):  # outro Open WebUI (execução anterior) já ocupa a porta: espera ele ficar pronto
                    say(f"A porta {port} já está em uso por outro Open WebUI (de uma execução anterior); aguardando ele ficar pronto…")
                    proc = None
                    continue
                say(f"O Open WebUI fechou sozinho (código {proc.returncode}). Fim do log ({OPENWEBUI_HOME / 'open-webui.log'}):", err=True)
                say(log_tail(OPENWEBUI_HOME / "open-webui.log", 30), err=True)
                return
            if time.monotonic() > deadline:
                say("O Open WebUI não respondeu em 15 min; veja o log em " + str(OPENWEBUI_HOME / "open-webui.log"), err=True)
                return
            if time.monotonic() - last > 20:
                last = time.monotonic()
                say(f"   Open WebUI ainda iniciando ({last - start:.0f}s); não está travado.")
            time.sleep(2)
        if setup:
            ok, msg = False, ""
            for attempt in range(4):  # logo após subir o banco ainda pode estar inicializando
                ok, msg = provision_open_webui(url, **setup)
                if ok:
                    break
                time.sleep(5 * (attempt + 1))
            if ok:
                say(f"Open WebUI configurado: {msg}. É só conversar; as ferramentas do IPED já estão ligadas.")
            else:
                say(f"AVISO: {msg}.", err=True)
                say("Cadastre à mão: Configurações > Admin > Integrações > Servidores de Ferramentas > + (MCP, URL e token acima).")
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:  # noqa: BLE001, S110
            pass

    threading.Thread(target=work, daemon=True).start()


def _healthy(url: str) -> bool:
    """O Open WebUI está pronto quando /api/config (o que a página pede ao abrir) responde com JSON válido."""
    try:
        cfg = _api("GET", url.rstrip("/") + "/api/config", None, None, 8)
        return isinstance(cfg, dict) and bool(cfg.get("name"))
    except Exception:  # noqa: BLE001
        return False


def log_tail(path: Path, lines: int = 15) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return "(sem log)"


def running_open_webui(*ports: int) -> str | None:
    """URL de um Open WebUI já respondendo nas portas dadas (o nosso, de uma execução anterior)."""
    import urllib.request

    for port in dict.fromkeys(ports):
        url = f"http://127.0.0.1:{port}/"
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310
                if b"open webui" in resp.read(200000).lower():
                    return url
        except Exception:  # noqa: BLE001, S112
            continue
    return None


UV_ZIP_URL = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"


def _get_uv() -> str | None:
    """Caminho de um `uv` (gerenciador que baixa o Python 3.11 sozinho): do PATH, via pip, ou baixado do GitHub."""
    found = shutil.which("uv")
    if found:
        return found
    tools = OPENWEBUI_HOME / "uvtool"
    uv_exe = tools / ("Scripts/uv.exe" if os.name == "nt" else "bin/uv")
    if uv_exe.is_file():
        return str(uv_exe)
    for py in python_candidates():  # qualquer Python (mesmo 3.13) serve só para instalar o uv
        if subprocess.call([str(py), "-m", "venv", str(tools)]) == 0:  # noqa: S603
            vpy = tools / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            if subprocess.call([str(vpy), "-m", "pip", "install", "--upgrade", "uv"]) == 0 and uv_exe.is_file():  # noqa: S603
                return str(uv_exe)
        break
    if os.name == "nt":  # sem nenhum Python: baixa o uv.exe
        try:
            import io
            import urllib.request
            import zipfile

            say("Baixando o uv (gerenciador do Python)…")
            with urllib.request.urlopen(UV_ZIP_URL, timeout=120) as resp:  # noqa: S310
                data = resp.read()
            tools.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for member in zf.namelist():
                    if member.lower().endswith("uv.exe"):
                        (tools / "uv.exe").write_bytes(zf.read(member))
                        return str(tools / "uv.exe")
        except Exception as exc:  # noqa: BLE001
            say(f"Não consegui baixar o uv: {exc}", err=True)
    return None


def install_open_webui(package: str = "open-webui") -> int:
    """Instala o Open WebUI numa venv própria (baixa centenas de MB; leva vários minutos).

    Usa um Python 3.11/3.12 do computador; se não houver (ex.: só 3.13), usa o `uv`, que baixa o 3.11 sozinho.
    """
    OPENWEBUI_HOME.mkdir(parents=True, exist_ok=True)
    venv = OPENWEBUI_HOME / "venv"
    vpy = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    py = find_python_311()
    if py is not None:
        say(f"Criando ambiente em {venv} com {' '.join(py)}…")
        if subprocess.call([*py, "-m", "venv", str(venv)]) != 0:  # noqa: S603
            say("ERRO ao criar o ambiente virtual.", err=True)
            return 1
        say("Instalando o Open WebUI (pode levar vários minutos; precisa de internet)…")
        code = subprocess.call([str(vpy), "-m", "pip", "install", "--upgrade", package])  # noqa: S603
    else:
        found = ", ".join(f"{p} ({python_version(p) or '?'})" for p in python_candidates()) or "nenhum"
        say(f"Não há Python 3.11/3.12 (encontrados: {found}). Vou usar o 'uv', que baixa o Python 3.11 sozinho.")
        uv = _get_uv()
        if uv is None:
            say("ERRO: não consegui obter o uv (precisa de internet). Instale o Python 3.11 em python.org e tente de novo.", err=True)
            return 1
        say(f"Criando ambiente em {venv} com Python 3.11 (via uv)…")
        if subprocess.call([uv, "venv", "--python", "3.11", str(venv)]) != 0:  # noqa: S603
            say("ERRO: o uv não conseguiu criar o ambiente com Python 3.11.", err=True)
            return 1
        say("Instalando o Open WebUI (pode levar vários minutos; precisa de internet)…")
        code = subprocess.call([uv, "pip", "install", "--python", str(vpy), "--upgrade", package])  # noqa: S603
    if code != 0:
        say("ERRO: a instalação do Open WebUI falhou (veja as mensagens acima).", err=True)
        return 1
    if package == "open-webui" and not OPENWEBUI_EXE.is_file():
        say("ERRO: instalou, mas não achei o open-webui.exe no ambiente.", err=True)
        return 1
    say("Open WebUI instalado. Agora inicie o chat com a IA.")
    return 0


def load_prompt() -> tuple[str, Path | None]:
    """Instruções do modelo: o `prompt.md` do próprio usuário (na pasta de dados do programa) ou o padrão do pacote."""
    for candidate in (CONFIG_PATH.parent / "prompt.md", ROOT / "prompts" / "analista.md"):
        try:
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8"), candidate
        except OSError:
            continue
    return "", None


def load_config() -> dict[str, str]:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict[str, str]) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # só deixa de lembrar na próxima vez


def pick_folder(title: str, initial: str = "") -> str:
    """Janela de escolha de pasta; sem ambiente gráfico, pergunta no console."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        chosen = filedialog.askdirectory(title=title, initialdir=initial or None)
        root.destroy()
        return chosen or ""
    except Exception:  # noqa: BLE001
        return input(f"{title}: ").strip().strip('"')


_LOG_FILE = None


def say(msg: str = "", *, err: bool = False) -> None:
    """Imprime e grava no arquivo de log (ultimo-log.txt) para diagnóstico."""
    global _LOG_FILE
    print(msg, file=sys.stderr if err else sys.stdout, flush=True)
    try:
        if _LOG_FILE is None:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            _LOG_FILE = open(LOG_PATH, "w", encoding="utf-8")  # noqa: SIM115
        _LOG_FILE.write(msg + "\n")
        _LOG_FILE.flush()
    except OSError:
        pass


def open_ports(count: int = 20) -> list[int]:
    return [p for p in range(WEBAPI_PORT, WEBAPI_PORT + count) if port_open("127.0.0.1", p)]


def _host_port(url: str) -> tuple[str, int]:
    host_port = url.split("//", 1)[-1].split("/", 1)[0]
    host, _, port = host_port.partition(":")
    return host, int(port or 80)


def run(args) -> int:  # noqa: C901 - roteiro linear
    cfg = load_config()
    iped_root = Path(args.root) if args.root else None
    if iped_root is None:
        chosen = cfg.get("iped_root") or pick_folder("Pasta do IPED (com jre e lib/iped-webapi.jar)")
        if not chosen:
            print("Cancelado: pasta do IPED não informada.", file=sys.stderr)
            return 2
        iped_root = Path(chosen)
    sources_file = Path(args.sources) if args.sources else None
    temp_sources: Path | None = None
    sources: list[dict[str, str]] = []
    if sources_file is not None:
        try:
            sources = json.loads(sources_file.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            sources = []
    if sources_file is None:
        cases = args.cases_dir or cfg.get("cases_dir") or pick_folder(
            "Pasta do caso indexado (ou pasta que contém vários casos)"
        )  # fmt: skip
        if not cases:
            print("Cancelado: pasta dos casos não informada.", file=sys.stderr)
            return 2
        sources = sources_from_cases_dir(Path(cases))
        if not sources:
            print(f"Nenhum caso do IPED encontrado em {cases} (procuro a pasta 'iped' ou IPED-SearchApp).",
                  file=sys.stderr)  # fmt: skip
            return 2
        temp_sources = Path(tempfile.mkdtemp(prefix="iped-mcp-")) / "multicases.json"
        temp_sources.write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")
        sources_file = temp_sources
        cfg["cases_dir"] = str(cases)
        print(f"Casos encontrados: {', '.join(s['id'] for s in sources)}")
    cfg["iped_root"] = str(iped_root)
    save_config(cfg)

    procs: list[subprocess.Popen] = []
    guards: list[OllamaGuard] = []
    webui_proc: subprocess.Popen | None = None
    try:
        say("[1/3] Subindo as Web APIs do IPED…")
        launcher = subprocess.Popen(
            [*self_command("webapi"), "--root", str(iped_root), "--sources", str(sources_file), "--host", "127.0.0.1", "--start-port", str(WEBAPI_PORT)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            encoding="utf-8", errors="replace", env=dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8"),
        )  # fmt: skip
        procs.append(launcher)
        lines: list[str] = []

        def pump() -> None:
            assert launcher.stdout is not None
            try:
                for line in launcher.stdout:
                    say("   " + line.rstrip())
                    lines.append(line)
            except Exception as exc:  # noqa: BLE001
                say(f"   (leitura da saída interrompida: {exc!r})", err=True)

        threading.Thread(target=pump, daemon=True).start()

        # Avisa de processos antigos (de uma execução anterior) ocupando as portas das Web APIs.
        stale = open_ports()
        if stale:
            say(f"   AVISO: as portas {stale} já estão em uso (execução anterior ainda aberta?). "
                "As Web APIs novas usarão a próxima porta livre; feche o processo antigo se algo não funcionar.")  # fmt: skip

        # Espera as Web APIs: primeiro pelas URLs que o launcher imprime (portas corretas); se essa linha não
        # chegar em 45 s, cai para a detecção por porta aberta.
        wanted = max(1, len(sources))
        waited = 0
        endpoints: dict[str, str] = {}
        while True:
            if launcher.poll() is not None:
                say(f"ERRO: o launcher das Web APIs encerrou (código {launcher.returncode}). Veja as mensagens acima.", err=True)
                return 1
            parsed = parse_endpoints(lines)
            if parsed and len(parsed) >= wanted and all(port_open(*_host_port(u)) for u in parsed.values()):
                endpoints = parsed
                break
            if waited >= 45 and not parsed:
                fresh = [p for p in open_ports() if p not in stale]
                if len(fresh) >= wanted:
                    ids = [src["id"] for src in sources] or [f"caso{i}" for i in range(1, wanted + 1)]
                    endpoints = {sid: f"http://127.0.0.1:{port}" for sid, port in zip(ids, fresh)}
                    say("   (a linha com as URLs não chegou; usando as portas novas que abriram)")
                    break
            if waited >= 240:
                say("ERRO: as Web APIs do IPED não abriram a porta em 4 minutos. Veja as mensagens acima.", err=True)
                return 1
            time.sleep(1)
            waited += 1
            if waited % 5 == 0:
                say(f"   ...aguardando o IPED abrir o caso ({waited}s)")
        say("   Web API(s) no ar: " + ", ".join(f"{sid} -> {url}" for sid, url in endpoints.items()))

        token = "" if args.no_token else secrets.token_urlsafe(24)
        wanted = args.mcp_port
        args.mcp_port = free_port(wanted)
        if args.mcp_port != wanted:
            say(f"AVISO: a porta {wanted} já está em uso (execução anterior ainda aberta?); usando {args.mcp_port}.")
        env = dict(os.environ, IPED_ENDPOINTS=endpoints_env(endpoints), IPED_MCP_PORT=str(args.mcp_port))
        env.pop("IPED_MCP_ALLOW_DESTRUCTIVE", None)  # remover marcadores se faz no próprio IPED: tools destrutivas sempre desligadas
        env.pop("IPED_URL", None)  # IPED_URL e IPED_ENDPOINTS juntas são erro no MCP
        env["IPED_MCP_TEXT_CHARS"] = str(text_chars_for(int(getattr(args, "num_ctx", 0) or NUM_CTX)))  # leituras curtas: cabem no contexto
        env["FASTMCP_CHECK_FOR_UPDATES"] = "off"  # sem consulta à internet na subida (pode demorar em rede restrita)
        if token:
            env["IPED_MCP_TOKEN"] = token
        say("[2/3] Subindo o MCP…")
        mcp = subprocess.Popen(self_command("mcp"), cwd=Path.cwd(), env=env)
        procs.append(mcp)
        started = time.monotonic()
        while not port_open("127.0.0.1", args.mcp_port):
            if mcp.poll() is not None or time.monotonic() - started > 120:
                say("ERRO: o MCP não subiu (veja as mensagens acima).", err=True)
                return 1
            time.sleep(0.5)
        say(f"   MCP no ar em {time.monotonic() - started:.0f}s.")

        ctx = int(getattr(args, "num_ctx", 0) or NUM_CTX)
        guard = OllamaGuard(args.ollama_url, port=free_port(GUARD_PORT), default_ctx=ctx, log=lambda m: say(f"   [contexto] {m}")).start()
        guards.append(guard)
        say(f"   Guarda de contexto no ar em {guard.url} (Ollama em {args.ollama_url}).")
        webui_url = f"http://127.0.0.1:{args.webui_port}"
        prompt_text, prompt_file = load_prompt()
        setup = {
            "mcp_url": f"http://127.0.0.1:{args.mcp_port}/mcp",
            "mcp_token": token or "",
            "system_prompt": prompt_text,
            "model": getattr(args, "modelo", "") or "",
            "tools": normalize(getattr(args, "ferramentas", "") or "") or list(DEFAULT),
            "num_ctx": int(getattr(args, "num_ctx", 0) or 0),
            "ollama_url": guard.url,
        }
        if not args.no_webui:
            running = running_open_webui(args.webui_port)
            webui_cmd = None if running else find_open_webui_command()
            if running:
                say(f"[3/3] Open WebUI já está aberto em {running}; usando esse.")
                webui_url = running
                finish_open_webui(running, None, setup)
            elif webui_cmd:
                say(f"[3/3] Subindo o Open WebUI ({' '.join(webui_cmd)}); a primeira vez demora…")
                OPENWEBUI_HOME.mkdir(parents=True, exist_ok=True)
                wenv = dict(os.environ, OLLAMA_BASE_URL=guard.url, DATA_DIR=str(OPENWEBUI_HOME / "data-local"), WEBUI_AUTH="False")  # sem login: só 127.0.0.1
                # Busca em documentos (RAG) do Open WebUI usaria um modelo local baixado do HuggingFace; aponta para o Ollama
                # (só vale numa instalação nova do Open WebUI: configurações já gravadas no banco dele prevalecem).
                wenv.setdefault("RAG_EMBEDDING_ENGINE", "ollama")
                wenv.setdefault("RAG_OLLAMA_BASE_URL", args.ollama_url)
                wenv.update(PYTHONUTF8="1", ANONYMIZED_TELEMETRY="false", SCARF_NO_ANALYTICS="true", DO_NOT_TRACK="true")
                log_file = open(OPENWEBUI_HOME / "open-webui.log", "ab")  # noqa: SIM115  (saída ruidosa vai para arquivo)
                say(f"   (saída do Open WebUI em {OPENWEBUI_HOME / 'open-webui.log'})")
                webui_proc = subprocess.Popen(  # noqa: S603
                    [*webui_cmd, "serve", "--host", "127.0.0.1", "--port", str(args.webui_port)], env=wenv, stdout=log_file, stderr=subprocess.STDOUT
                )
                procs.append(webui_proc)
                finish_open_webui(webui_url, args.webui_port, setup, webui_proc)
            else:
                say("[3/3] Não achei o Open WebUI neste computador. Pythons encontrados:")
                for py in python_candidates() or []:
                    say(f"      {py} ({python_version(py) or '?'})")
                say("      Use 'Localizar Open WebUI' para apontar o open-webui.exe, ou 'Instalar Open WebUI'.")

        say("\n=== PRONTO ===")
        say(f"MCP:        http://127.0.0.1:{args.mcp_port}/mcp")
        say(f"Token:      {token or '(sem token)'}  -> Authorization: Bearer <token>")
        say(f"Open WebUI: {webui_url}")
        say("O Open WebUI abre sozinho já configurado (modelo 'Analista IPED'); se a configuração automática falhar, aparece um aviso.")
        say(f"Instruções do modelo: {prompt_file or '(nenhuma)'}")
        say("Auditoria das chamadas: audit/iped-mcp-audit.jsonl (confira com --modo verificar)")
        say("Ctrl+C encerra tudo.")
        names = {id(launcher): "Web APIs do IPED", id(mcp): "servidor MCP"}
        if webui_proc is not None:
            names[id(webui_proc)] = "Open WebUI"
        while True:
            ended = [p for p in procs if p.poll() is not None]
            if not ended:
                time.sleep(1)
                continue
            if all(p is webui_proc for p in ended) and port_open("127.0.0.1", args.webui_port):
                procs.remove(webui_proc)  # nosso Open WebUI saiu porque já havia outro na porta: segue com ele
                webui_proc = None
                continue
            break
        for p in ended:
            say(f"Terminou: {names.get(id(p), 'processo')} (código {p.returncode}).", err=True)
            if p is webui_proc:
                say(f"Fim do log ({OPENWEBUI_HOME / 'open-webui.log'}):", err=True)
                say(log_tail(OPENWEBUI_HOME / "open-webui.log", 30), err=True)
        say("Um dos processos terminou; encerrando o resto.", err=True)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for g in guards:
            g.stop()
        for p in reversed(procs):
            stop(p)
        if temp_sources is not None:
            shutil.rmtree(temp_sources.parent, ignore_errors=True)
