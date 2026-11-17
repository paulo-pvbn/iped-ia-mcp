"""Teste simples de acesso à internet para a instalação (sem interface).

A instalação baixa arquivos na primeira vez (Ollama, modelos, pacotes Python, Java, Open WebUI). Em rede com proxy que pede
login, o Python e o pip costumam receber o erro 407; este teste mostra isso ao usuário antes de ele tentar instalar.
"""

from __future__ import annotations

import urllib.error
import urllib.request

HOSTS = (
    ("Ollama (programa)", "https://ollama.com"),
    ("Modelos de IA", "https://registry.ollama.ai"),
    ("Pacotes Python", "https://pypi.org/simple/"),
    ("GitHub (IPED, Java)", "https://github.com"),
)


def check_host(url: str, timeout: float = 8.0) -> tuple[bool, str]:
    """(ok, motivo). Qualquer resposta HTTP já prova que a rede chegou lá; 407 é o proxy pedindo login."""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "iped-ia-mcp"})
        urllib.request.urlopen(req, timeout=timeout)  # noqa: S310
        return True, "ok"
    except urllib.error.HTTPError as exc:
        if exc.code == 407:
            return False, "o proxy pede login (407)"
        return True, "ok"  # 403/404/405 etc.: o servidor respondeu
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        if "407" in text:
            return False, "o proxy pede login (407)"
        return False, "sem acesso (rede bloqueada ou sem internet)"


def proxy_in_use() -> bool:
    return bool(urllib.request.getproxies())


def check_all() -> list[tuple[str, bool, str]]:
    return [(name, *check_host(url)) for name, url in HOSTS]


def summary(results: list[tuple[str, bool, str]]) -> str:
    bad = [(n, why) for n, ok, why in results if not ok]
    proxy = " Esta rede usa proxy." if proxy_in_use() else ""
    if not bad:
        return "Internet: ok para todos os endereços de instalação." + proxy
    lines = "; ".join(f"{n}: {why}" for n, why in bad)
    tip = ""
    if any("407" in why for _n, why in bad):
        tip = (" O Ollama e o Java baixam pelo login do Windows, mas pip/uv e o próprio Ollama (modelos) podem falhar com 407: "
               "peça ao suporte para liberar esses endereços ou instale esses itens por outra rede e use 'Verificar de novo'.")
    return f"Internet com problema — {lines}.{proxy}{tip}"
