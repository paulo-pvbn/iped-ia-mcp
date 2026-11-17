"""Fontes de IA locais além do Ollama: o servidor do LM Studio (API compatível com a da OpenAI, só neste computador)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

LMSTUDIO_URL = "http://127.0.0.1:1234"
OLLAMA_URL = "http://127.0.0.1:11434"
LMSTUDIO_HINT = "No LM Studio: aba Developer (ícone </>) → ligue o servidor local (Status: Running, porta 1234) e carregue um modelo."


def _get_json(url: str, timeout: float = 3) -> object:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310  (127.0.0.1)
        return json.load(resp)


def lmstudio_models(url: str = LMSTUDIO_URL, fetch: Callable[[str], object] | None = None) -> list[str] | None:
    """Modelos de conversa que o servidor do LM Studio oferece; None se o servidor não respondeu.

    Modelos de embeddings são ignorados (não conversam). O LM Studio só lista aqui o que está disponível no servidor.
    """
    try:
        data = (fetch or _get_json)(url.rstrip("/") + "/v1/models")
    except Exception:  # noqa: BLE001
        return None
    items = data.get("data", []) if isinstance(data, dict) else []
    ids = [str(m.get("id", "")) for m in items if isinstance(m, dict) and m.get("id")]
    return sorted(i for i in ids if "embed" not in i.lower())


def lmstudio_installed() -> bool:
    """Há sinal de LM Studio neste computador (programa ou pasta de dados do usuário)?"""
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    local = os.environ.get("LOCALAPPDATA")
    paths = [home / ".lmstudio", home / ".cache" / "lm-studio"]
    if local:
        paths.append(Path(local) / "Programs" / "LM Studio")
    return any(p.exists() for p in paths)


def choose_backend(preferred: str, model: str, ollama: list[str] | None, lmstudio: list[str] | None) -> str:
    """'ollama' ou 'lmstudio'. `preferred` pode ser 'auto' (padrão), 'ollama' ou 'lmstudio'."""
    if preferred in ("ollama", "lmstudio"):
        return preferred
    if model and lmstudio and model in lmstudio:
        return "lmstudio"
    if model and ollama and model in ollama:
        return "ollama"
    if ollama:
        return "ollama"
    if lmstudio:
        return "lmstudio"
    return "ollama"


def lms_exe() -> str | None:
    """A linha de comando `lms` do LM Studio (no PATH ou em ~/.lmstudio/bin)."""
    found = shutil.which("lms")
    if found:
        return found
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    for name in ("lms.exe", "lms"):
        if (home / ".lmstudio" / "bin" / name).is_file():
            return str(home / ".lmstudio" / "bin" / name)
    return None


def parse_lms_ls(text: str) -> list[str]:
    """Modelos de conversa da saída de `lms ls` (a seção LLM; ignora a de EMBEDDING)."""
    models: list[str] = []
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        head = line.split()[0]
        if head in ("LLM", "EMBEDDING"):
            section = head
            continue
        if section == "LLM" and "/" in head and not line.startswith(("You have", "(")):
            models.append(head)
    return models


def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    from iped_mcp import webui_launcher as wl  # fora do topo: o launcher importa este módulo

    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace", check=False, **wl.hidden_kwargs())  # noqa: S603
        return out.returncode, (out.stdout or "") + (out.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def ensure_lmstudio_server(
    say: Callable[[str], None], *, run: Callable[[list[str]], tuple[int, str]] | None = None, sleep: Callable[[float], None] = time.sleep,
    models: Callable[[], list[str] | None] | None = None, exe: str | None = None,
) -> list[str] | None:
    """Liga o servidor local do LM Studio (`lms server start`) e espera os modelos aparecerem. None se não deu.

    Se o servidor sobe mas nenhum modelo aparece, tenta carregar o primeiro modelo de conversa (`lms load`); esses
    comandos seguem a ajuda do `lms` e não foram validados contra todas as versões.
    """
    run = run or (lambda c: _run(c, 600))
    models = models or lmstudio_models
    have = models()
    if have:
        return have
    exe = exe or lms_exe()
    if not exe:
        say("   LM Studio: não achei o programa `lms` (instale o LM Studio e abra-o uma vez).")
        return None
    if have is None:
        say("   LM Studio: ligando o servidor local ($ lms server start)…")
        code, out = run([exe, "server", "start"])
        if code != 0:
            say(f"   LM Studio: `lms server start` falhou (código {code}): {out.strip()[:200]}")
    for _ in range(20):
        have = models()
        if have is not None:
            break
        sleep(1)
    if have is None:
        say("   LM Studio: o servidor não respondeu. " + LMSTUDIO_HINT)
        return None
    if have:
        return have
    code, listing = run([exe, "ls"])
    chat = parse_lms_ls(listing) if code == 0 else []
    if not chat:
        say("   LM Studio: servidor ligado, mas não achei modelo de conversa baixado.")
        return []
    say(f"   LM Studio: carregando {chat[0]} ($ lms load)…")
    code, out = run([exe, "load", chat[0], "--context-length", "32768", "-y"])
    if code != 0:
        say(f"   LM Studio: não consegui carregar sozinho ({out.strip()[:200]}). Carregue o modelo na aba Developer.")
    for _ in range(10):
        have = models()
        if have:
            return have
        sleep(1)
    return have or []
