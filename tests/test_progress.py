from iped_mcp import ollama_guard as og
from iped_mcp import progress as pg


def test_tracker_follows_launcher_lines_and_never_goes_back():
    t = pg.Tracker()
    assert t.label.startswith("Subindo o IPED")
    for line, done in (
        ("[1/3] Subindo as Web APIs do IPED…", 0),
        ("   Web API(s) no ar: caso -> http://127.0.0.1:1234", 1),
        ("[2/3] Subindo o MCP…", 1),
        ("   MCP no ar em 3s.", 2),
        ("   Guarda de contexto no ar em http://127.0.0.1:11435", 3),
        ("[3/3] Subindo o Open WebUI", 3),
        ("=== PRONTO ===", 5),
    ):
        assert t.feed(line) == done
    assert t.feed("[1/3] de novo") == 5
    assert t.active is None and t.fraction == 1.0


def test_context_use_levels_and_speed():
    assert pg.context_use(None).level == "none"
    ok = pg.context_use({"ctx": 32768, "used": 10000, "tps": 18.4})
    assert ok.level == "ok" and "18,4 tok/s" in ok.text and round(ok.percent) == 31
    assert pg.context_use({"ctx": 10000, "used": 7500}).level == "warn"
    full = pg.context_use({"ctx": 10000, "used": 9000})
    assert full.level == "danger" and "nova conversa" in full.text


def test_parse_stats_from_ollama_tail():
    tail = b'{"done":true,"prompt_eval_count":1200,"eval_count":300,"eval_duration":15000000000}'
    assert og.parse_stats(tail) == {"prompt": 1200, "gen": 300, "tps": 20.0}
    assert og.parse_stats(b"{}") is None


def test_read_stats_missing_and_invalid(tmp_path):
    assert pg.read_stats(tmp_path / "x.json") is None
    bad = tmp_path / "b.json"
    bad.write_text("não é json", encoding="utf-8")
    assert pg.read_stats(bad) is None
