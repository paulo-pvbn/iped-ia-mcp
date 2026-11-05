"""Ponto de entrada único (script e executável).

    iped-ia-mcp.exe                 -> assistente: pergunta as pastas e sobe Web APIs + MCP + Open WebUI
    iped-ia-mcp.exe --modo mcp      -> só o servidor MCP (usado internamente)
    iped-ia-mcp.exe --modo webapi   -> só o web-api-launcher.py (usado internamente)
    iped-ia-mcp.exe --modo verificar [arquivo]   -> confere a cadeia de hash da auditoria
    iped-ia-mcp.exe --selftest      -> confere que tudo foi empacotado (usado no CI)
"""

from __future__ import annotations

import argparse
import asyncio
import runpy
import sys
from pathlib import Path

HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
for path in (HERE, HERE / "src"):  # script: usa o repositório; executável: o que foi empacotado
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

VERSION = "0.2.0"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modo", choices=["iniciar", "mcp", "webapi", "verificar"], default="iniciar")
    ap.add_argument("--version", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--root", help="pasta do IPED (com jre/ e lib/iped-webapi.jar)")
    ap.add_argument("--sources", help="multicases.json (padrão: gerado a partir da pasta de casos)")
    ap.add_argument("--cases-dir", help="pasta de um caso ou que contém vários casos")
    ap.add_argument("--mcp-port", type=int, default=8000)
    ap.add_argument("--webui-port", type=int, default=3000)
    ap.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    ap.add_argument("--no-webui", action="store_true")
    ap.add_argument("--no-token", action="store_true")
    args, rest = ap.parse_known_args(argv)

    if args.version:
        print(f"iped-ia-mcp {VERSION}")
        return 0
    if args.selftest:
        import main as server  # main.py do repositório (empacotado como módulo)

        tools = asyncio.run(server.mcp.list_tools())
        prompt = HERE / "prompts" / "analista.md"
        ok = len(tools) >= 30 and prompt.is_file() and (HERE / "web-api-launcher.py").is_file()
        print(f"selftest {'ok' if ok else 'FALHOU'}: {len(tools)} tools, prompt={prompt.is_file()}")
        return 0 if ok else 1
    if args.modo == "mcp":
        import main as server

        server.main()
        return 0
    if args.modo == "webapi":
        sys.argv = ["web-api-launcher.py", "--root", args.root or ".", "--sources", args.sources or "multicases.json"]
        sys.argv += rest
        runpy.run_path(str(HERE / "web-api-launcher.py"), run_name="__main__")
        return 0
    if args.modo == "verificar":
        from iped_mcp.security import SecurityConfig, verify_chain

        target = Path(rest[0]) if rest else SecurityConfig.from_env().audit_path
        if not target.is_file():
            print(f"Arquivo de auditoria não encontrado: {target}", file=sys.stderr)
            return 2
        ok, n, why = verify_chain(target)
        print(f"{'ÍNTEGRO' if ok else 'ADULTERADO'}: {n} registros verificados" + (f" ({why})" if why else ""))
        return 0 if ok else 1

    from iped_mcp import webui_launcher

    return webui_launcher.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
