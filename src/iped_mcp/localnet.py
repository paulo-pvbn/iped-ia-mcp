"""Acesso a serviços deste computador (127.0.0.1) sem passar pelo proxy do sistema.

Em redes com proxy (HTTP_PROXY ou configuração do Windows) o `urlopen` comum mandaria até 127.0.0.1 para o proxy, que não
enxerga este computador: o Ollama e o Open WebUI pareceriam desligados.
"""

from __future__ import annotations

import urllib.request

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def open_local(req: str | urllib.request.Request, timeout: float = 5):  # noqa: ANN201
    return _OPENER.open(req, timeout=timeout)
