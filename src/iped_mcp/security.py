"""Segurança e rastreabilidade do MCP (acréscimos do fork; não altera as tools originais).

- Token opcional (``IPED_MCP_TOKEN``): o cliente envia ``Authorization: Bearer <token>``.
- Escuta só em loopback por padrão; fora dele exige token (``IPED_MCP_HOST``).
- Tools que alteram ou apagam marcadores ficam DESLIGADAS por padrão (``IPED_MCP_ALLOW_DESTRUCTIVE=1`` liga).
- Auditoria em cadeia de hash (JSONL), uma linha por chamada de tool (``IPED_MCP_AUDIT``). Confira com
  ``python -m iped_mcp.security <arquivo>``.

A auditoria registra QUAL consulta foi feita (nome da tool e argumentos), nunca o conteúdo devolvido.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmcp.server.middleware import Middleware

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
# Tools que mudam o que o analista já organizou no caso (README: remove/rename/delete).
DESTRUCTIVE_TOOLS = (
    "bookmarks_remove",
    "bookmarks_rename",
    "bookmarks_delete",
    "bookmarks_remove_from_file",
)
GENESIS = "0" * 64
MAX_ARG_CHARS = 2000


class SecurityConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class SecurityConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    token: str = ""
    allow_destructive: bool = False
    audit_path: Path = Path("audit") / "iped-mcp-audit.jsonl"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> SecurityConfig:
        e = os.environ if env is None else env
        return cls(
            host=e.get("IPED_MCP_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=int(e.get("IPED_MCP_PORT", "8000") or 8000),
            token=e.get("IPED_MCP_TOKEN", "").strip(),
            allow_destructive=e.get("IPED_MCP_ALLOW_DESTRUCTIVE", "").strip().lower() in {"1", "true", "sim"},
            audit_path=Path(e.get("IPED_MCP_AUDIT", "") or Path("audit") / "iped-mcp-audit.jsonl"),
        )

    def validate(self) -> None:
        if self.host not in LOOPBACK and not self.token:
            raise SecurityConfigError(
                "Defina IPED_MCP_TOKEN para escutar fora de 127.0.0.1 (o servidor dá acesso aos casos)."
            )


def build_auth(token: str):
    """Verificador de token estático do FastMCP, ou ``None`` sem token."""
    if not token:
        return None
    from fastmcp.server.auth import StaticTokenVerifier

    return StaticTokenVerifier(tokens={token: {"client_id": "iped-mcp", "scopes": []}})


def apply_tool_policy(mcp: Any, allow_destructive: bool) -> tuple[str, ...]:
    """Desliga as tools destrutivas (a menos que liberadas). Devolve os nomes desligados."""
    if allow_destructive:
        return ()
    mcp.disable(names=set(DESTRUCTIVE_TOOLS))
    return DESTRUCTIVE_TOOLS


# ------------------------------------------------------------------------------------------------ auditoria
def _digest(prev: str, record: dict[str, Any]) -> str:
    canonical = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((prev + canonical).encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._last = GENESIS
        if self.path.is_file():
            with self.path.open(encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        self._last = json.loads(line)["hash"]

    def append(self, event: str, user: str, case: str | None, payload: dict[str, Any]) -> str:
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "user": user,
            "case": case,
            "payload": payload,
        }
        with self._lock:
            record["prev"] = self._last
            digest = _digest(self._last, record)
            record["hash"] = digest
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._last = digest
        return digest


def verify_chain(path: Path) -> tuple[bool, int, str | None]:
    prev, count = GENESIS, 0
    with Path(path).open(encoding="utf-8") as fh:
        for number, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            claimed = record.pop("hash", None)
            if record.get("prev") != prev:
                return False, count, f"linha {number}: encadeamento quebrado"
            if _digest(prev, record) != claimed:
                return False, count, f"linha {number}: conteúdo alterado"
            prev = claimed
            count += 1
    return True, count, None


def _clip(arguments: dict[str, Any] | None) -> dict[str, Any]:
    text = json.dumps(arguments or {}, ensure_ascii=False, default=str)
    if len(text) <= MAX_ARG_CHARS:
        return json.loads(text)
    return {"_truncado": text[:MAX_ARG_CHARS]}


class AuditMiddleware(Middleware):
    """Uma linha na cadeia por chamada de tool: nome, argumentos, resultado (ok/erro) e duração."""

    def __init__(self, log: AuditLog):
        self._log = log

    async def on_call_tool(self, context, call_next):
        started = time.monotonic()
        name = getattr(context.message, "name", "?")
        args = _clip(getattr(context.message, "arguments", None))
        try:
            result = await call_next(context)
        except Exception as exc:
            self._safe("mcp_tool_call", name, args, started, f"erro: {type(exc).__name__}")
            raise
        self._safe("mcp_tool_call", name, args, started, "ok")
        return result

    def _safe(self, event: str, name: str, args: dict[str, Any], started: float, status: str) -> None:
        try:
            self._log.append(
                event,
                "mcp",
                None,
                {"tool": name, "args": args, "status": status, "ms": int((time.monotonic() - started) * 1000)},
            )
        except OSError as exc:  # a auditoria não derruba a consulta, mas o erro aparece no log do servidor
            print(f"IPED MCP: falha ao gravar a auditoria: {exc}", file=sys.stderr)


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else SecurityConfig.from_env().audit_path
    ok, n, why = verify_chain(target)
    print(f"{'ÍNTEGRO' if ok else 'ADULTERADO'}: {n} registros verificados" + (f" ({why})" if why else ""))
    raise SystemExit(0 if ok else 1)
