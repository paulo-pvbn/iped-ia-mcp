"""Guarda de contexto entre o Open WebUI e o Ollama (tudo local, sem dependências além da biblioteca padrão).

Problema: numa investigação o modelo chama várias ferramentas e cada resultado entra na conversa. Quando o total
passa de `num_ctx`, o Ollama corta o começo do histórico, e o primeiro a sair é a pergunta do usuário; o template
Qwen então falha com "No user query found in messages" e o relatório final nunca aparece.

A guarda recebe as chamadas `/api/chat` do Open WebUI, confere o tamanho estimado, encurta resultados de
ferramentas antigos (a pergunta e o prompt de sistema nunca saem) e repassa ao Ollama. O resto das rotas passa direto.
Cada chamada vira uma linha de log: é a prova de quanto contexto está sendo usado.
"""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

CHARS_PER_TOKEN = 3.0  # português + JSON: estimativa pessimista
ANSWER_RESERVE = 1500  # tokens deixados para a resposta (inclui o raciocínio do modelo)
LEVELS = (3000, 1500, 600, 250)  # tetos de caracteres por resultado de ferramenta, do mais folgado ao mais apertado
MARK = "\n[…resultado resumido para caber na janela de contexto]"
OMITTED = "[resultado omitido para caber na janela de contexto; chame a ferramenta de novo se precisar]"
DEFAULT_CTX = 16384


def estimate_tokens(value: Any) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return int(len(text) / CHARS_PER_TOKEN) + 4


def _message_tokens(message: dict[str, Any]) -> int:
    return estimate_tokens(message.get("content") or "") + estimate_tokens(message.get("tool_calls") or "")


