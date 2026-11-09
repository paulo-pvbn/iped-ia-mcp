"""Guarda de contexto entre o Open WebUI e o Ollama (tudo local, sem dependências além da biblioteca padrão).

Problema: numa investigação o modelo chama várias ferramentas e cada resultado entra na conversa. Quando o total
passa de `num_ctx`, o Ollama corta o começo do histórico, e o primeiro a sair é a pergunta do usuário; o template
Qwen então falha com "No user query found in messages" e o relatório final nunca aparece.

A guarda recebe as chamadas `/api/chat` do Open WebUI, confere o tamanho estimado, encurta resultados de
ferramentas antigos (a pergunta e o prompt de sistema nunca saem) e repassa ao Ollama. O resto das rotas passa direto.
Cada chamada vira uma linha de log: é a prova de quanto contexto está sendo usado.

A conta de tokens é uma estimativa (caracteres / 3). Para não depender dela, a guarda se corrige sozinha: (1) se o Ollama
responder "No user query found", refaz a chamada com margem maior (o erro acontece antes de gerar, então é barato);
(2) quando o Ollama informa quantos tokens leu (`prompt_eval_count`), compara com a estimativa e aprende o fator do modelo.
O fator só sobe: com cache de prompt o Ollama conta menos do que leu, e isso não pode afrouxar o limite.
"""

from __future__ import annotations

import http.client
import json
import re
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

CHARS_PER_TOKEN = 3.0  # português + JSON: estimativa pessimista
ANSWER_RESERVE = 1500  # tokens deixados para a resposta (inclui o raciocínio do modelo)
LEVELS = (3000, 1500, 600, 250)  # tetos de caracteres por resultado de ferramenta, do mais folgado ao mais apertado
MARK = "\n[…resultado resumido para caber na janela de contexto]"
OMITTED = "[resultado omitido para caber na janela de contexto; chame a ferramenta de novo se precisar]"
DEFAULT_CTX = 131072
RETRY_FACTOR = 1.35  # a cada recusa por "no user query", a estimativa de tokens é multiplicada por isto
MAX_RETRIES = 3
MAX_RATIO = 4.0
_EVAL_COUNT = re.compile(rb'"prompt_eval_count"\s*:\s*(\d+)')
_GEN_COUNT = re.compile(rb'"eval_count"\s*:\s*(\d+)')
_GEN_NS = re.compile(rb'"eval_duration"\s*:\s*(\d+)')
STATS_NAME = "uso-contexto.json"


def parse_stats(tail: bytes) -> dict[str, float] | None:
    """Números que o Ollama informa no fim da resposta: tokens lidos, tokens gerados e velocidade (tok/s)."""
    read, gen, ns = _EVAL_COUNT.findall(tail), _GEN_COUNT.findall(tail), _GEN_NS.findall(tail)
    if not read:
        return None
    out: dict[str, float] = {"prompt": int(read[-1]), "gen": int(gen[-1]) if gen else 0}
    if gen and ns and int(ns[-1]) > 0:
        out["tps"] = round(int(gen[-1]) / (int(ns[-1]) / 1e9), 1)
    return out


def estimate_tokens(value: Any) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return int(len(text) / CHARS_PER_TOKEN) + 4


def _message_tokens(message: dict[str, Any]) -> int:
    return estimate_tokens(message.get("content") or "") + estimate_tokens(message.get("tool_calls") or "")


