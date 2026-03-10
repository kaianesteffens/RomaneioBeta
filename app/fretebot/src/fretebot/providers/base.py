from abc import ABC, abstractmethod
import asyncio
import os
import shutil
import socket
import subprocess
import tempfile

from fretebot.models import Cotacao
from fretebot.logging_conf import get_logger

_base_logger = get_logger(__name__)


# ---------------------------------------------------------------------------
#  Localizar Chrome instalado no sistema
# ---------------------------------------------------------------------------

def find_chrome() -> str:
    """Retorna o caminho do Google Chrome instalado. Levanta FileNotFoundError se nao encontrado."""
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%UserProfile%\AppData\Local\Google\Chrome\Application\chrome.exe"),
    ]
    try:
        import winreg
        for root_key in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                key = winreg.OpenKey(
                    root_key,
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
                )
                path, _ = winreg.QueryValueEx(key, "")
                winreg.CloseKey(key)
                if path and os.path.isfile(path):
                    candidates.append(path)
            except OSError:
                pass
    except ImportError:
        pass
    candidates.extend(filter(None, [shutil.which("chrome"), shutil.which("google-chrome")]))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    raise FileNotFoundError("Google Chrome nao encontrado. Instale o Chrome para usar o FreteBot.")


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _kill_proc(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# ---------------------------------------------------------------------------
#  Wrapper transparente: Browser Playwright + subprocess Chrome
# ---------------------------------------------------------------------------

class _ChromeBrowser:
    """Proxy que delega tudo ao browser Playwright, mas ao fechar tambem encerra o subprocess."""

    def __init__(self, browser, process, profile_dir):
        self._inner = browser
        self._process = process
        self._profile_dir = profile_dir

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def close(self):
        try:
            await self._inner.close()
        except Exception:
            pass
        _kill_proc(self._process)
        try:
            shutil.rmtree(self._profile_dir, ignore_errors=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
#  Lancar Chrome como subprocess + conectar via CDP
# ---------------------------------------------------------------------------

async def launch_browser_resilient(playwright, *, headless: bool = True, args: list[str] | None = None):
    """Lanca Chrome local como subprocess e conecta via CDP.

    Nunca usa Chromium embutido do Playwright.
    """
    chrome_path = find_chrome()
    port = _find_free_port()
    profile_dir = tempfile.mkdtemp(prefix="fretebot_chrome_")

    launch_args = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
    ]
    if headless:
        launch_args.append("--headless=new")
    else:
        launch_args.extend(["--window-position=-3000,-3000", "--window-size=1,1"])

    if args:
        existing_keys = {a.split("=")[0] for a in launch_args}
        for a in args:
            if a.split("=")[0] not in existing_keys:
                launch_args.append(a)

    proc = subprocess.Popen(
        launch_args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    for _ in range(50):
        await asyncio.sleep(0.1)
        if proc.poll() is not None:
            raise RuntimeError(f"Chrome encerrou inesperadamente (exit code {proc.returncode})")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                break
        except (ConnectionRefusedError, OSError):
            continue
    else:
        _kill_proc(proc)
        raise RuntimeError(f"Chrome nao respondeu na porta {port} em 5s")

    browser = await playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    _base_logger.info("Chrome conectado via CDP porta %d (headless=%s)", port, headless)
    return _ChromeBrowser(browser, proc, profile_dir)


class ProviderBase(ABC):
    def __init__(self, nome: str) -> None:
        self.nome = nome

    @abstractmethod
    async def coteir(self, origem: str, destino: str, peso: float, valor: float) -> Cotacao | None:
        pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.nome})"
