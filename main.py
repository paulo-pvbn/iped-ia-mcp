import logging
import sys

from fastmcp import FastMCP

from iped_mcp.bookmarks import register as register_bookmarks
from iped_mcp.core import register as register_core
from iped_mcp.regex import register as register_regex
from iped_mcp.router import RouterStartupError, get_router
from iped_mcp.security import (
    AuditLog,
    AuditMiddleware,
    SecurityConfig,
    SecurityConfigError,
    apply_tool_policy,
    build_auth,
)
from iped_mcp.ufed import register as register_ufed
from iped_mcp.whatsapp import register as register_whatsapp

SECURITY = SecurityConfig.from_env()
HOST = SECURITY.host
PORT = SECURITY.port

mcp = FastMCP(
    "iped_mcp",
    auth=build_auth(SECURITY.token),
    instructions=(
        "MCP para consultar dados telemáticos indexados no IPED. "
        "Comece com iped_list_sources para ver os sources disponíveis. "
        "Acesso genérico (família iped_): iped_search faz busca Lucene livre "
        "(devolve contagem + amostra de IDs), iped_doc_info mostra propriedades, "
        "iped_doc_text lê o texto (truncado), iped_doc_export/iped_thumb_export "
        "baixam arquivo bruto/thumbnail para o disco. "
        "Para atributos ufed: (IMEI, ICCID, etc.), use ufed_search ou ufed_device_info; "
        "ufed_list_fields mostra os campos válidos. "
        "Para atributos Regex:* (CNPJ, CPF, URL, etc.), comece com regex_list_types; "
        "regex_stats e regex_top_values dão totais e frequência, "
        "regex_value_docs investiga um valor e regex_export_csv exporta a tabela "
        "(passe output_dir com o caminho absoluto da pasta exports/ do seu projeto). "
        "Para gerenciar coleções de documentos (bookmarks/marcadores), use as tools "
        "bookmarks_*: bookmarks_list/bookmarks_docs listam, bookmarks_create/add/remove "
        "gerenciam, bookmarks_save_search salva uma busca, bookmarks_report amostra "
        "propriedades-chave, bookmarks_download baixa os arquivos e "
        "bookmarks_download_info exporta as informações estruturadas (propriedades "
        "completas + texto) para um JSON (lotes grandes de IDs entram via arquivo txt "
        "nas tools *_from_file). Todas as tools de bookmark "
        "recebem source_id (obrigatório) para rotear a operação. "
        "Para dados do WhatsApp (família whatsapp_), comece com whatsapp_overview "
        "(triagem: totais, período, top interlocutores) e whatsapp_account (dono); "
        "whatsapp_conversation/whatsapp_conversation_window analisam uma conversa "
        "privada (volume, intensidade, janela por tipo), whatsapp_export_conversation "
        "grava o JSON da conversa no disco, whatsapp_text_search busca um termo em "
        "mensagem/áudio/PDF e whatsapp_profile_photo baixa o avatar de um número. "
        "whatsapp_reference documenta os campos e queries canônicas validados."
    ),
)

register_core(mcp)
register_ufed(mcp)
register_regex(mcp)
register_bookmarks(mcp)
register_whatsapp(mcp)

# Acréscimos do fork: tools destrutivas desligadas por padrão, auditoria em cadeia e prompt do analista.
DISABLED_TOOLS = apply_tool_policy(mcp, SECURITY.allow_destructive)
mcp.add_middleware(AuditMiddleware(AuditLog(SECURITY.audit_path)))


@mcp.prompt(name="analista_forense", description="Instruções do analista forense (cole no modelo do Open WebUI)")
def analista_forense() -> str:
    from pathlib import Path

    return (Path(__file__).parent / "prompts" / "analista.md").read_text(encoding="utf-8")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        SECURITY.validate()
    except SecurityConfigError as exc:
        print(f"IPED MCP: {exc}", file=sys.stderr)
        sys.exit(1)
    if DISABLED_TOOLS:
        logging.getLogger("iped_mcp").info(
            "Tools desligadas (IPED_MCP_ALLOW_DESTRUCTIVE=1 liga): %s", ", ".join(DISABLED_TOOLS)
        )
    try:
        get_router()
    except RouterStartupError as exc:
        print(f"IPED MCP: {exc}", file=sys.stderr)
        sys.exit(1)
    mcp.run(transport="http", host=HOST, port=PORT)


if __name__ == "__main__":
    main()
