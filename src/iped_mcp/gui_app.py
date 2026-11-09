"""Janela do IPED-IA MCP (v1): assistente em etapas, tema escuro, menus suspensos.

Toda decisão (etapas, validação, comandos) está em `wizard`; aqui só se desenha e se dispara processos.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from iped_mcp import deps, gui, progress, sysmon, ui_theme, widgets
from iped_mcp import installers as ins
from iped_mcp import tuning
from iped_mcp import hardware as hw
from iped_mcp import tools_catalog as tc
from iped_mcp import webui_launcher as wl
from iped_mcp import wizard as wz

APP_TITLE = "IPED-IA"
P = ui_theme.PALETTE
PRESET_ESSENTIAL = f"Essenciais ({len(tc.DEFAULT)} ferramentas, recomendado)"
PRESET_ALL = f"Todas ({len(tc.NAMES)} ferramentas)"
PRESET_CUSTOM = "Personalizado…"


class Donut:  # pragma: no cover - desenho (precisa de display)
    """Mini rosca com o percentual no centro; a cor muda por faixa de uso (verde, âmbar, vermelho)."""

    SIZE, THICK = 52, 6

    def __init__(self, parent, tk, fonts: dict, label: str) -> None:
        self.tk = tk
        self.box = tk.Frame(parent, bg=P["side"])
        self.cv = tk.Canvas(self.box, width=self.SIZE, height=self.SIZE, bg=P["side"], highlightthickness=0)
        self.cv.pack()
        self.label = tk.Label(self.box, text=label, fg=P["text"], bg=P["side"], font=fonts["small"])
        self.label.pack(pady=(2, 0))
        self.detail = tk.Label(self.box, text=" ", fg=P["muted"], bg=P["side"], font=fonts["small"])
        self.detail.pack()
        self.fonts = fonts
        self.update_value(None, "")

    def update_value(self, percent: float | None, detail: str) -> None:
        cv, pad = self.cv, self.THICK // 2 + 1
        box = (pad, pad, self.SIZE - pad, self.SIZE - pad)
        cv.delete("all")
        cv.create_oval(*box, outline=P["border"], width=self.THICK)
        if percent is not None and percent > 0:
            color = P[{"ok": "ok", "warn": "warn", "danger": "danger"}[sysmon.level(percent)]]
            cv.create_arc(*box, start=90, extent=-min(359.9, 3.6 * percent), style="arc", outline=color, width=self.THICK)
        text = "—" if percent is None else f"{percent:.0f}%"
        cv.create_text(self.SIZE // 2, self.SIZE // 2, text=text, fill=P["text"], font=self.fonts["small"])
        self.detail.configure(text=detail or " ")


class App:  # pragma: no cover - interface gráfica (não roda sem display)
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self.tk, self.ttk, self.filedialog, self.messagebox = tk, ttk, filedialog, messagebox
        self.root = tk.Tk()
        self.root.withdraw()  # a janela principal só aparece quando a tela de abertura termina
        self.splash = widgets.Splash(self.root, tk, APP_TITLE, self._version())
        self.splash.step(0.2, "Carregando componentes…")
        self.root.title(f"{APP_TITLE} {self._version()}")
        self.root.geometry("1180x760")
        self.root.minsize(980, 660)
        self.f = ui_theme.apply(self.root)

        self.state = wz.State()
        self.i = 0
        self.vars = {k: tk.StringVar(value="") for k in ("iped", "origem", "destino", "extra", "profile", "modelo")}
        self.vars["modelo"].set(wl.load_config().get("modelo", ""))
        self.task = tk.StringVar(value=wz.NOVO)
        self.index_proc: subprocess.Popen | None = None
        self.chat_proc: subprocess.Popen | None = None
        self.lines: queue.Queue[str] = queue.Queue()
        self.log_buffer: list[str] = []
        self.log_widget = None
        self.wraps: list = []
        self.models: list[str] = []
        self.hw_cache: tuple | None = None
        self.status_chips: dict[str, tuple] = {}
        self.monitor = sysmon.Sampler()
        self.gpu_reading = sysmon.Gauge(None)
        self.run_state = ("idle", "")  # (idle|running|done|failed, texto)
        self.run_widgets: dict = {}
        self.closing = False
        self.env_checked = False  # o ambiente (Ollama, Open WebUI) já foi lido?
        self.tracker = progress.Tracker()
        self.log_open = False
        self.splash_since = 0.0

        self.splash.step(0.45, "Montando a interface…")
        self._build_shell()
        self.root.bind("<Return>", self._on_return)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(150, self.drain)
        self.render()
        self.splash.step(0.7, "Lendo o ambiente (IPED, Ollama, Open WebUI)…")

    def _finish_splash(self) -> None:
        """Fecha a abertura quando o ambiente foi lido (ou depois de 5 s, o que vier primeiro) e mostra a janela."""
        import time

        if self.splash is None:
            return
        waited = time.monotonic() - self.splash_since
        if not (self.env_checked and waited > 0.6) and waited < 5:
            self.root.after(100, self._finish_splash)
            return
        self.splash.step(1.0, "Pronto")
        self.splash.close()
        self.splash = None
        self.root.deiconify()
        self.root.lift()

    def _tick_context(self) -> None:
        """Uso do contexto da conversa: a guarda grava só números; aqui só se lê e se desenha."""
        if self.closing:
            return
        bar = self.run_widgets.get("context") if self.run_widgets else None
        if bar is not None and self.chat_proc is not None:
            try:
                bar.update_use(progress.context_use(progress.read_stats(wl.STATS_PATH)))
            except Exception:  # noqa: BLE001  (widget destruído na troca de etapa)
                pass
        self.root.after(2000, self._tick_context)

    def _start_background(self) -> None:
        """Leituras do ambiente (Ollama, Open WebUI); só começam com o laço da janela rodando."""
        threading.Thread(target=self.load_models, daemon=True).start()
        threading.Thread(target=self.refresh_status, daemon=True).start()  # só lê o ambiente (nada de widgets)
        threading.Thread(target=self._poll_gpu, daemon=True).start()
        self._tick_monitor()
        self._tick_context()

    def _poll_gpu(self) -> None:
        """O nvidia-smi demora alguns décimos de segundo: roda fora da janela e só guarda o resultado."""
        import time

        while not self.closing:
            self.gpu_reading = self.monitor.read_gpu()
            time.sleep(2)

    def _tick_monitor(self) -> None:
        """Atualiza as roscas (thread da janela); CPU e memória são leituras baratas."""
        if self.closing:
            return
        cpu, ram = self.monitor.read_cpu_ram()
        for key, gauge in (("cpu", cpu), ("gpu", self.gpu_reading), ("ram", ram)):
            self.gauges[key].update_value(gauge.percent, gauge.detail)
        self.root.after(2000, self._tick_monitor)

    @staticmethod
    def _version() -> str:
        from iped_mcp.version import VERSION

        return VERSION

    # ------------------------------------------------------------------------------------------ estrutura
    def _build_shell(self) -> None:
        tk, ttk = self.tk, self.ttk
        root = self.root
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)

        header = ttk.Frame(root, padding=(24, 8))
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(header, text=APP_TITLE, font=self.f["brand"]).pack(side="left")
        ttk.Label(header, text=f"  MCP · v{self._version()}", style="Muted.TLabel").pack(side="left", pady=(4, 0))
        ttk.Frame(header, width=24).pack(side="left")
        self._menu(header, "Caso", [(t, lambda c=c: self.jump(c)) for t, c in wz.TASKS.items()])
        self._menu(header, "IPED", [("Dependências…", self.show_dependencies), ("Instalar o que falta (assistido)…", lambda: self.show_dependencies(auto=True))])
        self._menu(header, "IA", [("Ferramentas da IA…", self.pick_tools), ("Desempenho e contexto…", self.show_hardware)])
        self._menu(header, "Ajuda", [("Sobre o programa", self.about)])
        ttk.Separator(root).grid(row=0, column=0, columnspan=2, sticky="sew")

        self.side = ttk.Frame(root, style="Side.TFrame", padding=(16, 24), width=290)
        self.side.grid(row=1, column=0, sticky="ns")
        self.side.grid_propagate(False)
        self.steps_box = ttk.Frame(self.side, style="Side.TFrame")
        self.steps_box.pack(fill="x")
        chips = ttk.Frame(self.side, style="Side.TFrame")
        chips.pack(side="bottom", fill="x")
        ttk.Label(chips, text="AMBIENTE", style="SideMuted.TLabel").pack(anchor="w", pady=(0, 6))
        for key, label in (("iped", "IPED"), ("ollama", "Ollama"), ("webui", "Open WebUI")):
            row = ttk.Frame(chips, style="Side.TFrame")
            row.pack(fill="x", pady=2)
            dot = tk.Label(row, text="●", fg=P["muted"], bg=P["side"], font=self.f["small"])
            dot.pack(side="left")
            ttk.Label(row, text=f" {label}", style="Side.TLabel").pack(side="left")
            note = ttk.Label(row, text="verificando…", style="SideMuted.TLabel")
            note.pack(side="right")
            self.status_chips[key] = (dot, note)
        link = tk.Label(chips, text="Gerenciar dependências…", fg=P["accent_text"], bg=P["side"], font=self.f["small"], cursor="hand2")
        link.pack(anchor="w", pady=(8, 0))
        link.bind("<Button-1>", lambda _e: self.show_dependencies())
        gauges = tk.Frame(chips, bg=P["side"])
        gauges.pack(fill="x", pady=(12, 0))
        self.gauges: dict[str, Donut] = {}
        for col, (key, label) in enumerate((("cpu", "CPU"), ("gpu", "GPU"), ("ram", "Memória"))):
            gauges.columnconfigure(col, weight=1, uniform="g")
            self.gauges[key] = Donut(gauges, tk, self.f, label)
            self.gauges[key].box.grid(row=0, column=col)

        main = ttk.Frame(root, padding=(40, 32, 40, 24))
        main.grid(row=1, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(4, weight=1)  # espaçador: o erro fica logo abaixo do formulário
        self.main = main
        self.kicker = ttk.Label(main, text="", style="Muted.TLabel", font=self.f["small"])
        self.kicker.grid(row=0, column=0, sticky="w")
        head = ttk.Frame(main)
        head.grid(row=1, column=0, sticky="ew", pady=(4, 16))
        self.title = ttk.Label(head, text="", style="Title.TLabel")
        self.title.pack(anchor="w")
        self.subtitle = ttk.Label(head, text="", style="Muted.TLabel", justify="left")
        self.subtitle.pack(anchor="w", pady=(4, 0))
        self.wraps.append(self.subtitle)
        self.form = ttk.Frame(main)
        self.form.grid(row=2, column=0, sticky="nsew")
        self.form.columnconfigure(0, weight=1)
        self.error = ttk.Label(main, text="", style="Danger.TLabel", justify="left")
        self.error.grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.wraps.append(self.error)
        foot = ttk.Frame(main)
        foot.grid(row=5, column=0, sticky="ew", pady=(16, 0))
        self.btn_back = ttk.Button(foot, text="‹  Voltar", style="Ghost.TButton", command=self.back)
        self.btn_back.pack(side="left")
        self.btn_next = ttk.Button(foot, text="Avançar  ›", style="Primary.TButton", command=self.next)
        self.btn_next.pack(side="right")
        main.bind("<Configure>", self._resize)

    def _menu(self, parent, text: str, items: list) -> None:
        ttk, tk = self.ttk, self.tk
        mb = ttk.Menubutton(parent, text=text, style="Menu.TMenubutton")
        menu = tk.Menu(mb, tearoff=False, bd=0, activeborderwidth=0)
        for label, cmd in items:
            menu.add_command(label=f"  {label}  ", command=cmd)
        mb["menu"] = menu
        mb.configure(width=len(text) + 2)
        mb.pack(side="left", padx=(8, 0))

    def _resize(self, event) -> None:
        width = max(300, event.width - 80)
        for label in self.wraps:
            try:
                label.configure(wraplength=width)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------------------------------ navegação
    def collect(self) -> None:
        s, v = self.state, self.vars
        s.task = self.task.get()
        s.iped, s.origem, s.destino = (v[k].get().strip() for k in ("iped", "origem", "destino"))
        s.extra, s.modelo = v["extra"].get().strip(), v["modelo"].get().strip()
        prof = v["profile"].get().strip()
        s.profile = "" if prof.startswith("Padrão") else prof

    def render(self) -> None:
        self.collect()
        steps = self.state.steps()
        self.i = min(self.i, len(steps) - 1)
        step = steps[self.i]
        self.kicker.configure(text=f"ETAPA {self.i + 1} DE {len(steps)}")
        self.title.configure(text=step.title)
        self.error.configure(text="")
        for child in self.form.winfo_children():
            child.destroy()
        self.run_widgets = {}
        self._draw_steps(steps)
        getattr(self, f"step_{step.key}")()
        last = step.key == "executar"
        self.main.rowconfigure(2, weight=1 if last else 0)
        self.main.rowconfigure(4, weight=0 if last else 1)
        self.btn_back.configure(state="normal" if self.i > 0 and not self._busy() else "disabled")
        if self.i == 0:
            self.btn_back.pack_forget()
        elif not self.btn_back.winfo_ismapped():
            self.btn_back.pack(side="left")
        if last:
            self.btn_next.configure(text="Recomeçar", style="Ghost.TButton", state="normal" if not self._busy() else "disabled")
        else:
            self.btn_next.configure(text="Ir para a execução  ›" if step.key == "revisar" else "Avançar  ›", style="Primary.TButton", state="normal")

    def _draw_steps(self, steps) -> None:
        tk, ttk = self.tk, self.ttk
        for child in self.steps_box.winfo_children():
            child.destroy()
        for n, st in enumerate(steps):
            current, done = n == self.i, n < self.i
            row = tk.Frame(self.steps_box, bg=P["surface"] if current else P["side"], cursor="hand2" if done else "")
            row.pack(fill="x", pady=2)
            tk.Frame(row, width=3, bg=P["accent"] if current else (P["surface"] if current else P["side"])).pack(side="left", fill="y")
            cv = tk.Canvas(row, width=32, height=32, bg=row["bg"], highlightthickness=0)
            cv.pack(side="left", padx=(10, 10), pady=8)
            if done:
                cv.create_oval(4, 4, 28, 28, fill=P["ok"], outline=P["ok"])
                cv.create_text(16, 16, text="✓", fill=P["bg"], font=self.f["bold"])
            elif current:
                cv.create_oval(4, 4, 28, 28, fill=P["accent"], outline=P["accent"])
                cv.create_text(16, 16, text=str(n + 1), fill=P["on_accent"], font=self.f["bold"])
            else:
                cv.create_oval(4, 4, 28, 28, fill=P["side"], outline=P["border"], width=2)
                cv.create_text(16, 16, text=str(n + 1), fill=P["muted"], font=self.f["bold"])
            text = tk.Frame(row, bg=row["bg"])
            text.pack(side="left", fill="x", pady=6)
            tk.Label(text, text=st.title, bg=row["bg"], fg=P["text"] if (current or done) else P["muted"], font=self.f["bold"], anchor="w").pack(anchor="w")
            tk.Label(text, text=st.subtitle, bg=row["bg"], fg=P["muted"], font=self.f["small"], anchor="w").pack(anchor="w")
            if done and not self._busy():
                for w in (row, cv, text, *text.winfo_children()):
                    w.bind("<Button-1>", lambda _e, k=n: self.goto(k))

    def goto(self, index: int) -> None:
        self.i = index
        self.render()

    def jump(self, task: str) -> None:
        if self._busy():
            self.messagebox.showinfo(APP_TITLE, "Há uma tarefa em andamento. Pare-a antes de começar outra.")
            return
        self.task.set(task)
        self.i = 1
        self.render()

    def back(self) -> None:
        if self.i > 0:
            self.i -= 1
            self.render()

    def next(self) -> None:
        self.collect()
        step = self.state.steps()[self.i]
        if step.key == "executar":
            if self._busy():
                return
            self.i = 0
            self.run_state = ("idle", "")
            self.render()
            return
        err = wz.validate(step.key, self.state)
        if err:
            self.error.configure(text="⚠  " + err)
            return
        self.i += 1
        if step.key == "iped":
            threading.Thread(target=self.refresh_status, args=(self.state.iped,), daemon=True).start()
        self.render()

    def _on_return(self, event) -> None:
        if isinstance(event.widget, self.tk.Text) or str(self.btn_next.cget("state")) == "disabled":
            return
        if self.state.steps()[self.i].key not in ("executar",):
            self.next()

    # ------------------------------------------------------------------------------------------ componentes
    def field(self, parent, title: str, var, *, hint: str = "", browse=None, live=None, row: int = 0):
        ttk = self.ttk
        box = ttk.Frame(parent)
        box.grid(row=row, column=0, sticky="ew", pady=(0, 16))
        box.columnconfigure(0, weight=1)
        ttk.Label(box, text=title, font=self.f["bold"]).grid(row=0, column=0, sticky="w", pady=(0, 6))
        entry = ttk.Entry(box, textvariable=var)
        entry.grid(row=1, column=0, sticky="ew")
        for col, (label, cmd) in enumerate(browse or [], start=1):
            ttk.Button(box, text=label, command=cmd).grid(row=1, column=col, padx=(8, 0))
        note = ttk.Label(box, text=hint, style="Muted.TLabel", justify="left")
        note.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self.wraps.append(note)
        if live:

            def update(*_a) -> None:
                self.collect()
                ok = live()
                note.configure(text=ok if ok else hint, style="Ok.TLabel" if ok else "Muted.TLabel")

            var.trace_add("write", update)
            update()
        return entry

    def ask_dir(self, var, title: str) -> None:
        chosen = self.filedialog.askdirectory(title=title)
        if chosen:
            var.set(chosen)

    # ------------------------------------------------------------------------------------------ etapas
    def step_tarefa(self) -> None:
        tk, ttk = self.tk, self.ttk
        self.subtitle.configure(text="Escolha o que deseja fazer. Você pode trocar depois, a qualquer momento, pelo menu Caso.")
        cards: dict[str, tk.Frame] = {}

        def paint(*_a) -> None:
            for code, card in cards.items():
                card.configure(highlightbackground=P["accent"] if self.task.get() == code else P["border"])

        for n, (label, code) in enumerate(wz.TASKS.items()):
            card = tk.Frame(self.form, bg=P["surface"], highlightthickness=2, highlightbackground=P["border"], cursor="hand2")
            card.grid(row=n, column=0, sticky="ew", pady=6)
            cards[code] = card
            inner = ttk.Frame(card, style="Card.TFrame", padding=(16, 12))
            inner.pack(fill="x")
            ttk.Radiobutton(inner, text=label, value=code, variable=self.task, style="TRadiobutton", command=paint).pack(anchor="w")
            desc = ttk.Label(inner, text=wz.TASK_HELP[code], style="CardMuted.TLabel", justify="left")
            desc.pack(anchor="w", padx=(24, 0), pady=(2, 0))
            self.wraps.append(desc)
            for w in (card, inner, desc):
                w.bind("<Button-1>", lambda _e, c=code: (self.task.set(c), paint()))
        paint()

    def step_iped(self) -> None:
        self.subtitle.configure(text="A pasta onde o IPED está instalado, a que contém o iped.jar. Cada pessoa informa a sua: nada é preenchido nem guardado.")
        self.field(
            self.form, "Pasta do IPED", self.vars["iped"], hint="Exemplo: C:\\IPED\\iped-4.3.1",
            browse=[("Procurar…", lambda: self.ask_dir(self.vars["iped"], "Pasta do IPED (a que contém o iped.jar)"))],
            live=lambda: "✓  iped.jar encontrado" if not wz.validate("iped", self.state) else "",
        )  # fmt: skip

    def step_origem(self) -> None:
        self.subtitle.configure(text="A imagem forense (E01, dd, UFDR…) ou a pasta com as evidências a processar.")

        def pick_file() -> None:
            chosen = self.filedialog.askopenfilename(title="Arquivo de imagem ou evidência")
            if chosen:
                self.vars["origem"].set(chosen)

        self.field(
            self.form, "Evidências (ORIGEM)", self.vars["origem"], hint="Arquivo de imagem ou pasta.",
            browse=[("Arquivo…", pick_file), ("Pasta…", lambda: self.ask_dir(self.vars["origem"], "Pasta com as evidências"))],
            live=lambda: "✓  Encontrado" if not wz.validate("origem", self.state) else "",
        )  # fmt: skip

    def step_destino(self) -> None:
        title, hint = wz.destino_label(self.state.task)
        self.subtitle.configure(text=hint)
        self.field(
            self.form, title, self.vars["destino"], hint="Escolha a pasta.",
            browse=[("Procurar…", lambda: self.ask_dir(self.vars["destino"], title))],
            live=lambda: "✓  Pasta válida" if self.vars["destino"].get().strip() and not wz.validate("destino", self.state) else "",
        )  # fmt: skip

    def step_proc(self) -> None:
        ttk = self.ttk
        self.subtitle.configure(text="O perfil define o que o IPED processa. Em dúvida, use o padrão do IPED.")
        profiles = wz.list_profiles(self.vars["iped"].get().strip())
        values = ["Padrão do IPED", *profiles]
        if not self.vars["profile"].get():
            self.vars["profile"].set(values[0])
        box = ttk.Frame(self.form)
        box.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        box.columnconfigure(0, weight=1)
        ttk.Label(box, text="Perfil de processamento", font=self.f["bold"]).grid(row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Combobox(box, textvariable=self.vars["profile"], values=values, state="readonly").grid(row=1, column=0, sticky="ew")
        note = ttk.Label(box, text=f"{len(profiles)} perfil(is) encontrado(s) na instalação do IPED." if profiles else "Nenhum perfil listado: será usado o padrão do IPED.", style="Muted.TLabel")
        note.grid(row=2, column=0, sticky="w", pady=(6, 0))
        adv = ttk.Frame(self.form)
        adv.grid(row=1, column=0, sticky="ew")
        adv.columnconfigure(0, weight=1)
        body = ttk.Frame(adv)
        shown = self.tk.BooleanVar(value=bool(self.vars["extra"].get()))
        toggle = ttk.Button(adv, style="Ghost.TButton")
        toggle.grid(row=0, column=0, sticky="w")

        def draw() -> None:
            toggle.configure(text=("▾  Opções avançadas" if shown.get() else "▸  Opções avançadas"))
            if shown.get():
                body.grid(row=1, column=0, sticky="ew", padx=(16, 0))
            else:
                body.grid_remove()

        toggle.configure(command=lambda: (shown.set(not shown.get()), draw()))
        ttk.Label(body, text="Argumentos extras para o IPED", font=self.f["bold"]).grid(row=0, column=0, sticky="w", pady=(8, 6))
        ttk.Entry(body, textvariable=self.vars["extra"]).grid(row=1, column=0, sticky="ew")
        ttk.Label(body, text="Uso avançado. Não repita -d, -o nem -profile: o assistente já os inclui.", style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=(6, 0))
        body.columnconfigure(0, weight=1)
        draw()

    def step_ia(self) -> None:
        ttk = self.ttk
        self.subtitle.configure(text="Modelo local (Ollama) e ferramentas que a IA pode usar no caso. Nada sai do seu computador.")
        box = ttk.Frame(self.form)
        box.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        box.columnconfigure(0, weight=1)
        ttk.Label(box, text="Modelo de IA (Ollama)", font=self.f["bold"]).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.cmb_modelo = ttk.Combobox(box, textvariable=self.vars["modelo"], values=self.models)
        self.cmb_modelo.grid(row=1, column=0, sticky="ew")
        ttk.Button(box, text="Hardware…", command=self.show_hardware).grid(row=1, column=1, padx=(8, 0))
        self.model_note = ttk.Label(box, text="", style="Muted.TLabel", justify="left")
        self.model_note.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.wraps.append(self.model_note)

        def hint(*_a) -> None:
            warn = gui.big_model_hint(self.vars["modelo"].get())
            if warn:
                self.model_note.configure(text="⚠  " + warn, style="Warn.TLabel")
            else:
                self.model_note.configure(text=self._model_note_text(), style="Muted.TLabel")

        self.vars["modelo"].trace_add("write", hint)
        hint()
        if not self.models:
            threading.Thread(target=self.load_models, daemon=True).start()
        threading.Thread(target=self.recommend_model, daemon=True).start()

        tools = ttk.Frame(self.form)
        tools.grid(row=1, column=0, sticky="ew")
        tools.columnconfigure(0, weight=1)
        ttk.Label(tools, text="Ferramentas da IA", font=self.f["bold"]).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.preset = self.tk.StringVar(value=self._preset_now())
        cmb = ttk.Combobox(tools, textvariable=self.preset, values=[PRESET_ESSENTIAL, PRESET_ALL, PRESET_CUSTOM], state="readonly")
        cmb.grid(row=1, column=0, sticky="ew")
        self.tools_note = ttk.Label(tools, text="", style="Muted.TLabel", justify="left")
        self.tools_note.grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.wraps.append(self.tools_note)
        self._tools_summary()
        cmb.bind("<<ComboboxSelected>>", self._preset_changed)

    def _model_note_text(self) -> str:
        return "Modelos de 4B a 8B respondem bem mais rápido em GPUs de 8 GB." if not self.models else f"{len(self.models)} modelo(s) disponível(is) no Ollama."

    def _preset_now(self) -> str:
        saved = wl.load_config().get("ferramentas", "")
        if not saved or set(tc.normalize(saved)) == set(tc.DEFAULT):
            return PRESET_ESSENTIAL
        return PRESET_ALL if set(tc.normalize(saved)) == set(tc.NAMES) else PRESET_CUSTOM

    def _tools_summary(self) -> None:
        tools = self.chosen_tools()
        tok = tc.tokens(tools)
        warn = "  ⚠ muitas ferramentas apertam o contexto do modelo" if tok > tc.TOKEN_WARN else ""
        self.tools_note.configure(text=f"{len(tools)} ligadas · cerca de {tok} tokens de contexto.{warn}", style="Warn.TLabel" if warn else "Muted.TLabel")

    def _preset_changed(self, _e=None) -> None:
        choice = self.preset.get()
        cfg = wl.load_config()
        if choice == PRESET_ESSENTIAL:
            cfg.pop("ferramentas", None)
        elif choice == PRESET_ALL:
            cfg["ferramentas"] = ",".join(tc.NAMES)
        else:
            self.pick_tools()
            self.preset.set(self._preset_now())
            self._tools_summary()
            return
        wl.save_config(cfg)
        self._tools_summary()

    def step_revisar(self) -> None:
        tk, ttk = self.tk, self.ttk
        self.subtitle.configure(text="Confira os dados. Nada será executado antes da próxima etapa.")
        card = ttk.Frame(self.form, style="Card.TFrame", padding=(20, 16))
        card.grid(row=0, column=0, sticky="ew")
        card.columnconfigure(1, weight=1)
        for n, (label, value) in enumerate(wz.review(self.state)):
            ttk.Label(card, text=label, style="CardMuted.TLabel").grid(row=n, column=0, sticky="nw", pady=4, padx=(0, 24))
            val = ttk.Label(card, text=value or "—", style="Card.TLabel", justify="left")
            val.grid(row=n, column=1, sticky="w", pady=4)
            self.wraps.append(val)
        if self.state.task == wz.NOVO:
            cmd = " ".join(f'"{c}"' if " " in c else c for c in wz.build_command(self.state))
            ttk.Label(self.form, text="Comando de indexação", font=self.f["bold"]).grid(row=1, column=0, sticky="w", pady=(20, 6))
            box = tk.Text(self.form, height=4, wrap="word", bg=P["surface"], fg=P["text"], relief="flat", font=self.f["mono"], padx=12, pady=10, highlightthickness=1, highlightbackground=P["border"])
            box.insert("1.0", cmd)
            box.configure(state="disabled")
            box.grid(row=2, column=0, sticky="ew")
            ttk.Label(self.form, text="Este é o comando que será executado. As opções do IPED ainda precisam ser validadas na sua instalação.", style="Muted.TLabel").grid(row=3, column=0, sticky="w", pady=(6, 0))

    def step_executar(self) -> None:
        tk, ttk = self.tk, self.ttk
        task = self.state.task
        self.subtitle.configure(text={wz.NOVO: "Indexe o caso e depois abra no IPED ou converse com a IA.", wz.ABRIR: "Abra o caso no IPED.",
                                      wz.CHAT: "Suba o chat local com a IA sobre o caso.", wz.MULTI: "Suba o chat local com a IA sobre todos os casos."}[task])  # fmt: skip
        top = ttk.Frame(self.form)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        dot = tk.Label(top, text="●", fg=P["muted"], bg=P["bg"], font=self.f["h2"])
        dot.grid(row=0, column=0, padx=(0, 8))
        status = ttk.Label(top, text="", font=self.f["h2"])
        status.grid(row=0, column=1, sticky="w")
        bar = ttk.Progressbar(self.form, mode="indeterminate")
        bar.grid(row=1, column=0, sticky="ew", pady=(12, 12))
        stage = widgets.StageBar(self.form, tk)
        stage.cv.grid(row=1, column=0, sticky="ew", pady=(8, 4))
        stage.set(self.tracker.done)
        context = widgets.ContextBar(self.form, tk)
        context.box.grid(row=2, column=0, sticky="w", pady=(0, 4))
        actions = ttk.Frame(self.form)
        actions.grid(row=3, column=0, sticky="w", pady=(12, 0))
        toggle = ttk.Button(self.form, text="", style="Ghost.TButton")
        toggle.grid(row=4, column=0, sticky="w", pady=(16, 6))
        log = tk.Text(self.form, height=12, wrap="word", bg=P["side"], fg=P["muted"], relief="flat", font=self.f["mono"], padx=12, pady=10, highlightthickness=1, highlightbackground=P["border"], state="disabled")
        log.grid(row=5, column=0, sticky="nsew")
        self.form.rowconfigure(5, weight=1)
        self.log_widget = log
        log.configure(state="normal")
        log.insert("end", "\n".join(self.log_buffer[-400:]) + ("\n" if self.log_buffer else ""))
        log.see("end")
        log.configure(state="disabled")

        def show_log() -> None:
            if self.log_open:
                log.grid()
                toggle.configure(text="▾  Ocultar detalhes técnicos")
            else:
                log.grid_remove()
                toggle.configure(text="▸  Mostrar detalhes técnicos")

        def flip() -> None:
            self.log_open = not self.log_open
            show_log()

        toggle.configure(command=flip)
        show_log()
        self.run_widgets = {"dot": dot, "status": status, "bar": bar, "stage": stage, "context": context, "actions": actions}
        self._paint_run()

    # ---- execução
    def _busy(self) -> bool:
        return self.index_proc is not None or self.chat_proc is not None

    def _paint_run(self) -> None:
        w = self.run_widgets
        if not w:
            return
        ttk = self.ttk
        kind, text = self.run_state
        colors = {"idle": P["muted"], "running": P["accent_text"], "done": P["ok"], "failed": P["danger"]}
        idle = {wz.NOVO: "Pronto para indexar.", wz.ABRIR: "Pronto para abrir.", wz.CHAT: "Pronto para iniciar o chat.", wz.MULTI: "Pronto para iniciar o chat."}[self.state.task]
        w["dot"].configure(fg=colors[kind])
        w["status"].configure(text=text or idle)
        for child in w["actions"].winfo_children():
            child.destroy()
        chat_view = self.chat_proc is not None
        w["stage"].cv.grid() if chat_view else w["stage"].cv.grid_remove()
        w["context"].box.grid() if chat_view else w["context"].box.grid_remove()
        if kind == "running" and not chat_view:
            w["bar"].grid()
            w["bar"].start(12)
        else:
            w["bar"].stop()
            w["bar"].grid_remove()
        task, a = self.state.task, w["actions"]
        chat_on = self.chat_proc is not None
        if task == wz.NOVO:
            if self.index_proc is not None:
                ttk.Button(a, text="Cancelar indexação", style="Danger.TButton", command=self.cancel).pack(side="left")
            elif kind == "done":
                self._result_buttons(a, primary="open")
            else:
                ttk.Button(a, text="Iniciar indexação" if kind != "failed" else "Tentar de novo", style="Primary.TButton", command=self.index).pack(side="left")
        elif task == wz.ABRIR:
            ttk.Button(a, text="Abrir no IPED", style="Primary.TButton", command=self.open_case).pack(side="left")
        else:
            label = "Parar o chat" if chat_on else "Iniciar o chat com a IA"
            ttk.Button(a, text=label, style="Danger.TButton" if chat_on else "Primary.TButton", command=self.toggle_chat).pack(side="left")
        self.btn_back.configure(state="normal" if self.i > 0 and not self._busy() else "disabled")
        self.btn_next.configure(state="normal" if not self._busy() else "disabled")

    def _result_buttons(self, parent, primary: str) -> None:
        ttk = self.ttk
        ttk.Button(parent, text="Abrir no IPED", style="Primary.TButton" if primary == "open" else "TButton", command=self.open_case).pack(side="left")
        chat_on = self.chat_proc is not None
        ttk.Button(parent, text="Parar o chat" if chat_on else "Conversar com a IA", command=self.toggle_chat).pack(side="left", padx=(8, 0))

    def run_proc(self, cmd: list[str], env: dict[str, str] | None = None, encoding: str = "utf-8") -> subprocess.Popen:
        self.say("$ " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
        proc = subprocess.Popen(  # noqa: S603
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding=encoding, errors="replace",
            env=env, creationflags=gui.NO_WINDOW,
        )  # fmt: skip

        def pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                self.say(line)

        threading.Thread(target=pump, daemon=True).start()
        return proc

    def index(self) -> None:
        self.collect()
        err = wz.validate("iped", self.state) or wz.validate("origem", self.state) or wz.validate("destino", self.state)
        if err:
            self.messagebox.showwarning(APP_TITLE, err)
            return
        self.index_proc = self.run_proc(wz.build_command(self.state), encoding=__import__("locale").getpreferredencoding(False))
        self.run_state = ("running", "Indexando… isso pode levar bastante tempo.")
        self._paint_run()
        threading.Thread(target=self.wait_index, daemon=True).start()

    def wait_index(self) -> None:
        assert self.index_proc is not None
        proc = self.index_proc
        code = proc.wait()
        cancelled = self.run_state[0] == "cancel"

        def done() -> None:
            self.index_proc = None
            if cancelled:
                self.run_state = ("idle", "Indexação cancelada.")
            elif code == 0:
                self.run_state = ("done", "Indexação concluída.")
            else:
                self.run_state = ("failed", f"A indexação terminou com erro (código {code}). Veja os detalhes abaixo.")
            self.say(f"Indexação terminou (código {code}).")
            self._paint_run()

        self.root.after(0, done)

    def cancel(self) -> None:
        if self.index_proc is not None:
            self.run_state = ("cancel", "Cancelando…")
            gui.kill_tree(self.index_proc)

    def open_case(self) -> None:
        self.collect()
        app = gui.find_case_app(Path(self.state.destino)) if self.state.destino else None
        if app is None:
            self.messagebox.showinfo(APP_TITLE, f"Não achei o IPED-SearchApp em:\n{self.state.destino}")
            return
        try:
            gui.launch_detached(app)
        except OSError as exc:
            self.messagebox.showerror(APP_TITLE, f"Não consegui abrir o caso: {exc}")
            return
        self.say(f"Abrindo caso: {app}")

    # ---- chat
    def toggle_chat(self) -> None:
        if self.chat_proc is not None:
            gui.kill_tree(self.chat_proc)
            self.chat_proc = None
            self.run_state = ("idle", "Chat encerrado.")
            self.say("Chat com IA encerrado.")
            self._paint_run()
            return
        self.collect()
        err = wz.validate("iped", self.state) or wz.validate("destino", self.state) or (wz.validate("ia", self.state) if self.state.task != wz.ABRIR else "")
        if err:
            self.messagebox.showwarning(APP_TITLE, err or "")
            return

        def start() -> None:
            env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            cfg = wl.load_config()
            cfg["modelo"] = self.state.modelo
            wl.save_config(cfg)
            tools = self.chosen_tools()
            cases = self.state.destino
            self.chat_proc = self.run_proc(
                gui.chat_command(self.state.iped, cases, sem_destrutivas=True, modelo=cfg["modelo"], ferramentas=tools, num_ctx=int(cfg.get("num_ctx") or 0) if cfg.get("num_ctx_manual") else 0), env=env  # sem escolha do usuário vale o padrão (131072)
            )
            self.tracker = progress.Tracker()
            self.say(f"Ferramentas da IA: {len(tools)} ligadas (~{tc.tokens(tools)} tokens de contexto).")
            hint = gui.big_model_hint(cfg["modelo"])
            if hint:
                self.say(hint)
            self.run_state = ("running", self.tracker.label)
            self._paint_run()
            if self.run_widgets.get("stage"):
                self.run_widgets["stage"].set(0)
            threading.Thread(target=self.watch_chat, args=(self.chat_proc,), daemon=True).start()

        self.run_state = ("running", "Procurando o Open WebUI neste computador…")
        self._paint_run()

        def check() -> None:
            found = wl.running_open_webui(3737) or wl.find_open_webui_command()

            def after() -> None:
                if found:
                    start()
                else:
                    self.run_state = ("idle", "")
                    self._paint_run()
                    self.ask_webui(start)

            self.root.after(0, after)

        threading.Thread(target=check, daemon=True).start()

    def watch_chat(self, proc: subprocess.Popen) -> None:
        code = proc.wait()

        def done() -> None:
            if self.chat_proc is proc:
                self.chat_proc = None
                self.run_state = ("failed", f"O chat terminou (código {code}). Veja os detalhes abaixo.") if code else ("idle", "Chat encerrado.")
                self._paint_run()

        self.root.after(0, done)

    def ask_webui(self, start) -> None:
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
        else:
            chosen = self.filedialog.askopenfilename(title="Escolha o open-webui.exe (pasta Scripts do seu Python)")
            if chosen:
                cfg = wl.load_config()
                cfg["openwebui_exe"] = chosen
                wl.save_config(cfg)
                start()

    def install_webui(self, then=None) -> None:
        self.run_state = ("running", "Instalando o Open WebUI: pode levar vários minutos…")
        self._paint_run()
        proc = self.run_proc(wl.self_command("instalar-webui"), env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))

        def wait() -> None:
            code = proc.wait()
            self.say("Instalação concluída." if code == 0 else f"Instalação falhou (código {code}).")

            def done() -> None:
                self.run_state = ("idle", "") if code == 0 else ("failed", f"A instalação falhou (código {code}).")
                self._paint_run()
                if code == 0 and then:
                    then()

            self.root.after(0, done)

        threading.Thread(target=wait, daemon=True).start()

    # ------------------------------------------------------------------------------------------ dados
    def say(self, text: str) -> None:
        self.lines.put(text)

    def drain(self) -> None:
        try:
            while True:
                text = self.lines.get_nowait().rstrip("\n")
                self.log_buffer.append(text)
                if self.chat_proc is not None:
                    self._advance_stage(text)
                if self.log_widget is not None and self.log_widget.winfo_exists():
                    self.log_widget.configure(state="normal")
                    self.log_widget.insert("end", text + "\n")
                    self.log_widget.see("end")
                    self.log_widget.configure(state="disabled")
        except queue.Empty:
            pass
        except Exception:  # noqa: BLE001  (widget destruído durante a troca de etapa)
            self.log_widget = None
        self.root.after(150, self.drain)

    def _advance_stage(self, text: str) -> None:
        before = self.tracker.done
        self.tracker.feed(text)
        stage = self.run_widgets.get("stage") if self.run_widgets else None
        if stage is not None and self.tracker.done != before:
            try:
                stage.set(self.tracker.done)
                if self.tracker.done >= len(progress.STAGES):
                    self.run_state = ("done", "Pronto! O chat abriu no navegador.")
                    self._paint_run()
                else:
                    self.run_state = ("running", self.tracker.label)
                    self.run_widgets["status"].configure(text=self.tracker.label)
            except Exception:  # noqa: BLE001
                pass

    def load_models(self) -> None:
        models = gui.ollama_models()

        def apply() -> None:
            self.models = models
            cmb = getattr(self, "cmb_modelo", None)
            try:
                if cmb is not None and cmb.winfo_exists():
                    cmb.configure(values=models)
            except Exception:  # noqa: BLE001
                pass
            if not self.vars["modelo"].get() and models:
                self.vars["modelo"].set(models[0])

        self.root.after(0, apply)

    def recommend_model(self) -> None:
        try:
            if self.hw_cache is None:
                self.hw_cache = gui.hardware_summary()
            found, fits, _loaded, _text = self.hw_cache
        except Exception:  # noqa: BLE001
            return
        rec = hw.recommend(fits)
        if not rec or not found.vram_gb or rec.fit != "gpu":
            return

        def show() -> None:
            note = getattr(self, "model_note", None)
            try:
                if note is not None and note.winfo_exists() and not gui.big_model_hint(self.vars["modelo"].get()):
                    note.configure(text=f"Recomendado para a sua placa ({found.vram_gb:g} GB): {rec.name}", style="Muted.TLabel")
            except Exception:  # noqa: BLE001
                pass

        self.root.after(0, show)

    def refresh_status(self, iped: str = "") -> None:
        ollama = len(gui.ollama_models())
        webui = bool(wl.running_open_webui(3737) or wl.find_open_webui_command())

        def paint() -> None:
            def chip(key: str, state: str, text: str) -> None:
                dot, note = self.status_chips[key]
                dot.configure(fg={"ok": P["ok"], "warn": P["warn"]}.get(state, P["muted"]))
                note.configure(text=text)

            chip("iped", "ok" if (Path(iped) / "iped.jar").is_file() else "idle", "pronto" if (Path(iped) / "iped.jar").is_file() else "a informar")
            chip("ollama", "ok" if ollama else "warn", f"{ollama} modelo(s)" if ollama else "fechado")
            chip("webui", "ok" if webui else "warn", "encontrado" if webui else "ausente")
            self.env_checked = True

        self.root.after(0, paint)

    # ------------------------------------------------------------------------------------------ janelas
    def toplevel(self, title: str, size: str):
        win = self.tk.Toplevel(self.root)
        win.title(title)
        win.geometry(size)
        win.configure(bg=P["bg"])
        win.transient(self.root)
        return win

    def chosen_tools(self) -> list[str]:
        saved = wl.load_config().get("ferramentas", "")
        return tc.normalize(saved) if saved else list(tc.DEFAULT)

    def pick_tools(self) -> None:
        tk, ttk = self.tk, self.ttk
        win = self.toplevel("Ferramentas da IA", "800x700")
        current = set(self.chosen_tools())
        vars_: dict[str, tk.BooleanVar] = {}
        total = tk.StringVar()

        def refresh(*_a: object) -> None:
            sel = [n for n, v in vars_.items() if v.get()]
            tok = tc.tokens(sel)
            warn = "  ⚠ muitas: modelos pequenos erram e o contexto aperta" if tok > tc.TOKEN_WARN else ""
            total.set(f"{len(sel)} de {len(tc.NAMES)} ligadas · cerca de {tok} tokens de contexto{warn}")

        top = ttk.Frame(win, padding=(24, 20, 24, 8))
        top.pack(fill="x")
        ttk.Label(top, text="Ferramentas da IA", font=self.f["h2"]).pack(anchor="w")
        ttk.Label(top, text="Cada ferramenta ligada ocupa contexto do modelo. Em GPU de 8 GB, menos é melhor. Vale no próximo chat.", style="Muted.TLabel", wraplength=640, justify="left").pack(anchor="w", pady=(4, 0))
        ttk.Label(top, textvariable=total, font=self.f["bold"]).pack(anchor="w", pady=(10, 0))

        bottom = ttk.Frame(win, padding=(24, 12, 24, 20))
        bottom.pack(side="bottom", fill="x")
        holder = ttk.Frame(win, padding=(24, 0))
        holder.pack(fill="both", expand=True)
        canvas = tk.Canvas(holder, highlightthickness=0, bg=P["bg"])
        scroll = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        group = ""
        for name, grp, tok, desc, _on in tc.CATALOG:
            if grp != group:
                group = grp
                ttk.Label(inner, text=grp.upper(), style="Muted.TLabel", font=self.f["small"]).pack(anchor="w", pady=(14, 2))
            var = tk.BooleanVar(value=name in current)
            var.trace_add("write", refresh)
            vars_[name] = var
            ttk.Checkbutton(inner, text=f"{name}  (~{tok})  —  {desc}", variable=var).pack(anchor="w", pady=1)
        refresh()

        def setall(names: object) -> None:
            chosen = set(names)  # type: ignore[arg-type]
            for n, v in vars_.items():
                v.set(n in chosen)

        def save_and_close() -> None:
            cfg = wl.load_config()
            cfg["ferramentas"] = ",".join(n for n, v in vars_.items() if v.get())
            wl.save_config(cfg)
            win.destroy()
            self.say("Ferramentas da IA salvas. Valem no próximo chat" + (" (pare e inicie o chat de novo)." if self.chat_proc is not None else "."))
            if hasattr(self, "tools_note") and self.tools_note.winfo_exists():
                self.preset.set(self._preset_now())
                self._tools_summary()

        ttk.Button(bottom, text="Essenciais", command=lambda: setall(tc.DEFAULT)).pack(side="left")
        ttk.Button(bottom, text="Todas", command=lambda: setall(tc.NAMES)).pack(side="left", padx=8)
        ttk.Button(bottom, text="Salvar", style="Primary.TButton", command=save_and_close).pack(side="right")
        ttk.Button(bottom, text="Cancelar", style="Ghost.TButton", command=win.destroy).pack(side="right", padx=8)

    def show_hardware(self) -> None:
        tk, ttk = self.tk, self.ttk
        win = self.toplevel("Desempenho e contexto", "820x680")
        box = tk.Text(win, wrap="word", bg=P["surface"], fg=P["text"], relief="flat", font=self.f["body"], padx=16, pady=14, highlightthickness=0)
        box.pack(fill="both", expand=True, padx=20, pady=(20, 8))
        box.insert("1.0", "Lendo GPU, memória e modelos do Ollama…")
        box.configure(state="disabled")
        bar = ttk.Frame(win, padding=(20, 8, 20, 16))
        bar.pack(fill="x")
        ttk.Button(bar, text="Fechar", style="Ghost.TButton", command=win.destroy).pack(side="right")

        def work() -> None:
            try:
                found, fits, loaded, text = gui.hardware_summary()
            except Exception as exc:  # noqa: BLE001
                found, fits, loaded, text = hw.Hardware(), [], [], f"Não consegui ler o hardware: {exc}"
            self.root.after(0, lambda: fill(found, fits, text))

        def fill(found: hw.Hardware, fits: list[hw.ModelFit], text: str) -> None:
            if not win.winfo_exists():
                return
            cfg = wl.load_config()
            adv = tuning.advise(found, fits, self.vars["modelo"].get() or cfg.get("modelo", ""))
            extra = "\n\nRECOMENDAÇÃO DE DESEMPENHO\n" + "\n".join(f"  - {x}" for x in adv.lines)
            extra += f"\n  - Janela de contexto recomendada: {adv.num_ctx}.\n  - Variáveis do Ollama: " + "; ".join(f"{k}={v}" for k, v in adv.env.items())
            extra += ("\n  - O Open WebUI é ajustado sozinho ao iniciar o chat: contexto escolhido, modelo mantido na memória e chamadas extras (título, etiquetas, sugestões) desligadas."
                      "\n  - Aplicar as variáveis grava no perfil do Windows e reinicia o Ollama; esse passo não foi validado na sua máquina.")  # fmt: skip
            box.configure(state="normal")
            box.delete("1.0", "end")
            box.insert("1.0", text + extra)
            box.configure(state="disabled")
            ctx_var = tk.StringVar(value=str(cfg.get("num_ctx") if cfg.get("num_ctx_manual") else adv.num_ctx))
            ttk.Label(bar, text="Janela de contexto").pack(side="left", padx=(0, 6))
            ttk.Combobox(bar, textvariable=ctx_var, values=[str(c) for c in tuning.CTX_OPTIONS], width=7, state="readonly").pack(side="left")
            note = tk.StringVar(value="")
            ttk.Label(win, textvariable=note, style="Muted.TLabel", wraplength=760, justify="left").pack(before=bar, anchor="w", padx=20, pady=(0, 6))
            acts = ttk.Frame(win)
            acts.pack(before=bar, fill="x", padx=20, pady=(0, 6))

            def save_ctx() -> None:
                c = wl.load_config()
                c["num_ctx"], c["num_ctx_manual"] = ctx_var.get(), True  # só a escolha feita aqui vale; valores antigos salvos são ignorados
                wl.save_config(c)
                note.set(f"Contexto {ctx_var.get()} salvo. Vale no próximo chat (pare e inicie o chat de novo).")

            def apply_ollama() -> None:
                note.set("Gravando variáveis e reiniciando o Ollama…")

                def work2() -> None:
                    done = tuning.apply_env(adv.env, self.say)
                    self.root.after(0, lambda: note.set("Ollama reiniciado com os ajustes." if done else "Não consegui aplicar tudo; veja os detalhes técnicos na etapa Executar."))

                threading.Thread(target=work2, daemon=True).start()

            ttk.Button(acts, text="Salvar contexto", command=save_ctx).pack(side="left")
            ttk.Button(acts, text="Aplicar ajustes no Ollama", command=apply_ollama).pack(side="left", padx=8)
            if adv.suggested_model:

                def get_model() -> None:
                    note.set(f"Baixando {adv.suggested_model} (vários GB; veja o andamento nos detalhes técnicos)…")

                    def work3() -> None:
                        res = deps.run_plan(deps.Plan("pull", (adv.suggested_model,)), self.say)
                        if res.ok:
                            self.vars["modelo"].set(adv.suggested_model)
                            c = wl.load_config()
                            c["modelo"] = adv.suggested_model
                            wl.save_config(c)
                        self.root.after(0, lambda: note.set(f"{adv.suggested_model} pronto e selecionado." if res.ok else f"Falhou: {res.message}"))

                    threading.Thread(target=work3, daemon=True).start()

                ttk.Button(acts, text=f"Baixar {adv.suggested_model}", style="Primary.TButton", command=get_model).pack(side="left")
            rec = hw.recommend(fits)
            if rec and found.vram_gb and rec.fit == "gpu":

                def use() -> None:
                    self.vars["modelo"].set(rec.name)
                    cfg = wl.load_config()
                    cfg["modelo"] = rec.name  # o contexto continua o padrão do programa
                    wl.save_config(cfg)
                    self.say(f"Modelo escolhido: {rec.name}."
                             + (" Ative OLLAMA_FLASH_ATTENTION=1 e OLLAMA_KV_CACHE_TYPE=q8_0 no Ollama (e reinicie-o)." if rec.q8 else ""))  # fmt: skip
                    win.destroy()

                ttk.Button(bar, text=f"Usar {rec.name}", style="Primary.TButton", command=use).pack(side="left")

        threading.Thread(target=work, daemon=True).start()

    def show_dependencies(self, auto: bool = False) -> None:
        """Gerenciador: o que o IPED e a IA precisam, o que foi encontrado e instalação direta quando existe."""
        import webbrowser

        tk, ttk = self.tk, self.ttk
        win = self.toplevel("Dependências", "900x700")
        ttk.Label(win, text="Dependências", font=self.f["h2"]).pack(anchor="w", padx=24, pady=(20, 2))
        ttk.Label(
            win, style="Muted.TLabel", wraplength=840, justify="left",
            text="Verde: encontrado. Amarelo: opcional ou ausente. Vermelho: obrigatório e ausente. Baixar instaladores precisa de internet; "
            "o processamento dos casos e da IA continua neste computador. Os comandos aparecem nos detalhes técnicos antes de rodar.",
        ).pack(anchor="w", padx=24)
        foot = ttk.Frame(win, padding=(24, 10))
        foot.pack(side="bottom", fill="x")
        mid = ttk.Frame(win)
        mid.pack(fill="both", expand=True, padx=(24, 8), pady=(12, 0))
        canvas = tk.Canvas(mid, bg=P["bg"], highlightthickness=0)
        scroll = ttk.Scrollbar(mid, orient="vertical", command=canvas.yview)
        body = ttk.Frame(canvas)
        body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        inner = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(inner, width=e.width))
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        win.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        ttk.Label(body, text="Verificando…", style="Muted.TLabel").pack(anchor="w")

        info = tk.StringVar(value="")
        ttk.Label(foot, textvariable=info, style="Muted.TLabel", wraplength=840, justify="left").pack(anchor="w")
        bar = ttk.Progressbar(foot, mode="determinate", maximum=100)
        buttons = ttk.Frame(foot)
        buttons.pack(fill="x", pady=(8, 0))
        state: dict = {"busy": False, "frac": None, "line": "", "deps": [], "pending_auto": auto}
        btn_all = ttk.Button(buttons, text="Instalar o que falta", style="Primary.TButton")
        btn_all.pack(side="left")
        ttk.Button(buttons, text="Verificar de novo", command=lambda: scan()).pack(side="left", padx=8)
        ttk.Button(buttons, text="Fechar", style="Ghost.TButton", command=win.destroy).pack(side="right")
        self.collect()
        iped_root = self.state.iped

        def say_line(text: str) -> None:
            state["line"] = text[:160]
            self.say(text)

        def scan() -> None:
            if state["busy"]:
                return
            info.set("Verificando o computador…")

            def work() -> None:
                found = deps.check_all(iped_root)
                self.root.after(0, lambda: fill(found))

            threading.Thread(target=work, daemon=True).start()

        def install(plans: list[tuple[deps.Dep, deps.Plan]]) -> None:
            if state["busy"] or not plans:
                return
            state.update(busy=True, frac=None)
            bar.pack(fill="x", pady=(8, 0))
            bar.configure(mode="indeterminate")
            bar.start(12)
            btn_all.configure(state="disabled")

            def work() -> None:
                failed = []
                for dep, plan in plans:
                    say_line(f"=== {dep.name} ===")
                    state["frac"] = None
                    res = deps.run_plan(plan, say_line, lambda f: state.update(frac=f))
                    # o que vale é o programa estar lá depois, não o código de saída do instalador
                    now = {d.key: d for d in deps.check_all(iped_root)}.get(dep.key)
                    if now is not None and now.status == deps.OK:
                        say_line(f"{dep.name}: instalado e verificado.")
                        continue
                    reason = res.message or f"o instalador terminou, mas {dep.name} ainda não foi encontrado (talvez seja preciso fechar e abrir este programa)."
                    failed.append(f"{dep.name}: {reason}")
                    say_line(reason)
                state["failed"] = failed
                state["busy"] = False
                self.root.after(0, finished)

            threading.Thread(target=work, daemon=True).start()
            poll()

        def poll() -> None:
            if not win.winfo_exists() or not state["busy"]:
                return
            if state["frac"] is not None:
                bar.stop()
                bar.configure(mode="determinate", value=state["frac"] * 100)
            else:
                bar.configure(mode="indeterminate")
                bar.start(12)
            info.set(state["line"] or "Instalando…")
            win.after(250, poll)

        def finished() -> None:
            if not win.winfo_exists():
                return
            bar.stop()
            bar.pack_forget()
            btn_all.configure(state="normal")
            failed = state.get("failed") or []
            scan()
            if failed:
                self.messagebox.showwarning(APP_TITLE, "Alguns itens não foram instalados:\n\n" + "\n".join(failed) + f"\n\nO registro completo está em:\n{ins.LOG_PATH}", parent=win)
            else:
                info.set("Concluído. Se algum item continuar ausente depois de instalar, feche e abra este programa para ele enxergar o novo PATH.")

        def fill(found: list[deps.Dep]) -> None:
            if not win.winfo_exists():
                return
            state["deps"] = found
            for child in body.winfo_children():
                child.destroy()
            groups = (("iped", "IPED e módulos opcionais"), ("ia", "Assistente de IA (tudo local)"))
            for key, title in groups:
                rows = [d for d in found if d.group == key]
                have = sum(d.status == deps.OK for d in rows)
                ttk.Label(body, text=f"{title}  ·  {have} de {len(rows)} encontrados", font=self.f["bold"]).pack(anchor="w", pady=(14, 2))
                for d in rows:
                    card = ttk.Frame(body, style="Card.TFrame", padding=(14, 8))
                    card.pack(fill="x", pady=3, padx=(0, 6))
                    color = {deps.OK: P["ok"], deps.OPTIONAL: P["warn"], deps.UNKNOWN: P["muted"], deps.MISSING: P["danger"] if d.required else P["warn"]}[d.status]
                    tk.Label(card, text="●", fg=color, bg=P["surface"], font=self.f["h2"]).pack(side="left", padx=(0, 12))
                    right = ttk.Frame(card, style="Card.TFrame")
                    right.pack(side="right", padx=(12, 0))
                    text = ttk.Frame(card, style="Card.TFrame")
                    text.pack(side="left", fill="x", expand=True)
                    tag = "  (obrigatório)" if d.required and d.status != deps.OK else ""
                    ttk.Label(text, text=d.name + tag, style="Card.TLabel", font=self.f["bold"]).pack(anchor="w")
                    extra = d.detail + (f" — {d.why}" if d.why and d.status != deps.OK else "")
                    ttk.Label(text, text=extra, style="CardMuted.TLabel", wraplength=560, justify="left").pack(anchor="w")
                    if d.plan and d.status != deps.OK:
                        if d.plan.automatic:
                            label = "Baixar modelo" if d.plan.kind == "pull" else "Instalar"
                            ttk.Button(right, text=label, command=lambda d=d: ask_install([d])).pack()
                            if not d.plan.validated:
                                ttk.Label(right, text="a validar", style="CardMuted.TLabel").pack()
                        else:
                            ttk.Button(right, text="Como obter", command=lambda u=d.plan.url: webbrowser.open(u)).pack()
            todo = deps.installable(found)
            btn_all.configure(state="normal" if todo else "disabled", command=lambda: ask_install(todo))
            miss = deps.missing_required(found)
            info.set("Tudo o que é obrigatório foi encontrado." if not miss else f"Faltam {len(miss)} item(ns) obrigatório(s): {', '.join(d.name for d in miss)}.")
            if state.pop("pending_auto", False) and todo:
                ask_install(todo)

        def ask_install(items: list[deps.Dep]) -> None:
            plans = [(d, d.plan) for d in items if d.plan and d.plan.automatic]
            listing = "\n".join(f"• {d.name}: {' '.join(p.command)}" for d, p in plans)
            heavy = any(p.kind in {"openwebui", "pull"} for _d, p in plans)
            ok = self.messagebox.askokcancel(
                APP_TITLE,
                "Vou instalar:\n\n" + listing + "\n\nPrecisa de internet para baixar" + (" (centenas de MB a alguns GB; pode demorar)" if heavy else "") +
                ". Alguns instaladores pedem permissão de administrador do Windows. Continuar?",
                parent=win,
            )
            if ok:
                install(plans)

        scan()

    def about(self) -> None:
        ttk = self.ttk
        win = self.toplevel("Sobre", "520x300")
        ttk.Label(win, text=f"{APP_TITLE} MCP", font=self.f["title"]).pack(anchor="w", padx=28, pady=(28, 2))
        ttk.Label(win, text=f"Versão {self._version()}", style="Muted.TLabel").pack(anchor="w", padx=28)
        ttk.Label(
            win, wraplength=460, justify="left", style="Muted.TLabel",
            text="Indexa casos com o IPED e conversa com eles por IA local. Todo o processamento fica neste computador; "
            "os dados do caso não são enviados para fora.\n\nA interface anterior continua disponível na versão 0.6.0 (branch estavel-v0.6).",
        ).pack(anchor="w", padx=28, pady=16)  # fmt: skip
        ttk.Button(win, text="Fechar", command=win.destroy).pack(anchor="e", padx=28, side="bottom", pady=20)

    # ------------------------------------------------------------------------------------------ ciclo de vida
    def close(self) -> None:
        self.closing = True
        for proc in (self.index_proc, self.chat_proc):
            if proc is not None:
                gui.kill_tree(proc)
        self.root.destroy()

    def run(self) -> int:
        import time

        self.splash_since = time.monotonic()
        self.root.after(100, self._start_background)
        self.root.after(100, self._finish_splash)
        self.root.mainloop()
        return 0


def main() -> int:  # pragma: no cover
    try:
        app = App()
    except Exception as exc:  # noqa: BLE001  (sem tkinter/display)
        print(f"ERRO: não consegui abrir a janela ({exc!r}). Use: iped-ia-mcp --modo iniciar --root <IPED> --cases-dir <casos>", file=sys.stderr)
        return 1
    gui.hide_console()
    return app.run()
