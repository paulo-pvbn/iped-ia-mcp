"""Tema escuro da janela: poucas cores com função clara (padrão Material/Fluent), contraste WCAG AA e grade de 8 px."""

from __future__ import annotations

import sys
from tkinter import ttk

PALETTE = {
    "bg": "#0F172A",  # fundo da janela
    "side": "#0B1220",  # barra lateral
    "surface": "#1E293B",  # cartões e campos
    "hover": "#2A3A52",
    "border": "#334155",
    "text": "#F1F5F9",
    "muted": "#9AA8BC",
    "accent": "#2563EB",  # única cor de destaque (ações principais)
    "accent_hover": "#1D4ED8",
    "accent_text": "#93C5FD",  # destaque como texto sobre fundo escuro
    "ok": "#4ADE80",
    "warn": "#FBBF24",
    "danger": "#F87171",
    "on_accent": "#FFFFFF",
}
SPACE = 8  # unidade da grade


def fonts() -> dict[str, tuple]:
    family = "Segoe UI" if sys.platform == "win32" else "DejaVu Sans"
    mono = "Consolas" if sys.platform == "win32" else "DejaVu Sans Mono"
    return {
        "title": (family, 20, "bold"),
        "h2": (family, 13, "bold"),
        "body": (family, 10),
        "small": (family, 9),
        "bold": (family, 10, "bold"),
        "mono": (mono, 9),
        "brand": (family, 15, "bold"),
    }


def _lin(channel: int) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def apply(root) -> dict[str, tuple]:  # pragma: no cover - precisa de display
    """Configura os estilos ttk e devolve as fontes."""
    p, f = PALETTE, fonts()
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(bg=p["bg"])
    import tkinter.font as tkfont

    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        tkfont.nametofont(name).configure(family=f["body"][0], size=f["body"][1])
    root.option_add("*TCombobox*Listbox.background", p["surface"])
    root.option_add("*TCombobox*Listbox.foreground", p["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", p["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", p["on_accent"])
    root.option_add("*Menu.background", p["surface"])
    root.option_add("*Menu.foreground", p["text"])
    root.option_add("*Menu.activeBackground", p["accent"])
    root.option_add("*Menu.activeForeground", p["on_accent"])
    root.option_add("*Menu.relief", "flat")

    style.configure(".", background=p["bg"], foreground=p["text"], fieldbackground=p["surface"], bordercolor=p["border"],
                    lightcolor=p["border"], darkcolor=p["border"], troughcolor=p["surface"], focuscolor=p["accent"], font=f["body"])  # fmt: skip
    style.configure("TFrame", background=p["bg"])
    style.configure("Side.TFrame", background=p["side"])
    style.configure("Card.TFrame", background=p["surface"])
    style.configure("TLabel", background=p["bg"], foreground=p["text"])
    for name, bg in (("Side", p["side"]), ("Card", p["surface"])):
        style.configure(f"{name}.TLabel", background=bg, foreground=p["text"])
        style.configure(f"{name}Muted.TLabel", background=bg, foreground=p["muted"], font=f["small"])
    style.configure("Title.TLabel", font=f["title"])
    style.configure("H2.TLabel", font=f["h2"])
    style.configure("Muted.TLabel", foreground=p["muted"])
    style.configure("Brand.TLabel", background=p["side"], font=f["brand"])
    for key in ("ok", "warn", "danger"):
        style.configure(f"{key.capitalize()}.TLabel", foreground=p[key])
    style.configure("Link.TLabel", foreground=p["accent_text"])

    style.configure("TEntry", padding=8, fieldbackground=p["surface"], foreground=p["text"], insertcolor=p["text"], bordercolor=p["border"])
    style.map("TEntry", bordercolor=[("focus", p["accent"])], lightcolor=[("focus", p["accent"])], darkcolor=[("focus", p["accent"])])
    style.configure("TCombobox", padding=8, arrowcolor=p["muted"], fieldbackground=p["surface"], foreground=p["text"], background=p["surface"])
    style.map("TCombobox", fieldbackground=[("readonly", p["surface"])], foreground=[("readonly", p["text"])],
              bordercolor=[("focus", p["accent"])], arrowcolor=[("active", p["text"])])  # fmt: skip

    style.configure("TButton", padding=(16, 8), background=p["surface"], foreground=p["text"], borderwidth=1, relief="flat", font=f["bold"])
    style.map("TButton", background=[("active", p["hover"]), ("disabled", p["bg"])], foreground=[("disabled", p["muted"])])
    style.configure("Primary.TButton", background=p["accent"], foreground=p["on_accent"], bordercolor=p["accent"])
    style.map("Primary.TButton", background=[("active", p["accent_hover"]), ("disabled", p["border"])],
              foreground=[("disabled", p["muted"])], bordercolor=[("active", p["accent_hover"])])  # fmt: skip
    style.configure("Ghost.TButton", background=p["bg"], bordercolor=p["bg"], foreground=p["text"])
    style.map("Ghost.TButton", background=[("active", p["surface"])], foreground=[("disabled", p["muted"])])
    style.configure("Danger.TButton", background=p["surface"], foreground=p["danger"])
    style.configure("Menu.TMenubutton", background=p["bg"], foreground=p["text"], padding=(12, 6), arrowcolor=p["muted"], borderwidth=0)
    style.map("Menu.TMenubutton", background=[("active", p["surface"])])

    style.configure("TCheckbutton", background=p["bg"], foreground=p["text"], indicatorbackground=p["surface"], indicatorforeground=p["accent_text"])
    style.configure("Card.TCheckbutton", background=p["surface"])
    style.map("TCheckbutton", background=[("active", p["bg"])], indicatorbackground=[("selected", p["accent"])])
    style.configure("TRadiobutton", background=p["surface"], foreground=p["text"], indicatorbackground=p["bg"])
    style.map("TRadiobutton", background=[("active", p["surface"])], indicatorbackground=[("selected", p["accent"])])
    style.configure("Horizontal.TProgressbar", background=p["accent"], troughcolor=p["surface"], bordercolor=p["surface"], thickness=6)
    style.configure("Vertical.TScrollbar", background=p["surface"], troughcolor=p["bg"], bordercolor=p["bg"], arrowcolor=p["muted"])
    style.configure("TSeparator", background=p["border"])
    return f
