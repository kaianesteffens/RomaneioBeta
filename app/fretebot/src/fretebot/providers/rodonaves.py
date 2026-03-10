"""Provider Rodonaves (RTE) – seletores extraidos da gravação Playwright."""
from typing import Optional
import asyncio
import json
import os
import re
import socket
import subprocess
from playwright.async_api import async_playwright
from fretebot.providers.base import ProviderBase, find_chrome, _find_free_port, _kill_proc
from fretebot.providers._win_taskbar import ocultar_taskbar_por_pagina, trazer_janela_frente
from fretebot.models import Cotacao
from fretebot.logging_conf import get_logger

logger = get_logger(__name__)


import random

# User-Agent completo de Chrome real (evita sinal de bot no reCAPTCHA)
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)

# Script de stealth injetado antes de qualquer página carregar.
# Remove sinais de automação que o reCAPTCHA usa para escalar dificuldade.
_STEALTH_JS = """
// 1. navigator.webdriver = undefined
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// 2. Simula plugins reais (Chrome sem plugins = sinal de bot)
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const arr = [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format',
              0: { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format' }, length: 1 },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '',
              0: { type: 'application/pdf', suffixes: 'pdf', description: '' }, length: 1 },
            { name: 'Native Client', filename: 'internal-nacl-plugin', description: '',
              0: { type: 'application/x-nacl', suffixes: '', description: 'Native Client Executable' },
              1: { type: 'application/x-pnacl', suffixes: '', description: 'Portable Native Client Executable' }, length: 2 },
        ];
        arr.refresh = () => {};
        return arr;
    }
});

// 3. Simula mimeTypes
Object.defineProperty(navigator, 'mimeTypes', {
    get: () => {
        const arr = [
            { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format', enabledPlugin: {} },
            { type: 'application/pdf', suffixes: 'pdf', description: '', enabledPlugin: {} },
        ];
        return arr;
    }
});

// 4. languages coerente com locale pt-BR
Object.defineProperty(navigator, 'languages', { get: () => ['pt-BR', 'pt', 'en-US', 'en'] });

// 5. chrome.runtime (existe em Chrome real, ausente em Playwright)
if (!window.chrome) window.chrome = {};
if (!window.chrome.runtime) window.chrome.runtime = { id: undefined };
// chrome.app / chrome.csi para parecer Chrome real
if (!window.chrome.app) {
    window.chrome.app = {
        isInstalled: false,
        InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
        RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
        getDetails: () => null,
        getIsInstalled: () => false,
        installState: () => 'not_installed',
        runningState: () => 'cannot_run',
    };
}

// 6. Permissions.query — evita leak de "denied" para notification
const origQuery = window.navigator.permissions?.query?.bind(window.navigator.permissions);
if (origQuery) {
    window.navigator.permissions.query = (params) => {
        if (params.name === 'notifications') {
            return Promise.resolve({ state: Notification.permission });
        }
        return origQuery(params);
    };
}

// 7. Esconde detecção de CDP (Chrome DevTools Protocol)
// reCAPTCHA verifica window.cdc_adoQpoasnfa76pfcZLmcfl_* props
(function() {
    const props = Object.getOwnPropertyNames(window).filter(p => /^cdc_/.test(p));
    for (const p of props) { delete window[p]; }
})();

// 8. Falsifica connection.rtt (IP fingerprint via RTT)
if (navigator.connection) {
    Object.defineProperty(navigator.connection, 'rtt', { get: () => 50 });
}

// 9. Garante que screen dimensions são coerentes
Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
Object.defineProperty(screen, 'pixelDepth', { get: () => 24 });

// 10. navigator.hardwareConcurrency (automação frequentemente reporta 0 ou 2)
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });

// 11. navigator.deviceMemory (ausente em automação)
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

// 12. WebGL vendor/renderer (evita fingerprint de "SwiftShader" que denuncia headless)
(function() {
    const getParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(param) {
        if (param === 37445) return 'Google Inc. (NVIDIA)';    // UNMASKED_VENDOR_WEBGL
        if (param === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650, OpenGL 4.5)'; // UNMASKED_RENDERER_WEBGL
        return getParam.call(this, param);
    };
    if (typeof WebGL2RenderingContext !== 'undefined') {
        const getParam2 = WebGL2RenderingContext.prototype.getParameter;
        WebGL2RenderingContext.prototype.getParameter = function(param) {
            if (param === 37445) return 'Google Inc. (NVIDIA)';
            if (param === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650, OpenGL 4.5)';
            return getParam2.call(this, param);
        };
    }
})();

// 13. Falsifica Notification.permission (evita sinal de "default" em automação)
try {
    Object.defineProperty(Notification, 'permission', { get: () => 'default' });
} catch(e) {}

// 14. Remove sourceURL/sourceMapping headers que indicam Playwright
(function() {
    const origEval = window.eval;
    window.eval = function() {
        try { return origEval.apply(this, arguments); }
        catch(e) { throw e; }
    };
    window.eval.toString = () => 'function eval() { [native code] }';
})();
"""


