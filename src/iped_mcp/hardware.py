"""Detecta GPU e memória e diz quais modelos do Ollama cabem, com o contexto usado pelo Analista IPED.

Tudo aqui é estimativa: o que vale é o que o Ollama mede (`/api/ps`, tamanho do modelo na GPU).
Sem dependências além da biblioteca padrão; nada é enviado para fora da máquina (só `127.0.0.1`).
"""

from __future__ import annotations

import ctypes
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

GB = 1024**3
OVERHEAD_GB = 0.8  # buffers de computação e do runtime
KV_GB_PER_1K_PER_B = 0.02  # cache de contexto: ~0,02 GB por 1000 tokens por bilhão de parâmetros (aprox.)
KV_Q8_FACTOR = 0.55  # OLLAMA_KV_CACHE_TYPE=q8_0 (com flash attention) quase corta o cache pela metade


@dataclass
class Gpu:
    name: str
    vram_gb: float | None  # None: não consegui ler a memória (comum em AMD/Intel)


@dataclass
class Hardware:
    gpus: list[Gpu] = field(default_factory=list)
    ram_gb: float | None = None

    @property
    def vram_gb(self) -> float | None:
        known = [g.vram_gb for g in self.gpus if g.vram_gb]
        return max(known) if known else None  # o Ollama usa uma GPU por modelo pequeno; vale a maior


def _run(cmd: list[str], timeout: int = 8) -> str:
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=flags, check=False)  # noqa: S603
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def parse_nvidia_smi(text: str) -> list[Gpu]:
    gpus = []
    for line in text.strip().splitlines():
        name, _, mem = line.rpartition(",")
        try:
            gpus.append(Gpu(name.strip(), round(float(mem.strip()) / 1024, 1)))  # MiB -> GB
        except ValueError:
            continue
    return gpus


