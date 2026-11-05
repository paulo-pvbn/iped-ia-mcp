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


def chat_command(iped_root: str, cases_dir: str, *, sem_destrutivas: bool = False) -> list[str]:
    cmd = [*wl.self_command("iniciar"), "--root", iped_root, "--cases-dir", cases_dir]
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

        bar = ttk.Frame(frame)
        bar.grid(row=3, column=0, columnspan=3, sticky="w", pady=10)
        self.btn_index = ttk.Button(bar, text="INDEXAR", command=self.index)
        self.btn_cancel = ttk.Button(bar, text="Cancelar", command=self.cancel, state="disabled")
        self.btn_open = ttk.Button(bar, text="Abrir caso", command=self.open_case)
        self.btn_multi = ttk.Button(bar, text="Multicasos...", command=self.pick_multi)
        self.btn_chat = ttk.Button(bar, text="Chat com IA", command=self.toggle_chat)
        self.btn_webui = ttk.Button(bar, text="Instalar Open WebUI", command=self.install_webui)
        for btn in (self.btn_index, self.btn_cancel, self.btn_open, self.btn_multi, self.btn_chat, self.btn_webui):
            btn.pack(side="left", padx=4)
        self.destrutivas = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Permitir remover marcadores", variable=self.destrutivas).pack(side="left", padx=12)

        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.grid(row=4, column=0, columnspan=3, sticky="ew")
        self.log = scrolledtext.ScrolledText(frame, height=24, state="disabled", wrap="word")
        self.log.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        frame.rowconfigure(5, weight=1)

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(150, self.drain)
        self.say("Pronto. Escolha a pasta do IPED, a ORIGEM e o DESTINO e clique em INDEXAR; "
                 "ou aponte o DESTINO para um caso já indexado e clique em Chat com IA.")  # fmt: skip

    @staticmethod
    def _version() -> str:
        try:
            sys.path.insert(0, str(wl.ROOT / "scripts"))
            import entry  # type: ignore

            return entry.VERSION
        except Exception:  # noqa: BLE001
            return ""

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

    def save(self) -> None:
        """Caminhos pessoais não são gravados em disco (cada usuário informa os seus)."""

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

    def install_webui(self) -> None:
        if wl.find_open_webui():
            self.messagebox.showinfo(APP_TITLE, "O Open WebUI já está instalado. Use 'Chat com IA'.")
            return
        if not self.messagebox.askyesno(
            APP_TITLE, "Instalar o Open WebUI agora?\n\nPrecisa de Python 3.11 e internet; baixa centenas de MB e leva vários minutos."
        ):
            return
        self.btn_webui.configure(state="disabled")
        self.progress.start(12)
        proc = self.run_proc(wl.self_command("instalar-webui"), env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))

        def wait() -> None:
            code = proc.wait()
            self.root.after(0, lambda: (self.progress.stop(), self.btn_webui.configure(state="normal")))
            self.say("Instalação concluída." if code == 0 else f"Instalação falhou (código {code}).")

        threading.Thread(target=wait, daemon=True).start()

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
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        self.chat_proc = self.run_proc(chat_command(iped, cases, sem_destrutivas=not self.destrutivas.get()), env=env)
        self.btn_chat.configure(text="Parar chat")
        self.say("Subindo Web APIs + MCP + Open WebUI… as instruções de conexão aparecem aqui em instantes.")

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
