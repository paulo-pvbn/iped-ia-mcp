"""Janela do iped-ia-mcp: indexar um caso com o IPED e conversar com ele no Open WebUI.

Campos: pasta do IPED, ORIGEM (evidência) e DESTINO (pasta do caso). Botões: INDEXAR, Cancelar, Abrir caso,
Multicasos… e Chat com IA (Web APIs do IPED + MCP + Open WebUI, reaproveitando ``webui_launcher``).

A indexação chama o IPED com a sintaxe padrão ``-d <origem> -o <destino>``. Não foi validada aqui contra o
IPED real: a linha de comando completa aparece no log antes de rodar.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from iped_mcp import tools_catalog as tc
from iped_mcp import webui_launcher as wl

APP_TITLE = "IPED-IA MCP"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW


# ------------------------------------------------------------------------------- lógica (sem interface)
def find_java(iped_root: Path) -> str:
    """Java do próprio IPED (``jre``) ou, na falta dele, o do sistema."""
    for rel in ("jre/bin/java.exe", "jre/bin/java"):
        candidate = iped_root / rel
        if candidate.is_file():
            return str(candidate)
    return "java"


def build_index_command(iped_root: Path, origem: Path, destino: Path) -> list[str]:
    """Linha de comando de indexação: ``java -jar iped.jar -d <origem> -o <destino>``."""
    return [find_java(iped_root), "-jar", str(iped_root / "iped.jar"), "-d", str(origem), "-o", str(destino)]


def check_index_inputs(iped_root: str, origem: str, destino: str) -> str:
    """Mensagem de erro em português, ou ``""`` se estiver tudo certo."""
    if not iped_root or not (Path(iped_root) / "iped.jar").is_file():
        return "Informe a pasta do IPED (a que contém o iped.jar)."
    if not origem or not Path(origem).exists():
        return "Informe a ORIGEM (arquivo de imagem ou pasta com as evidências)."
    if not destino:
        return "Informe o DESTINO (pasta onde o caso será gravado)."
    return ""


APP_NAMES = ("IPED-SearchApp.exe", "IPED-SearchApp")


def case_app(destino: Path) -> Path | None:
    """Executável do SearchApp diretamente dentro da pasta do caso, se existir."""
    for name in APP_NAMES:
        candidate = destino / name
        if candidate.is_file():
            return candidate
    return None


def find_case_app(folder: Path, depth: int = 2) -> Path | None:
    """Procura o SearchApp na pasta, nas subpastas (até `depth` níveis) e na pasta-mãe.

    Serve para caso já indexado: o usuário pode apontar a pasta do caso, a subpasta
    `iped` ou a pasta acima dela.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return None
    level = [folder]
    for _ in range(depth + 1):
        nxt: list[Path] = []
        for current in level:
            found = case_app(current)
            if found:
                return found
            try:
                nxt.extend(sorted(c for c in current.iterdir() if c.is_dir()))
            except OSError:
                continue
        level = nxt
    return case_app(folder.parent) if folder.name.lower() == "iped" else None


def launch_detached(app: Path) -> None:  # pragma: no cover - depende do SO
    """Abre o SearchApp sem prender a janela; cwd = pasta do executável."""
    if os.name == "nt":
        os.startfile(str(app))  # noqa: S606
    else:
        subprocess.Popen([str(app)], cwd=str(app.parent), start_new_session=True)  # noqa: S603


def ollama_models(url: str = "http://127.0.0.1:11434") -> list[str]:
    """Modelos baixados no Ollama (lista vazia se ele não estiver aberto)."""
    try:
        data = wl._api("GET", url.rstrip("/") + "/api/tags", None, None, 4)
        return sorted(m["name"] for m in (data or {}).get("models", []))
    except Exception:  # noqa: BLE001
        return []


def big_model_hint(modelo: str) -> str:
    """Aviso quando o modelo é grande para uma GPU de 8 GB (quantização padrão: ~0,6 GB por bilhão de parâmetros)."""
    import re as _re

    match = _re.search(r"(\d+(?:\.\d+)?)\s*b\b", modelo.lower())
    if match and float(match.group(1)) > 9:
        return (f"Aviso: '{modelo}' é um modelo grande; em GPU de 8 GB ele não cabe inteiro e fica lento (parte vai para a CPU). "
                "Modelos de 4B a 8B (ex.: qwen3:8b) respondem bem mais rápido.")
    return ""


