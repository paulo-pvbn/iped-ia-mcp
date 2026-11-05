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


def find_open_webui_command() -> list[str] | None:
    """Comando para subir o Open WebUI: o executável ou, se só o módulo estiver instalado, `python -m open_webui`."""
    exe = find_open_webui()
    if exe:
        return [exe]
    probe = "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('open_webui') else 1)"
    for py in (["py", "-3.11"], ["py", "-3.12"], ["py"], ["python"], ["python3"]):
        if not shutil.which(py[0]):
            continue
        try:
            if subprocess.run([*py, "-c", probe], capture_output=True, timeout=30).returncode == 0:  # noqa: S603
                return [*py, "-m", "open_webui"]
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def find_python_311() -> list[str] | None:
    """Comando de um Python 3.11/3.12 (o Open WebUI exige >=3.11,<3.13); None se não houver."""
    candidates = [["py", "-3.11"], ["py", "-3.12"], ["python3.11"], ["python3.12"], ["python"], ["python3"]]
    for cmd in candidates:
        if not shutil.which(cmd[0]):
            continue
        try:
            out = subprocess.run(  # noqa: S603
                [*cmd, "-c", "import sys;print(sys.version_info[:2])"], capture_output=True, text=True, timeout=20
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        if out in ("(3, 11)", "(3, 12)"):
            return cmd
    return None


def install_open_webui() -> int:
    """Cria uma venv própria e instala o Open WebUI nela (baixa centenas de MB; leva vários minutos)."""
    py = find_python_311()
    if py is None:
        say("ERRO: não achei Python 3.11 ou 3.12 (o Open WebUI não roda no 3.13+).", err=True)
        say("Instale o Python 3.11 em python.org (marque 'Add to PATH') e clique de novo, ou use Docker.", err=True)
        return 1
    venv = OPENWEBUI_HOME / "venv"
    OPENWEBUI_HOME.mkdir(parents=True, exist_ok=True)
    say(f"Criando ambiente em {venv} com {' '.join(py)}…")
    if subprocess.call([*py, "-m", "venv", str(venv)]) != 0:  # noqa: S603
        say("ERRO ao criar o ambiente virtual.", err=True)
        return 1
    vpy = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    say("Instalando o Open WebUI (pode levar vários minutos; precisa de internet)…")
    code = subprocess.call([str(vpy), "-m", "pip", "install", "--upgrade", "open-webui"])  # noqa: S603
    if code != 0 or not OPENWEBUI_EXE.is_file():
        say("ERRO: a instalação do Open WebUI falhou (veja as mensagens acima).", err=True)
        return 1
    say("Open WebUI instalado. Agora clique em 'Chat com IA'.")
    return 0


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
        if args.sem_destrutivas:
            env.pop("IPED_MCP_ALLOW_DESTRUCTIVE", None)
        else:  # a análise precisa limpar marcadores; cada chamada fica na auditoria
            env["IPED_MCP_ALLOW_DESTRUCTIVE"] = "1"
        env.pop("IPED_URL", None)  # IPED_URL e IPED_ENDPOINTS juntas são erro no MCP
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

        if not args.no_webui:
            webui_cmd = find_open_webui_command()
            if port_open("127.0.0.1", args.webui_port):
                say(f"[3/3] Já existe algo respondendo na porta {args.webui_port} (Open WebUI aberto?); usando esse.")
            elif webui_cmd:
                say(f"[3/3] Subindo o Open WebUI ({' '.join(webui_cmd)}); a primeira vez demora…")
                OPENWEBUI_HOME.mkdir(parents=True, exist_ok=True)
                wenv = dict(os.environ, OLLAMA_BASE_URL=args.ollama_url, DATA_DIR=str(OPENWEBUI_HOME / "data"))
                wenv.update(PYTHONUTF8="1", ANONYMIZED_TELEMETRY="false", SCARF_NO_ANALYTICS="true", DO_NOT_TRACK="true")
                procs.append(subprocess.Popen([*webui_cmd, "serve", "--port", str(args.webui_port)], env=wenv))  # noqa: S603
            else:
                say("[3/3] Não achei o Open WebUI: use 'Localizar Open WebUI' na janela para apontar o open-webui.exe,")
                say("      ou 'Instalar Open WebUI' (precisa de Python 3.11), ou suba o seu e aponte para este MCP.")

        prompt_file = ROOT / "prompts" / "analista.md"
        say("\n=== PRONTO ===")
        say(f"MCP:        http://127.0.0.1:{args.mcp_port}/mcp")
        say(f"Token:      {token or '(sem token)'}  -> Authorization: Bearer <token>")
        say(f"Open WebUI: http://127.0.0.1:{args.webui_port}")
        say("No Open WebUI: Admin Settings > External Tools > + > MCP (Streamable HTTP) > URL e token acima.")
        say(f"Prompt do analista (cole nas instruções do modelo): {prompt_file}")
        say("Auditoria das chamadas: audit/iped-mcp-audit.jsonl (confira com --modo verificar)")
        say("Ctrl+C encerra tudo.")
        while all(p.poll() is None for p in procs):
            time.sleep(1)
        say("Um dos processos terminou; encerrando o resto.", err=True)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for p in reversed(procs):
            stop(p)
        if temp_sources is not None:
            shutil.rmtree(temp_sources.parent, ignore_errors=True)
