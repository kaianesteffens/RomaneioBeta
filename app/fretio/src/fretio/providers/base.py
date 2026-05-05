from abc import ABC, abstractmethod
import atexit
import asyncio
import ctypes
import os
import shutil
import socket
import subprocess
import tempfile
import threading
from ctypes import wintypes

from playwright.async_api import async_playwright

from fretio.models import Cotacao
from fretio.logging_conf import get_logger

_base_logger = get_logger(__name__)

_WINDOWS_JOB_LOCK = threading.Lock()
_WINDOWS_JOB_HANDLE = None
_FALLBACK_OWNED_PIDS: set[int] = set()
_BROWSER_SHUTDOWN_STARTED = threading.Event()


if os.name == "nt":
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]


    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]


    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


def _kill_pid_tree(pid: int) -> None:
    if pid <= 0:
        return
    # No Windows, taskkill /T mata a arvore inteira (pai + filhos).
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
        except Exception:
            pass
    try:
        os.kill(pid, 9)
    except Exception:
        pass


def _get_windows_job_handle():
    if os.name != "nt":
        return None
    global _WINDOWS_JOB_HANDLE
    if _WINDOWS_JOB_HANDLE is not None:
        return _WINDOWS_JOB_HANDLE
    with _WINDOWS_JOB_LOCK:
        if _WINDOWS_JOB_HANDLE is not None:
            return _WINDOWS_JOB_HANDLE
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job_handle = kernel32.CreateJobObjectW(None, None)
        if not job_handle:
            raise ctypes.WinError(ctypes.get_last_error())

        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            job_handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(job_handle)
            raise ctypes.WinError(ctypes.get_last_error())

        _WINDOWS_JOB_HANDLE = job_handle
        return _WINDOWS_JOB_HANDLE


def _register_owned_proc(proc, *, source: str = "chrome") -> None:
    if proc is None or proc.poll() is not None:
        return
    if os.name != "nt":
        return

    pid = proc.pid
    try:
        job_handle = _get_windows_job_handle()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        proc_handle = kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE,
            False,
            pid,
        )
        if not proc_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            ok = kernel32.AssignProcessToJobObject(job_handle, proc_handle)
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel32.CloseHandle(proc_handle)
    except Exception as exc:
        with _WINDOWS_JOB_LOCK:
            _FALLBACK_OWNED_PIDS.add(pid)
        _base_logger.warning(
            "Falha ao associar %s PID=%d a Job Object; usando fallback por PID: %s",
            source,
            pid,
            exc,
        )


def _forget_owned_proc(proc) -> None:
    if proc is None:
        return
    with _WINDOWS_JOB_LOCK:
        _FALLBACK_OWNED_PIDS.discard(getattr(proc, "pid", None))


def _cleanup_fallback_owned_procs() -> None:
    with _WINDOWS_JOB_LOCK:
        pids = list(_FALLBACK_OWNED_PIDS)
        _FALLBACK_OWNED_PIDS.clear()
    for pid in pids:
        _kill_pid_tree(pid)


def request_browser_shutdown() -> None:
    _BROWSER_SHUTDOWN_STARTED.set()


def browser_shutdown_requested() -> bool:
    return _BROWSER_SHUTDOWN_STARTED.is_set()


atexit.register(_cleanup_fallback_owned_procs)


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
    raise FileNotFoundError("Google Chrome nao encontrado. Instale o Chrome para usar o Fretio.")


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _kill_proc(proc):
    """Encerra processo Chrome e todos os seus filhos (tree kill)."""
    if proc is None or proc.poll() is not None:
        _forget_owned_proc(proc)
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    if proc.poll() is None:
        _kill_pid_tree(proc.pid)
    _forget_owned_proc(proc)


# ---------------------------------------------------------------------------
#  Wrapper transparente: Browser Playwright + subprocess Chrome
# ---------------------------------------------------------------------------

class _ChromeBrowser:
    """Proxy que delega tudo ao browser Playwright, mas ao fechar tambem encerra o subprocess.

    Se *owned_playwright* for passado, o close() tambem encerra o driver Node.js.
    """

    def __init__(self, browser, process, profile_dir, owned_playwright=None):
        self._inner = browser
        self._process = process
        self._profile_dir = profile_dir
        self._owned_pw = owned_playwright

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __del__(self):
        # Garante que o Chrome seja morto mesmo que close() nunca seja chamado
        # (ex: quando o GC roda após o event loop fechar)
        try:
            _kill_proc(self._process)
            self._process = None
        except Exception:
            pass

    async def close(self):
        try:
            await self._inner.close()
        except Exception:
            pass
        _kill_proc(self._process)
        self._process = None
        if self._owned_pw:
            try:
                await self._owned_pw.stop()
            except Exception:
                pass
            self._owned_pw = None
        try:
            shutil.rmtree(self._profile_dir, ignore_errors=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
#  Lancar Chrome como subprocess + conectar via CDP
# ---------------------------------------------------------------------------

async def launch_browser_resilient(playwright=None, *, headless: bool = True, args: list[str] | None = None):
    """Lanca Chrome local como subprocess e conecta via CDP.

    Nunca usa Chromium embutido do Playwright.
    Se *playwright* for None, cria e gerencia o driver Node.js internamente
    com ate 3 tentativas (resiliente a crash do driver).
    """
    if browser_shutdown_requested():
        raise RuntimeError("Encerramento do aplicativo em andamento; novos browsers estao bloqueados")
    chrome_path = find_chrome()
    manage_pw = playwright is None
    last_error = None

    for attempt in range(3 if manage_pw else 1):
        pw = None
        port = _find_free_port()
        profile_dir = tempfile.mkdtemp(prefix="fretio_chrome_")
        proc = None

        try:
            pw = await async_playwright().start() if manage_pw else playwright

            launch_args = [
                chrome_path,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--no-sandbox",
                "--disable-gpu",
                "--do-not-de-elevate",
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
            _register_owned_proc(proc, source="launch_browser_resilient")

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
                raise RuntimeError(f"Chrome nao respondeu na porta {port} em 5s")

            browser = await asyncio.wait_for(
                pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}"),
                timeout=15,
            )
            _base_logger.info("Chrome conectado via CDP porta %d (headless=%s)", port, headless)

            owned_pw = pw if manage_pw else None
            return _ChromeBrowser(browser, proc, profile_dir, owned_pw)

        except Exception as e:
            last_error = e
            _kill_proc(proc)
            shutil.rmtree(profile_dir, ignore_errors=True)
            if manage_pw and pw:
                try:
                    await pw.stop()
                except Exception:
                    pass
            if not manage_pw:
                raise
            if attempt < 2:
                _base_logger.warning(
                    "launch_browser_resilient tentativa %d/3 falhou: %s", attempt + 1, e
                )
                await asyncio.sleep(1 + attempt)

    raise last_error


class ProviderBase(ABC):
    def __init__(self, nome: str) -> None:
        self.nome = nome

    @abstractmethod
    async def coteir(self, origem: str, destino: str, peso: float, valor: float) -> Cotacao | None:
        pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.nome})"
