"""Funções de apoio da janela do iped-ia-mcp (a interface está em ``gui_app``, o assistente em ``wizard``).

A indexação chama o IPED com a sintaxe padrão ``-d <origem> -o <destino>``. Não foi validada aqui contra o
IPED real: a linha de comando completa aparece no log antes de rodar.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from iped_mcp import hardware as hw
from iped_mcp import tools_catalog as tc
from iped_mcp import webui_launcher as wl

APP_TITLE = "IPED-IA MCP"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW


def hidden_kwargs() -> dict:
    """Argumentos do `subprocess` para o processo filho não abrir janela preta no Windows (vazio nos outros sistemas)."""
    return wl.hidden_kwargs()


# ------------------------------------------------------------------------------- lógica (sem interface)
def find_java(iped_root: Path) -> str:
    """Java do próprio IPED (``jre``) ou, na falta dele, o do sistema."""
    for rel in ("jre/bin/java.exe", "jre/bin/java"):
        candidate = iped_root / rel
        if candidate.is_file():
            return str(candidate)
    return "java"


def build_index_command(iped_root: Path, origem: Path, destino: Path) -> list[str]:
    """Linha de comando de indexação: ``java -jar iped.jar -d <origem> -o <destino>``."""
    return [find_java(iped_root), "-jar", str(iped_root / "iped.jar"), "-d", str(origem), "-o", str(destino)]


def check_index_inputs(iped_root: str, origem: str, destino: str) -> str:
    """Mensagem de erro em português, ou ``""`` se estiver tudo certo."""
    if not iped_root or not (Path(iped_root) / "iped.jar").is_file():
        return "Informe a pasta do IPED (a que contém o iped.jar)."
    if not origem or not Path(origem).exists():
        return "Informe a ORIGEM (arquivo de imagem ou pasta com as evidências)."
    if not destino:
        return "Informe o DESTINO (pasta onde o caso será gravado)."
    return ""


APP_NAMES = ("IPED-SearchApp.exe", "IPED-SearchApp")


def case_app(destino: Path) -> Path | None:
    """Executável do SearchApp diretamente dentro da pasta do caso, se existir."""
    for name in APP_NAMES:
        candidate = destino / name
        if candidate.is_file():
            return candidate
    return None


def find_case_app(folder: Path, depth: int = 2) -> Path | None:
    """Procura o SearchApp na pasta, nas subpastas (até `depth` níveis) e na pasta-mãe.

    Serve para caso já indexado: o usuário pode apontar a pasta do caso, a subpasta
    `iped` ou a pasta acima dela.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return None
    level = [folder]
    for _ in range(depth + 1):
        nxt: list[Path] = []
        for current in level:
            found = case_app(current)
            if found:
                return found
            try:
                nxt.extend(sorted(c for c in current.iterdir() if c.is_dir()))
            except OSError:
                continue
        level = nxt
    return case_app(folder.parent) if folder.name.lower() == "iped" else None


def launch_detached(app: Path) -> None:  # pragma: no cover - depende do SO
    """Abre o SearchApp sem prender a janela; cwd = pasta do executável."""
    if os.name == "nt":
        os.startfile(str(app))  # noqa: S606
    else:
        subprocess.Popen([str(app)], cwd=str(app.parent), start_new_session=True)  # noqa: S603


def ollama_models(url: str = "http://127.0.0.1:11434") -> list[str]:
    """Modelos baixados no Ollama (lista vazia se ele não estiver aberto)."""
    try:
        data = wl._api("GET", url.rstrip("/") + "/api/tags", None, None, 4)
        return sorted(m["name"] for m in (data or {}).get("models", []))
    except Exception:  # noqa: BLE001
        return []


def ollama_info(url: str = "http://127.0.0.1:11434") -> tuple[list[dict], dict | None]:
    """(modelos de /api/tags com tamanho, modelos carregados de /api/ps); vazios se o Ollama não responder."""
    base = url.rstrip("/")
    try:
        tags = (wl._api("GET", base + "/api/tags", None, None, 4) or {}).get("models", [])
    except Exception:  # noqa: BLE001
        tags = []
    try:
        ps = wl._api("GET", base + "/api/ps", None, None, 4)
    except Exception:  # noqa: BLE001
        ps = None
    return tags, ps


def hardware_summary(ctx: int = wl.NUM_CTX) -> tuple[hw.Hardware, list[hw.ModelFit], list[tuple[str, float]], str]:
    """Detecta GPU/RAM e confronta com os modelos do Ollama. Devolve (hardware, ajustes, carregados, relatório)."""
    found = hw.detect()
    tags, ps = ollama_info()
    fits = hw.fit_models(tags, found.vram_gb, ctx)
    loaded = hw.loaded_models(ps)
    return found, fits, loaded, hw.report(found, fits, loaded, ctx)


def big_model_hint(modelo: str) -> str:
    """Aviso quando o modelo é grande para uma GPU de 8 GB (quantização padrão: ~0,6 GB por bilhão de parâmetros)."""
    import re as _re

    match = _re.search(r"(\d+(?:\.\d+)?)\s*b\b", modelo.lower())
    if match and float(match.group(1)) > 9:
        return (f"Aviso: '{modelo}' é um modelo grande; em GPU de 8 GB ele não cabe inteiro e fica lento (parte vai para a CPU). "
                "Modelos de 4B a 8B (ex.: qwen3:8b) respondem bem mais rápido.")
    return ""


def chat_command(
    iped_root: str, cases_dir: str, *, sem_destrutivas: bool = False, modelo: str = "", ferramentas: object = None, num_ctx: int = 0
) -> list[str]:
    cmd = [*wl.self_command("iniciar"), "--root", iped_root, "--cases-dir", cases_dir]
    if modelo:
        cmd += ["--modelo", modelo]
    if num_ctx:
        cmd += ["--num-ctx", str(int(num_ctx))]
    chosen = tc.normalize(ferramentas) if ferramentas else []
    if chosen:
        cmd += ["--ferramentas", ",".join(chosen)]
    if sem_destrutivas:
        cmd.append("--sem-destrutivas")
    return cmd


def kill_tree(proc: subprocess.Popen) -> None:
    wl.stop(proc)


def hide_console() -> None:  # pragma: no cover
    """Esconde a janela preta quando o .exe foi aberto com duplo clique (Windows)."""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel, user = ctypes.windll.kernel32, ctypes.windll.user32  # type: ignore[attr-defined]
        hwnd = kernel.GetConsoleWindow()
        pids = (ctypes.c_ulong * 2)()
        if hwnd and kernel.GetConsoleProcessList(pids, 2) <= 1:  # só nós usamos este console
            user.ShowWindow(hwnd, 0)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:  # pragma: no cover
    from iped_mcp import gui_app

    return gui_app.main()