def fit_messages(
    messages: list[dict[str, Any]], num_ctx: int, tools: list[dict[str, Any]] | None = None, ratio: float = 1.0
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Ajusta `messages` para caber em `num_ctx`. Devolve (mensagens, contagens). `num_ctx<=0` não mexe.

    `ratio` multiplica a estimativa de tokens (1.0 = padrão; sobe quando o Ollama mostrou que contou mais).

    Ordem: (1) encurta resultados de ferramentas, dos mais antigos aos mais novos, em níveis cada vez menores;
    (2) descarta turnos inteiros anteriores à última pergunta; (3) omite o conteúdo dos resultados que sobrarem.
    O prompt de sistema e a última pergunta do usuário nunca são removidos nem alterados.
    """
    tools_tokens = int(estimate_tokens(tools) * ratio) if tools else 0
    limit = max(0, num_ctx - ANSWER_RESERVE - tools_tokens)
    info = {"tokens": 0, "limit": limit, "tools": tools_tokens, "shortened": 0, "dropped": 0, "omitted": 0}
    out = [dict(m) for m in messages]

    def total() -> int:
        return int(sum(_message_tokens(m) for m in out) * ratio)

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
        log: Callable[[str], None] | None = None, stats_path: Any = None,
    ) -> None:  # fmt: skip
        parts = urlsplit(upstream if "//" in upstream else f"http://{upstream}")
        self.up_host, self.up_port = parts.hostname or "127.0.0.1", parts.port or (443 if parts.scheme == "https" else 11434)
        self.up_https = parts.scheme == "https"
        self.default_ctx = default_ctx
        self.log = log or (lambda _m: None)
        self.calls = 0
        self.stats_path = stats_path  # arquivo com números de uso (sem texto das conversas) que a janela lê
        self.stats: dict[str, Any] = {}
        self.ratios: dict[str, float] = {}  # modelo -> tokens reais por token estimado (só sobe)
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
    def _adjust(self, body: bytes, attempt: int = 0) -> tuple[bytes, dict[str, Any] | None]:
        try:
            data = json.loads(body)
        except ValueError:
            return body, None
        messages = data.get("messages")
        if not isinstance(messages, list):
            return body, None
        options = data.get("options") if isinstance(data.get("options"), dict) else {}
        num_ctx = int(options.get("num_ctx") or self.default_ctx)
        if not options.get("num_ctx"):  # sem janela explícita o Ollama usaria a padrão (pequena): fixa a nossa
            data["options"] = {**options, "num_ctx": num_ctx}
        model = str(data.get("model", "?"))
        ratio = self.ratios.get(model, 1.0)
        fitted, info = fit_messages(messages, num_ctx, data.get("tools"), ratio)
        data["messages"] = fitted
        if attempt == 0:
            self.calls += 1
        extra = f", tentativa {attempt + 1}" if attempt else ""
        self.log(f"chat #{self.calls} ({model}{extra}): {describe(info, num_ctx)}" + (f" [fator {ratio:.2f}]" if ratio > 1 else ""))
        raw = (info.get("after", info["tokens"]) + info["tools"]) / ratio  # estimativa sem correção, para comparar com o Ollama
        return json.dumps(data, ensure_ascii=False).encode("utf-8"), {"model": model, "raw": raw, "ctx": num_ctx}

    def _record(self, meta: dict[str, Any] | None, tail: bytes) -> None:
        got = parse_stats(tail)
        if not meta or not got:
            return
        self.stats = {**got, "ctx": meta["ctx"], "model": meta["model"], "used": got["prompt"] + got["gen"]}
        if self.stats_path:
            try:
                Path(self.stats_path).parent.mkdir(parents=True, exist_ok=True)
                Path(self.stats_path).write_text(json.dumps(self.stats), encoding="utf-8")
            except OSError:
                pass

    def _learn(self, meta: dict[str, Any] | None, tail: bytes) -> None:
        self._record(meta, tail)
        found = _EVAL_COUNT.findall(tail)
        if not meta or not found or meta["raw"] <= 0:
            return
        real = int(found[-1])
        seen = real / meta["raw"]
        model = meta["model"]
        self.log(f"   Ollama leu {real} tokens (estimei ~{int(meta['raw'])}; relação {seen:.2f})")
        if seen > self.ratios.get(model, 1.0):
            self.ratios[model] = min(MAX_RATIO, seen)
            self.log(f"   Estimativa corrigida para {model}: fator {self.ratios[model]:.2f}")

    def _upstream(self, req: BaseHTTPRequestHandler, path: str, body: bytes) -> tuple[Any, http.client.HTTPResponse]:
        headers = {k: v for k, v in req.headers.items() if k.lower() not in {"host", "content-length", "accept-encoding", "connection"}}
        if body or req.command in {"POST", "PUT"}:
            headers["Content-Length"] = str(len(body))
        conn_cls = http.client.HTTPSConnection if self.up_https else http.client.HTTPConnection
        conn = conn_cls(self.up_host, self.up_port, timeout=1800)
        conn.request(req.command, path, body=body or None, headers=headers)
        return conn, conn.getresponse()

    def _handle(self, req: BaseHTTPRequestHandler, body: bytes) -> None:
        path = req.path
        chat = req.command == "POST" and path.split("?")[0] == "/api/chat"
        attempt = 0
        while True:
            sent, meta = self._adjust(body, attempt) if chat else (body, None)
            try:
                conn, resp = self._upstream(req, path, sent)
            except OSError as exc:
                self.log(f"Ollama não respondeu em {self.up_host}:{self.up_port} ({exc})")
                msg = json.dumps({"error": f"Ollama indisponível: {exc}"}).encode()
                req.send_response(502)
                req.send_header("Content-Type", "application/json")
                req.end_headers()
                req.wfile.write(msg)
                return
            data = b""
            if resp.status >= 400:  # erros são curtos: lê tudo e registra antes de responder
                data = resp.read()
                text = data.decode("utf-8", "replace")
                self.log(f"Ollama respondeu {resp.status} em {path}: {text[:300]}")
                if meta and resp.status == 500 and "no user query" in text.lower() and attempt < MAX_RETRIES:
                    conn.close()
                    attempt += 1
                    model = meta["model"]
                    self.ratios[model] = min(MAX_RATIO, self.ratios.get(model, 1.0) * RETRY_FACTOR)
                    self.log(f"   O contexto estourou: refazendo com margem maior (tentativa {attempt + 1} de {MAX_RETRIES + 1}).")
                    continue
            break
        try:
            req.send_response(resp.status)
            for key, value in resp.getheaders():
                if key.lower() not in {"transfer-encoding", "connection", "content-length"}:
                    req.send_header(key, value)
            req.end_headers()
            if resp.status >= 400:
                req.wfile.write(data)
            else:
                tail = b""
                while chunk := resp.read1(8192):
                    req.wfile.write(chunk)
                    req.wfile.flush()
                    tail = (tail + chunk)[-4096:]
                self._learn(meta, tail)
        except (BrokenPipeError, ConnectionResetError):
            pass  # o navegador parou a geração
        finally:
            conn.close()
