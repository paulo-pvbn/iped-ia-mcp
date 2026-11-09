"""Instaladores com caminho principal e reserva, e conferência do resultado (sem interface).

Regra: o código de saída de um instalador não prova nada; o que vale é o programa existir e responder depois.
Cada instalação tenta primeiro o `winget` e, se ele não existir ou não resolver, baixa o instalador oficial do fabricante.
Tudo é narrado em `say` e gravado em `instalacao.log` (pasta de dados do programa) para ser enviado em caso de erro.
Os endereços de download são os públicos dos fabricantes; não foram testados a partir do ambiente de desenvolvimento.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from iped_mcp import webui_launcher as wl

OLLAMA_URL = "https://ollama.com/download/OllamaSetup.exe"
JRE_URL = "https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jre/hotspot/normal/eclipse"
VCREDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
WINGET_COMMON = ["winget", "install", "-e", "--source", "winget", "--accept-package-agreements", "--accept-source-agreements", "--silent", "--id"]
WINGET_OK = {0, -1978335135, -1978335189}  # instalado agora / já instalado / sem versão mais nova
LOG_PATH = wl.CONFIG_PATH.parent / "instalacao.log"
OLLAMA_API = "http://127.0.0.1:11434"


@dataclass
class Result:
    ok: bool
    code: int = 0
    message: str = ""
    log: list[str] = field(default_factory=list)


def winget_cmd(package: str) -> list[str]:
    return [*WINGET_COMMON, package]


class Ops:
    """Tudo o que toca o computador. Os testes trocam esta classe por uma falsa."""

    def __init__(self, say: Callable[[str], None]) -> None:
        self._say = say

    def say(self, text: str) -> None:
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")
        except OSError:
            pass
        self._say(text)

    def has_winget(self) -> bool:
        return bool(shutil.which("winget"))

    def stream(self, cmd: list[str], elevate: bool = False) -> int:
        """Roda o comando escondido, narrando a saída. `elevate`: pede permissão de administrador (janela do Windows)."""
        if elevate and os.name == "nt":
            quoted = ",".join("'" + a.replace("'", "''") + "'" for a in cmd[1:])
            args = f" -ArgumentList {quoted}" if len(cmd) > 1 else ""
            cmd = ["powershell", "-NoProfile", "-Command", f"$p = Start-Process -FilePath '{cmd[0]}'{args} -Verb RunAs -Wait -PassThru; exit $p.ExitCode"]
        self.say("$ " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
        try:
            proc = subprocess.Popen(  # noqa: S603
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                env=dict(os.environ, PYTHONUTF8="1"), **wl.hidden_kwargs(),
            )  # fmt: skip
        except OSError as exc:
            self.say(f"Não consegui executar: {exc}")
            return 1
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.strip() and not set(line.strip()) <= set("-\\|/ "):  # some com os "giradores" do winget
                self.say(line.rstrip())
        return proc.wait()

    def download(self, url: str, name: str, progress: Callable[[float | None], None]) -> Path | None:
        dest = Path(tempfile.mkdtemp(prefix="iped-ia-")) / name
        self.say(f"Baixando {url}")
        for attempt in (1, 2, 3):
            try:
                with urllib.request.urlopen(url, timeout=60) as resp, dest.open("wb") as out:  # noqa: S310
                    total = int(resp.headers.get("Content-Length") or 0)
                    done = 0
                    while chunk := resp.read(1 << 20):
                        out.write(chunk)
                        done += len(chunk)
                        progress(done / total if total else None)
                self.say(f"Baixado: {dest.stat().st_size / 1e6:.0f} MB")
                return dest
            except Exception as exc:  # noqa: BLE001
                self.say(f"Falha no download (tentativa {attempt}/3): {exc}")
                time.sleep(2 * attempt)
        return None

    def extract_zip(self, archive: Path, target: Path) -> bool:
        try:
            with tempfile.TemporaryDirectory(prefix="iped-ia-x-") as tmp, zipfile.ZipFile(archive) as z:
                z.extractall(tmp)
                kids = list(Path(tmp).iterdir())
                root = kids[0] if len(kids) == 1 and kids[0].is_dir() else Path(tmp)
                target.mkdir(parents=True, exist_ok=True)
                for item in root.iterdir():
                    shutil.move(str(item), str(target / item.name))
            return True
        except (OSError, zipfile.BadZipFile) as exc:
            self.say(f"Não consegui extrair: {exc}")
            return False

    def ollama_exe(self) -> str | None:
        found = shutil.which("ollama")
        if found:
            return found
        local = os.environ.get("LOCALAPPDATA")
        exe = Path(local) / "Programs" / "Ollama" / "ollama.exe" if local else None
        return str(exe) if exe and exe.is_file() else None

    def ollama_up(self) -> bool:
        try:
            with urllib.request.urlopen(f"{OLLAMA_API}/api/tags", timeout=2) as resp:  # noqa: S310
                json.load(resp)
            return True
        except Exception:  # noqa: BLE001
            return False

    def start_ollama(self) -> None:
        exe = self.ollama_exe()
        if not exe:
            return
        from iped_mcp import tuning

        self.say(f"$ {exe} serve   (em segundo plano)")
        flags = {"creationflags": 0x08000000 | 0x00000008 | 0x00000200} if os.name == "nt" else {"start_new_session": True}  # sem janela, destacado
        subprocess.Popen(  # noqa: S603
            [exe, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={**os.environ, **tuning.OLLAMA_ENV}, **flags,
        )

    def sleep(self, secs: float) -> None:
        time.sleep(secs)


def _winget(ops: Ops, package: str) -> bool:
    if not ops.has_winget():
        ops.say("winget não encontrado neste Windows; vou direto ao instalador oficial.")
        return False
    code = ops.stream(winget_cmd(package))
    if code not in WINGET_OK:
        ops.say(f"winget terminou com código {code}.")
    return code in WINGET_OK


def wait_ollama(ops: Ops, seconds: int = 60) -> bool:
    for _ in range(max(1, seconds // 2)):
        if ops.ollama_up():
            return True
        ops.sleep(2)
    return ops.ollama_up()


def ensure_ollama_running(ops: Ops) -> Result:
    """Deixa o Ollama respondendo (inicia em segundo plano se estiver instalado e parado)."""
    if ops.ollama_up():
        return Result(True)
    if not ops.ollama_exe():
        return Result(False, 1, "O Ollama não está instalado. Instale-o primeiro (https://ollama.com/download).")
    ops.say("Ollama instalado, mas parado: iniciando…")
    if wait_ollama(ops, 8):  # o aplicativo pode estar subindo sozinho
        return Result(True)
    ops.start_ollama()
    if wait_ollama(ops, 60):
        ops.say("Ollama respondendo.")
        return Result(True)
    return Result(False, 1, "O Ollama não respondeu em http://127.0.0.1:11434 depois de iniciado. Abra o Ollama pelo menu Iniciar e tente de novo.")


def ensure_ollama(ops: Ops, progress: Callable[[float | None], None]) -> Result:
    if not ops.ollama_exe():
        _winget(ops, "Ollama.Ollama")
    if not ops.ollama_exe():
        ops.say("Instalando pelo instalador oficial do Ollama…")
        setup = ops.download(OLLAMA_URL, "OllamaSetup.exe", progress)
        if setup:
            ops.stream([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
    if not ops.ollama_exe():
        return Result(False, 1, "Não consegui instalar o Ollama. Baixe e instale manualmente em https://ollama.com/download e use 'Verificar de novo'.")
    return ensure_ollama_running(ops)


def ensure_jre(ops: Ops, target: Path, progress: Callable[[float | None], None]) -> Result:
    """Coloca um JRE na pasta `jre` do IPED (é onde o IPED e as Web APIs o procuram). Nunca sobrescreve uma pasta que já tenha conteúdo."""
    java = target / "bin" / ("java.exe" if os.name == "nt" else "java")
    if java.is_file():
        return Result(True)
    if target.exists() and any(target.iterdir()):
        return Result(False, 1, f"A pasta {target} já existe e tem conteúdo sem o Java dentro; não vou mexer nela. Verifique o IPED.")
    archive = ops.download(JRE_URL, "jre.zip", progress)
    if not archive or not ops.extract_zip(archive, target) or not java.is_file():
        return Result(False, 1, "Não consegui instalar o Java no IPED. Baixe um JRE 17 (Temurin) e coloque o conteúdo em " + str(target))
    ops.say(f"Java instalado em {target}")
    return Result(True)


def ensure_vcredist(ops: Ops, progress: Callable[[float | None], None]) -> Result:
    if _winget(ops, "Microsoft.VCRedist.2015+.x64"):
        return Result(True)
    setup = ops.download(VCREDIST_URL, "vc_redist.x64.exe", progress)
    if setup and ops.stream([str(setup), "/install", "/quiet", "/norestart"], elevate=True) in (0, 1638, 3010):  # 1638: já há versão mais nova; 3010: reiniciar
        return Result(True)
    return Result(False, 1, "Não consegui instalar o Visual C++. Baixe em https://aka.ms/vs/17/release/vc_redist.x64.exe")
