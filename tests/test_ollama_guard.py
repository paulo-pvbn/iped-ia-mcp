import urllib.error
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from iped_mcp.ollama_guard import OMITTED, OllamaGuard, estimate_tokens, fit_messages


def tool_turn(i: int, size: int) -> list[dict]:
    return [
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "doc_info", "arguments": {"doc_id": i}}}]},
        {"role": "tool", "content": "x" * size},
    ]


def convo(turns: int, size: int) -> list[dict]:
    msgs = [{"role": "system", "content": "SISTEMA"}, {"role": "user", "content": "PERGUNTA"}]
    for i in range(turns):
        msgs += tool_turn(i, size)
    return msgs


def test_small_conversation_is_untouched() -> None:
    msgs = convo(2, 200)
    out, info = fit_messages(msgs, 16384)
    assert out is msgs and info["tokens"] <= info["limit"]


def test_old_tool_results_are_shortened_and_user_question_kept() -> None:
    msgs = convo(18, 4000)  # ~24k tokens de resultados
    out, info = fit_messages(msgs, 16384)
    assert info["after"] <= info["limit"]
    assert out[0]["content"] == "SISTEMA" and out[1]["content"] == "PERGUNTA"
    assert info["shortened"] > 0
    assert sum(estimate_tokens(m.get("content") or "") for m in out) <= info["limit"]


def test_tools_spec_counts_and_extreme_case_omits_but_keeps_question() -> None:
    tools = [{"type": "function", "function": {"name": "t", "description": "d" * 9000}}]
    msgs = convo(30, 6000)
    out, info = fit_messages(msgs, 8192, tools)
    assert out[1]["content"] == "PERGUNTA"
    assert info["after"] <= info["limit"] or info["omitted"] > 0
    assert any(m.get("content") == OMITTED for m in out) or info["after"] <= info["limit"]


def test_older_turns_dropped_before_last_question() -> None:
    msgs = convo(10, 3000) + [{"role": "user", "content": "OUTRA PERGUNTA"}] + tool_turn(99, 3000)
    out, info = fit_messages(msgs, 8192)
    assert out[-3]["content"] == "OUTRA PERGUNTA" or any(m["content"] == "OUTRA PERGUNTA" for m in out)
    assert out[0]["content"] == "SISTEMA"
    assert info["dropped"] > 0 or info["omitted"] > 0 or info["shortened"] > 0


def test_zero_ctx_disables() -> None:
    msgs = convo(40, 5000)
    out, _ = fit_messages(msgs, 0)
    assert out is msgs


def start_fake_ollama(seen: list) -> HTTPServer:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"models": []}')

        def do_POST(self) -> None:  # noqa: N802
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append(json.loads(body))
            if self.path == "/api/boom":
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'{"error": "No user query found in messages."}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            for part in ("a", "b"):
                self.wfile.write((json.dumps({"message": {"content": part}, "done": False}) + "\n").encode())
                self.wfile.flush()
            self.wfile.write(b'{"done": true}\n')

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def post(url: str, payload: dict) -> bytes:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
        return r.read()


def test_guard_trims_chat_streams_and_logs() -> None:
    seen: list = []
    fake = start_fake_ollama(seen)
    logs: list[str] = []
    guard = OllamaGuard(f"http://127.0.0.1:{fake.server_address[1]}", default_ctx=8192, log=logs.append).start()
    try:
        out = post(guard.url + "/api/chat", {"model": "m", "messages": convo(20, 3000), "stream": True})
        assert out.count(b"\n") == 3 and b'"done": true' in out
        sent = seen[0]
        assert sent["options"]["num_ctx"] == 8192  # janela fixada quando o Open WebUI não manda
        assert sent["messages"][1]["content"] == "PERGUNTA"
        assert sum(len(m.get("content") or "") for m in sent["messages"]) < 20 * 3000
        assert any("passou" in line for line in logs)
        # o resto passa direto
        with urllib.request.urlopen(guard.url + "/api/tags", timeout=10) as r:  # noqa: S310
            assert json.loads(r.read()) == {"models": []}
    finally:
        guard.stop()
        fake.shutdown()


def test_guard_logs_upstream_errors_and_respects_explicit_ctx() -> None:
    seen: list = []
    fake = start_fake_ollama(seen)
    logs: list[str] = []
    guard = OllamaGuard(f"http://127.0.0.1:{fake.server_address[1]}", log=logs.append).start()
    try:
        post(guard.url + "/api/chat", {"model": "m", "messages": convo(1, 10), "options": {"num_ctx": 12288}})
        assert seen[0]["options"]["num_ctx"] == 12288
        try:
            post(guard.url + "/api/boom", {"x": 1})
        except urllib.error.HTTPError as exc:
            assert exc.code == 500
        assert any("No user query" in line for line in logs)
    finally:
        guard.stop()
        fake.shutdown()


def test_guard_reports_unreachable_ollama() -> None:
    logs: list[str] = []
    guard = OllamaGuard("http://127.0.0.1:9", log=logs.append).start()
    try:
        try:
            post(guard.url + "/api/chat", {"model": "m", "messages": convo(1, 10)})
        except urllib.error.HTTPError as exc:
            assert exc.code == 502
        assert any("não respondeu" in line for line in logs)
    finally:
        guard.stop()
