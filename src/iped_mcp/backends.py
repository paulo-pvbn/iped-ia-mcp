"""Fontes de IA locais além do Ollama: o servidor do LM Studio (API compatível com a da OpenAI, só neste computador)."""

from __future__ import annotations

import json
import os
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
