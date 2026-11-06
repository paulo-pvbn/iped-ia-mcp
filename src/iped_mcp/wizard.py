"""Lógica do assistente (sem interface): etapas por tarefa, validação, comando de indexação e dependências.

A janela (`gui_app`) só desenha; tudo que decide fica aqui, para ser testado sem display.
Nada pessoal é gravado em disco: os caminhos são informados a cada uso.
"""

from __future__ import annotations

import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

from iped_mcp import gui
from iped_mcp import hardware as hw

NOVO, ABRIR, CHAT, MULTI = "novo", "abrir", "chat", "multi"

# Texto do menu suspenso da primeira etapa -> código da tarefa
TASKS = {
    "Indexar um novo caso": NOVO,
    "Abrir um caso já indexado no IPED": ABRIR,
    "Conversar com a IA sobre um caso": CHAT,
    "Conversar com a IA sobre vários casos (multicasos)": MULTI,
}
TASK_HELP = {
    NOVO: "Processa as evidências com o IPED, cria o caso e, no fim, abre no IPED ou conversa com a IA.",
    ABRIR: "Abre no IPED um caso que já foi indexado.",
    CHAT: "Sobe o IPED, o servidor MCP e o chat local (Open WebUI) sobre um caso já indexado.",
    MULTI: "Igual ao chat, mas a IA enxerga todos os casos de uma pasta.",
}


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    subtitle: str


STEPS = {
    "tarefa": Step("tarefa", "Tarefa", "O que você quer fazer"),
    "iped": Step("iped", "IPED", "Pasta de instalação"),
    "origem": Step("origem", "Evidências", "Origem dos dados"),
    "destino": Step("destino", "Pasta do caso", "Onde o caso fica"),
    "proc": Step("proc", "Processamento", "Perfil do IPED"),
    "ia": Step("ia", "Assistente de IA", "Modelo e ferramentas"),
    "revisar": Step("revisar", "Revisar", "Confira antes de executar"),
    "executar": Step("executar", "Executar", "Acompanhe e abra o resultado"),
}
ORDER = {
    NOVO: ("tarefa", "iped", "origem", "destino", "proc", "ia", "revisar", "executar"),
    ABRIR: ("tarefa", "destino", "revisar", "executar"),
    CHAT: ("tarefa", "iped", "destino", "ia", "revisar", "executar"),
    MULTI: ("tarefa", "iped", "destino", "ia", "revisar", "executar"),
}


@dataclass
class State:
    task: str = NOVO
    iped: str = ""
    origem: str = ""
    destino: str = ""
    profile: str = ""  # vazio = padrão do IPED
    extra: str = ""  # argumentos adicionais para o IPED
    modelo: str = ""

    def steps(self) -> list[Step]:
        return [STEPS[k] for k in ORDER[self.task]]


def destino_label(task: str) -> tuple[str, str]:
    """(título, explicação) do campo da pasta, conforme a tarefa."""
    return {
        NOVO: ("Pasta do caso (DESTINO)", "Pasta onde o IPED vai gravar o caso. Pode ser nova ou vazia."),
        ABRIR: ("Pasta do caso indexado", "A pasta do caso, a subpasta iped ou a pasta acima dela."),
        CHAT: ("Pasta do caso indexado", "A IA consulta este caso. Nada sai do seu computador."),
        MULTI: ("Pasta com vários casos", "A IA consulta todos os casos encontrados nesta pasta."),
    }[task]