def parse_video_controllers(text: str) -> list[Gpu]:
    """Saída JSON do PowerShell (`Get-CimInstance Win32_VideoController`): só nomes; a memória não é confiável."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    items = data if isinstance(data, list) else [data]
    names = [str(i.get("Name", "")).strip() for i in items if isinstance(i, dict)]
    return [Gpu(n, None) for n in names if n and "microsoft basic" not in n.lower()]


def detect_ram_gb() -> float | None:
    if sys.platform == "win32":

        class Status(ctypes.Structure):
            _fields_ = [  # noqa: RUF012
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]  # fmt: skip

        try:
            st = Status()
            st.dwLength = ctypes.sizeof(Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))  # type: ignore[attr-defined]
            return round(st.ullTotalPhys / GB, 1)
        except (OSError, AttributeError):
            return None
    try:  # Linux (usado só em testes/desenvolvimento)
        with open("/proc/meminfo", encoding="utf-8") as fh:
            kb = int(next(line for line in fh if line.startswith("MemTotal")).split()[1])
        return round(kb / 1024 / 1024, 1)
    except (OSError, StopIteration, ValueError):
        return None


def detect() -> Hardware:
    hw = Hardware(ram_gb=detect_ram_gb())
    smi = shutil.which("nvidia-smi")
    if smi:
        hw.gpus = parse_nvidia_smi(_run([smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]))
    if not hw.gpus and sys.platform == "win32":
        ps = "Get-CimInstance Win32_VideoController | Select-Object Name | ConvertTo-Json"
        hw.gpus = parse_video_controllers(_run(["powershell", "-NoProfile", "-Command", ps], 15))
    return hw


@dataclass
class ModelFit:
    name: str
    params_b: float | None
    size_gb: float
    need_gb: float  # tamanho + cache de contexto (16384, sem q8) + folga
    fit: str  # "gpu" (cabe inteiro), "parcial" (parte na CPU), "cpu" (não cabe), "?" (GPU desconhecida)
    ctx: int = 0  # melhor contexto que cabe inteiro na GPU (0 = nenhum)
    q8: bool = False  # esse contexto exige OLLAMA_KV_CACHE_TYPE=q8_0


def params_of(name: str, parameter_size: str = "") -> float | None:
    for text in (parameter_size, name.lower()):
        m = re.search(r"(\d+(?:\.\d+)?)\s*([bm])\b", text.lower())
        if m:
            value = float(m.group(1))
            return value / 1000 if m.group(2) == "m" else value
    return None


def kv_gb(params_b: float | None, ctx: int, q8: bool = False) -> float:
    base = KV_GB_PER_1K_PER_B * (params_b or 8) * ctx / 1000
    return base * (KV_Q8_FACTOR if q8 else 1)


# Do mais confortável ao mais apertado. Abaixo de 12288 não sobra espaço para o prompt + ferramentas + conversa.
PLANS = ((16384, False), (16384, True), (12288, True))


def plan(size_gb: float, params_b: float | None, vram_gb: float) -> tuple[int, bool] | None:
    for ctx, q8 in PLANS:
        if size_gb + kv_gb(params_b, ctx, q8) + OVERHEAD_GB <= vram_gb:
            return ctx, q8
    return None


def fit_models(models: list[dict], vram_gb: float | None, ctx: int = 16384, q8: bool = False) -> list[ModelFit]:
    """`models`: itens de `/api/tags` do Ollama (name, size em bytes, details.parameter_size)."""
    out = []
    for m in models:
        name = m.get("name", "?")
        size = float(m.get("size", 0)) / GB
        params = params_of(name, str((m.get("details") or {}).get("parameter_size", "")))
        need = size + kv_gb(params, ctx, q8) + OVERHEAD_GB
        best = plan(size, params, vram_gb) if vram_gb is not None else None
        if vram_gb is None:
            fit = "?"
        elif best:
            fit = "gpu"
        elif size + OVERHEAD_GB <= vram_gb * 1.6:  # a maior parte ainda fica na GPU
            fit = "parcial"
        else:
            fit = "cpu"
        out.append(ModelFit(name, params, round(size, 1), round(need, 1), fit, best[0] if best else 0, best[1] if best else False))
    return sorted(out, key=lambda f: f.need_gb)


def recommend(fits: list[ModelFit]) -> ModelFit | None:
    """O maior modelo que cabe inteiro na GPU (maior = melhor qualidade); sem nenhum, o menor."""
    ok = [f for f in fits if f.fit == "gpu"]
    if ok:
        return max(ok, key=lambda f: f.need_gb)
    return fits[0] if fits else None


def loaded_models(ps: dict | None) -> list[tuple[str, float]]:
    """De `/api/ps`: (modelo, % que está na GPU) — a medida real, depois de o modelo carregar."""
    out = []
    for m in (ps or {}).get("models", []):
        size, vram = float(m.get("size", 0)), float(m.get("size_vram", 0))
        out.append((m.get("name", "?"), round(100 * vram / size) if size else 0))
    return out


LABEL = {"gpu": "cabe na GPU", "parcial": "parte vai para a CPU (mais lento)", "cpu": "não cabe (roda na CPU, muito lento)", "?": "GPU não detectada"}


def report(hw: Hardware, fits: list[ModelFit], loaded: list[tuple[str, float]], ctx: int = 16384) -> str:
    lines = []
    if hw.gpus:
        for g in hw.gpus:
            mem = f"{g.vram_gb:g} GB de VRAM" if g.vram_gb else "VRAM não detectada"
            lines.append(f"GPU: {g.name} ({mem})")
    else:
        lines.append("GPU: nenhuma detectada (o Ollama vai usar a CPU: lento)")
    lines.append(f"Memória RAM: {hw.ram_gb:g} GB" if hw.ram_gb else "Memória RAM: não detectada")
    lines.append("")
    if fits:
        lines.append(f"Modelos do Ollama (estimativa com contexto de {ctx}):")
        for f in fits:
            how = ""
            if f.fit == "gpu":
                how = f" (contexto {f.ctx}" + (" com cache q8_0)" if f.q8 else ")")
            lines.append(f"  - {f.name}: {f.size_gb:g} GB + contexto ≈ {f.need_gb:g} GB → {LABEL[f.fit]}{how}")
        rec = recommend(fits)
        if rec and hw.vram_gb:
            if rec.fit == "gpu":
                extra = " Ative OLLAMA_FLASH_ATTENTION=1 e OLLAMA_KV_CACHE_TYPE=q8_0 no Ollama e reinicie-o." if rec.q8 else ""
                lines.append(f"\nRecomendado: {rec.name} com contexto {rec.ctx} (o maior modelo que cabe inteiro na GPU).{extra}")
            else:
                lines.append(f"\nNenhum modelo baixado cabe inteiro na GPU. O mais leve é {rec.name}; "
                             "baixe um de 4B a 8B (ex.: ollama pull qwen3:8b) ou reduza o contexto.")  # fmt: skip
    else:
        lines.append("Não consegui listar os modelos (o Ollama está aberto?).")
    if loaded:
        lines.append("\nMedido agora pelo Ollama:")
        for name, pct in loaded:
            lines.append(f"  - {name}: {pct}% na GPU" + ("" if pct >= 95 else "  ← parte na CPU: lento; use um modelo menor"))
    lines += [
        "",
        "Condições ideais:",
        "  - Fechar programas que usam a GPU (navegador com aceleração, jogos, VMs) antes de conversar.",
        "  - No Ollama (reinicie-o depois): OLLAMA_FLASH_ATTENTION=1 e OLLAMA_KV_CACHE_TYPE=q8_0 reduzem o cache de contexto quase pela metade.",
        "  - Poucas ferramentas ligadas (menu IA > Ferramentas da IA…): cada uma gasta contexto.",
        "  - Estes números são estimativas; o '% na GPU' medido pelo Ollama é o que vale.",
    ]
    return "\n".join(lines)
