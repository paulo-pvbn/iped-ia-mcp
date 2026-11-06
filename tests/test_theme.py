from iped_mcp import ui_theme as t

P = t.PALETTE


def test_text_contrast_meets_wcag_aa() -> None:
    for fg, bg in (
        ("text", "bg"), ("text", "surface"), ("muted", "bg"), ("muted", "surface"), ("muted", "side"),
        ("on_accent", "accent"), ("on_accent", "accent_hover"), ("accent_text", "bg"), ("accent_text", "surface"),
        ("ok", "surface"), ("warn", "surface"), ("danger", "surface"), ("danger", "bg"), ("text", "hover"),
    ):  # fmt: skip
        assert t.contrast(P[fg], P[bg]) >= 4.5, (fg, bg, round(t.contrast(P[fg], P[bg]), 2))


def test_contrast_reference_values() -> None:
    assert round(t.contrast("#000000", "#FFFFFF")) == 21 and t.contrast("#777777", "#777777") == 1