def fit_messages(
    messages: list[dict[str, Any]], num_ctx: int, tools: list[dict[str, Any]] | None = None
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Ajusta `messages` para caber em `num_ctx`. Devolve (mensagens, contagens). `num_ctx<=0` não mexe.

    Ordem: (1) encurta resultados de ferramentas, dos mais antigos aos mais novos, em níveis cada vez menores;
    (2) descarta turnos inteiros anteriores à última pergunta; (3) omite o conteúdo dos resultados que sobrarem.
    O prompt de sistema e a última pergunta do usuário nunca são removidos nem alterados.
    """
    limit = max(0, num_ctx - ANSWER_RESERVE - (estimate_tokens(tools) if tools else 0))
    info = {"tokens": 0, "limit": limit, "shortened": 0, "dropped": 0, "omitted": 0}
    out = [dict(m) for m in messages]

    def total() -> int:
        return sum(_message_tokens(m) for m in out)

    info["tokens"] = total()
    if num_ctx <= 0 or info["tokens"] <= limit:
        info["after"] = info["tokens"]
        return messages, info

    last_user = max((i for i, m in enumerate(out) if m.get("role") == "user"), default=-1)
    tool_idx = [i for i, m in enumerate(out) if m.get("role") == "tool" and isinstance(m.get("content"), str)]
    shortened: set[int] = set()
    for cap in LEVELS:  # 1) encurta, dos antigos aos novos
        for i in tool_idx:
            if total() <= limit:
                break
            content = out[i]["content"]
            if len(content) > cap and not content.endswith(OMITTED):
                out[i]["content"] = content[:cap] + MARK
                shortened.add(i)
        if total() <= limit:
            break
    info["shortened"] = len(shortened)

    def blocks() -> list[tuple[int, int]]:  # turnos descartáveis antes da última pergunta (sem o sistema)
        found, i = [], 1 if out and out[0].get("role") == "system" else 0
        while i < last_user:
            j = i + 1
            if out[i].get("tool_calls"):
                while j < last_user and out[j].get("role") == "tool":
                    j += 1
            found.append((i, j))
            i = j
        return found

    while total() > limit and last_user > 0:  # 2) turnos antigos
        found = blocks()
        if not found:
            break
        a, b = found[0]
        del out[a:b]
        last_user -= b - a
        info["dropped"] += b - a
    for i, m in enumerate(out):  # 3) o que ainda estourar: omite os resultados, do mais antigo ao mais novo
        if total() <= limit:
            break
        if m.get("role") == "tool" and i > last_user >= 0 and m.get("content") != OMITTED:
            m["content"] = OMITTED
            info["omitted"] += 1
    info["after"] = total()
    return out, info


def describe(info: dict[str, int], num_ctx: int) -> str:
    base = f"~{info['tokens']} de {info['limit']} tokens úteis (janela {num_ctx})"
    if info["tokens"] <= info["limit"]:
        return f"{base}: cabe."
    parts = []
    if info["shortened"]:
        parts.append(f"{info['shortened']} resultado(s) encurtado(s)")
    if info["dropped"]:
        parts.append(f"{info['dropped']} mensagem(ns) antigas descartada(s)")
    if info["omitted"]:
        parts.append(f"{info['omitted']} resultado(s) omitido(s)")
    return f"{base}: passou. Ajuste: {', '.join(parts) or 'nenhum possível'}; agora ~{info.get('after', 0)}."


class OllamaGuard:
    """Servidor HTTP local que repassa ao Ollama aplicando `fit_messages` nas chamadas de chat."""

    def __init__(
        self, upstream: str, port: int = 0, host: str = "127.0.0.1", default_ctx: int = DEFAULT_CTX,
        log: Callable[[str], None] | None = None,
    ) -> None:  # fmt: skip
        parts = urlsplit(upstream if "//" in upstream else f"http://{upstream}")
        self.up_host, self.up_port = parts.hostname or "127.0.0.1", parts.port or (443 if parts.scheme == "https" else 11434)
        self.up_https = parts.scheme == "https"
        self.default_ctx = default_ctx
        self.log = log or (lambda _m: None)
        self.calls = 0
        guard = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"  # resposta termina ao fechar a conexão: serve para streaming sem Content-Length

            def log_message(self, *_args: Any) -> None:
                pass

            def _forward(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                guard._handle(self, body)

            do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _forward  # noqa: N815

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = f"http://{host}:{self.port}"
        self._thread: threading.Thread | None = None

    def start(self) -> OllamaGuard:
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    # -- internos --------------------------------------------------------------------------------------
    def _adjust(self, body: bytes) -> bytes:
        try:
            data = json.loads(body)
        except ValueError:
            return body
        messages = data.get("messages")
        if not isinstance(messages, list):
            return body
        options = data.get("options") if isinstance(data.get("options"), dict) else {}
        num_ctx = int(options.get("num_ctx") or self.default_ctx)
        if not options.get("num_ctx"):  # sem janela explícita o Ollama usaria a padrão (pequena): fixa a nossa
            data["options"] = {**options, "num_ctx": num_ctx}
        fitted, info = fit_messages(messages, num_ctx, data.get("tools"))
        data["messages"] = fitted
        self.calls += 1
        self.log(f"chat #{self.calls} ({data.get('model', '?')}): {describe(info, num_ctx)}")
        return json.dumps(data, ensure_ascii=False).encode("utf-8")

    def _handle(self, req: BaseHTTPRequestHandler, body: bytes) -> None:
        path = req.path
        if req.command == "POST" and path.split("?")[0] == "/api/chat":
            body = self._adjust(body)
        headers = {k: v for k, v in req.headers.items() if k.lower() not in {"host", "content-length", "accept-encoding", "connection"}}
        if body or req.command in {"POST", "PUT"}:
            headers["Content-Length"] = str(len(body))
        conn_cls = http.client.HTTPSConnection if self.up_https else http.client.HTTPConnection
        try:
            conn = conn_cls(self.up_host, self.up_port, timeout=1800)
            conn.request(req.command, path, body=body or None, headers=headers)
            resp = conn.getresponse()
        except OSError as exc:
            self.log(f"Ollama não respondeu em {self.up_host}:{self.up_port} ({exc})")
            msg = json.dumps({"error": f"Ollama indisponível: {exc}"}).encode()
            req.send_response(502)
            req.send_header("Content-Type", "application/json")
            req.end_headers()
            req.wfile.write(msg)
            return
        try:
            data = b""
            if resp.status >= 400:  # erros são curtos: lê tudo e registra antes de responder
                data = resp.read()
                self.log(f"Ollama respondeu {resp.status} em {path}: {data.decode('utf-8', 'replace')[:300]}")
            req.send_response(resp.status)
            for key, value in resp.getheaders():
                if key.lower() not in {"transfer-encoding", "connection", "content-length"}:
                    req.send_header(key, value)
            req.end_headers()
            if resp.status >= 400:
                req.wfile.write(data)
            else:
                while chunk := resp.read1(8192):
                    req.wfile.write(chunk)
                    req.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # o navegador parou a geração
        finally:
            conn.close()
