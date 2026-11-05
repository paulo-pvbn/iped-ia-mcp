# iped_mcp

MCP server (FastMCP) para interagir com dados telemáticos indexados no IPED.

- Endpoints IPED: variáveis de ambiente `IPED_ENDPOINTS` (múltiplos) ou
  `IPED_URL` (único, padrão `http://localhost:1234`)
- Transporte: HTTP (streamable) em `http://127.0.0.1:8000/mcp`

## Multi-endpoint

O MCP aceita **N endpoints IPED** e age como proxy/roteador transparente: para
o agente continua parecendo um único IPED com um conjunto unificado de
sources (o endpoint nunca é exposto; erros são centrados em source).

```
# múltiplos endpoints (ids únicos):
IPED_ENDPOINTS=ep1=http://host:1234,ep2=http://host:1235

# ou um único (id `default`):
IPED_URL=http://host:1234
```

- `IPED_ENDPOINTS` e `IPED_URL` juntas → erro explícito no startup.
- No startup o MCP sonda `GET /sources/` em paralelo em cada endpoint
  (timeout ~5 s). **Source id duplicado entre endpoints → o MCP não sobe**
  (source id é globalmente único).
- Endpoint offline no startup → aviso no log; o MCP sobe e os sources dele
  simplesmente não aparecem. Todos offline → aviso em destaque; o MCP sobe.
- Toda busca exige `source_id` → 1 request (roteada por registry em memória,
  custo zero extra).
- **Bookmarks são por servidor** (restrição da API do IPED): toda tool
  `bookmarks_*` recebe `source_id` obrigatório e age apenas no servidor
  correspondente. Docs de outro servidor → erro.

## Tools

Nomes como o agente os vê no opencode (prefixo `iped_` = nome do servidor;
no código, as funções do domínio core não levam o prefixo).

| Tool | Descrição |
|---|---|
| `iped_list_sources` | Lista os sources registrados no IPED (`GET /sources/`) |
| `iped_search` | Busca Lucene livre: contagem total + amostra de doc IDs |
| `iped_doc_info` | Propriedades de um documento (conjunto curado, `fields` ou `all`) |
| `iped_doc_text` | Texto extraído de um documento (truncado, paginado) |
| `iped_doc_export` | Baixa o arquivo bruto de um documento para o disco |
| `iped_thumb_export` | Baixa a thumbnail de um documento para o disco |
| `iped_ufed_search` | Busca documentos por atributo `ufed:` (escape `ufed\:` tratado internamente); retorna `id`, `name` e o campo buscado (+ `ufed:EntryValue` quando `attribute` é `EntryName`) |
| `iped_ufed_device_info` | Device Information do source: tabela nome/valor (IMEI, ICCID, IMSI, MSISDN, Serial, modelo, SO, Apple ID, operadora, etc.) |
| `iped_ufed_list_fields` | Referência dos campos `ufed:`: indexados (buscáveis) e não indexados (retornam HTTP 500) |
| `iped_get_document` | Retorna as `properties` completas de um documento |
| `iped_regex_list_types` | Tipos `Regex:*` disponíveis no source (CNPJ, CPF, URL, etc.) |
| `iped_regex_stats` | Matches e valores distintos de um tipo `Regex:*` |
| `iped_regex_top_values` | Top valores mais frequentes de um tipo `Regex:*` |
| `iped_regex_export_csv` | Exporta a tabela de frequência de um tipo `Regex:*` como CSV |
| `iped_regex_value_docs` | Docs onde um valor específico de um tipo `Regex:*` aparece |
| `iped_bookmarks_list` | Lista os nomes dos bookmarks |
| `iped_bookmarks_docs` | Docs de um bookmark (IDs por source, paginado) |
| `iped_bookmarks_create` | Cria um bookmark vazio |
| `iped_bookmarks_add` | Adiciona documentos a um bookmark |
| `iped_bookmarks_remove` | Remove documentos de um bookmark |
| `iped_bookmarks_rename` | Renomeia um bookmark |
| `iped_bookmarks_delete` | Exclui um bookmark (não os documentos indexados) |
| `iped_bookmarks_save_search` | Executa uma busca Lucene e salva os resultados num bookmark |
| `iped_bookmarks_report` | Amostra de documentos de um bookmark com propriedades-chave |
| `iped_bookmarks_download` | Baixa todos os arquivos de um bookmark para uma pasta local |
| `iped_bookmarks_download_info` | Exporta todas as informações textuais estruturadas de um bookmark para um arquivo JSON |
| `iped_bookmarks_create_from_file` | Cria um bookmark a partir de um txt (1 doc ID ou MD5 por linha) |
| `iped_bookmarks_add_from_file` | Adiciona a um bookmark os docs de um txt |
| `iped_bookmarks_remove_from_file` | Remove de um bookmark os docs de um txt |
| `iped_whatsapp_overview` | Triagem do WhatsApp no source: totais, período, série mensal, conversas distintas e top interlocutores |
| `iped_whatsapp_account` | Conta do dono do WhatsApp: JID, número, nome de exibição e status |
| `iped_whatsapp_profile_photo` | Baixa a foto de perfil cacheada de um número (dono ou interlocutor) |
| `iped_whatsapp_conversation` | Analisa uma conversa privada: volume total, período, intensidade e divisão enviado/recebido |
| `iped_whatsapp_conversation_window` | Analisa uma conversa privada numa janela de tempo, segmentada por tipo (texto, áudio, imagem, vídeo, PDF) |
| `iped_whatsapp_export_conversation` | Exporta as mensagens de uma conversa privada para um JSON local (texto, transcrição de áudio, texto de PDF) |
| `iped_whatsapp_text_search` | Busca um termo em todas as conversas privadas (corpo da mensagem, transcrição de áudio e texto de PDF) |
| `iped_whatsapp_reference` | Referência validada de campos, padrões de name, marcadores de tipo e queries canônicas do WhatsApp |

