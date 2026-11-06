from iped_mcp import sysmon


def test_parse_gpu_reads_first_gpu_and_formats_vram() -> None:
    gauge = sysmon.parse_gpu("37, 2048, 8192\n5, 100, 4096\n")
    assert gauge.percent == 37.0 and gauge.detail == "2,0/8 GB"


def test_parse_gpu_tolerates_garbage() -> None:
    assert sysmon.parse_gpu("").percent is None
    assert sysmon.parse_gpu("N/A, N/A, N/A").percent is None
    assert sysmon.parse_gpu("250, 1, 2").percent == 100.0  # limita a 100


def test_cpu_from_ticks() -> None:
    assert sysmon.cpu_from_ticks(None, (10, 20)) is None
    assert sysmon.cpu_from_ticks((0, 0), (50, 100)) == 50.0
    assert sysmon.cpu_from_ticks((10, 100), (10, 100)) is None  # sem tempo decorrido


def test_level_bands() -> None:
    assert [sysmon.level(p) for p in (None, 10, 69.9, 70, 89.9, 90)] == ["none", "ok", "ok", "warn", "warn", "danger"]


def test_sampler_reads_without_crashing() -> None:
    sampler = sysmon.Sampler()
    cpu, ram = sampler.read_cpu_ram()
    assert cpu.percent is None or 0 <= cpu.percent <= 100
    assert ram.percent is None or 0 < ram.percent <= 100
    assert sampler.read_gpu().percent is None or 0 <= sampler.read_gpu().percent <= 100
