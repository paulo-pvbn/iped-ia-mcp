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
import threading
import time
from pathlib import Path

ENDPOINT_RE = re.compile(r"^-\s+(?P<id>.+?):\s+(?P<url>https?://\S+?)/?\s+\(PID\s+\d+\)")
READY_MARK = "SERVIDORES IPED INICIADOS"
FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))  # onde estão main.py e prompts/
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


def sources_from_cases_dir(cases_dir: Path) -> list[dict[str, str]]:
    """Fontes ``[{"id","path"}]`` para uma pasta que é um caso ou que contém vários casos."""
    cases = [cases_dir] if looks_like_case(cases_dir) else sorted(
        p for p in cases_dir.iterdir() if p.is_dir() and looks_like_case(p)
    )  # fmt: skip
    used: set[str] = set()
    out = []
    for case in cases:
        base = re.sub(r"[^A-Za-z0-9_-]+", "_", case.name).strip("_") or "caso"
        sid, n = base, 2
        while sid in used:
            sid, n = f"{base}_{n}", n + 1
        used.add(sid)
        out.append({"id": sid, "path": str(case)})
    return out


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
        print("[1/3] Subindo as Web APIs do IPED…")
        launcher = subprocess.Popen(
            [*self_command("webapi"), "--root", str(iped_root), "--sources", str(sources_file), "--host", "127.0.0.1"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=dict(os.environ, PYTHONUNBUFFERED="1"),
        )  # fmt: skip
        procs.append(launcher)
        lines: list[str] = []
        ready = threading.Event()

        def pump() -> None:
            assert launcher.stdout is not None
            for line in launcher.stdout:
                print("   " + line.rstrip())
                lines.append(line)
                if READY_MARK in line:
                    ready.set()
            ready.set()

        threading.Thread(target=pump, daemon=True).start()
        if not ready.wait(180) or launcher.poll() is not None:
            print("ERRO: as Web APIs do IPED não subiram (veja as mensagens acima).", file=sys.stderr)
            return 1
        time.sleep(1.0)  # o bloco com as URLs é impresso logo depois da marca
        endpoints = parse_endpoints(lines)
        if not endpoints:
            print("ERRO: não consegui ler as URLs impressas pelo web-api-launcher.py.", file=sys.stderr)
            return 1

        token = "" if args.no_token else secrets.token_urlsafe(24)
        env = dict(os.environ, IPED_ENDPOINTS=endpoints_env(endpoints), IPED_MCP_PORT=str(args.mcp_port))
        if args.sem_destrutivas:
            env.pop("IPED_MCP_ALLOW_DESTRUCTIVE", None)
        else:  # a análise precisa limpar marcadores; cada chamada fica na auditoria
            env["IPED_MCP_ALLOW_DESTRUCTIVE"] = "1"
        env.pop("IPED_URL", None)  # IPED_URL e IPED_ENDPOINTS juntas são erro no MCP
        if token:
            env["IPED_MCP_TOKEN"] = token
        print("[2/3] Subindo o MCP…")
        mcp = subprocess.Popen(self_command("mcp"), cwd=Path.cwd(), env=env)
        procs.append(mcp)
        deadline = time.monotonic() + 90
        while not port_open("127.0.0.1", args.mcp_port):
            if mcp.poll() is not None or time.monotonic() > deadline:
                print("ERRO: o MCP não subiu (veja as mensagens acima).", file=sys.stderr)
                return 1
            time.sleep(0.5)

        if not args.no_webui:
            exe = shutil.which("open-webui")
            if exe:
                print("[3/3] Subindo o Open WebUI…")
                wenv = dict(os.environ, OLLAMA_BASE_URL=args.ollama_url)
                procs.append(subprocess.Popen([exe, "serve", "--port", str(args.webui_port)], env=wenv))
            else:
                print("[3/3] Open WebUI não encontrado: instale com 'pip install open-webui' (Python 3.11) ou use")
                print("      Docker e aponte para este MCP com host.docker.internal.")

        prompt_file = ROOT / "prompts" / "analista.md"
        print("\n=== PRONTO ===")
        print(f"MCP:        http://127.0.0.1:{args.mcp_port}/mcp")
        print(f"Token:      {token or '(sem token)'}  -> Authorization: Bearer <token>")
        print(f"Open WebUI: http://127.0.0.1:{args.webui_port}")
        print("No Open WebUI: Admin Settings > External Tools > + > MCP (Streamable HTTP) > URL e token acima.")
        print(f"Prompt do analista (cole nas instruções do modelo): {prompt_file}")
        print("Auditoria das chamadas: audit/iped-mcp-audit.jsonl (confira com --modo verificar)")
        print("Ctrl+C encerra tudo.")
        while all(p.poll() is None for p in procs):
            time.sleep(1)
        print("Um dos processos terminou; encerrando o resto.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for p in reversed(procs):
            stop(p)
        if temp_sources is not None:
            shutil.rmtree(temp_sources.parent, ignore_errors=True)