Campos `ufed:` indexados: `EntryName`, `EntryValue`, `EntryCategory`, `Source`,
`extractionName`, `Name`, `URL`, `ChangeTime`, `decoding_confidence`, `isrelated`,
`fs`, `MD5`, `SHA256`. Não indexados: `local_path`, `labels`.

## Estrutura

- `main.py` — servidor MCP (cria o FastMCP, registra as tools, roda)
- `src/iped_mcp/client.py` — `IPEDClient` (API REST do IPED, 1 endpoint)
- `src/iped_mcp/router.py` — `Router` (proxy multi-endpoint: sonda, registry
  source→endpoint, roteamento, bookmarks por servidor)
- `src/iped_mcp/core/` — tools de acesso genérico (`tool.py`)
- `src/iped_mcp/ufed/` — tools de atributos `ufed:` (`tool.py`, `queries.py`)
- `src/iped_mcp/regex/` — tools de atributos `Regex:*` (`tool.py`, `queries.py`, `scan.py`)
- `src/iped_mcp/bookmarks/` — tools de bookmarks (`tool.py`, `download.py`, `idsfile.py`)
- `src/iped_mcp/whatsapp/` — tools de WhatsApp (`tool.py`, `queries.py`, `collect.py`)
- `tests/` — testes unitários e de integração (integração pula se o IPED estiver fora)

## Setup

```bash
uv sync
```

## Uso

Subir o servidor manualmente (HTTP):

```bash
uv run main.py
```

O servidor fica disponível em `http://127.0.0.1:8000/mcp`.

## Testes

```bash
uv run pytest
```

## Configuração no opencode

