"""Ajuste de desempenho: contexto, variáveis do Ollama e sugestão de modelo, a partir do hardware (sem interface).

Tudo é estimativa; o que vale é o que o Ollama mede (`/api/ps`). As variáveis do Ollama citadas são as documentadas por ele
(OLLAMA_FLASH_ATTENTION, OLLAMA_KV_CACHE_TYPE, OLLAMA_NUM_PARALLEL, OLLAMA_MAX_LOADED_MODELS, OLLAMA_KEEP_ALIVE); aplicá-las
exige reiniciar o Ollama. A forma de aplicar no Windows (setx + reinício) NÃO foi validada na sua máquina.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from iped_mcp import hardware as hw
from iped_mcp import webui_launcher as wl

OLLAMA_ENV = {
    "OLLAMA_FLASH_ATTENTION": "1",  # atenção mais econômica; pré-requisito do cache quantizado
    "OLLAMA_KV_CACHE_TYPE": "q8_0",  # cache de contexto na metade do tamanho, perda de qualidade pequena
    "OLLAMA_NUM_PARALLEL": "1",  # um chat por vez: nada de dividir a janela de contexto
    "OLLAMA_MAX_LOADED_MODELS": "1",  # não deixa dois modelos disputando a placa
    "OLLAMA_KEEP_ALIVE": "30m",  # não descarrega o modelo entre perguntas (recarregar leva minutos num modelo grande)
}
CTX_OPTIONS = (12288, 16384, 24576, 32768, 49152, 65536)
CTX_CAP = 32768  # além disso o ganho some: cada leitura de documento já é limitada e a conversa fica lenta
MOE = re.compile(r"a\d+b|moe", re.I)


@dataclass
class Tuning:
    num_ctx: int
    env: dict[str, str]
    suggested_model: str = ""
    lines: list[str] = field(default_factory=list)


def is_moe(name: str) -> bool:
    return bool(MOE.search(name))


def advise(found: hw.Hardware, fits: list[hw.ModelFit], current: str) -> Tuning:
    """Contexto e variáveis recomendados para o modelo atual, e um modelo melhor se o atual não cabe na placa."""
    vram, ram = found.vram_gb, found.ram_gb or 0.0
    cur = next((f for f in fits if f.name == current), None) or hw.recommend(fits)
    lines: list[str] = []
    suggested = ""
    ctx = 16384
    if cur is not None and vram is not None:
        fits_gpu = [c for c in CTX_OPTIONS if c <= CTX_CAP and cur.size_gb + hw.kv_gb(cur.params_b, c, True) + hw.OVERHEAD_GB <= vram]
        if fits_gpu:
            ctx = max(fits_gpu)
            lines.append(f"{cur.name} cabe inteiro na placa ({vram:g} GB) com contexto de {ctx} usando o cache quantizado.")
        else:
            spill = cur.size_gb + hw.kv_gb(cur.params_b, 32768, True)
            ctx = 32768 if ram >= 32 and ram - spill >= 8 else 16384
            lines.append(f"{cur.name} ({cur.size_gb:g} GB) não cabe inteiro na placa ({vram:g} GB): parte roda na CPU e fica lento. Contexto {ctx} (a RAM de {ram:g} GB comporta).")
        if cur.fit in ("parcial", "cpu") and (cur.params_b or 0) > 14 and not is_moe(cur.name):
            if ram >= 32:
                suggested = "qwen3:30b"
                lines.append("Sugestão: qwen3:30b (arquitetura MoE, ~3 bilhões de parâmetros ativos por token). Mesmo com parte na RAM costuma ser bem mais rápido que um modelo denso grande; ocupa ~19 GB de disco. É uma estimativa: meça com o seu hardware.")
            else:
                suggested = "qwen3:8b"
                lines.append("Sugestão: qwen3:8b, que cabe inteiro na placa.")
    elif vram is None:
        lines.append("Não consegui ler a memória da placa; mantive 16384.")
    lines.append("As variáveis do Ollama abaixo reduzem o cache de contexto e evitam recarregar o modelo; o Ollama precisa ser reiniciado.")
    return Tuning(ctx, dict(OLLAMA_ENV), suggested, lines)


def commands(env: dict[str, str]) -> list[list[str]]:
    """Comandos (Windows) que gravam as variáveis no perfil do usuário; o Ollama só as lê ao reiniciar."""
    return [["setx", k, v] for k, v in env.items()]


def ollama_app() -> Path | None:
    local = os.environ.get("LOCALAPPDATA")
    path = Path(local) / "Programs" / "Ollama" / "ollama app.exe" if local else None
    return path if path and path.is_file() else None


def apply_env(env: dict[str, str], say: Callable[[str], None], run: Callable[[list[str]], int] | None = None, restart: bool = True) -> bool:
    """Grava as variáveis e reinicia o Ollama. Fora do Windows só mostra os `export` para o usuário aplicar."""
    if os.name != "nt" and run is None:
        for k, v in env.items():
            say(f"export {k}={v}")
        say("Coloque estas linhas no serviço/perfil do Ollama e reinicie-o (aplicação automática só existe no Windows).")
        return False
    runner = run or (lambda cmd: subprocess.run(cmd, capture_output=True, check=False, **wl.hidden_kwargs()).returncode)  # noqa: S603
    ok = True
    for cmd in commands(env):
        say("$ " + " ".join(cmd))
        ok = runner(cmd) == 0 and ok
    if restart and ok:
        for name in ("ollama app.exe", "ollama.exe"):
            say(f"$ taskkill /F /IM {name}")
            runner(["taskkill", "/F", "/IM", name])
        exe = ollama_app()
        if exe is not None and run is None:
            say(f"$ {exe}")
            subprocess.Popen([str(exe)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **wl.hidden_kwargs())  # noqa: S603
        else:
            say("Abra o Ollama de novo para valer as variáveis.")
    return ok
