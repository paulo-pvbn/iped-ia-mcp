"""Uso de CPU, memória e placa de vídeo para o painel "Ambiente" (só biblioteca padrão, tudo local).

CPU e memória: Windows via `kernel32` (GetSystemTimes / GlobalMemoryStatusEx); Linux via /proc (desenvolvimento).
GPU: `nvidia-smi` (NVIDIA). Sem ele, a GPU aparece como indisponível, sem erro.
"""

from __future__ import annotations

import ctypes
import shutil
import sys
from dataclasses import dataclass

from iped_mcp import hardware as hw

GB = 1024**3


@dataclass(frozen=True)
class Gauge:
    percent: float | None  # None = indisponível
    detail: str = ""


def level(percent: float | None) -> str:
    """Faixa de cor do indicador: ok, warn ou danger (None = sem dado)."""
    if percent is None:
        return "none"
    return "ok" if percent < 70 else "warn" if percent < 90 else "danger"


def parse_gpu(text: str) -> Gauge:
    """Saída de `nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits`."""
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            try:
                util, used, total = float(parts[0]), float(parts[1]), float(parts[2])
            except ValueError:
                continue
            detail = f"{used / 1024:.1f}/{total / 1024:.0f} GB".replace(".", ",") if total else ""
            return Gauge(max(0.0, min(100.0, util)), detail)
    return Gauge(None)


def cpu_from_ticks(prev: tuple[int, int] | None, now: tuple[int, int]) -> float | None:
    """`ticks` = (ocioso, total). Percentual de uso entre duas leituras."""
    if prev is None:
        return None
    total = now[1] - prev[1]
    if total <= 0:
        return None
    return max(0.0, min(100.0, 100.0 * (1 - (now[0] - prev[0]) / total)))


def _cpu_ticks() -> tuple[int, int] | None:
    if sys.platform == "win32":
        try:
            class FileTime(ctypes.Structure):
                _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]

            idle, kernel, user = FileTime(), FileTime(), FileTime()
            ok = ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))  # type: ignore[attr-defined]
            if not ok:
                return None
            val = lambda f: (f.hi << 32) | f.lo
            return val(idle), val(kernel) + val(user)  # o tempo de kernel já inclui o ocioso
        except (OSError, AttributeError):
            return None
    try:
        with open("/proc/stat", encoding="utf-8") as fh:
            nums = [int(x) for x in fh.readline().split()[1:]]
        return nums[3] + (nums[4] if len(nums) > 4 else 0), sum(nums)
    except (OSError, ValueError, IndexError):
        return None


def _memory() -> Gauge:
    if sys.platform == "win32":
        try:
            class Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32), ("ullTotalPhys", ctypes.c_uint64),
                            ("ullAvailPhys", ctypes.c_uint64), ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                            ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64), ("ullAvailExtendedVirtual", ctypes.c_uint64)]  # fmt: skip

            st = Status()
            st.dwLength = ctypes.sizeof(Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))  # type: ignore[attr-defined]
            total, avail = st.ullTotalPhys, st.ullAvailPhys
        except (OSError, AttributeError):
            return Gauge(None)
    else:
        try:
            with open("/proc/meminfo", encoding="utf-8") as fh:
                info = {k: int(v.split()[0]) * 1024 for k, v in (line.split(":", 1) for line in fh if ":" in line)}
            total, avail = info["MemTotal"], info["MemAvailable"]
        except (OSError, KeyError, ValueError):
            return Gauge(None)
    if not total:
        return Gauge(None)
    used = total - avail
    return Gauge(100.0 * used / total, f"{used / GB:.1f}/{total / GB:.0f} GB".replace(".", ","))


class Sampler:
    """Lê CPU e memória (barato) e GPU (chama o nvidia-smi; use `read_gpu` fora da thread da janela)."""

    def __init__(self) -> None:
        self._ticks: tuple[int, int] | None = _cpu_ticks()
        self._smi = shutil.which("nvidia-smi")

    def read_cpu_ram(self) -> tuple[Gauge, Gauge]:
        now = _cpu_ticks()
        pct = cpu_from_ticks(self._ticks, now) if now else None
        if now:
            self._ticks = now
        return Gauge(pct), _memory()

    def read_gpu(self) -> Gauge:
        if not self._smi:
            return Gauge(None, "sem NVIDIA")
        text = hw._run([self._smi, "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"], 3)
        return parse_gpu(text)
