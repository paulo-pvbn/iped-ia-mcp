from iped_mcp import hardware as hw
from iped_mcp import tuning as tn


def fit(name, size, params):
    return hw.fit_models([{"name": name, "size": int(size * hw.GB), "details": {"parameter_size": f"{params}B"}}], 8.0)[0]


def hardware(vram=8.0, ram=64.0):
    return hw.Hardware(gpus=[hw.Gpu("RTX", vram)], ram_gb=ram)


def test_big_dense_model_on_small_gpu_suggests_moe_and_32k():
    big = fit("qwen3.8:27b", 17.0, 27)
    t = tn.advise(hardware(), [big], "qwen3.8:27b")
    assert t.num_ctx == 32768 and t.suggested_model == "qwen3:30b"
    assert t.env["OLLAMA_KV_CACHE_TYPE"] == "q8_0" and t.env["OLLAMA_NUM_PARALLEL"] == "1"


def test_small_ram_suggests_small_model_and_16k():
    big = fit("qwen3.8:27b", 17.0, 27)
    t = tn.advise(hardware(ram=16), [big], "qwen3.8:27b")
    assert t.num_ctx == 16384 and t.suggested_model == "qwen3:8b"


def test_model_that_fits_gets_largest_context_that_fits_and_no_suggestion():
    small = fit("qwen3:4b", 2.5, 4)
    t = tn.advise(hardware(), [small], "qwen3:4b")
    assert t.num_ctx == 32768 and t.suggested_model == ""


def test_moe_is_not_replaced():
    assert tn.is_moe("qwen3:30b-a3b") and not tn.is_moe("qwen3.8:27b")
    moe = fit("qwen3:30b-a3b", 19.0, 30)
    assert tn.advise(hardware(), [moe], "qwen3:30b-a3b").suggested_model == ""


def test_apply_env_runs_setx_then_restarts():
    log, calls = [], []
    ok = tn.apply_env({"A": "1", "B": "2"}, log.append, run=lambda c: calls.append(c) or 0, restart=True)
    assert ok and calls[:2] == [["setx", "A", "1"], ["setx", "B", "2"]]
    assert ["taskkill", "/F", "/IM", "ollama.exe"] in calls
    calls.clear()
    assert not tn.apply_env({"A": "1"}, log.append, run=lambda c: calls.append(c) or 1) and ["taskkill"] not in [c[:1] for c in calls]