def list_profiles(iped_root: str) -> list[str]:
    """Perfis da instalação do IPED (subpastas de `profiles`)."""
    base = Path(iped_root) / "profiles" if iped_root else None
    if not base or not base.is_dir():
        return []
    try:
        return sorted(p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []


def split_extra(text: str) -> list[str]:
    try:
        return shlex.split(text, posix=False) if text.strip() else []
    except ValueError:
        return []


def build_command(state: State) -> list[str]:
    """Indexação: `java -jar iped.jar -d <origem> -o <destino> [-profile <perfil>] [extras]`."""
    cmd = gui.build_index_command(Path(state.iped), Path(state.origem), Path(state.destino))
    if state.profile:
        cmd += ["-profile", state.profile]
    return cmd + split_extra(state.extra)


def validate(step_key: str, state: State) -> str:
    """Mensagem de erro em português para a etapa, ou "" se estiver tudo certo."""
    if step_key == "iped":
        if not state.iped or not (Path(state.iped) / "iped.jar").is_file():
            return "Não encontrei o iped.jar nessa pasta. Escolha a pasta de instalação do IPED."
    elif step_key == "origem":
        if not state.origem or not Path(state.origem).exists():
            return "Escolha o arquivo de imagem ou a pasta com as evidências."
    elif step_key == "destino":
        if not state.destino:
            return "Escolha a pasta."
        if state.task == NOVO:
            target = Path(state.destino)
            if target.exists() and not target.is_dir():
                return "O destino precisa ser uma pasta."
            if state.origem and _same_or_inside(target, Path(state.origem)):
                return "O destino não pode ficar dentro da origem das evidências."
        elif state.task == ABRIR:
            if gui.find_case_app(Path(state.destino)) is None:
                return "Não achei o IPED-SearchApp nessa pasta. Escolha a pasta de saída (-o) do caso indexado."
        elif not Path(state.destino).is_dir():
            return "A pasta não existe."
    elif step_key == "proc":
        args = split_extra(state.extra)
        if any(a in {"-d", "-o", "-profile"} for a in args):
            return "Não repita -d, -o nem -profile nos argumentos extras: o assistente já cuida deles."
    elif step_key == "ia" and not state.modelo.strip():
        return "Escolha um modelo do Ollama (abra o Ollama se a lista estiver vazia)."
    return ""


def _same_or_inside(path: Path, parent: Path) -> bool:
    """O destino está dentro da pasta de evidências? (uma imagem em arquivo não conta: o caso pode ficar ao lado dela)."""
    if not parent.is_dir():
        return path.resolve() == parent.resolve()
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def review(state: State) -> list[tuple[str, str]]:
    rows = [("Tarefa", next(k for k, v in TASKS.items() if v == state.task))]
    if state.task != ABRIR:
        rows.append(("Pasta do IPED", state.iped))
    if state.task == NOVO:
        rows += [("Evidências", state.origem), ("Perfil", state.profile or "padrão do IPED")]
    rows.append((destino_label(state.task)[0].split(" (")[0], state.destino))
    if state.task != ABRIR:
        rows.append(("Modelo de IA", state.modelo or "—"))
    return rows


# ------------------------------------------------------------------------------------------- dependências
@dataclass
class Check:
    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str
    hint: str = ""


def dependencies(iped_root: str, ollama_url: str = "http://127.0.0.1:11434", webui_found: bool = False) -> list[Check]:
    out: list[Check] = []
    jar = Path(iped_root) / "iped.jar" if iped_root else None
    if jar and jar.is_file():
        out.append(Check("IPED", "ok", f"iped.jar em {jar.parent}"))
        java = gui.find_java(Path(iped_root))
        if java != "java":
            out.append(Check("Java do IPED", "ok", "JRE empacotada no IPED"))
        elif shutil.which("java"):
            out.append(Check("Java", "ok", "Java do sistema"))
        else:
            out.append(Check("Java", "fail", "Nenhum Java encontrado", "O IPED traz um JRE na pasta jre: confira a instalação."))
    else:
        out.append(Check("IPED", "fail" if iped_root else "warn", "iped.jar não encontrado" if iped_root else "Pasta ainda não informada",
                         "Escolha a pasta que contém o iped.jar."))  # fmt: skip
    tags, _ps = gui.ollama_info(ollama_url)
    if tags:
        out.append(Check("Ollama", "ok", f"{len(tags)} modelo(s) baixado(s)"))
    else:
        out.append(Check("Ollama", "warn", "Não respondeu ou sem modelos", "Abra o Ollama e baixe um modelo (ex.: ollama pull qwen3:8b)."))
    out.append(Check("Open WebUI", "ok" if webui_found else "warn", "Encontrado" if webui_found else "Não encontrado",
                     "" if webui_found else "O assistente oferece instalar na hora de abrir o chat."))  # fmt: skip
    try:
        found = hw.detect()
        if found.gpus:
            gpu = found.gpus[0]
            out.append(Check("Placa de vídeo", "ok", f"{gpu.name}" + (f" ({gpu.vram_gb:g} GB)" if gpu.vram_gb else "")))
        else:
            out.append(Check("Placa de vídeo", "warn", "Nenhuma GPU detectada", "O Ollama usará a CPU (lento)."))
    except Exception as exc:  # noqa: BLE001
        out.append(Check("Placa de vídeo", "warn", f"Não consegui ler ({exc})"))
    return out
