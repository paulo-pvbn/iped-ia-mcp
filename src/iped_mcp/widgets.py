"""Peças de desenho da janela: tela de abertura, barra de etapas e barra de uso do contexto (precisam de display)."""

from __future__ import annotations

from iped_mcp import progress, ui_theme

P = ui_theme.PALETTE
LEVEL_COLOR = {"ok": "ok", "warn": "warn", "danger": "danger", "none": "border"}


class Splash:  # pragma: no cover - desenho
    """Janelinha de abertura sem moldura: nome, etapa atual e uma barra feita de segmentos (um por etapa)."""

    W, H, SEGMENTS = 420, 170, 12

    def __init__(self, root, tk, title: str, version: str) -> None:
        self.tk, self.root = tk, root
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.configure(bg=P["border"])
        x = (root.winfo_screenwidth() - self.W) // 2
        y = (root.winfo_screenheight() - self.H) // 3
        self.win.geometry(f"{self.W}x{self.H}+{x}+{y}")
        self.win.attributes("-topmost", True)
        card = tk.Frame(self.win, bg=P["bg"])
        card.pack(fill="both", expand=True, padx=1, pady=1)  # borda de 1 px
        tk.Label(card, text=title, fg=P["text"], bg=P["bg"], font=("Segoe UI", 20, "bold")).pack(anchor="w", padx=28, pady=(26, 0))
        tk.Label(card, text=f"Análise de casos com IA local · v{version}", fg=P["muted"], bg=P["bg"], font=("Segoe UI", 9)).pack(anchor="w", padx=28)
        self.cv = tk.Canvas(card, width=self.W - 56, height=10, bg=P["bg"], highlightthickness=0)
        self.cv.pack(padx=28, pady=(22, 6))
        self.label = tk.Label(card, text="", fg=P["accent_text"], bg=P["bg"], font=("Segoe UI", 9))
        self.label.pack(anchor="w", padx=28)
        self.step(0.0, "Iniciando…")

    def step(self, fraction: float, text: str) -> None:
        cv, n = self.cv, self.SEGMENTS
        width = int(cv["width"])
        gap = 4
        seg = (width - gap * (n - 1)) / n
        lit = round(max(0.0, min(1.0, fraction)) * n)
        cv.delete("all")
        for i in range(n):
            x0 = i * (seg + gap)
            cv.create_rectangle(x0, 2, x0 + seg, 8, fill=P["accent"] if i < lit else P["surface"], outline="")
        self.label.configure(text=text)
        self.win.update_idletasks()
        self.win.update()

    def close(self) -> None:
        try:
            self.win.destroy()
        except Exception:  # noqa: BLE001
            pass


class StageBar:  # pragma: no cover - desenho
    """Linha com as etapas do chat: pontos preenchidos nas concluídas, anel na atual e uma barra fina de progresso."""

    H = 58

    def __init__(self, parent, tk, stages=progress.STAGES) -> None:
        self.stages = stages
        self.cv = tk.Canvas(parent, height=self.H, bg=P["bg"], highlightthickness=0)
        self.done = 0
        self.cv.bind("<Configure>", lambda _e: self.draw())

    def set(self, done: int) -> None:
        self.done = done
        self.draw()

    def draw(self) -> None:
        cv, n = self.cv, len(self.stages)
        width = max(cv.winfo_width(), 300)
        cv.delete("all")
        pad = 36
        step = (width - 2 * pad) / (n - 1)
        y = 20
        cv.create_line(pad, y, width - pad, y, fill=P["surface"], width=4, capstyle="round")
        if self.done > 0:
            reach = pad + step * min(self.done, n - 1)
            cv.create_line(pad, y, reach, y, fill=P["accent"], width=4, capstyle="round")
        for i, name in enumerate(self.stages):
            x = pad + step * i
            finished = i < self.done or self.done >= n
            if finished:
                cv.create_oval(x - 7, y - 7, x + 7, y + 7, fill=P["accent"], outline="")
                cv.create_text(x, y, text="✓", fill=P["on_accent"], font=("Segoe UI", 8, "bold"))
            elif i == self.done:
                cv.create_oval(x - 8, y - 8, x + 8, y + 8, fill=P["bg"], outline=P["accent_text"], width=2)
            else:
                cv.create_oval(x - 6, y - 6, x + 6, y + 6, fill=P["surface"], outline="")
            cv.create_text(x, y + 22, text=name, fill=P["text"] if i <= self.done else P["muted"], font=("Segoe UI", 9))


class ContextBar:  # pragma: no cover - desenho
    """Barra do uso da janela de contexto da conversa, com texto (tokens e velocidade) ao lado."""

    def __init__(self, parent, tk) -> None:
        self.box = tk.Frame(parent, bg=P["bg"])
        tk.Label(self.box, text="Contexto", fg=P["muted"], bg=P["bg"], font=("Segoe UI", 9)).pack(side="left")
        self.cv = tk.Canvas(self.box, width=180, height=10, bg=P["bg"], highlightthickness=0)
        self.cv.pack(side="left", padx=10)
        self.text = tk.Label(self.box, text="", fg=P["muted"], bg=P["bg"], font=("Segoe UI", 9))
        self.text.pack(side="left")
        self.update_use(progress.context_use(None))

    def update_use(self, use: progress.ContextUse) -> None:
        cv = self.cv
        cv.delete("all")
        cv.create_rectangle(0, 2, 180, 8, fill=P["surface"], outline="")
        if use.percent:
            cv.create_rectangle(0, 2, 180 * use.percent / 100, 8, fill=P[LEVEL_COLOR[use.level]], outline="")
        suffix = f"  {use.percent:.0f}%" if use.percent is not None else ""
        self.text.configure(text=use.text + suffix, fg=P["danger"] if use.level == "danger" else P["muted"])