def chat_command(
    iped_root: str, cases_dir: str, *, sem_destrutivas: bool = False, modelo: str = "", ferramentas: object = None
) -> list[str]:
    cmd = [*wl.self_command("iniciar"), "--root", iped_root, "--cases-dir", cases_dir]
    if modelo:
        cmd += ["--modelo", modelo]
    chosen = tc.normalize(ferramentas) if ferramentas else []
    if chosen:
        cmd += ["--ferramentas", ",".join(chosen)]
    if sem_destrutivas:
        cmd.append("--sem-destrutivas")
    return cmd


def kill_tree(proc: subprocess.Popen) -> None:
    wl.stop(proc)


# ------------------------------------------------------------------------------------------- interface
class App:  # pragma: no cover - interface gráfica (não roda sem display)
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, scrolledtext, ttk

        self.tk, self.filedialog, self.messagebox = tk, filedialog, messagebox
        self.root = tk.Tk()
        self.root.title(f"{APP_TITLE} {self._version()}")
        self.root.geometry("1000x620")
        # Cada usuário informa os seus caminhos: nada vem preenchido.
        self.vars = {k: tk.StringVar(value="") for k in ("iped", "origem", "destino")}
        self.modelo = tk.StringVar(value=wl.load_config().get("modelo", ""))  # preferência do programa, não é caminho pessoal
        self.multi_dir = ""
        self.index_proc: subprocess.Popen | None = None
        self.chat_proc: subprocess.Popen | None = None
        self.lines: queue.Queue[str] = queue.Queue()

        frame = ttk.Frame(self.root, padding=10)
        frame.pack(fill="both", expand=True)
        for row, (label, key) in enumerate((("Pasta do IPED", "iped"), ("ORIGEM", "origem"), ("DESTINO", "destino"))):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Entry(frame, textvariable=self.vars[key]).grid(row=row, column=1, sticky="ew", padx=6)
            ttk.Button(frame, text="...", width=3, command=lambda k=key: self.pick(k)).grid(row=row, column=2)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Modelo (Ollama)").grid(row=3, column=0, sticky="w", pady=2)
        self.cmb_modelo = ttk.Combobox(frame, textvariable=self.modelo, values=[])
        self.cmb_modelo.grid(row=3, column=1, sticky="ew", padx=6)
        threading.Thread(target=self.load_models, daemon=True).start()

        bar = ttk.Frame(frame)
        bar.grid(row=4, column=0, columnspan=3, sticky="w", pady=10)
        self.btn_index = ttk.Button(bar, text="INDEXAR", command=self.index)
        self.btn_cancel = ttk.Button(bar, text="Cancelar", command=self.cancel, state="disabled")
        self.btn_open = ttk.Button(bar, text="Abrir caso", command=self.open_case)
        self.btn_multi = ttk.Button(bar, text="Multicasos...", command=self.pick_multi)
        self.btn_chat = ttk.Button(bar, text="Chat com IA", command=self.toggle_chat)
        self.btn_tools = ttk.Button(bar, text="Ferramentas da IA...", command=self.pick_tools)
        for btn in (self.btn_index, self.btn_cancel, self.btn_open, self.btn_multi, self.btn_chat, self.btn_tools):
            btn.pack(side="left", padx=4)
        self.bar = bar

        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.grid(row=5, column=0, columnspan=3, sticky="ew")
        self.log = scrolledtext.ScrolledText(frame, height=24, state="disabled", wrap="word")
        self.log.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        frame.rowconfigure(6, weight=1)

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(150, self.drain)
        self.say("Pronto. Escolha a pasta do IPED, a ORIGEM e o DESTINO e clique em INDEXAR; "
                 "ou aponte o DESTINO para um caso já indexado e clique em Chat com IA.")  # fmt: skip

    @staticmethod
    def _version() -> str:
        from iped_mcp.version import VERSION

        return VERSION

    # ---- util
    def say(self, text: str) -> None:
        self.lines.put(text)

    def drain(self) -> None:
        try:
            while True:
                text = self.lines.get_nowait()
                self.log.configure(state="normal")
                self.log.insert("end", text.rstrip("\n") + "\n")
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(150, self.drain)

    def load_models(self) -> None:
        models = ollama_models()

        def apply() -> None:
            self.cmb_modelo.configure(values=models)
            if not self.modelo.get() and models:
                self.modelo.set(models[0])
            self.say(f"Ollama: {len(models)} modelo(s) encontrado(s)." if models else "Ollama não respondeu: abra o Ollama antes do Chat com IA.")

        self.root.after(0, apply)

    def save(self) -> None:
        """Caminhos pessoais não são gravados em disco (cada usuário informa os seus)."""

    def chosen_tools(self) -> list[str]:
        saved = wl.load_config().get("ferramentas", "")
        return tc.normalize(saved) if saved else list(tc.DEFAULT)

    def pick_tools(self) -> None:
        """Janela para ligar/desligar cada ferramenta do IPED que o modelo enxerga (vale no próximo Chat com IA)."""
        tk = self.tk
        from tkinter import ttk

        win = tk.Toplevel(self.root)
        win.title("Ferramentas da IA")
        win.geometry("640x640")
        win.transient(self.root)
        current = set(self.chosen_tools())
        vars_: dict[str, tk.BooleanVar] = {}
        total = tk.StringVar()

        def refresh(*_a: object) -> None:
            sel = [n for n, v in vars_.items() if v.get()]
            tok = tc.tokens(sel)
            warn = "  ⚠ muitas: modelos pequenos erram e o contexto aperta" if tok > tc.TOKEN_WARN else ""
            total.set(f"{len(sel)} de {len(tc.NAMES)} ligadas — cerca de {tok} tokens de contexto{warn}")

        top = ttk.Frame(win, padding=8)
        top.pack(fill="x")
        ttk.Label(
            top,
            text="Cada ferramenta ligada ocupa contexto do modelo. Em GPU de 8 GB, menos é melhor.\n"
            "A mudança vale na próxima vez que você clicar em Chat com IA.",
            justify="left",
        ).pack(anchor="w")
        ttk.Label(top, textvariable=total, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(6, 0))

        canvas = tk.Canvas(win, highlightthickness=0)
        scroll = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas, padding=8)
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        group = ""
        for name, grp, tok, desc, _on in tc.CATALOG:
            if grp != group:
                group = grp
                ttk.Label(inner, text=grp, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(8, 0))
            var = tk.BooleanVar(value=name in current)
            var.trace_add("write", refresh)
            vars_[name] = var
            ttk.Checkbutton(inner, text=f"{name}  (~{tok})  —  {desc}", variable=var).pack(anchor="w")
        refresh()

        def setall(names: object) -> None:
            chosen = set(names)  # type: ignore[arg-type]
            for n, v in vars_.items():
                v.set(n in chosen)

        def save_and_close() -> None:
            cfg = wl.load_config()
            cfg["ferramentas"] = ",".join(n for n, v in vars_.items() if v.get())
            wl.save_config(cfg)
            canvas.unbind_all("<MouseWheel>")
            win.destroy()
            self.say("Ferramentas da IA salvas. Valem na próxima vez que você clicar em Chat com IA"
                     + (" (pare e inicie o chat de novo)." if self.chat_proc is not None else "."))

        bottom = ttk.Frame(win, padding=8)
        bottom.pack(side="bottom", fill="x")
        ttk.Button(bottom, text="Essenciais", command=lambda: setall(tc.DEFAULT)).pack(side="left", padx=2)
        ttk.Button(bottom, text="Todas", command=lambda: setall(tc.NAMES)).pack(side="left", padx=2)
        ttk.Button(bottom, text="Salvar", command=save_and_close).pack(side="right", padx=2)
        ttk.Button(bottom, text="Cancelar", command=win.destroy).pack(side="right", padx=2)

    def pick(self, key: str) -> None:
        if key == "origem":  # imagem (arquivo) ou pasta: pergunta primeiro pelo arquivo
            chosen = self.filedialog.askopenfilename(title="ORIGEM: arquivo de imagem (cancele para escolher uma pasta)")
            if not chosen:
                chosen = self.filedialog.askdirectory(title="ORIGEM: pasta com as evidências")
        else:
            chosen = self.filedialog.askdirectory(title=key.upper())
        if chosen:
            self.vars[key].set(chosen)
            self.save()

    def pick_multi(self) -> None:
        chosen = self.filedialog.askdirectory(title="Pasta que contém vários casos indexados")
        if chosen:
            self.multi_dir = chosen
            self.save()
            self.say(f"Multicasos: o Chat com IA vai usar todos os casos de {chosen}")

    def run_proc(self, cmd: list[str], env: dict[str, str] | None = None, encoding: str = "utf-8") -> subprocess.Popen:
        self.say("$ " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
        proc = subprocess.Popen(  # noqa: S603
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding=encoding, errors="replace",
            env=env, creationflags=NO_WINDOW,
        )  # fmt: skip

        def pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                self.say(line)

        threading.Thread(target=pump, daemon=True).start()
        return proc

    # ---- ações
    def index(self) -> None:
        iped, origem, destino = (self.vars[k].get().strip() for k in ("iped", "origem", "destino"))
        error = check_index_inputs(iped, origem, destino)
        if error:
            self.messagebox.showwarning(APP_TITLE, error)
            return
        self.save()
        cmd = build_index_command(Path(iped), Path(origem), Path(destino))
        self.index_proc = self.run_proc(cmd, encoding=__import__("locale").getpreferredencoding(False))
        self.btn_index.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.progress.start(12)
        threading.Thread(target=self.wait_index, daemon=True).start()

    def wait_index(self) -> None:
        assert self.index_proc is not None
        code = self.index_proc.wait()
        self.say(f"Indexação terminou (código {code})." if code == 0 else f"Indexação terminou com erro (código {code}).")
        self.root.after(0, self.index_done)

    def index_done(self) -> None:
        self.progress.stop()
        self.btn_index.configure(state="normal")
        self.btn_cancel.configure(state="disabled")
        self.index_proc = None

    def cancel(self) -> None:
        if self.index_proc is not None:
            kill_tree(self.index_proc)
            self.say("Indexação cancelada.")

    def open_case(self) -> None:
        """Abre um caso já indexado: usa o DESTINO ou pede a pasta do caso."""
        typed = self.vars["destino"].get().strip()
        app = find_case_app(Path(typed)) if typed else None
        if app is None:
            chosen = self.filedialog.askdirectory(title="Abrir caso indexado: escolha a pasta do caso")
            if not chosen:
                return
            app = find_case_app(Path(chosen))
            if app is None:
                self.messagebox.showinfo(
                    APP_TITLE, f"Não achei o IPED-SearchApp em:\n{chosen}\n\nEscolha a pasta de saída (-o) do caso indexado."
                )
                return
            self.vars["destino"].set(str(app.parent.parent if app.parent.name.lower() == "iped" else app.parent))
        try:
            launch_detached(app)
        except OSError as exc:
            self.messagebox.showerror(APP_TITLE, f"Não consegui abrir o caso: {exc}")
            return
        self.say(f"Abrindo caso: {app}")

    def find_webui(self) -> bool:
        """Aponta o open-webui(.exe) que o usuário já tem (guardado só como configuração do programa)."""
        found = wl.find_open_webui()
        if found:
            self.say(f"Open WebUI encontrado: {found}")
        chosen = self.filedialog.askopenfilename(
            title="Escolha o open-webui.exe (pasta Scripts do seu Python); cancele para manter o atual"
        )
        if not chosen:
            return bool(found)
        cfg = wl.load_config()
        cfg["openwebui_exe"] = chosen
        wl.save_config(cfg)
        self.say(f"Open WebUI definido: {chosen}")
        return True

    def install_webui(self, then=None) -> None:
        """Instala o Open WebUI (processo filho) e, se der certo, chama `then`."""
        self.btn_chat.configure(state="disabled")
        self.progress.start(12)
        self.say("Instalando o Open WebUI: pode levar vários minutos, a janela continua aberta…")
        proc = self.run_proc(wl.self_command("instalar-webui"), env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))

        def wait() -> None:
            code = proc.wait()
            self.say("Instalação concluída." if code == 0 else f"Instalação falhou (código {code}).")

            def done() -> None:
                self.progress.stop()
                self.btn_chat.configure(state="normal")
                if code == 0 and then:
                    then()

            self.root.after(0, done)

        threading.Thread(target=wait, daemon=True).start()

    def ask_webui(self, start) -> None:
        """Open WebUI ausente: pergunta se instala, se o usuário aponta o dele, ou cancela."""
        answer = self.messagebox.askyesnocancel(
            APP_TITLE,
            "Não encontrei o Open WebUI neste computador.\n\n"
            "SIM: instalar agora (baixa o Python 3.11 se preciso; centenas de MB; vários minutos).\n"
            "NÃO: eu já tenho, quero indicar o open-webui.exe.\n"
            "CANCELAR: não fazer nada.",
        )
        if answer is None:
            return
        if answer:
            self.install_webui(then=start)
        elif self.find_webui():
            start()

    def toggle_chat(self) -> None:
        if self.chat_proc is not None:
            kill_tree(self.chat_proc)
            self.chat_proc = None
            self.btn_chat.configure(text="Chat com IA")
            self.say("Chat com IA encerrado.")
            return
        iped = self.vars["iped"].get().strip()
        if not iped:
            self.messagebox.showwarning(APP_TITLE, "Informe a pasta do IPED.")
            return
        cases = self.multi_dir or self.vars["destino"].get().strip()
        if not cases:
            cases = self.filedialog.askdirectory(title="Chat com IA: pasta do caso indexado (ou pasta com vários casos)")
            if not cases:
                return
            self.multi_dir = cases
        self.save()

        def start() -> None:
            env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            cfg = wl.load_config()
            cfg["modelo"] = self.modelo.get().strip()
            wl.save_config(cfg)
            tools = self.chosen_tools()
            self.chat_proc = self.run_proc(
                chat_command(iped, cases, sem_destrutivas=True, modelo=cfg["modelo"], ferramentas=tools), env=env
            )
            self.say(f"Ferramentas da IA: {len(tools)} ligadas (~{tc.tokens(tools)} tokens de contexto). Botão 'Ferramentas da IA...' para mudar.")
            hint = big_model_hint(cfg["modelo"])
            if hint:
                self.say(hint)
            self.btn_chat.configure(text="Parar chat", state="normal")
            self.say("Subindo Web APIs + MCP + Open WebUI… as instruções de conexão aparecem aqui em instantes.")

        self.btn_chat.configure(state="disabled")
        self.say("Procurando o Open WebUI neste computador…")

        def check() -> None:
            found = wl.running_open_webui(3737) or wl.find_open_webui_command()

            def after() -> None:
                self.btn_chat.configure(state="normal")
                if found:
                    start()
                else:
                    self.ask_webui(start)

            self.root.after(0, after)

        threading.Thread(target=check, daemon=True).start()

    def close(self) -> None:
        for proc in (self.index_proc, self.chat_proc):
            if proc is not None:
                kill_tree(proc)
        self.root.destroy()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def hide_console() -> None:  # pragma: no cover
    """Esconde a janela preta quando o .exe foi aberto com duplo clique (Windows)."""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel, user = ctypes.windll.kernel32, ctypes.windll.user32  # type: ignore[attr-defined]
        hwnd = kernel.GetConsoleWindow()
        pids = (ctypes.c_ulong * 2)()
        if hwnd and kernel.GetConsoleProcessList(pids, 2) <= 1:  # só nós usamos este console
            user.ShowWindow(hwnd, 0)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:  # pragma: no cover
    try:
        app = App()
    except Exception as exc:  # noqa: BLE001  (sem tkinter/display)
        print(f"ERRO: não consegui abrir a janela ({exc!r}). Use: iped-ia-mcp --modo iniciar --root <IPED> --cases-dir <casos>",
              file=sys.stderr)  # fmt: skip
        return 1
    hide_console()
    return app.run()