class RodonavesProvider(ProviderBase):
    """Provider Rodonaves via portal cliente.rte.com.br (seletores gravados)."""

    PORTAL_URL = "https://cliente.rte.com.br/Quotation"
    CAPTCHA_MAX_WAIT_S = 120

    def __init__(
        self,
        dominio: str,
        usuario: str,
        senha: str,
        cnpj_pagador: str,
        login_url: str = "",
        cotacao_url: str = "",
        headless: bool = True,
    ):
        super().__init__(nome="RODONAVES")
        self.dominio = str(dominio or "").strip()
        self.usuario = str(usuario or "").strip()
        self.senha = str(senha or "").strip()
        self.cnpj_pagador = self._digits(cnpj_pagador)
        self.login_url = str(login_url or "").strip()
        self.cotacao_url = str(cotacao_url or "").strip()
        self.headless = bool(headless)
        self.last_error: str | None = None
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._logged_in = False
        self._cdp_session = None
        self._window_id = None
        self._chrome_proc = None

    # ── helpers ────────────────────────────────────────────────────────

    async def _simular_interacao_humana(self, page) -> None:
        """Simula mouse/scroll naturais para reduzir score de bot no reCAPTCHA."""
        import math
        vw = 820
        vh = 720

        # Curva Bézier entre dois pontos para movimento suave (não-linear)
        async def _bezier_move(x1, y1, x2, y2, steps=None):
            if steps is None:
                steps = random.randint(12, 25)
            # Ponto de controle aleatório para curvatura
            cx = (x1 + x2) / 2 + random.randint(-80, 80)
            cy = (y1 + y2) / 2 + random.randint(-60, 60)
            for i in range(steps + 1):
                t = i / steps
                x = (1 - t)**2 * x1 + 2 * (1 - t) * t * cx + t**2 * x2
                y = (1 - t)**2 * y1 + 2 * (1 - t) * t * cy + t**2 * y2
                await page.mouse.move(x, y)
                await page.wait_for_timeout(random.randint(5, 20))

        # Move mouse por pontos aleatórios (reCAPTCHA monitora eventos de mouse)
        px, py = random.randint(100, 300), random.randint(50, 150)
        await page.mouse.move(px, py)
        await page.wait_for_timeout(random.randint(200, 500))

        pontos = [
            (random.randint(200, vw - 200), random.randint(100, 250)),
            (random.randint(100, vw - 100), random.randint(250, 450)),
            (random.randint(150, vw - 150), random.randint(400, vh - 100)),
            (random.randint(80, 400), random.randint(150, 350)),
        ]
        for x, y in pontos:
            await _bezier_move(px, py, x, y)
            px, py = x, y
            await page.wait_for_timeout(random.randint(150, 500))

        # Scroll suave para baixo e volta (vários passos pequenos)
        for _ in range(random.randint(2, 4)):
            await page.mouse.wheel(0, random.randint(30, 80))
            await page.wait_for_timeout(random.randint(100, 300))
        await page.wait_for_timeout(random.randint(300, 700))
        for _ in range(random.randint(1, 3)):
            await page.mouse.wheel(0, -random.randint(20, 50))
            await page.wait_for_timeout(random.randint(100, 250))

        # Pausa final (reCAPTCHA mede tempo na página)
        await page.wait_for_timeout(random.randint(500, 1200))

    @staticmethod
    def _digits(value: str) -> str:
        return re.sub(r"\D", "", str(value or ""))

    @staticmethod
    def _format_cnpj(digits: str) -> str:
        d = re.sub(r"\D", "", str(digits or ""))
        if len(d) == 14:
            return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
        return d

    @staticmethod
    def _format_cep(digits: str) -> str:
        d = re.sub(r"\D", "", str(digits or ""))
        if len(d) == 8:
            return f"{d[:5]}-{d[5:]}"
        return d

    @staticmethod
    def _peso_str(cub: dict) -> str:
        """Peso por volume em kg (mínimo 1), arredondado para inteiro."""
        try:
            v = float(cub.get("peso_por_volume_kg") or 0.0)
            if v > 0:
                return str(max(1, round(v)))
        except Exception:
            pass
        raise RuntimeError("Peso por volume ausente no romaneio")

    @staticmethod
    def _normalizar_cubagens_cm(cubagens: Optional[list[dict]]) -> list[dict]:
        validas: list[dict] = []
        if not isinstance(cubagens, list):
            return validas
        for row in cubagens:
            if not isinstance(row, dict):
                continue
            try:
                qtd = int(row.get("quantidade", 0) or 0)
                comp = int(row.get("comprimento_cm", 0) or 0)
                larg = int(row.get("largura_cm", 0) or 0)
                alt = int(row.get("altura_cm", 0) or 0)
            except Exception:
                continue
            if qtd <= 0 or comp <= 0 or larg <= 0 or alt <= 0:
                continue
            peso_por_volume_kg = None
            try:
                peso_raw = row.get("peso_por_volume_kg", None)
                if peso_raw is not None:
                    peso_val = float(peso_raw)
                    if peso_val > 0:
                        peso_por_volume_kg = peso_val
            except Exception:
                peso_por_volume_kg = None
            validas.append(
                {
                    "quantidade": qtd,
                    "comprimento_cm": comp,
                    "largura_cm": larg,
                    "altura_cm": alt,
                    "peso_por_volume_kg": peso_por_volume_kg,
                }
            )
        return validas

    # ── browser lifecycle ──────────────────────────────────────────────

    @staticmethod
    def _user_data_dir() -> str:
        """Diretório persistente para cache do navegador (evita redownload)."""
        base = os.path.join(os.path.expanduser("~"), ".fretebot", "browser_data")
        os.makedirs(base, exist_ok=True)
        return base

    @staticmethod
    def _fix_preferences(user_data_dir: str) -> None:
        """Marca exit_type como Normal e limpa sessão para evitar 'Restaurar páginas'."""
        import shutil as _shutil
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
            _shutil.rmtree(sessions_dir, ignore_errors=True)

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
        import sys as _sys
        import subprocess as _subprocess
        if _sys.platform != "win32":
            return
        try:
            result = _subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                 "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"],
                capture_output=True, text=True, timeout=15,
                creationflags=_subprocess.CREATE_NO_WINDOW,
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
                        logger.debug("[RODONAVES] Matou Chrome órfão PID=%d", pid)
                    except (ValueError, OSError):
                        pass
        except Exception as e:
            logger.debug("[RODONAVES] _kill_stale_chrome falhou: %s", e)

    async def _init_browser(self):
        if self._context:
            if self._browser and not self._browser.is_connected():
                logger.warning(f"[{self.nome}] Browser desconectado, reinicializando...")
                await self.cleanup()
            else:
                return
        # Lanca Chrome como subprocess + conecta via CDP (sem Chromium)
        chrome_path = find_chrome()
        port = _find_free_port()

        if self.headless:
            import tempfile
            self._profile_tmp = tempfile.mkdtemp(prefix="fretebot_rodo_")
            udd = self._profile_tmp
        else:
            udd = self._user_data_dir()
            self._kill_stale_chrome(udd)
            self._fix_preferences(udd)
            self._profile_tmp = None

        launch_args = [
            chrome_path,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={udd}",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
        ]
        if self.headless:
            launch_args.append("--headless=new")
        else:
            launch_args.extend([
                "--window-position=-3000,-3000",
                "--window-size=1920,1080",
                "--disable-session-crashed-bubble",
                "--disable-features=InfiniteSessionRestore",
                "--hide-crash-restore-bubble",
                "--noerrdialogs",
                "--disable-infobars",
                "--enable-features=NetworkService,NetworkServiceInProcess",
                "--disable-features=IsolateOrigins,site-per-process,TranslateUI",
                "--disable-component-extensions-with-background-pages",
            ])

        self._chrome_proc = subprocess.Popen(
            launch_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        for _ in range(50):
            await asyncio.sleep(0.1)
            if self._chrome_proc.poll() is not None:
                raise RuntimeError(f"Chrome (Rodonaves) encerrou inesperadamente (exit {self._chrome_proc.returncode})")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                    break
            except (ConnectionRefusedError, OSError):
                continue
        else:
            _kill_proc(self._chrome_proc)
            raise RuntimeError(f"Chrome (Rodonaves) nao respondeu na porta {port} em 5s")

        # Conecta Playwright via CDP com retry (driver Node.js pode crashar)
        last_err = None
        for _attempt in range(3):
            try:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                logger.info(f"[{self.nome}] Chrome conectado via CDP porta {port} (headless={self.headless})")
                break
            except Exception as e:
                last_err = e
                logger.warning(f"[{self.nome}] CDP tentativa {_attempt+1}/3 falhou: {e}")
                try:
                    if self._playwright:
                        await self._playwright.stop()
                        self._playwright = None
                except Exception:
                    pass
                if _attempt < 2:
                    await asyncio.sleep(1 + _attempt)
        else:
            _kill_proc(self._chrome_proc)
            raise RuntimeError(f"Falha ao conectar CDP apos 3 tentativas: {last_err}")

        self._context = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=_CHROME_UA,
            locale="pt-BR",
        )

        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        if not self.headless:
            # Força janela off-screen via CDP (--window-position pode ser ignorado pelo persistent context)
            try:
                self._cdp_session = await self._context.new_cdp_session(self._page)
                resp = await self._cdp_session.send("Browser.getWindowForTarget")
                self._window_id = resp.get("windowId")
                if self._window_id:
                    await self._cdp_session.send("Browser.setWindowBounds", {
                        "windowId": self._window_id,
                        "bounds": {"windowState": "normal"},
                    })
                    await self._cdp_session.send("Browser.setWindowBounds", {
                        "windowId": self._window_id,
                        "bounds": {"left": -3000, "top": -3000, "width": 1920, "height": 1080},
                    })
            except Exception as e:
                logger.debug(f"[{self.nome}] Falha ao mover janela off-screen via CDP: {e}")
            await ocultar_taskbar_por_pagina(self._page)
        # Stealth script: injetar em todas as páginas (funciona também para persistent context)
        await self._context.add_init_script(_STEALTH_JS)
        # Injetar no primeiro page já existente (add_init_script só afeta navegações futuras)
        try:
            await self._page.evaluate(_STEALTH_JS)
        except Exception:
            pass

    async def _ocultar_janela(self):
        """Move a janela para coordenadas fora da tela (invisível)."""
        try:
            if not self._cdp_session:
                self._cdp_session = await self._context.new_cdp_session(self._page)
            if not self._window_id:
                resp = await self._cdp_session.send("Browser.getWindowForTarget")
                self._window_id = resp.get("windowId")
            if self._window_id:
                # Primeiro tira do modo maximizado (necessário para reposicionar)
                await self._cdp_session.send("Browser.setWindowBounds", {
                    "windowId": self._window_id,
                    "bounds": {"windowState": "normal"},
                })
                # Move para fora da tela com tamanho grande (página renderiza normalmente)
                await self._cdp_session.send("Browser.setWindowBounds", {
                    "windowId": self._window_id,
                    "bounds": {"left": -3000, "top": -3000, "width": 1920, "height": 1080},
                })
                logger.debug(f"[{self.nome}] Janela movida off-screen")
            await ocultar_taskbar_por_pagina(self._page)
        except Exception as e:
            logger.warning(f"[{self.nome}] Falha ao ocultar janela: {e}")

    async def _mostrar_janela(self):
        """Mostra janela compacta centralizada na tela, só para o CAPTCHA."""
        try:
            if not self._cdp_session:
                self._cdp_session = await self._context.new_cdp_session(self._page)
            if not self._window_id:
                resp = await self._cdp_session.send("Browser.getWindowForTarget")
                self._window_id = resp.get("windowId")
            if self._window_id:
                # Obtém resolução real da tela via JS
                try:
                    screen_info = await self._page.evaluate(
                        "() => ({ w: screen.width, h: screen.height })"
                    )
                    screen_w = int(screen_info.get("w", 1920))
                    screen_h = int(screen_info.get("h", 1080))
                except Exception:
                    screen_w, screen_h = 1920, 1080

                # Janela maior para reCAPTCHA (menos suspeita para detecção)
                w, h = 820, 720
                left = max(0, (screen_w - w) // 2)
                top = max(0, (screen_h - h) // 2)
                await self._cdp_session.send("Browser.setWindowBounds", {
                    "windowId": self._window_id,
                    "bounds": {"windowState": "normal"},
                })
                await self._cdp_session.send("Browser.setWindowBounds", {
                    "windowId": self._window_id,
                    "bounds": {"left": left, "top": top, "width": w, "height": h},
                })
                # Traz a janela para frente de todas as outras
                await self._page.bring_to_front()
                await trazer_janela_frente(self._page)
                logger.debug(f"[{self.nome}] Janela compacta (CAPTCHA) visível em ({left},{top}) tela {screen_w}x{screen_h}")

            # Scroll para o captcha/botão Calcular ficar visível
            try:
                await self._page.locator("#calculateQuotationBtn").scroll_into_view_if_needed()
            except Exception:
                pass
            try:
                captcha_iframe = self._page.locator("iframe[title*='reCAPTCHA'], iframe[src*='recaptcha']")
                if await captcha_iframe.count() > 0:
                    await captcha_iframe.first.scroll_into_view_if_needed()
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"[{self.nome}] Falha ao mostrar janela: {e}")

    async def cleanup(self):
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
        _kill_proc(self._chrome_proc)
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        # Marca sessão como limpa para evitar "Restaurar páginas?" na próxima abertura
        try:
            self._fix_preferences(self._user_data_dir())
        except Exception:
            pass
        # Limpa perfil temporario (headless)
        if getattr(self, "_profile_tmp", None):
            import shutil as _shutil
            _shutil.rmtree(self._profile_tmp, ignore_errors=True)
            self._profile_tmp = None
        self._context = None
        self._browser = None
        self._page = None
        self._playwright = None
        self._logged_in = False
        self._cdp_session = None
        self._window_id = None
        self._chrome_proc = None

    # ── login ──────────────────────────────────────────────────────────

    async def _navegar_cotacao(self):
        """Navega para /Quotation e aguarda o formulário ficar visível."""
        page = self._page

        # Verifica se o formulário já está na página atual
        try:
            await page.locator("#ReceiverTaxId").wait_for(timeout=3000)
            logger.info(f"[{self.nome}] Formulário já visível na página atual")
            return
        except Exception:
            pass

        logger.info(f"[{self.nome}] Navegando para /Quotation... URL atual: {page.url}")

        # Navega direto via goto (mais confiável que clicar em menus dropdown)
        await page.goto(
            "https://cliente.rte.com.br/Quotation",
            wait_until="domcontentloaded",
            timeout=60000,
        )

        # Verifica se sessão expirou (redirecionou para home/login)
        try:
            await page.locator("#ReceiverTaxId").wait_for(timeout=15000)
            logger.info(f"[{self.nome}] Formulário visível após goto /Quotation")
            return
        except Exception:
            pass

        # Sessão provavelmente expirou — detecta login modal ou redirect
        url_atual = page.url.lower()
        tem_login = await page.locator("#cpfcnp").count() > 0
        if tem_login or "showlogin" in url_atual or "/quotation" not in url_atual:
            logger.warning(f"[{self.nome}] Sessão expirada (URL: {page.url}), refazendo login...")
            self._logged_in = False
            await self._login()
            # Após re-login, _login() já chama _navegar_cotacao() internamente
            return

        raise RuntimeError(f"Formulário de cotação não carregou (URL: {page.url})")

    async def _login(self):
        if self._logged_in:
            return
        page = self._page
        logger.info(f"[{self.nome}] Iniciando login...")
        logger.info(f"[{self.nome}] URL atual: {page.url}")

        # Acessa página de login
        logger.info(f"[{self.nome}] Acessando página de login...")
        await page.goto(
            "https://cliente.rte.com.br/?showLogin=true",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        logger.info(f"[{self.nome}] URL após goto showLogin=true: {page.url}")

        # Aceitar cookies / banners
        try:
            btn_cookies = page.get_by_role("button", name=re.compile(r"Aceitar", re.IGNORECASE)).first
            if await btn_cookies.count() > 0:
                await btn_cookies.click(timeout=2000)
        except Exception:
            pass

        # Preencher CPF/CNPJ e Senha (IDs exatos do formulário)
        cpf_field = page.locator("#cpfcnp")

        # Tenta aguardar o campo ficar visível normalmente
        try:
            await cpf_field.wait_for(timeout=5000)
        except Exception:
            logger.warning(f"[{self.nome}] Campo #cpfcnp não visível, tentando abrir modal de login...")

            # Tenta clicar em botões/links que abram o modal de login
            login_triggers = [
                page.locator("a[href*='showLogin'], a[href*='login']").first,
                page.get_by_role("link", name=re.compile(r"Entrar|Login|Acessar", re.IGNORECASE)).first,
                page.get_by_role("button", name=re.compile(r"Entrar|Login|Acessar", re.IGNORECASE)).first,
                page.locator(".login-btn, .btn-login, #loginBtn, #btnLogin, [data-target*='login']").first,
            ]
            modal_aberto = False
            for trigger in login_triggers:
                try:
                    if await trigger.count() > 0:
                        await trigger.click(timeout=3000)
                        logger.info(f"[{self.nome}] Clicou trigger de login")
                        try:
                            await cpf_field.wait_for(timeout=5000)
                            modal_aberto = True
                            break
                        except Exception:
                            continue
                except Exception:
                    continue

            if not modal_aberto:
                # Fallback: força visibilidade via JS (modal pode estar display:none)
                logger.warning(f"[{self.nome}] Triggers não funcionaram, forçando visibilidade via JS...")
                try:
                    await page.evaluate("""() => {
                        const field = document.getElementById('cpfcnp');
                        if (!field) return;
                        let el = field;
                        while (el) {
                            if (el.style) {
                                el.style.display = '';
                                el.style.visibility = 'visible';
                                el.style.opacity = '1';
                            }
                            if (el.classList) {
                                el.classList.add('show', 'in', 'active');
                                el.classList.remove('hide', 'hidden', 'fade');
                            }
                            el = el.parentElement;
                        }
                        // Bootstrap modal: tenta abrir via jQuery/Bootstrap
                        if (window.jQuery) {
                            window.jQuery('.modal').modal('show');
                        }
                    }""")
                    await cpf_field.wait_for(timeout=5000)
                except Exception:
                    # Último recurso: mostra a janela para o usuário resolver
                    logger.error(f"[{self.nome}] Não foi possível abrir formulário de login, mostrando janela...")
                    await self._mostrar_janela()
                    await cpf_field.wait_for(timeout=30000)

        login_doc = self._digits(self.usuario) or self._digits(self.dominio) or self.cnpj_pagador
        await cpf_field.fill(login_doc)
        await page.locator("#passwordToLogin").fill(self.senha)

        # LGPD
        try:
            chk_lgpd = page.locator("#lgpAcceptTerms")
            if await chk_lgpd.count() > 0 and not await chk_lgpd.is_checked():
                await chk_lgpd.check()
        except Exception:
            pass

        # Fechar popup PTE (promoção) que intercepta cliques no botão Entrar
        try:
            popup_pte = page.locator("#popUpPTE")
            if await popup_pte.count() > 0:
                # Tenta fechar via botão de fechar do modal
                close_btn = popup_pte.locator("button.close, [data-dismiss='modal'], .btn-close").first
                if await close_btn.count() > 0:
                    await close_btn.click(timeout=3000)
                    logger.info(f"[{self.nome}] Popup PTE fechado via botão")
                else:
                    # Remove o modal via JS
                    await page.evaluate("""() => {
                        const popup = document.getElementById('popUpPTE');
                        if (popup) {
                            popup.classList.remove('in', 'show');
                            popup.style.display = 'none';
                            popup.setAttribute('aria-hidden', 'true');
                        }
                        // Remove backdrop do modal
                        document.querySelectorAll('.modal-backdrop').forEach(el => el.remove());
                        document.body.classList.remove('modal-open');
                        document.body.style.overflow = '';
                    }""")
                    logger.info(f"[{self.nome}] Popup PTE removido via JS")
                await page.wait_for_timeout(500)
        except Exception as e:
            logger.debug(f"[{self.nome}] Erro ao fechar popup PTE (ignorado): {e}")

        # Clica no botão Entrar (com force=True caso ainda haja overlay residual)
        try:
            await page.locator("#loginSubmit").click(timeout=10000)
        except Exception:
            logger.warning(f"[{self.nome}] Click normal no loginSubmit falhou, tentando force click...")
            try:
                await page.locator("#loginSubmit").click(force=True, timeout=10000)
            except Exception:
                logger.warning(f"[{self.nome}] Force click falhou, tentando via JS...")
                await page.evaluate("document.getElementById('loginSubmit')?.click()")
        logger.info(f"[{self.nome}] Botão Entrar clicado, aguardando redirecionamento...")

        # Aguarda redirecionamento completo
        await page.wait_for_load_state("networkidle", timeout=30000)
        logger.info(f"[{self.nome}] URL pós-login: {page.url}")

        # Verifica se já estamos na página de cotação (SPA redireciona automaticamente)
        # Senão, tenta navegação via menu ou reload
        await self._navegar_cotacao()

        self._logged_in = True
        logger.info(f"[{self.nome}] Login OK – formulário visível")

    async def pre_login(self):
        await self._init_browser()
        try:
            await self._login()
        except Exception as e:
            logger.warning(f"[{self.nome}] Pre-login falhou: {e}")
            await self.cleanup()

    # ── preenchimento do formulário (seletores por ID do HTML real) ───

    async def _preencher_cotacao(
        self,
        valor: float,
        cubagens: list[dict],
        cnpj_destinatario: str,
        cep_destino: str,
        cep_origem: str = "",
    ) -> None:
        page = self._page

        # Remove overlays que interceptam cliques (cookie banner, navbar fixa)
        try:
            await page.evaluate("""() => {
                const cookieBtn = document.getElementById('adopt-controller-button');
                if (cookieBtn) cookieBtn.remove();
                const cookieBanner = document.getElementById('cookie-banner');
                if (cookieBanner) cookieBanner.remove();
                const nav = document.getElementById('mainNav');
                if (nav) nav.style.position = 'relative';
            }""")
        except Exception:
            pass

        # ─── Contato ───
        await page.locator("#contactName").fill("DARLU IND")
        await page.locator("#contactPhoneNumber").fill("(54) 99999-9999")

        # ─── CEP origem ───
        if cep_origem:
            origin_zip = page.locator("#originZipCode")
            if await origin_zip.count() > 0:
                await origin_zip.fill(self._format_cep(cep_origem))
                await origin_zip.press("Tab")
                for _ in range(20):
                    await page.wait_for_timeout(500)
                    city_val = await page.locator("#originCity").input_value()
                    if city_val.strip():
                        break
                await page.wait_for_timeout(500)

        # ─── Destinatário ───
        await page.locator("#ReceiverTaxId").fill(self._format_cnpj(cnpj_destinatario))

        # ─── CEP destino ───
        await page.locator("#destinationZipCode").fill(self._format_cep(cep_destino))
        await page.locator("#destinationZipCode").press("Tab")
        # Aguarda auto-preenchimento de cidade/estado/bairro via API do CEP
        for _ in range(20):
            await page.wait_for_timeout(500)
            city_val = await page.locator("#destinationCity").input_value()
            if city_val.strip():
                break
        await page.wait_for_timeout(500)

        # Preencher Bairro se ficou vazio (campo obrigatório: DestinationAddress.District)
        district_loc = page.locator("#destinationDistrict")
        if await district_loc.count() > 0:
            district_val = (await district_loc.input_value()).strip()
            if not district_val:
                await district_loc.fill("Centro")
                logger.info(f"[{self.nome}] Bairro destino vazio, preenchido 'Centro'")

        # ─── Número destino ───
        await page.locator("#destinationNumber").fill("1")

        # ─── Valor NF ───
        # Campo #eletronicInvoiceValue tem máscara jQuery currency —
        # fill() seta value diretamente sem acionar a máscara, causando
        # interpretação incorreta. Usar type() digita char-a-char,
        # permitindo que a máscara processe corretamente (centavos à direita).
        nf_loc = page.locator("#eletronicInvoiceValue")
        nf_centavos = str(int(round(float(valor) * 100)))  # ex: 1500.50 → "150050"
        await nf_loc.click()
        await nf_loc.press("Control+a")
        await nf_loc.type(nf_centavos, delay=30)

        # ─── Tipo embalagem ───
        await page.locator("#packageType").select_option("1")

        # ─── Primeira linha de volume (IDs fixos: amountPacks1, height1, ...) ───
        primeiro = cubagens[0]
        await page.locator("#amountPacks1").fill(str(primeiro["quantidade"]))
        await page.locator("#height1").fill(str(primeiro["altura_cm"]))
        await page.locator("#width1").fill(str(primeiro["largura_cm"]))
        await page.locator("#length1").fill(str(primeiro["comprimento_cm"]))
        await page.locator("#weight1").fill(self._peso_str(primeiro))

        # ─── Linhas adicionais de volume ───
        for idx, linha in enumerate(cubagens[1:], start=2):
            await page.locator("#addPack").click()
            await page.wait_for_timeout(800)

            # Novos campos seguem padrão: amountPacks{n}, height{n}, ...
            await page.locator(f"#amountPacks{idx}").fill(str(linha["quantidade"]))
            await page.locator(f"#height{idx}").fill(str(linha["altura_cm"]))
            await page.locator(f"#width{idx}").fill(str(linha["largura_cm"]))
            await page.locator(f"#length{idx}").fill(str(linha["comprimento_cm"]))
            await page.locator(f"#weight{idx}").fill(self._peso_str(linha))

        # Reforça número destino (autocomplete pode sobrescrever)
        await page.locator("#destinationNumber").fill("1")
        await page.wait_for_timeout(300)

        # Simula pequenas interações naturais ao terminar de preencher o formulário.
        # Acumula score de comportamento humano que o reCAPTCHA analisa.
        try:
            await page.mouse.move(
                random.randint(300, 600), random.randint(200, 400),
                steps=random.randint(5, 12),
            )
            await page.wait_for_timeout(random.randint(300, 800))
            await page.mouse.wheel(0, random.randint(40, 100))
            await page.wait_for_timeout(random.randint(200, 600))
        except Exception:
            pass

    # ── submissão e extração de resultado ──────────────────────────────

    async def _submeter_e_extrair(self) -> Optional[Cotacao]:
        page = self._page
        self.last_error = None

        # ─── E-mail ───
        try:
            cb = page.locator("#cbSendEmail")
            await cb.scroll_into_view_if_needed(timeout=3000)
            await cb.click(timeout=3000)
        except Exception:
            # Fallback: clique via JS (checkbox pode estar encoberto por overlay)
            try:
                await page.evaluate("document.getElementById('cbSendEmail')?.click()")
            except Exception:
                logger.warning(f"[{self.nome}] Não conseguiu clicar no checkbox de e-mail")

        # ─── reCAPTCHA ───
        await self._mostrar_janela()
        await page.wait_for_timeout(300)
        try:
            await page.locator("#calculateQuotationBtn").scroll_into_view_if_needed()
            await page.wait_for_timeout(300)
        except Exception:
            pass
        logger.info(f"[{self.nome}] Janela compacta visível para CAPTCHA")

        # Simula interação natural antes de clicar no captcha:
        # mover mouse pela página, scroll suave, pausa aleatória.
        # reCAPTCHA monitora eventos de mouse/teclado para reduzir dificuldade.
        try:
            await self._simular_interacao_humana(page)
        except Exception:
            pass

        try:
            captcha_frame = page.frame_locator("iframe[title*='reCAPTCHA'], iframe[src*='recaptcha']")
            chk_captcha = captcha_frame.get_by_role("checkbox", name="Não sou um robô")
            if await chk_captcha.count() > 0:
                # Move o mouse para perto do checkbox antes de clicar (trajetória natural)
                try:
                    box = await chk_captcha.bounding_box()
                    if box:
                        # Aproxima de um ponto aleatório da página até o checkbox
                        start_x = random.randint(200, 500)
                        start_y = random.randint(200, 400)
                        await page.mouse.move(start_x, start_y, steps=random.randint(8, 15))
                        await page.wait_for_timeout(random.randint(200, 500))
                        # Move até perto do checkbox com pequeno offset aleatório
                        target_x = box["x"] + box["width"] / 2 + random.randint(-3, 3)
                        target_y = box["y"] + box["height"] / 2 + random.randint(-3, 3)
                        await page.mouse.move(target_x, target_y, steps=random.randint(15, 30))
                        await page.wait_for_timeout(random.randint(300, 800))
                except Exception:
                    pass
                await chk_captcha.click(timeout=5000)
                await page.wait_for_timeout(random.randint(1000, 2500))
        except Exception:
            pass

        async def _captcha_token() -> str:
            try:
                return str(await page.evaluate(
                    "() => { const el = document.querySelector('textarea[name=\"g-recaptcha-response\"]'); "
                    "return (el && el.value) ? String(el.value) : ''; }"
                ) or "")
            except Exception:
                return ""

        token = await _captcha_token()
        if not token.strip() and not self.headless:
            logger.warning(
                f"[{self.nome}] reCAPTCHA: resolva manualmente no navegador; aguardando até {self.CAPTCHA_MAX_WAIT_S}s..."
            )
            for _ in range(self.CAPTCHA_MAX_WAIT_S):
                await page.wait_for_timeout(1000)
                token = await _captcha_token()
                if token.strip():
                    break
            if not token.strip():
                self.last_error = "Rodonaves: timeout aguardando reCAPTCHA manual"
                return None

        # Oculta a janela novamente
        await self._ocultar_janela()
        logger.info(f"[{self.nome}] CAPTCHA resolvido, janela ocultada")

        # Log de debug: estado dos campos antes de submeter
        try:
            form_state = await page.evaluate("""() => {
                const fields = {};
                const ids = ['contactName', 'ReceiverTaxId', 'destinationZipCode', 'destinationNumber'];
                for (const id of ids) {
                    const el = document.getElementById(id);
                    fields[id] = el ? el.value : '(not found)';
                }
                // Captcha token
                const cap = document.querySelector('textarea[name="g-recaptcha-response"]');
                fields['captcha_token_len'] = cap && cap.value ? cap.value.length : 0;
                return fields;
            }""")
            logger.info(f"[{self.nome}] Estado do formulário antes de Calcular: {form_state}")
        except Exception as e:
            logger.warning(f"[{self.nome}] Não foi possível logar estado do formulário: {e}")

        # ─── Calcular ───
        calc_btn = page.locator("#calculateQuotationBtn")
        try:
            await calc_btn.wait_for(state="visible", timeout=15000)
        except Exception:
            logger.warning(f"[{self.nome}] Botão Calcular não visível, aguardando...")
            await page.wait_for_timeout(3000)
        for _click_attempt in range(3):
            try:
                await calc_btn.click(timeout=15000)
                break
            except Exception as click_err:
                if _click_attempt == 2:
                    raise
                logger.warning(f"[{self.nome}] Click no Calcular falhou (tentativa {_click_attempt+1}): {click_err}")
                await page.wait_for_timeout(2000)
        logger.info(f"[{self.nome}] Botão Calcular clicado, aguardando resultado...")

        # Aguarda resultado: td.col-result aparece quando a tabela de resultado renderiza
        try:
            await page.locator("td.col-result").first.wait_for(timeout=30000)
            logger.info(f"[{self.nome}] Resultado detectado (td.col-result)")
        except Exception:
            # Fallback: aguarda networkidle
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                await page.wait_for_timeout(3000)
            logger.info(f"[{self.nome}] Resultado nao detectado via td.col-result, tentando extracao")

        # ─── Extrair resultado ───
        valor_frete = None
        prazo_dias = 0

        # Obtém o texto da página para procurar o valor
        body_txt = await page.inner_text("body")
        body_norm = (body_txt or "").replace("\xa0", " ")

        # Verificar erro de validação da API (JSON com "errors")
        erro_validacao = re.search(r'Alerta\s*\{.*?"errors".*?\}', body_norm, re.DOTALL)
        if erro_validacao:
            self.last_error = f"Rodonaves: erro de validação - {erro_validacao.group(0)[:300]}"
            logger.error(f"[{self.nome}] {self.last_error}")
            return None

        # Estratégia 1: extrair da tabela de resultado via td.col-result
        try:
            col_results = page.locator("td.col-result")
            count = await col_results.count()
            for i in range(count):
                txt = (await col_results.nth(i).inner_text()).strip()
                # Valor do frete (R$ XX,XX)
                if valor_frete is None:
                    m_val = re.search(r"R\$\s*([\d.]+,\d{2})", txt)
                    if m_val:
                        valor_frete = float(m_val.group(1).replace(".", "").replace(",", "."))
                        continue
                # Prazo ("Até X dias úteis")
                if prazo_dias == 0:
                    m_prazo = re.search(r"(\d+)\s*dias?", txt, re.IGNORECASE)
                    if m_prazo:
                        prazo_dias = int(m_prazo.group(1))
        except Exception as e:
            logger.debug(f"[{self.nome}] Extração via td.col-result falhou: {e}")

        # Estratégia 2: busca por cells com role
        if valor_frete is None:
            try:
                cells = page.get_by_role("cell", name=re.compile(r"R\\$"))
                count = await cells.count()
                for i in range(count):
                    txt = await cells.nth(i).inner_text()
                    m = re.search(r"R\$\s*([\d.]+,\d{2})", txt)
                    if m:
                        valor_frete = float(m.group(1).replace(".", "").replace(",", "."))
                        break
            except Exception:
                pass

        # Estratégia 3: fallback no body inteiro
        if valor_frete is None:
            for m in re.finditer(r"R\$\s*([\d.]+,\d{2})", body_norm):
                trecho = body_norm[max(0, m.start() - 120): m.end() + 120].lower()
                if any(kw in trecho for kw in ("frete", "cotação", "cotacao", "total geral", "valor total", "prazo")):
                    try:
                        valor_frete = float(m.group(1).replace(".", "").replace(",", "."))
                        break
                    except ValueError:
                        continue

        if valor_frete is None:
            self.last_error = "Rodonaves: valor de frete não encontrado no resultado"
            logger.warning(f"[{self.nome}] {self.last_error}")
            logger.info(f"[{self.nome}] Trecho body: {body_norm[:1500]}")
            return None

        # Prazo fallback no body
        if prazo_dias == 0:
            m_prazo = re.search(r"(\d+)\s*dias?", body_norm, re.IGNORECASE)
            if m_prazo:
                prazo_dias = int(m_prazo.group(1))

        return Cotacao(
            transportadora=self.nome,
            prazo_dias=prazo_dias,
            valor_frete=round(float(valor_frete), 2),
            restricoes="Cotação via portal cliente.rte.com.br",
        )

    # ── orquestração ───────────────────────────────────────────────────

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
        preencher_cep_origem: bool = False,
    ) -> Optional[Cotacao]:
        try:
            self.last_error = None
            cubagens_cm = self._normalizar_cubagens_cm(cubagens)
            if cubagens_cm:
                soma = sum(int(c["quantidade"]) for c in cubagens_cm)
                if int(volumes or 0) > 0 and int(volumes) != soma:
                    self.last_error = f"VOL ({volumes}) diverge da soma das cubagens ({soma})"
                    logger.error(f"[{self.nome}] {self.last_error}")
                    return None
                volumes = soma
            elif volumes > 0 and comprimento_cm > 0 and largura_cm > 0 and altura_cm > 0:
                cubagens_cm = [
                    {
                        "quantidade": int(volumes),
                        "comprimento_cm": int(comprimento_cm),
                        "largura_cm": int(largura_cm),
                        "altura_cm": int(altura_cm),
                    }
                ]
            else:
                self.last_error = (
                    f"Cubagens ausentes/inválidas (volumes={volumes}, "
                    f"dims_cm={comprimento_cm}x{largura_cm}x{altura_cm})"
                )
                logger.error(f"[{self.nome}] {self.last_error}")
                return None

            cnpj_dest = self._digits(cnpj_destinatario)
            cep_dest = self._digits(destino)
            if len(cnpj_dest) != 14:
                raise RuntimeError("CNPJ do destinatário inválido")
            if len(cep_dest) != 8:
                raise RuntimeError("CEP de destino inválido")

            await self._init_browser()
            logger.info(f"[{self.nome}] Browser inicializado OK")
            await self._login()
            logger.info(f"[{self.nome}] Login OK")

            # Na segunda cotação em diante, navega de volta ao formulário
            await self._navegar_cotacao()

            cep_orig = self._digits(origem) if preencher_cep_origem else ""
            await self._preencher_cotacao(
                valor=valor,
                cubagens=cubagens_cm,
                cnpj_destinatario=cnpj_dest,
                cep_destino=cep_dest,
                cep_origem=cep_orig,
            )
            return await self._submeter_e_extrair()
        except Exception as error:
            self.last_error = str(error)
            logger.error(f"[{self.nome}] Erro na cotação: {error}")
            # Detectar browser morto e resetar para próxima tentativa
            browser_morto = False
            if self._browser and not self._browser.is_connected():
                browser_morto = True
            elif self._context and not self._browser and self._page:
                # Persistent context sem Browser separado: verificar se página responde
                try:
                    await self._page.evaluate("1")
                except Exception:
                    browser_morto = True
            if browser_morto:
                try:
                    await self.cleanup()
                except Exception:
                    pass
            return None
