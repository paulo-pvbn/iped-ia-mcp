from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP

from iped_mcp.bookmarks import register as register_bookmarks
from iped_mcp.security import (
    DESTRUCTIVE_TOOLS,
    AuditLog,
    AuditMiddleware,
    SecurityConfig,
    SecurityConfigError,
    apply_tool_policy,
    build_auth,
    verify_chain,
)


def names(server: FastMCP) -> set[str]:
    async def go():
        async with Client(server) as client:
            return {t.name for t in await client.list_tools()}

    return asyncio.run(go())


def test_config_defaults_and_env():
    cfg = SecurityConfig.from_env({})
    assert cfg.host == "127.0.0.1" and cfg.port == 8000 and not cfg.token and not cfg.allow_destructive
    cfg = SecurityConfig.from_env({"IPED_MCP_ALLOW_DESTRUCTIVE": "1", "IPED_MCP_PORT": "9000"})
    assert cfg.allow_destructive and cfg.port == 9000


def test_non_loopback_requires_token():
    with pytest.raises(SecurityConfigError):
        SecurityConfig.from_env({"IPED_MCP_HOST": "0.0.0.0"}).validate()
    SecurityConfig.from_env({"IPED_MCP_HOST": "0.0.0.0", "IPED_MCP_TOKEN": "x"}).validate()
    SecurityConfig.from_env({}).validate()


def test_auth_only_with_token():
    assert build_auth("") is None
    assert build_auth("segredo") is not None


def test_destructive_bookmark_tools_off_by_default():
    server = FastMCP("t")
    register_bookmarks(server)
    assert set(DESTRUCTIVE_TOOLS) <= names(server)
    apply_tool_policy(server, allow_destructive=False)
    left = names(server)
    assert not set(DESTRUCTIVE_TOOLS) & left
    assert {"bookmarks_create", "bookmarks_add", "bookmarks_list"} <= left  # criar/acrescentar continua

    other = FastMCP("t2")
    register_bookmarks(other)
    apply_tool_policy(other, allow_destructive=True)
    assert set(DESTRUCTIVE_TOOLS) <= names(other)


def test_audit_chain_records_calls_and_detects_tampering(tmp_path: Path):
    log_path = tmp_path / "audit" / "a.jsonl"
    server = FastMCP("t")

    @server.tool
    def ping(q: str) -> str:
        return "pong " + q

    @server.tool
    def boom() -> str:
        raise RuntimeError("falhou")

    server.add_middleware(AuditMiddleware(AuditLog(log_path)))

    async def go():
        async with Client(server) as client:
            await client.call_tool("ping", {"q": "oi"})
            await client.call_tool("boom", {}, raise_on_error=False)

    asyncio.run(go())
    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [r["payload"]["tool"] for r in lines] == ["ping", "boom"]
    assert lines[0]["payload"]["args"] == {"q": "oi"} and lines[0]["payload"]["status"] == "ok"
    assert lines[1]["payload"]["status"].startswith("erro")
    assert "pong" not in log_path.read_text(encoding="utf-8")  # o conteúdo devolvido não é gravado
    assert verify_chain(log_path)[:2] == (True, 2)

    log_path.write_text(log_path.read_text(encoding="utf-8").replace('"oi"', '"outro"'), encoding="utf-8")
    ok, _, why = verify_chain(log_path)
    assert not ok and "alterado" in why


def test_audit_resumes_chain_across_restarts(tmp_path: Path):
    path = tmp_path / "a.jsonl"
    AuditLog(path).append("e", "mcp", None, {"n": 1})
    AuditLog(path).append("e", "mcp", None, {"n": 2})
    assert verify_chain(path)[:2] == (True, 2)


def test_main_registers_prompt_and_disables_destructive_tools():
    import main

    tools = names(main.mcp)
    assert not set(DESTRUCTIVE_TOOLS) & tools and "bookmarks_create" in tools
    assert (Path(main.__file__).parent / "prompts" / "analista.md").is_file()

    async def prompts():
        async with Client(main.mcp) as client:
            return [p.name for p in await client.list_prompts()]

    assert "analista_forense" in asyncio.run(prompts())
