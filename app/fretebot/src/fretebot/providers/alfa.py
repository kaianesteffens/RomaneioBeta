"""Provider Alfa Transportes via Playwright com login manual (Turnstile)."""
from __future__ import annotations

from datetime import datetime
import os
import re
import base64
import json
import shutil
import socket
import struct
import subprocess
import asyncio
import urllib.parse
import urllib.request
from typing import Optional, Any

from playwright.async_api import async_playwright

from fretebot.providers.base import ProviderBase
from fretebot.models import Cotacao
from fretebot.logging_conf import get_logger

logger = get_logger(__name__)


class AlfaProvider(ProviderBase):
    """Provider Alfa com login manual e cotacao automatizada."""

    BASE_URL = "https://arearestrita.alfatransportes.com.br"
    LOGIN_URL = "https://arearestrita.alfatransportes.com.br/login/"
    COTACAO_URL = "https://arearestrita.alfatransportes.com.br/cotacao/"
    COTACAO_API_URL = "https://arearestrita.alfatransportes.com.br/cotacao/api/"
    LOGIN_MAX_WAIT_S = 120

    def __init__(
        self,
        login: str,
        senha: str,
        login_url: str = "",
        cotacao_url: str = "",
        headless: bool = False,
    ) -> None:
        super().__init__(nome="ALFA")
        self.login = str(login or "").strip()
        self.senha = str(senha or "").strip()
        self.headless = bool(headless)
        self.login_url = str(login_url or self.LOGIN_URL).strip()

        if cotacao_url and "/api/" in str(cotacao_url):
            self.cotacao_api_url = str(cotacao_url).strip()
            self.cotacao_url = self.COTACAO_URL
        else:
            self.cotacao_url = str(cotacao_url or self.COTACAO_URL).strip()
            self.cotacao_api_url = self.COTACAO_API_URL

        self.last_error: str | None = None
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._logged_in = False
        self._cdp_session = None
        self._window_id = None
        self._chrome_proc = None
        self._debug_port = 0

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _digits(value: str) -> str:
        return re.sub(r"\D", "", str(value or ""))

    @staticmethod
    def _fmt_decimal(value: float, decimals: int = 2, comma: bool = True) -> str:
        txt = f"{float(value):.{decimals}f}"
        return txt.replace(".", ",") if comma else txt

    @staticmethod
    def _parse_decimal_any(raw: Any) -> float | None:
        txt = re.sub(r"[^\d,\.\-]", "", str(raw or "").strip())
        if not txt:
            return None
        if "," in txt and "." in txt:
            if txt.rfind(",") > txt.rfind("."):
                txt = txt.replace(".", "").replace(",", ".")
            else:
                txt = txt.replace(",", "")
        elif "," in txt:
            txt = txt.replace(".", "").replace(",", ".")
        try:
            return float(txt)
        except ValueError:
            return None

    @staticmethod
    def _parse_int_any(raw: Any) -> int:
        m = re.search(r"\d+", str(raw or ""))
        return int(m.group(0)) if m else 0

    @staticmethod
    def _format_doc(value: str) -> str:
        digits = re.sub(r"\D", "", str(value or ""))
        if len(digits) == 11:
            return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
        if len(digits) == 14:
            return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
        return digits

    @staticmethod
    def _calc_cubagem_m3(cubagens: Optional[list[dict]]) -> float:
        total = 0.0
        if not isinstance(cubagens, list):
            return total
        for row in cubagens:
            if not isinstance(row, dict):
                continue
            try:
                qtd = int(row.get("quantidade", 0) or 0)
                comp = float(row.get("comprimento_cm", 0) or 0)
                larg = float(row.get("largura_cm", 0) or 0)
                alt = float(row.get("altura_cm", 0) or 0)
            except Exception:
                continue
            if qtd <= 0 or comp <= 0 or larg <= 0 or alt <= 0:
                continue
            total += (comp * larg * alt / 1_000_000.0) * qtd
        return total

    @staticmethod
    def _sum_volumes(cubagens: Optional[list[dict]], fallback: int) -> int:
        if not isinstance(cubagens, list):
            return int(fallback or 0)
        total = 0
        for row in cubagens:
            if not isinstance(row, dict):
                continue
            try:
                qtd = int(row.get("quantidade", 0) or 0)
            except Exception:
                qtd = 0
            total += max(qtd, 0)
        return total if total > 0 else int(fallback or 0)

    @staticmethod
    def _today_str() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    # ── browser lifecycle ─────────────────────────────────────────────

    @staticmethod
    def _user_data_dir() -> str:
        base = os.path.join(os.path.expanduser("~"), ".fretebot", "alfa_browser_data")
        os.makedirs(base, exist_ok=True)
        return base

    @staticmethod
    def _find_chrome() -> str | None:
        """Localiza o executável do Chrome instalado no sistema."""
        candidates = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%UserProfile%\AppData\Local\Google\Chrome\Application\chrome.exe"),
        ]
        # Verifica também no registro (caso Chrome tenha sido instalado em path customizado)
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
        candidates.extend([shutil.which("chrome"), shutil.which("google-chrome")])
        for path in candidates:
            if path and os.path.isfile(path):
                return path
        return None

    @staticmethod
    def _free_port() -> int:
        """Encontra uma porta TCP livre."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    @staticmethod
    def _fix_preferences(user_data_dir: str) -> None:
        """Marca exit_type como Normal e limpa sessão para evitar 'Restaurar páginas'."""
        default_dir = os.path.join(user_data_dir, "Default")
        os.makedirs(default_dir, exist_ok=True)

        # Remove arquivos de sessão que disparam "Restaurar páginas?"
        for fname in ("Current Session", "Current Tabs", "Last Session", "Last Tabs"):
            fpath = os.path.join(default_dir, fname)
            try:
                if os.path.isfile(fpath):
                    os.remove(fpath)
            except Exception:
                pass
        sessions_dir = os.path.join(default_dir, "Sessions")
        if os.path.isdir(sessions_dir):
            shutil.rmtree(sessions_dir, ignore_errors=True)

        prefs_path = os.path.join(default_dir, "Preferences")
        prefs = {}
        if os.path.isfile(prefs_path):
            try:
                with open(prefs_path, "r", encoding="utf-8") as f:
                    prefs = json.load(f)
            except Exception:
                prefs = {}
        prefs.setdefault("profile", {})["exit_type"] = "Normal"
        prefs["profile"]["exited_cleanly"] = True
        prefs.setdefault("session", {})["restore_on_startup"] = 5
        try:
            with open(prefs_path, "w", encoding="utf-8") as f:
                json.dump(prefs, f)
        except Exception:
            pass

    @staticmethod
    def _kill_stale_chrome(user_data_dir: str) -> None:
        """Mata processos Chrome órfãos que usem o mesmo user-data-dir."""
        import sys
        if sys.platform != "win32":
            return
        try:
            # Usa PowerShell Get-CimInstance (wmic foi removido do Windows moderno)
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                 "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"],
                capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            norm = user_data_dir.replace("/", "\\").rstrip("\\").lower()
            for line in result.stdout.splitlines():
                line = line.strip()
                if "|" not in line:
                    continue
                pid_str, cmd = line.split("|", 1)
                if norm in cmd.lower():
                    try:
                        pid = int(pid_str.strip())
                        os.kill(pid, 9)
                        logger.debug("[ALFA] Matou Chrome órfão PID=%d", pid)
                    except (ValueError, OSError):
                        pass
        except Exception as e:
            logger.debug("[ALFA] _kill_stale_chrome falhou: %s", e)

    async def _init_browser(self) -> None:
        """Lança Chrome como subprocess SEM conectar Playwright (exceto headless)."""
        if self.headless:
            if self._context:
                return
            from fretebot.providers.base import launch_browser_resilient
            self._browser = await launch_browser_resilient(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            self._context = await self._browser.new_context(
                viewport={"width": 1920, "height": 1080},
                locale="pt-BR",
            )
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            self._page.set_default_timeout(45000)
            return

        # Não-headless: lança Chrome real como processo separado
        if self._chrome_proc and self._chrome_proc.poll() is None:
            return  # Chrome já está rodando

        # Chrome morreu ou nunca foi lançado — limpa estado Playwright antigo
        if self._chrome_proc and self._chrome_proc.poll() is not None:
            logger.warning("[ALFA] Chrome morreu (exit=%s), reiniciando...", self._chrome_proc.poll())
            await self._disconnect_playwright()
            self._chrome_proc = None

        chrome_path = self._find_chrome()
        if not chrome_path:
            raise RuntimeError("Chrome não encontrado no sistema")

        self._debug_port = self._free_port()
        user_data = self._user_data_dir()

        # Mata processos Chrome órfãos que ainda travem o user-data-dir
        self._kill_stale_chrome(user_data)

        self._fix_preferences(user_data)
        cmd = [
            chrome_path,
            f"--remote-debugging-port={self._debug_port}",
            f"--user-data-dir={user_data}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
            "--disable-features=InfiniteSessionRestore",
            "--hide-crash-restore-bubble",
            "--window-position=-3000,-3000",
            "--window-size=900,760",
            self.login_url,
        ]
        logger.info(f"[ALFA] Lançando Chrome real na porta {self._debug_port}")
        self._chrome_proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for _ in range(30):
            await asyncio.sleep(0.5)
            try:
                with socket.create_connection(("127.0.0.1", self._debug_port), timeout=1):
                    break
            except OSError:
                continue
        else:
            logger.warning("[ALFA] Timeout esperando Chrome debug port")

        # Oculta da barra de tarefas imediatamente (Chrome nasce off-screen)
        self._set_taskbar_visible(False)

    async def _connect_playwright(self) -> None:
        """Conecta Playwright ao Chrome já rodando via CDP."""
        if self._context:
            return
        last_err = None
        for _attempt in range(3):
            try:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{self._debug_port}"
                )
                self._context = self._browser.contexts[0]
                self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
                self._page.set_default_timeout(30000)
                logger.info("[ALFA] Playwright conectado ao Chrome via CDP")
                return
            except Exception as e:
                last_err = e
                logger.warning("[ALFA] CDP tentativa %d/3 falhou: %s", _attempt + 1, e)
                try:
                    if self._playwright:
                        await self._playwright.stop()
                        self._playwright = None
                except Exception:
                    pass
                if _attempt < 2:
                    await asyncio.sleep(1 + _attempt)
        raise RuntimeError(f"[ALFA] Falha ao conectar CDP apos 3 tentativas: {last_err}")

    async def _disconnect_playwright(self) -> None:
        """Desconecta Playwright do Chrome sem fechar o navegador."""
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._context = None
        self._browser = None
        self._playwright = None
        self._page = None
        self._cdp_session = None
        self._window_id = None

    def _get_page_url_sync(self) -> str:
        """Obtém URL da aba ativa via Chrome DevTools HTTP API (sem Playwright)."""
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{self._debug_port}/json",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                pages = json.loads(resp.read())
            for page in pages:
                if page.get("type") == "page":
                    url = page.get("url", "")
                    if url and url != "about:blank":
                        return url
        except Exception:
            pass
        return ""

    async def _cdp_eval_raw(self, expression: str) -> None:
        """Executa JavaScript via CDP WebSocket direto (sem Playwright).

        Conecta, envia Runtime.evaluate, desconecta imediatamente.
        Isso NÃO deixa rastros de automação para o Turnstile detectar.
        """
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{self._debug_port}/json",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                pages = json.loads(resp.read())
            ws_url = None
            for page in pages:
                if page.get("type") == "page":
                    ws_url = page.get("webSocketDebuggerUrl")
                    break
            if not ws_url:
                return

            parsed = urllib.parse.urlparse(ws_url)
            host = parsed.hostname
            port = parsed.port
            path = parsed.path

            key = base64.b64encode(os.urandom(16)).decode()
            reader, writer = await asyncio.open_connection(host, port)
            handshake = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n"
                f"\r\n"
            )
            writer.write(handshake.encode())
            await writer.drain()

            response = b""
            while b"\r\n\r\n" not in response:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=5)
                if not chunk:
                    break
                response += chunk
            if b"101" not in response:
                writer.close()
                return

            msg = json.dumps({
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {"expression": expression},
            })
            await self._ws_send_text(writer, msg)
            await asyncio.sleep(0.3)

            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"[ALFA] CDP eval raw falhou: {e}")

    @staticmethod
    async def _ws_send_text(writer, text: str) -> None:
        """Envia um frame de texto WebSocket."""
        data = text.encode("utf-8")
        mask_key = os.urandom(4)
        frame = bytearray()
        frame.append(0x81)  # FIN + opcode text
        length = len(data)
        if length <= 125:
            frame.append(0x80 | length)
        elif length <= 65535:
            frame.append(0x80 | 126)
            frame.extend(struct.pack(">H", length))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack(">Q", length))
        frame.extend(mask_key)
        masked = bytearray(len(data))
        for i in range(len(data)):
            masked[i] = data[i] ^ mask_key[i % 4]
        frame.extend(masked)
        writer.write(bytes(frame))
        await writer.drain()

    def _set_taskbar_visible(self, visible: bool) -> None:
        """Mostra/oculta a janela do Chrome na barra de tarefas (Windows)."""
        import sys
        if sys.platform != "win32" or not self._chrome_proc:
            return
        try:
            import ctypes
            import ctypes.wintypes as wt

            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_APPWINDOW = 0x00040000
            SW_HIDE = 0
            SW_SHOWNOACTIVATE = 4

            root_pid = self._chrome_proc.pid
            pids = {root_pid}
            # Busca TODOS os descendentes (multi-nível) do processo Chrome
            try:
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f"function Get-Desc($p){{ Get-CimInstance Win32_Process -Filter \"ParentProcessId=$p\" | "
                     f"ForEach-Object {{ $_.ProcessId; Get-Desc $_.ProcessId }} }}; Get-Desc {root_pid}"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                for line in result.stdout.splitlines():
                    stripped = line.strip()
                    if stripped.isdigit():
                        pids.add(int(stripped))
            except Exception:
                pass

            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
            count = [0]

            def _enum_cb(hwnd, _lparam):
                # Verifica todas as janelas (visíveis E ocultas que possam reaparecer)
                tid = wt.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(tid))
                if tid.value in pids:
                    # Só processa janelas top-level com texto (não Chrome internals)
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0 or user32.IsWindowVisible(hwnd):
                        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                        if visible:
                            nex = (ex | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
                        else:
                            nex = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
                        if nex != ex:
                            user32.ShowWindow(hwnd, SW_HIDE)
                            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, nex)
                            user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                            count[0] += 1
                return True

            user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
            logger.debug("[ALFA] Taskbar visibility=%s (%d janelas)", visible, count[0])
        except Exception as e:
            logger.debug(f"[ALFA] _set_taskbar_visible falhou: {e}")

    def _ensure_chrome_visible(self) -> None:
        """Garante que a janela do Chrome esteja visível para o usuário (Win32)."""
        import sys
        if sys.platform != "win32" or not self._chrome_proc:
            return
        try:
            import ctypes
            import ctypes.wintypes as wt

            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_APPWINDOW = 0x00040000
            SW_RESTORE = 9
            SWP_SHOWWINDOW = 0x0040

            root_pid = self._chrome_proc.pid
            pids = {root_pid}
            try:
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f"function Get-Desc($p){{ Get-CimInstance Win32_Process -Filter \"ParentProcessId=$p\" | "
                     f"ForEach-Object {{ $_.ProcessId; Get-Desc $_.ProcessId }} }}; Get-Desc {root_pid}"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                for line in result.stdout.splitlines():
                    stripped = line.strip()
                    if stripped.isdigit():
                        pids.add(int(stripped))
            except Exception:
                pass

            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
            found = []

            def _enum_cb(hwnd, _lparam):
                tid = wt.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(tid))
                if tid.value in pids:
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        # Filtra janelas pequenas (popups/dialogs como "Restaurar páginas?")
                        rect = wt.RECT()
                        user32.GetWindowRect(hwnd, ctypes.byref(rect))
                        w = rect.right - rect.left
                        h = rect.bottom - rect.top
                        if w >= 500 and h >= 400:
                            found.append(hwnd)
                        elif user32.IsWindowVisible(hwnd):
                            # Esconde popups/dialogs do Chrome
                            user32.ShowWindow(hwnd, 0)  # SW_HIDE
                            logger.debug("[ALFA] Ocultou popup Chrome %dx%d", w, h)
                return True

            user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)

            KEYEVENTF_EXTENDEDKEY = 0x0001
            KEYEVENTF_KEYUP = 0x0002
            VK_MENU = 0x12
            kernel32 = ctypes.windll.kernel32

            for hwnd in found:
                ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                nex = (ex | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
                if nex != ex:
                    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, nex)
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.SetWindowPos(hwnd, 0, 200, 100, 900, 760, SWP_SHOWWINDOW)
                # AttachThreadInput + keybd_event trick para Win10/11
                fg_hwnd = user32.GetForegroundWindow()
                fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
                our_tid = kernel32.GetCurrentThreadId()
                attached = False
                if fg_tid != our_tid:
                    attached = bool(user32.AttachThreadInput(our_tid, fg_tid, True))
                user32.keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY, 0)
                user32.keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
                if attached:
                    user32.AttachThreadInput(our_tid, fg_tid, False)

            logger.debug("[ALFA] Chrome janela posicionada para visivel (%d janelas)", len(found))
        except Exception as e:
            logger.debug(f"[ALFA] _ensure_chrome_visible falhou: {e}")

    def _move_window_win32(self, left: int, top: int, width: int, height: int) -> bool:
        """Move a janela do Chrome via Win32 API (fallback quando CDP falha)."""
        import sys
        if sys.platform != "win32" or not self._chrome_proc:
            return False
        try:
            import ctypes
            import ctypes.wintypes as wt

            user32 = ctypes.windll.user32
            SWP_NOACTIVATE = 0x0010

            root_pid = self._chrome_proc.pid
            pids = {root_pid}
            try:
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f"function Get-Desc($p){{ Get-CimInstance Win32_Process -Filter \"ParentProcessId=$p\" | "
                     f"ForEach-Object {{ $_.ProcessId; Get-Desc $_.ProcessId }} }}; Get-Desc {root_pid}"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                for line in result.stdout.splitlines():
                    stripped = line.strip()
                    if stripped.isdigit():
                        pids.add(int(stripped))
            except Exception:
                pass

            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
            moved = [0]

            def _enum_cb(hwnd, _lparam):
                tid = wt.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(tid))
                if tid.value in pids:
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        rect = wt.RECT()
                        user32.GetWindowRect(hwnd, ctypes.byref(rect))
                        w = rect.right - rect.left
                        h = rect.bottom - rect.top
                        if w >= 400 and h >= 300:
                            user32.SetWindowPos(hwnd, 0, left, top, width, height, SWP_NOACTIVATE)
                            moved[0] += 1
                return True

            user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
            return moved[0] > 0
        except Exception as e:
            logger.debug(f"[ALFA] _move_window_win32 falhou: {e}")
            return False

    async def _ocultar_janela(self) -> None:
        if self.headless:
            return
        cdp_ok = False
        try:
            if not self._cdp_session:
                self._cdp_session = await self._context.new_cdp_session(self._page)
            if not self._window_id:
                resp = await self._cdp_session.send("Browser.getWindowForTarget")
                self._window_id = resp.get("windowId")
            if self._window_id:
                await self._cdp_session.send(
                    "Browser.setWindowBounds",
                    {"windowId": self._window_id, "bounds": {"windowState": "normal"}},
                )
                await self._cdp_session.send(
                    "Browser.setWindowBounds",
                    {
                        "windowId": self._window_id,
                        "bounds": {"left": -3000, "top": -3000, "width": 1920, "height": 1080},
                    },
                )
                cdp_ok = True
                logger.debug("[ALFA] Janela movida off-screen via CDP")
        except Exception as e:
            logger.debug(f"[ALFA] CDP ocultar falhou (esperado em modo subprocess): {e}")
        if not cdp_ok:
            if self._move_window_win32(-3000, -3000, 1920, 1080):
                logger.debug("[ALFA] Janela movida off-screen via Win32")
        self._set_taskbar_visible(False)

    async def _mostrar_janela(self) -> None:
        if self.headless:
            return
        w, h = 900, 760
        left = max((1920 - w) // 2, 0)
        top = max((1080 - h) // 2, 0)
        cdp_ok = False
        try:
            if not self._cdp_session:
                self._cdp_session = await self._context.new_cdp_session(self._page)
            if not self._window_id:
                resp = await self._cdp_session.send("Browser.getWindowForTarget")
                self._window_id = resp.get("windowId")
            if self._window_id:
                await self._cdp_session.send(
                    "Browser.setWindowBounds",
                    {"windowId": self._window_id, "bounds": {"windowState": "normal"}},
                )
                await self._cdp_session.send(
                    "Browser.setWindowBounds",
                    {"windowId": self._window_id, "bounds": {"left": left, "top": top, "width": w, "height": h}},
                )
                await self._page.bring_to_front()
                cdp_ok = True
                logger.debug("[ALFA] Janela visivel para login via CDP")
        except Exception as e:
            logger.debug(f"[ALFA] CDP mostrar falhou (esperado em modo subprocess): {e}")
        if not cdp_ok:
            if self._move_window_win32(left, top, w, h):
                logger.debug("[ALFA] Janela visivel para login via Win32")
        self._set_taskbar_visible(True)
        self._ensure_chrome_visible()

    async def cleanup(self) -> None:
        try:
            if self._page and not self._page.is_closed():
                await self._page.close()
        except Exception:
            pass
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        try:
            if self._browser and self._browser.is_connected():
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        # Encerra o Chrome lançado como subprocess de forma limpa
        if self._chrome_proc:
            # Tenta fechar via CDP (graceful) para evitar "Restaurar páginas"
            if self._debug_port:
                try:
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{self._debug_port}/json/version",
                        headers={"Accept": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=2) as resp:
                        data = json.loads(resp.read())
                    ws_url = data.get("webSocketDebuggerUrl")
                    if ws_url:
                        # Envia Browser.close via CDP
                        parsed = urllib.parse.urlparse(ws_url)
                        key = base64.b64encode(os.urandom(16)).decode()
                        reader, writer = await asyncio.open_connection(
                            parsed.hostname, parsed.port
                        )
                        handshake = (
                            f"GET {parsed.path} HTTP/1.1\r\n"
                            f"Host: {parsed.hostname}:{parsed.port}\r\n"
                            f"Upgrade: websocket\r\n"
                            f"Connection: Upgrade\r\n"
                            f"Sec-WebSocket-Key: {key}\r\n"
                            f"Sec-WebSocket-Version: 13\r\n"
                            f"\r\n"
                        )
                        writer.write(handshake.encode())
                        await writer.drain()
                        response = b""
                        while b"\r\n\r\n" not in response:
                            chunk = await asyncio.wait_for(reader.read(4096), timeout=3)
                            if not chunk:
                                break
                            response += chunk
                        if b"101" in response:
                            msg = json.dumps({"id": 1, "method": "Browser.close", "params": {}})
                            await self._ws_send_text(writer, msg)
                            await asyncio.sleep(1)
                        writer.close()
                        try:
                            await writer.wait_closed()
                        except Exception:
                            pass
                except Exception:
                    pass
            # Garante encerramento
            try:
                self._chrome_proc.terminate()
                self._chrome_proc.wait(timeout=5)
            except Exception:
                try:
                    self._chrome_proc.kill()
                except Exception:
                    pass
            # Marca como encerrado limpo
            try:
                self._fix_preferences(self._user_data_dir())
            except Exception:
                pass
        self._context = None
        self._browser = None
        self._playwright = None
        self._page = None
        self._logged_in = False
        self._cdp_session = None
        self._window_id = None
        self._chrome_proc = None

    # ── login ─────────────────────────────────────────────────────────

    async def _save_debug_screenshot(self, suffix: str = "") -> None:
        """Salva screenshot para diagnóstico em ~/.fretebot/alfa_debug/."""
        try:
            debug_dir = os.path.join(os.path.expanduser("~"), ".fretebot", "alfa_debug")
            os.makedirs(debug_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"alfa_{ts}_{suffix}.png" if suffix else f"alfa_{ts}.png"
            fpath = os.path.join(debug_dir, fname)
            if self._page:
                await self._page.screenshot(path=fpath, full_page=True)
                logger.info("[ALFA] Screenshot salvo: %s", fpath)
        except Exception as e:
            logger.debug("[ALFA] Falha ao salvar screenshot: %s", e)

    async def _navegar_para_cotacao(self) -> bool:
        """Navega para o formulário de cotação: card 'Painel de Cotações' → botão 'Nova Cotação'."""
        page = self._page
        try:
            current = page.url.lower()
            logger.info("[ALFA] _navegar_para_cotacao — URL atual: %s", page.url)

            # Se já está na página com o formulário, não precisa navegar
            if "cotacao" in current and "login" not in current:
                try:
                    await page.wait_for_selector("#tipoPagador", timeout=2000)
                    logger.info("[ALFA] Já estava na página de cotação com formulário pronto")
                    return True
                except Exception:
                    logger.debug("[ALFA] URL contém 'cotacao' mas #tipoPagador não encontrado")

            # Após cotação anterior, pode ter botão "Fazer outra Cotação"
            try:
                btn_outra = page.locator("a[href='/cotacao/api/']").first
                if await btn_outra.is_visible(timeout=1500):
                    await btn_outra.click()
                    logger.info("[ALFA] Clicou em 'Fazer outra Cotação'")
                    try:
                        await page.wait_for_selector("#tipoPagador", timeout=8000)
                        return True
                    except Exception:
                        pass
            except Exception:
                pass

            # Tenta navegação direta pela URL da cotação (mais rápido que menu)
            logger.info("[ALFA] Navegação direta para %s", self.cotacao_api_url)
            try:
                await page.goto(self.cotacao_api_url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(800)
                try:
                    await page.wait_for_selector("#tipoPagador", timeout=8000)
                    logger.info("[ALFA] Formulário encontrado via URL direta")
                    return True
                except Exception:
                    logger.debug("[ALFA] URL direta não encontrou formulário, tentando via menu...")
            except Exception as e:
                logger.debug("[ALFA] Navegação direta falhou: %s", e)

            # Fallback: volta à página base e navega via menu de cards
            await page.goto(self.BASE_URL, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1000)

            # Clicar no card "Painel de Cotações" via JS (combina todas as tentativas)
            card_clicked = await page.evaluate("""() => {
                // Tenta card com class 'opcoes'
                const cards = document.querySelectorAll('div.opcoes');
                for (const c of cards) {
                    const txt = (c.textContent || '').toLowerCase();
                    if (txt.includes('painel') && txt.includes('cota')) {
                        c.click();
                        return true;
                    }
                }
                // Fallback: qualquer link/botão contendo cotação
                const links = document.querySelectorAll('a, button, div[onclick], div.opcoes');
                for (const el of links) {
                    const txt = (el.textContent || '').toLowerCase();
                    if (txt.includes('cota') && (txt.includes('painel') || txt.includes('frete'))) {
                        el.click();
                        return true;
                    }
                }
                return false;
            }""")

            if not card_clicked:
                logger.warning("[ALFA] Não encontrou card 'Painel de Cotações'")
                await self._save_debug_screenshot("card_nao_encontrado")
                return False

            logger.info("[ALFA] Clicou no card 'Painel de Cotações'")
            await page.wait_for_timeout(1000)

            # Clicar em "Nova Cotação" via JS
            nova_clicked = await page.evaluate("""() => {
                const a = document.querySelector('a[href*="/cotacao/api"]');
                if (a) { a.click(); return true; }
                const links = document.querySelectorAll('a');
                for (const el of links) {
                    const txt = (el.textContent || '').toLowerCase();
                    if (txt.includes('nova') && txt.includes('cota')) {
                        el.click();
                        return true;
                    }
                }
                return false;
            }""")

            if not nova_clicked:
                logger.warning("[ALFA] Não encontrou botão 'Nova Cotação'")
                await self._save_debug_screenshot("nova_cotacao_nao_encontrado")
                return False

            logger.info("[ALFA] Clicou em 'Nova Cotação'")

            # Espera formulário renderizar
            try:
                await page.wait_for_selector("#tipoPagador", timeout=10000)
                return True
            except Exception:
                logger.warning("[ALFA] Formulário não renderizou após navegação por menu")
                await self._save_debug_screenshot("formulario_nao_renderizou")
                return False
        except Exception as e:
            logger.warning("[ALFA] _navegar_para_cotacao falhou com exceção: %s", e)
            return False

    async def _is_logged_in(self) -> bool:
        """Verifica se está autenticado navegando até cotação via menu."""
        page = self._page
        try:
            current_url = page.url.lower()
            logger.info("[ALFA] _is_logged_in — URL atual: %s", page.url)
            if "login" in current_url:
                logger.info("[ALFA] Ainda na página de login")
                return False
            return await self._navegar_para_cotacao()
        except Exception as e:
            logger.warning("[ALFA] _is_logged_in check falhou: %s", e)
            return False

    async def _login(self) -> bool:
        if self._logged_in:
            return True

        await self._init_browser()

        # ── Headless: login direto via Playwright (Turnstile não funciona) ──
        if self.headless:
            page = self._page
            await page.goto(self.login_url, wait_until="domcontentloaded", timeout=60000)
            await page.locator("#username").fill(self.login)
            await page.locator("#password").fill(self.senha)
            try:
                await page.locator("#btn-enviar").click(timeout=5000)
            except Exception:
                pass
            await page.wait_for_timeout(5000)
            if "login" not in page.url.lower():
                self._logged_in = True
                return True
            self.last_error = "Login Alfa falhou (headless, Turnstile bloqueou)"
            return False

        # ── Não-headless ──
        # Espera a página carregar (apenas no primeiro launch)
        chrome_was_running = self._chrome_proc is not None and self._chrome_proc.poll() is None
        if not chrome_was_running:
            await asyncio.sleep(2)

        # Verifica se já está logado (sessão persistente do user-data-dir)
        current_url = self._get_page_url_sync()
        if current_url and self.BASE_URL.lower() in current_url.lower() and "login" not in current_url.lower():
            await self._connect_playwright()
            if await self._is_logged_in():
                self._logged_in = True
                await self._ocultar_janela()
                self._set_taskbar_visible(False)
                return True

        # Turnstile necessário: desconecta Playwright
        await self._disconnect_playwright()

        # Preenche login/senha via CDP bruto (sem Playwright = sem detecção)
        fill_js = (
            "(function(){"
            f"var u=document.querySelector('#username');"
            f"var p=document.querySelector('#password');"
            f"if(u){{u.value={json.dumps(self.login)};u.dispatchEvent(new Event('input',{{bubbles:true}}));}}"
            f"if(p){{p.value={json.dumps(self.senha)};p.dispatchEvent(new Event('input',{{bubbles:true}}));}}"
            "})();"
        )
        await self._cdp_eval_raw(fill_js)
        logger.info("[ALFA] Credenciais preenchidas via CDP direto (sem Playwright)")

        # Tenta clicar submit (Turnstile pode auto-resolver para browsers conhecidos)
        click_js = "(function(){var b=document.querySelector('#btn-enviar');if(b){b.click();}})();"
        await self._cdp_eval_raw(click_js)

        # Aguarda até 15s por auto-pass do Turnstile (janela fica oculta)
        logger.info("[ALFA] Tentando auto-login (sem janela)...")
        auto_pass = False
        for _ in range(30):
            await asyncio.sleep(0.5)
            url = self._get_page_url_sync()
            if url and self.BASE_URL.lower() in url.lower() and "login" not in url.lower():
                auto_pass = True
                break

        if not auto_pass:
            # Auto-pass falhou — mostra janela para resolução manual do Turnstile
            self._ensure_chrome_visible()
            self._set_taskbar_visible(True)
            logger.info("[ALFA] Aguardando usuario resolver Turnstile e clicar Continuar...")

            for _ in range(self.LOGIN_MAX_WAIT_S):
                await asyncio.sleep(0.5)
                url = self._get_page_url_sync()
                if url and self.BASE_URL.lower() in url.lower() and "login" not in url.lower():
                    break
            else:
                self.last_error = "Login Alfa timeout (aguardando login manual)"
                logger.error(f"[ALFA] {self.last_error}")
                return False

        # Login OK — espera a página estabilizar antes de conectar Playwright
        logger.info("[ALFA] Login detectado! Aguardando p\u00e1gina estabilizar...")
        await asyncio.sleep(1)

        # Conecta Playwright (Turnstile já passou)
        await self._connect_playwright()
        # Oculta janela e taskbar após login
        await self._ocultar_janela()
        self._set_taskbar_visible(False)

        # Verifica se o formulário de cotação renderizou (com retry)
        for attempt in range(2):
            if await self._is_logged_in():
                self._logged_in = True
                logger.info("[ALFA] Login OK — Playwright conectado após Turnstile")
                return True
            logger.info(f"[ALFA] Formul\u00e1rio n\u00e3o renderizou (tentativa {attempt+1}/2), aguardando...")
            await asyncio.sleep(1)

        self._logged_in = False
        self.last_error = "Login realizado mas formulário não renderizou após 2 tentativas"
        logger.error(f"[ALFA] {self.last_error}")
        return False

    async def pre_login(self) -> None:
        await self._init_browser()
        try:
            await self._login()
        except Exception as e:
            logger.warning(f"[ALFA] Pre-login falhou: {e}")

    # ── cotacao ───────────────────────────────────────────────────────

    async def _preencher_formulario(
        self,
        *,
        cnpj_remetente: str,
        cnpj_destinatario: str,
        cep_remetente: str,
        cep_destinatario: str,
        peso: float,
        valor: float,
        volumes: int,
        cubagem_m3: float,
        tipo_pagador: str = "1",
    ) -> None:
        page = self._page

        # Navega para cotação via menu (não por URL direta)
        logger.info("[ALFA] Iniciando navegação para formulário de cotação...")
        if not await self._navegar_para_cotacao():
            # Se caiu na tela de login, precisa relogar
            if "login" in page.url.lower():
                logger.info("[ALFA] Sessão expirou, refazendo login...")
                self._logged_in = False
                if not await self._login():
                    raise RuntimeError("Login Alfa falhou")
                # Garante janela oculta após re-login por sessão expirada
                if not self.headless:
                    await self._ocultar_janela()
                    self._set_taskbar_visible(False)

            if not await self._navegar_para_cotacao():
                logger.error("[ALFA] Navegação para cotação falhou")
                await self._save_debug_screenshot("navegacao_falhou")
                raise RuntimeError("Não conseguiu navegar para cotação")

        # Espera o formulário renderizar
        await page.wait_for_selector("#tipoPagador", timeout=10000)

        await page.select_option("#tipoPagador", tipo_pagador)
        await page.wait_for_timeout(400)

        # Preenche todos os campos via JS de uma vez (mais rápido que fills individuais)
        await page.evaluate(
            """(data) => {
                function setVal(sel, val) {
                    const el = document.querySelector(sel);
                    if (!el) return;
                    el.value = val;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }
                function setSelect(sel, val) {
                    const el = document.querySelector(sel);
                    if (!el) return;
                    el.value = val;
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }
                setVal('#pesoMercadoria', data.peso);
                setVal('#valorMercadoria', data.valor);
                setVal('#dataInicialColeta', data.data);
                setVal('#totalVolumes', data.volumes);
                setVal('#totalCubagem', data.cubagem);
                setSelect('#tipoCarga', '0');
                setSelect('#tipoZona', '0');
            }""",
            {
                "peso": self._fmt_decimal(peso, 3, comma=True),
                "valor": self._fmt_decimal(valor, 2, comma=True),
                "data": self._today_str(),
                "volumes": str(int(volumes or 0)),
                "cubagem": self._fmt_decimal(cubagem_m3, 3, comma=False),
            },
        )

        # Preenche CNPJs e CEPs por último para não ser sobrescrito pelo Angular
        await page.wait_for_timeout(300)
        await page.evaluate(
            """(data) => {
                function setVal(sel, val) {
                    const el = document.querySelector(sel);
                    if (!el) return;
                    el.value = val;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.dispatchEvent(new Event('blur', {bubbles: true}));
                }
                setVal('#cnpjRemetente', data.cnpjRem);
                setVal('#cepRemetente', data.cepRem);
                setVal('#cnpjDestinatario', data.cnpjDest);
                setVal('#cepDestinatario', data.cepDest);
            }""",
            {
                "cnpjRem": self._format_doc(cnpj_remetente),
                "cepRem": self._digits(cep_remetente),
                "cnpjDest": self._format_doc(cnpj_destinatario),
                "cepDest": self._digits(cep_destinatario),
            },
        )

    async def _do_submit_click(self, submit_btn) -> None:
        """Clica no botão submit com fallbacks."""
        try:
            await submit_btn.click(timeout=8000)
        except Exception:
            logger.warning("[ALFA] Click normal falhou, tentando via JS...")
            await self._page.evaluate("document.querySelector(\"button[type='submit']\")?.click()")

    async def _extrair_resultado(self, api_response=None) -> Optional[Cotacao]:
        page = self._page

        valor_frete = None
        prazo_dias = 0

        # Tenta extrair da response da API (já capturada durante o click)
        if api_response is not None and asyncio.iscoroutine(api_response):
            api_response = await api_response
        if api_response is not None and api_response.ok:
            try:
                data = await api_response.json()
                valor_frete = self._find_json_value(data, ["frete", "valor", "total"])
                prazo_dias = int(self._find_json_value(data, ["prazo", "dia", "dias"]) or 0)
            except Exception:
                valor_frete = None
                prazo_dias = 0

        # Fallback: extrai do DOM (resultado já está renderizado)
        if valor_frete is None:
            await page.wait_for_timeout(500)
            body_txt = await page.inner_text("body")
            body_txt = (body_txt or "").replace("\xa0", " ")
            m_val = re.search(r"R\$\s*([\d.]+,\d{2})", body_txt)
            if m_val:
                valor_frete = self._parse_decimal_any(m_val.group(1))
            m_prazo = re.search(r"(\d+)\s*dias?", body_txt, re.IGNORECASE)
            if m_prazo:
                prazo_dias = int(m_prazo.group(1))

        if valor_frete is None:
            self.last_error = "ALFA: valor de frete nao encontrado"
            return None

        return Cotacao(
            transportadora=self.nome,
            prazo_dias=int(prazo_dias or 0),
            valor_frete=round(float(valor_frete), 2),
            restricoes="Cotacao via portal Alfa",
            timestamp=datetime.now(),
        )

    def _find_json_value(self, data: Any, keys: list[str]) -> float | None:
        if isinstance(data, dict):
            for k, v in data.items():
                k_low = str(k).lower()
                if any(key in k_low for key in keys):
                    parsed = self._parse_decimal_any(v)
                    if parsed is not None:
                        return parsed
                nested = self._find_json_value(v, keys)
                if nested is not None:
                    return nested
        elif isinstance(data, list):
            for item in data:
                nested = self._find_json_value(item, keys)
                if nested is not None:
                    return nested
        return None

    async def coteir(
        self,
        origem: str,
        destino: str,
        peso: float,
        valor: float,
        volumes: int = 1,
        cubagem_m3: float = 0.0,
        comprimento_cm: int = 0,
        largura_cm: int = 0,
        altura_cm: int = 0,
        cnpj_remetente: str = "",
        cnpj_destinatario: str = "",
        cubagens: Optional[list[dict]] = None,
        tipo_pagador: str = "1",
    ) -> Optional[Cotacao]:
        try:
            self.last_error = None
            if not await self._login():
                return None

            # Garante que a janela está oculta após login (mesmo que Turnstile tenha sido resolvido)
            if not self.headless:
                await self._ocultar_janela()
                self._set_taskbar_visible(False)

            vol_total = self._sum_volumes(cubagens, volumes)
            cub_total = self._calc_cubagem_m3(cubagens)
            if cub_total <= 0:
                if cubagem_m3 and float(cubagem_m3) > 0:
                    cub_total = float(cubagem_m3)
                elif comprimento_cm > 0 and largura_cm > 0 and altura_cm > 0 and vol_total > 0:
                    cub_total = (float(comprimento_cm) * float(largura_cm) * float(altura_cm) / 1_000_000.0) * vol_total

            await self._preencher_formulario(
                cnpj_remetente=cnpj_remetente,
                cnpj_destinatario=cnpj_destinatario,
                cep_remetente=origem,
                cep_destinatario=destino,
                peso=peso,
                valor=valor,
                volumes=vol_total,
                cubagem_m3=cub_total,
                tipo_pagador=tipo_pagador,
            )

            submit_btn = self._page.locator("button[type='submit']")
            try:
                await submit_btn.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass

            # Captura a response da API durante o click para não perder
            # respostas rápidas (causa raiz do delay de 30s)
            api_response = None
            try:
                async with self._page.expect_response(
                    lambda r: self.cotacao_api_url in r.url and r.request.method.upper() in {"POST", "GET"},
                    timeout=15000,
                ) as response_info:
                    await self._do_submit_click(submit_btn)
                api_response = response_info.value
                if asyncio.iscoroutine(api_response) or asyncio.isfuture(api_response):
                    api_response = await api_response
            except Exception:
                # Se expect_response falhar (timeout ou click falhou antes),
                # continua sem response — fallback DOM será usado
                pass

            return await self._extrair_resultado(api_response)

        except Exception as e:
            self.last_error = str(e)
            logger.error(f"[ALFA] Erro na cotacao: {e}")
            return None
