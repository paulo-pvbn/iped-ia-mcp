# iped_mcp

MCP server (FastMCP) para interagir com dados telemáticos indexados no IPED.

> ## Este repositório é um fork: o que é do original e o que foi acrescentado
>
> - **Original** ([`caiocfcs/iped-mcp`](https://github.com/caiocfcs/iped-mcp), autoria de Caio): o servidor MCP
>   e as ~37 tools sobre a Web API do IPED, o roteamento multi-endpoint e o `web-api-launcher.py`.
>   **Nada disso foi alterado.**
> - **Acrescentado neste fork** (para usar com o Open WebUI e distribuir como programa):
>   token e escuta só em 127.0.0.1, auditoria em cadeia de hash, tools de remoção de marcadores sob controle
>   (`IPED_MCP_ALLOW_DESTRUCTIVE`), prompt do analista, launcher, e o executável `iped-ia-mcp`
>   (Windows e macOS, na aba Releases). Veja a seção "Open WebUI (fork…)" abaixo.
> - Para fazer o seu modelo conversar com os casos: use o executável deste repositório + Open WebUI + Ollama.

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
  `bookmarks_remove_from_file`. Criar e acrescentar continuam. O servidor (`python main.py`) só as liga com `IPED_MCP_ALLOW_DESTRUCTIVE=1`; **o launcher e a janela do programa as mantêm sempre desligadas** (remover marcadores se faz no próprio IPED).
- **Auditoria em cadeia de hash** (`audit/iped-mcp-audit.jsonl`): uma linha por chamada (tool, argumentos,
  ok/erro, duração), sem o conteúdo devolvido. Confira com `python -m iped_mcp.security`.
- **Instruções do modelo** (`prompts/analista.md`, também exposto como prompt MCP `analista_forense`): um texto curto
  e genérico. Para usar as suas, salve-as como `prompt.md` em `%APPDATA%\iped-ia-mcp`; esse arquivo tem prioridade e fica só no seu computador.
- **Launcher / executável** `iped-ia-mcp.exe`: sobe as Web APIs do IPED (só em 127.0.0.1), o MCP com token e o
  Open WebUI. Sem argumentos abre a janela, onde cada usuário informa as suas pastas (nada vem preenchido). O botão
  **Chat com IA** procura o Open WebUI e, se faltar, oferece instalar um ambiente próprio em `%APPDATA%\iped-ia-mcp\openwebui` (precisa de Python 3.11 e
  internet); depois, **Chat com IA** sobe tudo e o Open WebUI usa o Ollama em `http://127.0.0.1:11434`.

**Janela v1.0 (duplo clique no `.exe`, sem argumentos).** Assistente em etapas, tema escuro e menus suspensos. A primeira
etapa pergunta a **tarefa** e o assistente mostra só as etapas que ela precisa:

| Tarefa | Etapas |
|---|---|
| Indexar um novo caso | Tarefa, IPED, Evidências, Pasta do caso, Processamento, Assistente de IA, Revisar, Executar (8) |
| Abrir um caso já indexado | Tarefa, Pasta do caso, Revisar, Executar |
| Conversar com a IA (um caso ou vários) | Tarefa, IPED, Pasta do caso, Assistente de IA, Revisar, Executar |

Cada etapa valida o que foi informado antes de avançar (mensagem logo abaixo do campo) e o que já foi feito pode ser revisitado
pela barra lateral. **Processamento** lê os perfis da instalação do IPED (subpastas de `profiles`) num menu suspenso e aceita
argumentos extras em "Opções avançadas". **Revisar** mostra o comando exato (`java -jar iped.jar -d ORIGEM -o DESTINO
[-profile PERFIL] [extras]`); essas opções do IPED **não foram validadas aqui contra o IPED real**. Em **Executar** ficam o
andamento, os botões *Abrir no IPED* e *Conversar com a IA* e os detalhes técnicos (log). Menus: **Caso** (trocar de tarefa),
**IA** (Ferramentas da IA, Hardware e modelo, Dependências) e **Ajuda**. Nada pessoal é preenchido nem gravado: cada pessoa
informa as suas pastas a cada uso. Com argumentos (`--root`, `--cases-dir`…) o programa usa o modo de console.

No rodapé da barra lateral, **Ambiente** mostra se o IPED, o Ollama e o Open WebUI estão prontos e, em mini roscas, o uso de
**CPU**, **GPU** e **memória** (atualizado a cada 2 s; verde abaixo de 70 %, âmbar até 90 %, vermelho acima). A GPU vem do
`nvidia-smi`; sem placa NVIDIA ela aparece como "—". Tudo é lido localmente.

**Voltar à versão anterior.** A janela da 0.6.x continua na release `v0.6.0` e na branch `estavel-v0.6`.

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

### Open WebUI configurado automaticamente

O **Chat com IA** sobe o Open WebUI **sem tela de login, só em `127.0.0.1`** (dados em `%APPDATA%\iped-ia-mcp\openwebui\data-local`),
cadastra sozinho o servidor MCP do IPED (URL e token da execução), cria o modelo **Analista IPED** (sobre o modelo do
Ollama escolhido na janela, com as instruções do modelo e as ferramentas do IPED já ligadas) e abre o navegador. Validado
contra o Open WebUI 0.13; versões muito diferentes podem exigir cadastro manual (o programa avisa).

O Open WebUI do programa usa a porta **3737** (e não a 3000): outro endereço evita que o navegador reaproveite páginas,
sessões ou *service workers* de outros programas que já usaram `localhost:3000` (causa comum da tela
"Backend Open WebUI necessário").

**Contexto do modelo e ferramentas.** O "Analista IPED" é criado com `num_ctx` 16384 e as ferramentas internas do
Open WebUI desligadas. Cada ferramenta do IPED ocupa contexto (as 33 somam ~9 mil tokens): o menu **IA > Ferramentas da IA…**
da janela abre a lista com todas, agrupadas (Geral, Regex, UFED, WhatsApp, Marcadores), mostra o total de tokens e deixa
ligar/desligar cada uma; o padrão são as 12 essenciais (busca, leitura, WhatsApp, listar/criar/acrescentar marcador; Regex e UFED ficam
desligadas e se ligam no painel). A escolha vale no próximo chat. Pela linha de comando: `--ferramentas busca,doc_info,...`.
Com as 33 ferramentas e contexto de 8k o Ollama cortava a pergunta e o modelo Qwen falhava com
"No user query found in messages". O servidor MCP em si continua expondo todas as ferramentas a outros clientes.

**GPU de 8 GB.** Modelos acima de ~9B parâmetros (ex.: 27B) não cabem na GPU e rodam em parte na CPU, muito lentos; prefira
modelos de 4B a 8B (ex.: `qwen3:8b`) e deixe só as ferramentas de que precisa. A janela avisa quando o modelo escolhido é grande.

**Hardware e modelo recomendado.** Ao abrir, a janela detecta a GPU (via `nvidia-smi`; em AMD/Intel só o nome, a memória
não é confiável) e a RAM e escreve uma linha com o modelo recomendado. O menu **IA > Hardware e modelo…** lê os modelos baixados no
Ollama, estima quais cabem inteiros na VRAM (tamanho do modelo + cache de contexto + folga) e mostra, depois que o modelo
carrega, a medida real do Ollama (`% na GPU`). **Usar \<modelo\>** escolhe o modelo e o contexto (16384 ou 12288) para o
próximo Chat com IA. As contas são estimativas; para 8 GB de VRAM costuma servir um modelo de 4B a 8B com
`OLLAMA_FLASH_ATTENTION=1` e `OLLAMA_KV_CACHE_TYPE=q8_0` no Ollama. Nada sai da máquina (só `127.0.0.1`).

**Busca de vários termos (`topic_search`).** A busca livre do IPED casa a palavra **exata** (`comprar` não casa `comprou`), e modelos pequenos tendem a tentar uma frase literal, receber zero e concluir que não há
nada. `topic_search` roda cada termo **que o próprio modelo escolheu** como consulta separada (palavras longas ganham
radical automático), informando a contagem por termo e a união. Não há lista de temas nem de sinônimos embutida: quem decide
o que procurar é o modelo.

**Guarda de contexto (v0.6.0).** Entre o Open WebUI e o Ollama o programa sobe um pequeno intermediário local
(`127.0.0.1:11435`, só biblioteca padrão). Toda chamada de chat passa por ele: se a conversa (prompt + ferramentas +
resultados) ultrapassa a janela do modelo, ele **encurta os resultados de ferramentas mais antigos** (depois descarta turnos
antigos) mantendo sempre o prompt e a pergunta; sem isso o Ollama corta o começo e o Qwen falha com "No user query found
in messages". Cada chamada vira uma linha no console, por exemplo `[contexto] chat #7 (qwen3:8b): ~13200 de 14400 tokens
úteis (janela 16384): cabe.` ou `...: passou. Ajuste: 5 resultado(s) encurtado(s)...`; erros do Ollama (como o 500) também
aparecem ali, com o texto original. O Open WebUI é reconfigurado a cada execução para usar a guarda; se o Ollama estiver
em outra máquina, `--ollama-url` continua valendo (a guarda repassa para ele).

**Autocorreção (v1.0.2).** A conta de tokens da guarda é uma estimativa e pode subestimar (português, JSON, caminhos). Por isso
ela confere com o Ollama: se vier "No user query found", **refaz a chamada com mais margem** (até 3 vezes; o erro ocorre antes
de gerar, então é rápido) e lembra o fator para as próximas; quando o Ollama informa quantos tokens leu, a guarda compara com a
estimativa e só aperta o limite, nunca afrouxa. No console: `Ollama leu 14200 tokens (estimei ~9800; relação 1.45)`.

**Resultados que já se explicam.** `search` devolve, para os primeiros resultados (`preview`, padrão 8), o nome, o caminho,
a data e o **trecho onde o termo aparece**, buscados no próprio servidor. O modelo julga a relevância sem uma chamada
`doc_info` por item e sem ler o documento inteiro.
