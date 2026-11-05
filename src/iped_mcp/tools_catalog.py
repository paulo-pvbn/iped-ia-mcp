"""Catálogo das ferramentas do IPED e filtro que o Open WebUI usa para mostrá-las ao modelo.

Cada ferramenta ocupa contexto do modelo (a lista inteira passa de 9 mil tokens). Modelos locais pequenos
funcionam melhor com poucas: a janela do programa deixa o usuário ligar/desligar cada uma.
O Open WebUI compara o filtro com o FIM do nome da ferramenta (sufixo); `build_filter` cuida das ambiguidades.
"""

from __future__ import annotations

# (nome, grupo, tokens aproximados, descrição curta, ligada por padrão)
CATALOG: tuple[tuple[str, str, int, str, bool], ...] = (
    ("list_sources", "Geral", 95, "Lista as fontes (celular, nuvem, computador) do caso", True),
    ("search", "Geral", 884, "Busca Lucene livre (contagem + amostra de IDs)", True),
    ("doc_info", "Geral", 323, "Propriedades de um item", True),
    ("doc_text", "Geral", 303, "Texto extraído de um item", True),
    ("get_document", "Geral", 157, "Todas as propriedades de um item", True),
    ("doc_export", "Geral", 251, "Salva o arquivo bruto de um item em disco", False),
    ("thumb_export", "Geral", 244, "Salva a miniatura de um item em disco", False),
    ("regex_list_types", "Regex", 290, "Tipos Regex:* disponíveis (CPF, CNPJ, URL…)", True),
    ("regex_stats", "Regex", 219, "Totais de um tipo Regex:*", True),
    ("regex_top_values", "Regex", 290, "Valores mais frequentes de um tipo", True),
    ("regex_value_docs", "Regex", 273, "Itens onde um valor aparece", True),
    ("regex_export_csv", "Regex", 373, "Exporta a tabela de frequência em CSV", False),
    ("ufed_device_info", "UFED", 164, "Dados do aparelho (IMEI, modelo…)", True),
    ("ufed_list_fields", "UFED", 91, "Campos ufed: buscáveis", True),
    ("ufed_search", "UFED", 481, "Busca por atributo ufed:", True),
    ("whatsapp_overview", "WhatsApp", 291, "Triagem: totais, período, principais conversas", True),
    ("whatsapp_account", "WhatsApp", 148, "Conta do dono do WhatsApp", True),
    ("whatsapp_conversation", "WhatsApp", 301, "Análise de uma conversa privada", True),
    ("whatsapp_text_search", "WhatsApp", 311, "Busca um termo em todas as conversas", True),
    ("whatsapp_reference", "WhatsApp", 140, "Campos e consultas canônicas do WhatsApp", True),
    ("whatsapp_conversation_window", "WhatsApp", 285, "Conversa dentro de uma janela de tempo", False),
    ("whatsapp_export_conversation", "WhatsApp", 505, "Exporta uma conversa em JSON", False),
    ("whatsapp_profile_photo", "WhatsApp", 294, "Baixa a foto de perfil", False),
    ("bookmarks_list", "Marcadores", 112, "Lista os marcadores", True),
    ("bookmarks_create", "Marcadores", 172, "Cria um marcador novo (prefixo IA:)", True),
    ("bookmarks_add", "Marcadores", 246, "Acrescenta itens a um marcador", True),
    ("bookmarks_docs", "Marcadores", 317, "Itens de um marcador", True),
    ("bookmarks_report", "Marcadores", 245, "Amostra de itens de um marcador", False),
    ("bookmarks_save_search", "Marcadores", 314, "Salva o resultado de uma busca num marcador", False),
    ("bookmarks_add_from_file", "Marcadores", 242, "Acrescenta itens de um arquivo txt", False),
    ("bookmarks_create_from_file", "Marcadores", 264, "Cria marcador a partir de um arquivo txt", False),
    ("bookmarks_download", "Marcadores", 255, "Baixa os arquivos de um marcador", False),
    ("bookmarks_download_info", "Marcadores", 308, "Exporta informações de um marcador em JSON", False),
)

NAMES = tuple(c[0] for c in CATALOG)
DEFAULT = tuple(c[0] for c in CATALOG if c[4])
# Tokens do prompt padrão + folga para a conversa; acima disso o contexto de 16k começa a apertar.
TOKEN_WARN = 7000


def normalize(selected: object) -> list[str]:
    """Aceita lista ou texto 'a,b,c'; descarta nomes desconhecidos, sem repetir, na ordem do catálogo."""
    items = selected.split(",") if isinstance(selected, str) else list(selected or [])
    wanted = {str(i).strip() for i in items}
    return [n for n in NAMES if n in wanted]


def tokens(selected: object) -> int:
    chosen = set(normalize(selected))
    return sum(c[2] for c in CATALOG if c[0] in chosen)


def build_filter(selected: object, all_names: tuple[str, ...] | list[str] = NAMES) -> str:
    """Texto para `function_name_filter_list` do Open WebUI (casa por sufixo).

    Um nome curto como `search` também casaria `ufed_search`; as que sobram são bloqueadas com `!nome`.
    Sem nenhuma ferramenta escolhida devolve um filtro que não casa com nada.
    """
    chosen = normalize(selected)
    if not chosen:
        return "__nenhuma_ferramenta__"
    extras = [n for n in all_names if n not in chosen and any(n.endswith(c) for c in chosen)]
    return ",".join([*chosen, *(f"!{n}" for n in extras)])
