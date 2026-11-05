#!/usr/bin/env python3
"""Sobe tudo para conversar com os casos do IPED no Open WebUI: Web APIs do IPED + MCP + Open WebUI.

    python iniciar-webui.py --root "C:\\CIAF\\IPED431\\iped-4.3.1" --sources multicases.json

Ordem: (1) web-api-launcher.py (uma Web API por caso, só em 127.0.0.1), (2) o MCP (main.py) apontando para elas,
com token, (3) o Open WebUI, se estiver instalado (``pip install open-webui``; Python 3.11). Ctrl+C encerra tudo.
Depois, no Open WebUI: Admin Settings > External Tools > + > MCP (Streamable HTTP), com a URL e o token mostrados.

Nada aqui foi validado contra o Open WebUI/IPED reais: é um roteiro automatizado dos passos manuais.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENDPOINT_RE = re.compile(r"^-\s+(?P<id>.+?):\s+(?P<url>https?://\S+?)/?\s+\(PID\s+\d+\)")
READY_MARK = "SERVIDORES IPED INICIADOS"


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


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, required=True, help="pasta do IPED (com jre/ e lib/iped-webapi.jar)")
    ap.add_argument("--sources", type=Path, default=Path("multicases.json"))
    ap.add_argument("--mcp-port", type=int, default=8000)
    ap.add_argument("--webui-port", type=int, default=3000)
    ap.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    ap.add_argument("--no-webui", action="store_true", help="sobe só as Web APIs e o MCP")
    ap.add_argument("--no-token", action="store_true", help="MCP sem token (só se tudo for local)")
    args = ap.parse_args()

    procs: list[subprocess.Popen] = []
    try:
        # 1) Web APIs do IPED
        print("[1/3] Subindo as Web APIs do IPED…")
        launcher = subprocess.Popen(
            [sys.executable, "-u", str(HERE / "web-api-launcher.py"), "--root", str(args.root),
             "--sources", str(args.sources), "--host", "127.0.0.1"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
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

        # 2) MCP
        token = "" if args.no_token else secrets.token_urlsafe(24)
        env = dict(os.environ, IPED_ENDPOINTS=endpoints_env(endpoints), IPED_MCP_PORT=str(args.mcp_port))
        env.pop("IPED_URL", None)  # IPED_URL e IPED_ENDPOINTS juntas são erro no MCP
        if token:
            env["IPED_MCP_TOKEN"] = token
        print("[2/3] Subindo o MCP…")
        mcp = subprocess.Popen([sys.executable, str(HERE / "main.py")], cwd=HERE, env=env)
        procs.append(mcp)
        deadline = time.monotonic() + 60
        while not port_open("127.0.0.1", args.mcp_port):
            if mcp.poll() is not None or time.monotonic() > deadline:
                print("ERRO: o MCP não subiu (veja as mensagens acima).", file=sys.stderr)
                return 1
            time.sleep(0.5)

        # 3) Open WebUI
        if not args.no_webui:
            exe = shutil.which("open-webui")
            if exe:
                print("[3/3] Subindo o Open WebUI…")
                wenv = dict(os.environ, OLLAMA_BASE_URL=args.ollama_url)
                procs.append(subprocess.Popen([exe, "serve", "--port", str(args.webui_port)], env=wenv))
            else:
                print("[3/3] Open WebUI não encontrado: instale com 'pip install open-webui' (Python 3.11) ou use")
                print("      Docker e aponte para este MCP com host.docker.internal.")

        print("\n=== PRONTO ===")
        print(f"MCP:        http://127.0.0.1:{args.mcp_port}/mcp")
        print(f"Token:      {token or '(sem token)'}  -> Authorization: Bearer <token>")
        print(f"Open WebUI: http://127.0.0.1:{args.webui_port}")
        print("No Open WebUI: Admin Settings > External Tools > + > MCP (Streamable HTTP) > URL e token acima.")
        print(f"Prompt do analista (cole nas instruções do modelo): {HERE / 'prompts' / 'analista.md'}")
        print("Auditoria das chamadas: audit/iped-mcp-audit.jsonl (confira com: python -m iped_mcp.security)")
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


if __name__ == "__main__":
    raise SystemExit(main())
