"""Progresso do chat em etapas e uso do contexto (sem interface; a janela só desenha).

O launcher imprime linhas conhecidas ao subir cada parte; `Tracker` converte essas linhas em "quantas etapas já terminaram".
O uso do contexto vem de um arquivo com números gravado pela guarda (`ollama_guard`): nenhum texto de conversa passa por ele.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

STAGES = ("IPED", "MCP", "Guarda", "Open WebUI", "Pronto")

# (trecho da linha, etapas concluídas depois dela)
_MARKS = (
    ("[1/3]", 0),
    ("Web API(s) no ar", 1),
    ("[2/3]", 1),
    ("MCP no ar", 2),
    ("Guarda de contexto no ar", 3),
    ("[3/3]", 3),
    ("=== PRONTO ===", 5),
)


class Tracker:
    """Acompanha o chat subindo. `done` só cresce; `active` é a etapa em andamento (None quando acabou)."""

    def __init__(self) -> None:
        self.done = 0

    def feed(self, line: str) -> int:
        for mark, count in _MARKS:
            if mark in line and count > self.done:
                self.done = count
        return self.done

    @property
    def active(self) -> int | None:
        return self.done if self.done < len(STAGES) else None

    @property
    def fraction(self) -> float:
        return min(1.0, self.done / len(STAGES))

    @property
    def label(self) -> str:
        names = ("Subindo o IPED…", "Subindo o servidor MCP…", "Ligando a guarda de contexto…", "Abrindo o Open WebUI…", "Pronto")
        return names[min(self.done, len(names) - 1)]


# ------------------------------------------------------------------------------------------ contexto
@dataclass(frozen=True)
class ContextUse:
    percent: float | None
    level: str  # ok | warn | danger | none
    text: str


def context_use(stats: dict | None) -> ContextUse:
    """Percentual da janela usado na última chamada (tokens lidos + gerados) e a velocidade, em português."""
    if not stats or not stats.get("ctx"):
        return ContextUse(None, "none", "Sem conversa ainda")
    used, ctx = int(stats.get("used") or 0), int(stats["ctx"])
    pct = min(100.0, 100.0 * used / ctx)
    level = "danger" if pct >= 85 else "warn" if pct >= 70 else "ok"
    text = f"{used / 1000:.1f}k de {ctx / 1000:.0f}k tokens"
    if stats.get("tps"):
        text += f" · {str(stats['tps']).replace('.', ',')} tok/s"
    if pct >= 80:
        text += " · contexto quase cheio: comece uma nova conversa"
    return ContextUse(pct, level, text)


def read_stats(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
