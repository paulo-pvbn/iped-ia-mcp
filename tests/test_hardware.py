from iped_mcp import hardware as hw

GB = 1024**3
TAGS = [
    {"name": "qwen3.8:27b-ipediar8k", "size": int(16.5 * GB), "details": {"parameter_size": "27B"}},
    {"name": "qwen3:8b", "size": int(5.2 * GB), "details": {"parameter_size": "8.2B"}},
    {"name": "qwen3:4b", "size": int(2.6 * GB), "details": {"parameter_size": "4.0B"}},
]


def test_parse_nvidia_smi() -> None:
    gpus = hw.parse_nvidia_smi("NVIDIA GeForce RTX 4060 Laptop GPU, 8188\n")
    assert gpus[0].name.endswith("4060 Laptop GPU") and gpus[0].vram_gb == 8.0
    assert hw.parse_nvidia_smi("lixo\n") == []


def test_parse_video_controllers() -> None:
    out = hw.parse_video_controllers('[{"Name":"Intel(R) UHD Graphics"},{"Name":"Microsoft Basic Display Adapter"}]')
    assert [g.name for g in out] == ["Intel(R) UHD Graphics"] and out[0].vram_gb is None
    assert hw.parse_video_controllers("não é json") == []


def test_fit_on_8gb_recommends_8b_and_flags_27b() -> None:
    fits = {f.name: f for f in hw.fit_models(TAGS, 8.0, 16384)}
    assert fits["qwen3.8:27b-ipediar8k"].fit == "cpu"
    assert fits["qwen3:4b"].fit == "gpu"
    rec = hw.recommend(list(fits.values()))
    assert rec.name == "qwen3:8b" and rec.ctx == 16384 and rec.q8  # 8B em 8 GB: cabe com cache q8
    assert fits["qwen3:4b"].ctx == 16384 and not fits["qwen3:4b"].q8
    # com q8 no cache o 8b tem mais folga que sem
    assert hw.kv_gb(8.2, 16384, q8=True) < hw.kv_gb(8.2, 16384)


def test_fit_with_big_gpu_and_unknown_gpu() -> None:
    assert {f.fit for f in hw.fit_models(TAGS, 48.0)} == {"gpu"}
    assert {f.fit for f in hw.fit_models(TAGS, None)} == {"?"}
    assert hw.recommend([]) is None


def test_loaded_models_and_report() -> None:
    loaded = hw.loaded_models({"models": [{"name": "m", "size": 100, "size_vram": 40}]})
    assert loaded == [("m", 40)]
    hardware = hw.Hardware([hw.Gpu("RTX", 8.0)], 32.0)
    text = hw.report(hardware, hw.fit_models(TAGS, 8.0), loaded)
    assert "RTX (8 GB de VRAM)" in text and "40% na GPU" in text and "FLASH_ATTENTION" in text
    assert "Recomendado:" in text


def test_params_of() -> None:
    assert hw.params_of("qwen3:8b") == 8 and hw.params_of("x", "8.2B") == 8.2 and hw.params_of("gemma:270m") == 0.27
    assert hw.params_of("llama") is None


def test_detect_runs_here() -> None:
    found = hw.detect()  # sem GPU nesta máquina: não pode levantar exceção
    assert found.ram_gb is None or found.ram_gb > 0