Adicione ao `opencode.json` (raiz do projeto ou `~/.config/opencode/opencode.json`),
apontando para a porta do servidor:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "iped": {
      "type": "remote",
      "url": "http://localhost:8000/mcp",
      "enabled": true
    }
  }
}
```

Suba o servidor com `uv run main.py` antes de usar o opencode.
As tools acima ficarão disponíveis para o agente.

## Launcher da IPED Web API

O launcher (`web-api-launcher.py`) sobe uma instância da IPED Web API para cada
source de um `multicases.json`, alocando uma porta livre a partir de `1234` e
encerrando todos os servidores no Ctrl+C. É **recomendado no Windows** (gerencia
os processos Java via Job Object).

```bash
python web-api-launcher.py --root "C:\caminho\para\iped" --sources "C:\caminho\multicases.json"
```

Sem argumentos, usa `./` como root e `./multicases.json`. Detalhes em
`WEB-API-LAUNCHER.md`.

## Open WebUI (fork: segurança, auditoria e launcher)

Este fork acrescenta, **sem alterar as tools originais**:

- **Token** (`IPED_MCP_TOKEN`) e escuta só em `127.0.0.1` por padrão; fora dele o servidor não sobe sem token.
- **Tools destrutivas desligadas por padrão**: `bookmarks_remove`, `bookmarks_rename`, `bookmarks_delete` e
  `bookmarks_remove_from_file`. Criar e acrescentar continuam. `IPED_MCP_ALLOW_DESTRUCTIVE=1` liga de volta. **O launcher/executável já liga essas tools** (a análise precisa remover o que não serve; tudo fica na auditoria e só afeta marcadores, não as evidências); use `--sem-destrutivas` para desligar.
- **Auditoria em cadeia de hash** (`audit/iped-mcp-audit.jsonl`): uma linha por chamada (tool, argumentos,
  ok/erro, duração), sem o conteúdo devolvido. Confira com `python -m iped_mcp.security`.
- **Prompt do analista** (`prompts/analista.md`, também exposto como prompt MCP `analista_forense`): cole nas
  instruções do modelo no Open WebUI.
- **Launcher / executável** `iped-ia-mcp.exe`: sobe as Web APIs do IPED (só em 127.0.0.1), o MCP com token e o
  Open WebUI (se instalado). Sem argumentos abre janelas para escolher a pasta do IPED e a pasta dos casos
  (lembra da escolha em `%APPDATA%\iped-ia-mcp\config.json`).

```bash
# executável (baixe em Releases; gerado pelo workflow "Release (executável Windows)")
iped-ia-mcp.exe
iped-ia-mcp.exe --root "C:\CIAF\IPED431\iped-4.3.1" --cases-dir "D:\casos"
iped-ia-mcp.exe --modo verificar          # confere a cadeia de hash da auditoria
# a partir do código
python scripts/entry.py --root "C:\CIAF\IPED431\iped-4.3.1" --sources multicases.json
```

Executáveis gerados no GitHub Actions (PyInstaller) para Windows (`.exe`) e macOS (Apple Silicon e Intel, `.tar.gz`); **não são assinados**: confira o `.sha256` da release. No macOS: `tar -xzf` e, se o Gatekeeper bloquear, `xattr -d com.apple.quarantine ./iped-ia-mcp` (ou botão direito → Abrir). Os executáveis de macOS ainda não foram testados em um Mac.
O IPED (Java) **não** vai dentro dele; aponte a pasta do IPED. O Open WebUI também não: `pip install open-webui`
(Python 3.11) ou Docker.

No Open WebUI (v0.6.31+): Admin Settings → External Tools → **+** → **MCP (Streamable HTTP)** → URL
`http://127.0.0.1:8000/mcp` e o token mostrado pelo launcher. No modelo: contexto de 16k ou mais e raciocínio
ligado. Use modelo **local** (Ollama): o servidor dá acesso a dados de casos. **Não validado ainda** contra o
Open WebUI e o IPED reais (os testes usam clientes MCP em memória).

## Ambiente

É recomendável executar o MCP **no mesmo ambiente do harness** (opencode),
preferencialmente WSL2 — foi testado com o opencode no mesmo ambiente WSL2.
