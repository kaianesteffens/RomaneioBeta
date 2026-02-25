"""Provider TRD Transportes - Plataforma Senior X."""
from datetime import datetime
from typing import Optional, Any
import asyncio
import json
import re
import time
import tempfile
from pathlib import Path
from playwright.async_api import async_playwright, Frame
from fretebot.providers.base import ProviderBase
from fretebot.models import Cotacao
from fretebot.logging_conf import get_logger

logger = get_logger(__name__)


class TRDProvider(ProviderBase):
    """Provider TRD Transportes usando Playwright."""
    
    LOGIN_URL = "https://platform.senior.com.br/login/?redirectTo=https%3A%2F%2Fplatform.senior.com.br%2Fsenior-x%2F&tenant=trdtransportes.com"
    COTACAO_URL = "https://platform.senior.com.br/logistica-documentos/tms/documentos-frontend/#/cotacao/adicao"

    @staticmethod
    def _digits(value: str) -> str:
        return re.sub(r"\D", "", str(value or ""))

    @staticmethod
    def _fmt_peso_3casas(value: float) -> str:
        return f"{float(value):.3f}".replace(".", ",")

    @staticmethod
    def _erro_potencial_headless(msg: Optional[str]) -> bool:
        txt = str(msg or "").lower()
        if not txt:
            return False
        pistas = (
            "timeout",
            "não foi possível preencher",
            "na etapa 1",
            "na etapa 2",
            "etapa 2 não carregou",
            "locator.fill",
        )
        return any(p in txt for p in pistas)
    
    def __init__(self, email: str, senha: str, usar_cache: bool = True, headless: bool = True):
        super().__init__(nome="TRD")
        self.email = email
        self.senha = senha
        self.headless = bool(headless)
        self.last_error: str | None = None
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._logged_in = False
    
    _STEALTH_JS = """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer'},
                {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
                {name: 'Native Client', filename: 'internal-nacl-plugin'},
            ],
        });
        Object.defineProperty(navigator, 'languages', {get: () => ['pt-BR', 'pt', 'en-US', 'en']});
        if (navigator.connection) {
            try { Object.defineProperty(navigator.connection, 'rtt', {get: () => 50}); } catch {}
        }
        for (const k of Object.keys(window)) {
            if (/^cdc_/.test(k)) { try { delete window[k]; } catch {} }
        }
    """

    async def _init_browser(self):
        """Inicializa browser Playwright."""
        if self._browser:
            if self._browser.is_connected():
                return
            logger.warning(f"[{self.nome}] Browser desconectado, reinicializando...")
            await self.cleanup()
        
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            channel="chrome",
            headless=self.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--enable-features=NetworkService,NetworkServiceInProcess',
                '--disable-features=IsolateOrigins,site-per-process,TranslateUI',
            ]
        )
        self._context = await self._browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/133.0.0.0 Safari/537.36'
            ),
        )
        self._page = await self._context.new_page()
        await self._page.add_init_script(self._STEALTH_JS)
    
    async def _login(self):
        """Faz login na plataforma Senior X (até 2 tentativas p/ erros de rede)."""
        if self._logged_in:
            return True

        max_tentativas = 2
        for tentativa in range(1, max_tentativas + 1):
            try:
                logger.info(f"[{self.nome}] Fazendo login (tentativa {tentativa})...")
                await self._page.goto(self.LOGIN_URL, wait_until='domcontentloaded', timeout=60000)

                # Etapa 1: Email + clicar "Próximo"
                email_loc = self._page.locator('#username-input-field')
                await email_loc.wait_for(state='visible', timeout=30000)
                await self._page.wait_for_timeout(500)
                await email_loc.fill(self.email)
                await self._page.locator('#nextBtn').click()
                await self._page.wait_for_timeout(2000)

                # Etapa 2: Senha + clicar "Autenticar"
                pwd = self._page.locator('#password-input-field')
                await pwd.wait_for(state='visible', timeout=15000)
                await pwd.fill(self.senha)
                await self._page.wait_for_timeout(300)
                await self._page.locator('#loginbtn').click()

                await self._page.wait_for_timeout(5000)
                if 'senior-x/#/' in self._page.url:
                    logger.info(f"[{self.nome}] Login realizado com sucesso")
                    self._logged_in = True
                    return True

                self.last_error = f"Login falhou - URL: {self._page.url}"
                logger.error(f"[{self.nome}] Login falhou - URL: {self._page.url}")
                return False

            except Exception as e:
                is_net = any(k in str(e) for k in ("ERR_CONNECTION", "ERR_NAME", "ERR_TIMED_OUT", "net::"))
                if is_net and tentativa < max_tentativas:
                    logger.warning(f"[{self.nome}] Login tentativa {tentativa} falhou: {e}")
                    await asyncio.sleep(5)
                    continue
                self.last_error = f"Erro no login: {e}"
                logger.error(f"[{self.nome}] Erro no login após {tentativa} tentativa(s): {e}")
                return False
        return False
    
    async def pre_login(self):
        """Inicializa browser e faz login antecipadamente."""
        await self._init_browser()
        await self._login()

    async def cleanup(self):
        """Fecha o browser."""
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
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
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._logged_in = False

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
            validas.append(
                {
                    "quantidade": qtd,
                    "comprimento_cm": comp,
                    "largura_cm": larg,
                    "altura_cm": alt,
                }
            )
        return validas

    @staticmethod
    def _parse_monetary_value(raw: Any) -> Optional[float]:
        """Converte string/número monetário em float, aceitando formatos BR e EN."""
        if raw is None:
            return None
        if isinstance(raw, (int, float)):
            try:
                return float(raw)
            except Exception:
                return None

        txt = str(raw).strip()
        if not txt:
            return None
        txt = txt.replace("\xa0", " ")

        m = re.search(
            r"(-?\d{1,3}(?:[.\s]\d{3})*(?:[.,]\d{2,4})|-?\d+(?:[.,]\d{2,4}))",
            txt,
        )
        if not m:
            return None

        num = (m.group(1) or "").replace(" ", "")
        if not num:
            return None

        if "," in num and "." in num:
            if num.rfind(",") > num.rfind("."):
                num = num.replace(".", "").replace(",", ".")
            else:
                num = num.replace(",", "")
        elif "," in num:
            num = num.replace(".", "").replace(",", ".")
        elif "." in num:
            parts = num.split(".")
            if not parts or len(parts[-1]) not in (2, 3, 4):
                num = num.replace(".", "")

        try:
            return float(num)
        except Exception:
            return None

    @staticmethod
    def _extrair_valor_frete(texto: str, valor_mercadoria: float) -> Optional[float]:
        txt = (texto or "").replace("\xa0", " ")

        # Regra principal da TRD: o valor vem logo após "VALOR DA PRESTAÇÃO".
        marcador = re.search(r'(?i)valor\s+da\s+presta[çc][ãa]o\s*[:\-]?', txt)
        if marcador:
            trecho = txt[marcador.end(): marcador.end() + 140]
            m = re.search(
                r'(?i)(?:R\$\s*)?([\d]{1,3}(?:\.\d{3})*[.,]\d{2}|[\d]+[.,]\d{2})',
                trecho,
            )
            if m:
                val = TRDProvider._parse_monetary_value(m.group(1))
                if val is not None and 1 < val < 100000 and abs(val - float(valor_mercadoria or 0.0)) > 0.01:
                    return val

        for pat in [
            r'(?i)valor\s+(?:total\s+da\s+)?presta[çc][ãa]o\s*(?:R\$\s*)?([\d]{1,3}(?:\.\d{3})*[.,]\d{2}|[\d]+[.,]\d{2})',
            r'(?i)valor\s+do\s+frete\s*(?:R\$\s*)?([\d]{1,3}(?:\.\d{3})*[.,]\d{2}|[\d]+[.,]\d{2})',
            r'(?i)\bfrete\b[^\n\r]{0,90}?(?:R\$\s*)?([\d]{1,3}(?:\.\d{3})*[.,]\d{2}|[\d]+[.,]\d{2})',
        ]:
            m = re.search(pat, txt)
            if not m:
                continue
            val = TRDProvider._parse_monetary_value(m.group(1))
            if val is None:
                continue
            if 1 < val < 100000 and abs(val - float(valor_mercadoria or 0.0)) > 0.01:
                return val

        candidatos: list[tuple[int, int, float]] = []
        for m in re.finditer(r'R\$\s*([\d.,]+)', txt, re.IGNORECASE):
            val = TRDProvider._parse_monetary_value(m.group(1))
            if val is None:
                continue
            if not (1 < val < 100000):
                continue
            if abs(val - float(valor_mercadoria or 0.0)) <= 0.01:
                continue
            ini = max(0, m.start() - 90)
            fim = min(len(txt), m.end() + 90)
            janela = txt[ini:fim].lower()
            score = 0
            if "presta" in janela:
                score += 3
            if "frete" in janela:
                score += 2
            if "mercadoria" in janela:
                score -= 3
            candidatos.append((score, -m.start(), val))

        # Fallback sem "R$": procura números com duas casas em contexto de frete/prestação.
        for m in re.finditer(
            r'(?<!\d)([\d]{1,3}(?:[.\s]\d{3})*[.,]\d{2}|[\d]+[.,]\d{2})(?!\d)',
            txt,
            re.IGNORECASE,
        ):
            val = TRDProvider._parse_monetary_value(m.group(1))
            if val is None or not (1 < val < 100000):
                continue
            ini = max(0, m.start() - 100)
            fim = min(len(txt), m.end() + 100)
            janela = txt[ini:fim].lower()
            score = 0
            if "presta" in janela:
                score += 5
            if "frete" in janela:
                score += 4
            if "valor" in janela:
                score += 2
            if "mercadoria" in janela or "nota" in janela:
                score -= 4
            if score <= 0:
                continue
            if abs(val - float(valor_mercadoria or 0.0)) <= 0.01:
                score -= 1
            candidatos.append((score, -m.start(), val))

        if not candidatos:
            return None
        candidatos.sort(reverse=True)
        return float(candidatos[0][2])

    @staticmethod
    def _extrair_valor_frete_da_payloads(
        payloads: list[dict[str, Any]],
        valor_mercadoria: float,
    ) -> Optional[float]:
        """Tenta extrair valor de frete de respostas XHR/fetch capturadas."""
        if not payloads:
            return None

        def _walk(node: Any, path: str = ""):
            if isinstance(node, dict):
                for k, v in node.items():
                    next_path = f"{path}.{k}" if path else str(k)
                    yield from _walk(v, next_path)
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    next_path = f"{path}[{i}]"
                    yield from _walk(v, next_path)
            else:
                yield path, node

        candidatos: list[tuple[int, float]] = []
        for sample in payloads:
            url = str(sample.get("url", "") or "").lower()

            payload_json = sample.get("json")
            if isinstance(payload_json, (dict, list)):
                for path, value in _walk(payload_json):
                    val = TRDProvider._parse_monetary_value(value)
                    if val is None or not (1 < val < 100000):
                        continue
                    ctx = f"{url} {path}".lower()
                    score = 0
                    if "presta" in ctx:
                        score += 6
                    if "frete" in ctx:
                        score += 5
                    if "cotac" in ctx or "simul" in ctx:
                        score += 3
                    if "valor" in ctx:
                        score += 2
                    if "mercadoria" in ctx or "invoice" in ctx or "nota" in ctx or "produto" in ctx:
                        score -= 4
                    if abs(val - float(valor_mercadoria or 0.0)) <= 0.01:
                        score -= 1
                    if score > 0:
                        candidatos.append((score, float(val)))

            payload_text = sample.get("text")
            if isinstance(payload_text, str) and payload_text.strip():
                val_txt = TRDProvider._extrair_valor_frete(payload_text, valor_mercadoria)
                if val_txt is not None:
                    candidatos.append((3, float(val_txt)))

        if not candidatos:
            return None

        candidatos.sort(key=lambda item: (-item[0], abs(item[1] - float(valor_mercadoria or 0.0))))
        return float(candidatos[0][1])

    async def _preencher_cnpj_destinatario_js(self, frame: Frame, cnpj_digits: str) -> bool:
        """Fallback robusto para localizar/preencher CNPJ do destinatário na Etapa 1."""
        try:
            result = await frame.evaluate(
                """(cnpj) => {
                    const clean = (v) => (v || '').replace(/\\s+/g, ' ').trim();
                    const digits = (v) => String(v || '').replace(/\\D/g, '');
                    const toLower = (v) => clean(v).toLowerCase();
                    const isVisible = (el) => {
                        if (!el || !(el instanceof HTMLElement)) return false;
                        const r = el.getBoundingClientRect();
                        const st = window.getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
                    };
                    const trigger = (el, name) => {
                        try {
                            el.dispatchEvent(new Event(name, { bubbles: true }));
                        } catch {}
                    };
                    const setValueSafely = (el, value) => {
                        if (!el) return false;
                        try {
                            el.removeAttribute('readonly');
                            if (el.disabled) el.disabled = false;
                        } catch {}
                        try { el.focus(); } catch {}
                        try { el.click(); } catch {}

                        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                        try {
                            if (setter) setter.call(el, '');
                            else el.value = '';
                        } catch {
                            return false;
                        }
                        trigger(el, 'input');

                        for (const ch of String(value || '')) {
                            const next = `${el.value || ''}${ch}`;
                            if (setter) setter.call(el, next);
                            else el.value = next;
                            trigger(el, 'input');
                        }

                        trigger(el, 'change');
                        trigger(el, 'blur');
                        return digits(el.value).length >= 14;
                    };
                    const associatedLabelText = (el) => {
                        const parts = [];
                        const id = el.id || '';
                        if (id) {
                            const labels = document.querySelectorAll(`label[for="${CSS.escape(id)}"]`);
                            for (const l of labels) parts.push(clean(l.textContent || ''));
                        }
                        const parentLabel = el.closest('label');
                        if (parentLabel) parts.push(clean(parentLabel.textContent || ''));
                        const ariaLabelledBy = el.getAttribute('aria-labelledby') || '';
                        if (ariaLabelledBy) {
                            for (const ref of ariaLabelledBy.split(/\\s+/)) {
                                const node = document.getElementById(ref);
                                if (node) parts.push(clean(node.textContent || ''));
                            }
                        }
                        return clean(parts.join(' '));
                    };
                    const collectInputs = () => {
                        const out = [];
                        const seen = new Set();
                        const visit = (root) => {
                            if (!root || !root.querySelectorAll) return;
                            const found = root.querySelectorAll(
                                'input[type="text"], input[type="tel"], input[type="search"], input[type="number"], input:not([type])'
                            );
                            for (const el of found) {
                                if (!(el instanceof HTMLInputElement)) continue;
                                if (seen.has(el)) continue;
                                seen.add(el);
                                out.push(el);
                            }
                            const hosts = root.querySelectorAll('*');
                            for (const h of hosts) {
                                if (h && h.shadowRoot) visit(h.shadowRoot);
                            }
                        };
                        visit(document);
                        return out;
                    };
                    const scoreInput = (el) => {
                        const attrs = toLower(
                            `${el.id || ''} ${el.name || ''} ${el.placeholder || ''} ` +
                            `${el.getAttribute('aria-label') || ''} ${el.className || ''} ${el.getAttribute('data-testid') || ''}`
                        );
                        const wrapper = el.closest('div,section,form,tr,td,mat-form-field') || el.parentElement;
                        const wrapperTxt = toLower((wrapper?.innerText || '').slice(0, 320));
                        const labelTxt = toLower(associatedLabelText(el));
                        const txt = `${attrs} ${wrapperTxt} ${labelTxt}`;

                        let score = 0;
                        if (txt.includes('cpfoucnpj')) score += 5;
                        if (txt.includes('cnpj') || txt.includes('cpf')) score += 4;
                        if (txt.includes('destinat') || txt.includes('destino') || txt.includes('consignat') || txt.includes('recebed')) score += 9;
                        if (txt.includes('remet') || txt.includes('origem') || txt.includes('pagador') || txt.includes('tomador')) score -= 10;
                        if ((el.maxLength || 0) >= 14 && (el.maxLength || 0) <= 18) score += 2;

                        return score;
                    };

                    const candidates = collectInputs().filter((el) => isVisible(el));
                    if (!candidates.length) return { ok: false, reason: 'no_visible_inputs' };

                    // Tentativa 1: seletores explícitos conhecidos.
                    const explicitSelectors = [
                        'input[name*="cpfoucnpjdestinatario" i]',
                        'input[id*="cpfoucnpjdestinatario" i]',
                        'input[name*="destinat" i][name*="cnpj" i]',
                        'input[id*="destinat" i][id*="cnpj" i]',
                        'input[name*="dest" i][name*="cnpj" i]',
                        'input[id*="dest" i][id*="cnpj" i]',
                        'input[aria-label*="destinat" i][aria-label*="cnpj" i]',
                        'input[placeholder*="destinat" i][placeholder*="cnpj" i]',
                        'input[name*="destinat" i][name*="cpf" i]',
                        'input[id*="destinat" i][id*="cpf" i]',
                    ];
                    for (const sel of explicitSelectors) {
                        const node = document.querySelector(sel);
                        if (node && isVisible(node) && setValueSafely(node, cnpj)) {
                            return { ok: true, reason: 'explicit_selector', selector: sel };
                        }
                    }

                    // Tentativa 2: ranking heurístico.
                    const ranked = candidates
                        .map((el) => ({ el, score: scoreInput(el) }))
                        .sort((a, b) => b.score - a.score);
                    const top = ranked[0];
                    if (!top || top.score < 1) {
                        return { ok: false, reason: 'no_candidate', bestScore: top ? top.score : null };
                    }
                    const ok = setValueSafely(top.el, cnpj);
                    return {
                        ok,
                        reason: 'ranked',
                        bestScore: top.score,
                        valueLen: digits(top.el.value).length,
                    };
                }""",
                cnpj_digits,
            )
            return bool(isinstance(result, dict) and result.get("ok"))
        except Exception:
            return False

    async def _preencher_cnpj_destinatario_etapa1(self, cnpj_digits: str) -> bool:
        """Preenche o CNPJ destinatário tentando locator + fallback JS em todos os frames."""
        if not self._page:
            return False

        selectors = [
            "input[name*='cpfoucnpjdestinatario' i]",
            "input[id*='cpfoucnpjdestinatario' i]",
            "input[name*='destinat' i][name*='cnpj' i]",
            "input[id*='destinat' i][id*='cnpj' i]",
            "input[name*='dest' i][name*='cnpj' i]",
            "input[id*='dest' i][id*='cnpj' i]",
            "input[aria-label*='destinat' i][aria-label*='cnpj' i]",
            "input[placeholder*='destinat' i][placeholder*='cnpj' i]",
        ]

        frames: list[Frame] = []
        seen: set[int] = set()
        for frame in [self._page.main_frame, *self._page.frames]:
            fid = id(frame)
            if fid in seen:
                continue
            seen.add(fid)
            frames.append(frame)

        for frame in frames:
            # 1) Tenta preencher por seletores explícitos com APIs do Playwright.
            for sel in selectors:
                loc = frame.locator(sel).first
                try:
                    if await loc.count() == 0:
                        continue
                except Exception:
                    continue

                try:
                    await loc.click(timeout=1200)
                except Exception:
                    pass

                try:
                    await loc.press("Control+a")
                except Exception:
                    pass

                typed = False
                try:
                    await loc.fill(cnpj_digits, timeout=1800)
                    typed = True
                except Exception:
                    try:
                        await loc.type(cnpj_digits, delay=35, timeout=1800)
                        typed = True
                    except Exception:
                        typed = False

                if not typed:
                    continue

                try:
                    await loc.press("Tab")
                except Exception:
                    pass

                try:
                    value_now = await loc.input_value(timeout=1000)
                except Exception:
                    value_now = ""

                if len(self._digits(value_now)) >= 14:
                    return True

            # 2) Fallback robusto via evaluate (inclui shadow DOM).
            if await self._preencher_cnpj_destinatario_js(frame, cnpj_digits):
                return True

        return False

    async def _coletar_alertas_ui(self) -> list[str]:
        """Coleta mensagens de alerta/erro visíveis na página atual."""
        if not self._page:
            return []
        try:
            alerts = await self._page.evaluate(
                """() => {
                    const clean = (v) => (v || '').replace(/\\s+/g, ' ').trim();
                    const nodes = document.querySelectorAll(
                        '.alert, .alert-danger, .alert-warning, .mat-snack-bar-container, [role="alert"], .toast-message'
                    );
                    return Array.from(nodes)
                        .map((n) => clean(n.innerText || n.textContent))
                        .filter(Boolean)
                        .slice(0, 5);
                }"""
            )
            return alerts if isinstance(alerts, list) else []
        except Exception:
            return []

    async def _aguardar_etapa2_pronta(self, timeout_ms: int = 15000) -> bool:
        """Aguarda elementos típicos da etapa 2 ficarem disponíveis."""
        if not self._page:
            return False
        selectors = [
            "#quantidadeVolumes",
            "#alturaInput",
            "#larguraInput",
            "#comprimentoInput",
            "input[name*='peso' i], input[id*='peso' i], input[aria-label*='peso' i]",
            "button:has-text('Adicionar')",
        ]
        deadline = time.monotonic() + (max(timeout_ms, 1000) / 1000.0)
        while time.monotonic() < deadline:
            for sel in selectors:
                loc = self._page.locator(sel).first
                try:
                    if await loc.count() > 0 and await loc.is_visible(timeout=250):
                        return True
                except Exception:
                    continue
            await self._page.wait_for_timeout(300)
        return False

    async def _preencher_valor_mercadoria_etapa2(self, valor: float) -> bool:
        """Preenche o campo de valor da mercadoria na etapa 2 com fallback por seletor/heurística."""
        if not self._page:
            return False

        valor_br = f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        candidatos_valor = [f"R$ {valor_br}", valor_br]

        selectors = [
            "input[name*='valor' i][name*='merc' i]",
            "input[id*='valor' i][id*='merc' i]",
            "input[aria-label*='valor' i][aria-label*='merc' i]",
            "input[placeholder*='R$' i]",
            "input[aria-label='R$']",
            "input[aria-label*='R$' i]",
            "input[name*='valor' i]",
            "input[id*='valor' i]",
        ]

        for sel in selectors:
            loc = self._page.locator(sel).first
            try:
                if await loc.count() == 0:
                    continue
            except Exception:
                continue

            for texto in candidatos_valor:
                try:
                    await loc.click(timeout=1200)
                except Exception:
                    pass
                try:
                    await loc.press("Control+a")
                except Exception:
                    pass

                ok = False
                try:
                    await loc.fill(texto, timeout=1500)
                    ok = True
                except Exception:
                    try:
                        await loc.type(texto, delay=35, timeout=1500)
                        ok = True
                    except Exception:
                        ok = False
                if not ok:
                    continue

                try:
                    await loc.press("Tab")
                except Exception:
                    pass

                try:
                    atual = await loc.input_value(timeout=800)
                except Exception:
                    atual = ""
                if len(self._digits(atual)) >= 3:
                    return True

        # Fallback final via evaluate com heurística de contexto da etapa 2.
        try:
            result = await self._page.evaluate(
                """(payload) => {
                    const clean = (v) => (v || '').replace(/\\s+/g, ' ').trim();
                    const digits = (v) => String(v || '').replace(/\\D/g, '');
                    const isVisible = (el) => {
                        if (!el || !(el instanceof HTMLElement)) return false;
                        const r = el.getBoundingClientRect();
                        const st = window.getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
                    };
                    const setVal = (el, val) => {
                        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                        if (setter) setter.call(el, val);
                        else el.value = val;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        el.dispatchEvent(new Event('blur', { bubbles: true }));
                        return digits(el.value).length >= 3;
                    };
                    const inputs = Array.from(document.querySelectorAll(
                        'input[type="text"], input[type="tel"], input[type="search"], input[type="number"], input:not([type])'
                    )).filter((el) => isVisible(el));
                    if (!inputs.length) return false;
                    const score = (el) => {
                        const attrs = `${el.id || ''} ${el.name || ''} ${el.placeholder || ''} ${el.getAttribute('aria-label') || ''}`.toLowerCase();
                        const wrapper = el.closest('div,section,form,tr,td,mat-form-field') || el.parentElement;
                        const txt = clean((wrapper?.innerText || '').slice(0, 350)).toLowerCase();
                        let s = 0;
                        if (attrs.includes('valor')) s += 4;
                        if (attrs.includes('merc')) s += 6;
                        if (attrs.includes('peso')) s -= 6;
                        if (txt.includes('valor da mercadoria') || txt.includes('valor mercadoria')) s += 10;
                        if (txt.includes('peso')) s -= 6;
                        return s;
                    };
                    const ranked = inputs.map((el) => ({ el, s: score(el) })).sort((a, b) => b.s - a.s);
                    const target = ranked[0];
                    if (!target || target.s < 2) return false;
                    return setVal(target.el, payload.valorComSimbolo) || setVal(target.el, payload.valorSemSimbolo);
                }""",
                {"valorComSimbolo": candidatos_valor[0], "valorSemSimbolo": candidatos_valor[1]},
            )
            return bool(result)
        except Exception:
            return False

    async def _peso_preenchido_etapa2(self) -> bool:
        """Valida se o campo de peso da etapa 2 está preenchido."""
        if not self._page:
            return False
        try:
            return bool(
                await self._page.evaluate(
                    """() => {
                        const clean = (v) => (v || '').replace(/\\s+/g, ' ').trim();
                        const digits = (v) => String(v || '').replace(/\\D/g, '');
                        const isVisible = (el) => {
                            if (!el || !(el instanceof HTMLElement)) return false;
                            const r = el.getBoundingClientRect();
                            const st = window.getComputedStyle(el);
                            return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
                        };
                        const inputs = Array.from(document.querySelectorAll(
                            'input[type="text"], input[type="tel"], input[type="search"], input[type="number"], input:not([type])'
                        )).filter((el) => isVisible(el));
                        if (!inputs.length) return false;
                        const score = (el) => {
                            const attrs = `${el.id || ''} ${el.name || ''} ${el.placeholder || ''} ${el.getAttribute('aria-label') || ''}`.toLowerCase();
                            const wrapper = el.closest('div,section,form,tr,td,mat-form-field') || el.parentElement;
                            const txt = clean((wrapper?.innerText || '').slice(0, 380)).toLowerCase();
                            let s = 0;
                            if (attrs.includes('peso')) s += 10;
                            if (attrs.includes('kg')) s += 5;
                            if (txt.includes('peso')) s += 8;
                            if (txt.includes('kg')) s += 3;
                            if (attrs.includes('valor') || attrs.includes('merc')) s -= 10;
                            if (txt.includes('valor da mercadoria') || txt.includes('mercadoria')) s -= 8;
                            if (txt.includes('altura') || txt.includes('largura') || txt.includes('comprimento') || txt.includes('quantidade')) s -= 6;
                            return s;
                        };
                        const ranked = inputs.map((el) => ({ el, s: score(el) })).sort((a, b) => b.s - a.s);
                        const target = ranked[0];
                        if (!target || target.s < 1) return false;
                        return digits(target.el.value).length >= 2;
                    }"""
                )
            )
        except Exception:
            return False

    async def _preencher_peso_etapa2(self, peso: float) -> bool:
        """Preenche peso na etapa 2 com fallback por seletores e heurística."""
        if not self._page:
            return False

        peso_fmt = self._fmt_peso_3casas(peso)
        candidatos = [peso_fmt, peso_fmt.replace(",", "."), str(float(peso))]
        selectors = [
            "input[name*='peso' i]",
            "input[id*='peso' i]",
            "input[aria-label*='peso' i]",
            "input[placeholder*='kg' i]",
            "input[aria-label*='kg' i]",
            "input[placeholder='0,000']",
            "input[placeholder='0,00']",
            "input[placeholder='0.000']",
            "input[placeholder='0.00']",
        ]

        # Tentativas por seletor explícito.
        for sel in selectors:
            loc = self._page.locator(sel).first
            try:
                if await loc.count() == 0:
                    continue
            except Exception:
                continue

            for txt in candidatos:
                ok = False
                try:
                    await loc.click(timeout=1200)
                except Exception:
                    pass
                try:
                    await loc.press("Control+a")
                except Exception:
                    pass
                try:
                    await loc.fill(txt, timeout=1500)
                    ok = True
                except Exception:
                    try:
                        await loc.type(txt, delay=35, timeout=1500)
                        ok = True
                    except Exception:
                        ok = False
                if not ok:
                    continue
                try:
                    await loc.press("Tab")
                except Exception:
                    pass
                if await self._peso_preenchido_etapa2():
                    return True

        # Fallback por foco atual (caso Tab do valor tenha deixado no campo correto).
        try:
            await self._page.wait_for_timeout(150)
            await self._page.keyboard.type(peso_fmt, delay=35)
            await self._page.keyboard.press("Tab")
            if await self._peso_preenchido_etapa2():
                return True
        except Exception:
            pass

        # Fallback final via heurística de contexto em JS.
        try:
            result = await self._page.evaluate(
                """(payload) => {
                    const clean = (v) => (v || '').replace(/\\s+/g, ' ').trim();
                    const digits = (v) => String(v || '').replace(/\\D/g, '');
                    const isVisible = (el) => {
                        if (!el || !(el instanceof HTMLElement)) return false;
                        const r = el.getBoundingClientRect();
                        const st = window.getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
                    };
                    const setVal = (el, val) => {
                        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                        if (setter) setter.call(el, val);
                        else el.value = val;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        el.dispatchEvent(new Event('blur', { bubbles: true }));
                        return digits(el.value).length >= 2;
                    };
                    const inputs = Array.from(document.querySelectorAll(
                        'input[type="text"], input[type="tel"], input[type="search"], input[type="number"], input:not([type])'
                    )).filter((el) => isVisible(el));
                    if (!inputs.length) return false;
                    const score = (el) => {
                        const attrs = `${el.id || ''} ${el.name || ''} ${el.placeholder || ''} ${el.getAttribute('aria-label') || ''}`.toLowerCase();
                        const wrapper = el.closest('div,section,form,tr,td,mat-form-field') || el.parentElement;
                        const txt = clean((wrapper?.innerText || '').slice(0, 380)).toLowerCase();
                        let s = 0;
                        if (attrs.includes('peso')) s += 10;
                        if (attrs.includes('kg')) s += 5;
                        if (txt.includes('peso')) s += 8;
                        if (txt.includes('kg')) s += 3;
                        if (attrs.includes('valor') || attrs.includes('merc')) s -= 10;
                        if (txt.includes('valor da mercadoria') || txt.includes('mercadoria')) s -= 8;
                        if (txt.includes('altura') || txt.includes('largura') || txt.includes('comprimento') || txt.includes('quantidade')) s -= 6;
                        return s;
                    };
                    const ranked = inputs.map((el) => ({ el, s: score(el) })).sort((a, b) => b.s - a.s);
                    const target = ranked[0];
                    if (!target || target.s < 1) return false;
                    return (
                        setVal(target.el, payload.peso1) ||
                        setVal(target.el, payload.peso2) ||
                        setVal(target.el, payload.peso3)
                    );
                }""",
                {"peso1": candidatos[0], "peso2": candidatos[1], "peso3": candidatos[2]},
            )
            if bool(result) and await self._peso_preenchido_etapa2():
                return True
        except Exception:
            pass

        return False

    async def _capturar_diagnostico_etapa2(
        self,
        motivo: str,
        extra_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, str]:
        """Salva screenshot + HTML + JSON para diagnosticar falhas na etapa 2."""
        paths: dict[str, str] = {}
        if not self._page:
            return paths

        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        base_dir = Path(tempfile.gettempdir()) / "fretebot_trd_debug"
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return paths

        png_path = base_dir / f"{ts}_{motivo}.png"
        html_path = base_dir / f"{ts}_{motivo}.html"
        json_path = base_dir / f"{ts}_{motivo}.json"

        try:
            await self._page.screenshot(path=str(png_path), full_page=True)
            paths["screenshot"] = str(png_path)
        except Exception:
            pass

        try:
            html = await self._page.content()
            html_path.write_text(html, encoding="utf-8")
            paths["html"] = str(html_path)
        except Exception:
            pass

        try:
            alerts = await self._coletar_alertas_ui()
        except Exception:
            alerts = []

        try:
            inputs_info = await self._page.evaluate(
                """() => {
                    const clean = (v) => (v || '').replace(/\\s+/g, ' ').trim();
                    const isVisible = (el) => {
                        if (!el || !(el instanceof HTMLElement)) return false;
                        const r = el.getBoundingClientRect();
                        const st = window.getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
                    };
                    const inputs = Array.from(document.querySelectorAll('input')).filter((el) => isVisible(el));
                    return inputs.slice(0, 80).map((el) => {
                        const wrapper = el.closest('div,section,form,tr,td,mat-form-field') || el.parentElement;
                        return {
                            type: el.type || '',
                            id: el.id || '',
                            name: el.name || '',
                            placeholder: el.placeholder || '',
                            ariaLabel: el.getAttribute('aria-label') || '',
                            value: el.value || '',
                            maxLength: el.maxLength || 0,
                            className: String(el.className || '').slice(0, 200),
                            wrapperText: clean((wrapper?.innerText || '').slice(0, 240)),
                            disabled: !!el.disabled,
                            readOnly: !!el.readOnly,
                        };
                    });
                }"""
            )
        except Exception:
            inputs_info = []

        try:
            payload = {
                "motivo": motivo,
                "url": self._page.url,
                "timestamp": ts,
                "alerts": alerts,
                "inputs_visiveis": inputs_info,
                "extra": extra_data or {},
            }
            json_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            paths["json"] = str(json_path)
        except Exception:
            pass

        paths["dir"] = str(base_dir)
        return paths

    async def cotear(self, origem: str, destino: str, peso: float, valor: float,
                    volumes: int = 1, altura: float = 0.0, largura: float = 0.0,
                    comprimento: float = 0.0, cubagens: Optional[list[dict]] = None,
                    cnpj_destinatario: Optional[str] = None,
                    cnpj_remetente: Optional[str] = None,
                    cep_remetente: Optional[str] = None) -> Optional[Cotacao]:
        """
        Realiza cotação de frete na plataforma TRD.
        """
        try:
            self.last_error = None
            await self._init_browser()
            cnpj_dest_digits = self._digits(cnpj_destinatario or "")
            if len(cnpj_dest_digits) != 14:
                self.last_error = "CNPJ do destinatário ausente/inválido para TRD"
                logger.error(f"[{self.nome}] {self.last_error}")
                return None
            cubagens_m: list[dict] = []
            if isinstance(cubagens, list):
                for row in cubagens:
                    if not isinstance(row, dict):
                        continue
                    try:
                        qtd = int(row.get("quantidade", 0) or 0)
                        comp_m = float(row.get("comprimento_m", 0) or 0)
                        larg_m = float(row.get("largura_m", 0) or 0)
                        alt_m = float(row.get("altura_m", 0) or 0)
                    except Exception:
                        continue
                    if qtd <= 0 or comp_m <= 0 or larg_m <= 0 or alt_m <= 0:
                        continue
                    cubagens_m.append(
                        {
                            "quantidade": qtd,
                            "comprimento_m": comp_m,
                            "largura_m": larg_m,
                            "altura_m": alt_m,
                        }
                    )
            if not cubagens_m and volumes > 0 and altura > 0 and largura > 0 and comprimento > 0:
                cubagens_m = [
                    {
                        "quantidade": int(volumes),
                        "comprimento_m": float(comprimento),
                        "largura_m": float(largura),
                        "altura_m": float(altura),
                    }
                ]
            if not cubagens_m:
                self.last_error = "Cubagem inválida: nenhuma cubagem real disponível"
                logger.error(
                    f"[{self.nome}] Cubagem inválida: nenhuma cubagem real disponível"
                )
                return None
            volumes_total = sum(int(c["quantidade"]) for c in cubagens_m)
            logger.info(
                f"[{self.nome}] Parâmetros: origem={origem} destino={destino} "
                f"peso={peso} valor={valor} volumes={volumes_total} "
                f"linhas_cubagem={len(cubagens_m)} cnpj_dest={cnpj_dest_digits[:8]}..."
            )
            
            if not await self._login():
                if not self.last_error:
                    self.last_error = "Falha no login TRD"
                return None
            
            # Criar nova página para cada cotação (mantém sessão via cookies do contexto)
            if self._logged_in:
                try:
                    await self._page.close()
                except Exception:
                    pass
                self._page = await self._context.new_page()
                await self._page.add_init_script(self._STEALTH_JS)

            network_payloads: list[dict[str, Any]] = []

            async def _capturar_resposta_rede(response) -> None:
                try:
                    resource_type = str(getattr(response.request, "resource_type", "") or "").lower()
                    if resource_type not in {"xhr", "fetch"}:
                        return

                    url = str(response.url or "")
                    url_l = url.lower()
                    content_type = str((response.headers or {}).get("content-type", "") or "").lower()

                    should_track = any(
                        kw in url_l
                        for kw in (
                            "cotac",
                            "simul",
                            "frete",
                            "presta",
                            "quotation",
                            "tms",
                        )
                    ) or ("json" in content_type)
                    if not should_track:
                        return

                    sample: dict[str, Any] = {
                        "url": url,
                        "status": int(getattr(response, "status", 0) or 0),
                        "content_type": content_type[:120],
                    }

                    if "json" in content_type:
                        try:
                            sample["json"] = await response.json()
                        except Exception:
                            text = await response.text()
                            if text:
                                sample["text"] = text[:4000]
                    else:
                        text = await response.text()
                        text_l = text.lower() if text else ""
                        if not text or not any(k in text_l for k in ("valor", "frete", "presta", "r$")):
                            return
                        sample["text"] = text[:4000]

                    network_payloads.append(sample)
                    if len(network_payloads) > 40:
                        del network_payloads[0 : len(network_payloads) - 40]
                except Exception:
                    return

            self._page.on(
                "response",
                lambda response: asyncio.create_task(_capturar_resposta_rede(response)),
            )
            
            logger.info(f"[{self.nome}] Navegando para cotação...")
            await self._page.goto(self.COTACAO_URL, wait_until='networkidle', timeout=60000)
            await self._page.wait_for_timeout(1500)

            # Verificar se sessão expirou (redirecionou para login ou não carregou o form)
            current_url = self._page.url
            session_expired = '/login' in current_url.lower()
            if not session_expired:
                # Verifica se o formulário da etapa 1 está presente
                try:
                    await self._page.locator('#destinatarioInscricaoFiscalInput').wait_for(timeout=5000)
                except Exception:
                    session_expired = True
            if session_expired:
                logger.warning(f"[{self.nome}] Sessão expirada (URL: {current_url}), refazendo login...")
                self._logged_in = False
                if not await self._login():
                    return None
                # Recriar página após re-login (cookies do contexto mantêm sessão)
                try:
                    await self._page.close()
                except Exception:
                    pass
                self._page = await self._context.new_page()
                await self._page.add_init_script(self._STEALTH_JS)
                self._page.on(
                    "response",
                    lambda response: asyncio.create_task(_capturar_resposta_rede(response)),
                )
                await self._page.goto(self.COTACAO_URL, wait_until='networkidle', timeout=60000)
                await self._page.wait_for_timeout(1500)
            
            # ETAPA 1: DADOS DO FRETE
            logger.info(f"[{self.nome}] Preenchendo ETAPA 1...")

            # Modo fornecedor: trocar pagador para DESTINATARIO e preencher remetente
            cnpj_rem_digits = self._digits(cnpj_remetente or "")
            cep_rem_digits = self._digits(cep_remetente or "")
            modo_fornecedor = len(cnpj_rem_digits) == 14

            if modo_fornecedor:
                logger.info(f"[{self.nome}] Modo fornecedor: pagador=DESTINATARIO, remetente={cnpj_rem_digits[:8]}...")
                # Selecionar pagador = DESTINATARIO
                try:
                    await self._page.select_option(
                        'select[ng-model="vm.selectCotacaoPagador"]', "DESTINATARIO"
                    )
                    await self._page.wait_for_timeout(1000)
                except Exception as e:
                    logger.warning(f"[{self.nome}] Falha ao trocar pagador para DESTINATARIO: {e}")

            try:
                await self._page.wait_for_timeout(500)
            except Exception:
                pass

            if modo_fornecedor:
                # Preencher CNPJ do remetente (fornecedor)
                cnpj_rem_ok = False
                for tentativa in range(4):
                    try:
                        loc_rem = self._page.locator('#remetenteInscricaoFiscalInput')
                        await loc_rem.click(timeout=3000)
                        await loc_rem.fill(cnpj_rem_digits)
                        await loc_rem.press("Tab")
                        await self._page.wait_for_timeout(500)
                        val = await loc_rem.input_value()
                        cnpj_rem_ok = len(self._digits(val)) >= 14
                    except Exception:
                        cnpj_rem_ok = False
                    if cnpj_rem_ok:
                        break
                    logger.warning(
                        f"[{self.nome}] Tentativa {tentativa + 1}/4 de preencher CNPJ remetente falhou"
                    )
                    await self._page.wait_for_timeout(700)

                if not cnpj_rem_ok:
                    self.last_error = "TRD: não foi possível preencher CNPJ do remetente na etapa 1"
                    logger.error(f"[{self.nome}] {self.last_error}")
                    return None

                # Aguardar autocomplete do CEP coleta após CNPJ remetente
                cep_coleta_ok = False
                for _ in range(16):
                    try:
                        cep_val_col = self._digits(
                            await self._page.locator('#numeroCepColetaInput').input_value()
                        )
                        cep_coleta_ok = len(cep_val_col) == 8
                    except Exception:
                        pass
                    if cep_coleta_ok:
                        break
                    await self._page.wait_for_timeout(500)

                # Fallback: preencher CEP coleta manualmente
                if not cep_coleta_ok and len(cep_rem_digits) == 8:
                    logger.info(f"[{self.nome}] CEP coleta não completou; preenchendo manualmente...")
                    try:
                        loc_cep_col = self._page.locator('#numeroCepColetaInput')
                        await loc_cep_col.click(timeout=2000)
                        await loc_cep_col.fill(cep_rem_digits)
                        await loc_cep_col.press("Tab")
                        await self._page.wait_for_timeout(800)
                    except Exception:
                        pass

                # No modo fornecedor com pagador=DESTINATARIO, o CNPJ destinatário
                # é auto-preenchido (empresa logada). Aguardar CEP entrega auto-completar.
                cep_ok = False
                cidade_uf_ok = False
                for _ in range(24):
                    try:
                        cep_val = self._digits(await self._page.locator('#numeroCepEntregaInput').input_value())
                        cidade_val = (await self._page.evaluate(
                            """() => {
                                const el = document.querySelector('input[ng-model="vm.cotacao.cepEntregaCidadeEstado"]');
                                return el ? el.value.trim() : '';
                            }"""
                        )) or ""
                    except Exception:
                        cep_val = ""
                        cidade_val = ""
                    cep_ok = len(cep_val) == 8
                    cidade_uf_ok = len(cidade_val) >= 3
                    if cep_ok and cidade_uf_ok:
                        break
                    await self._page.wait_for_timeout(500)

                # Fallback: preencher CEP entrega manualmente
                if not cep_ok:
                    destino_digits = self._digits(destino)
                    if len(destino_digits) == 8:
                        logger.info(f"[{self.nome}] CEP entrega não completou; preenchendo manualmente...")
                        try:
                            loc_cep = self._page.locator('#numeroCepEntregaInput')
                            await loc_cep.click(timeout=2000)
                            await loc_cep.fill(destino_digits)
                            await loc_cep.press("Tab")
                        except Exception:
                            pass
                        for _ in range(24):
                            try:
                                cep_val = self._digits(await self._page.locator('#numeroCepEntregaInput').input_value())
                                cidade_val = (await self._page.evaluate(
                                    """() => {
                                        const el = document.querySelector('input[ng-model="vm.cotacao.cepEntregaCidadeEstado"]');
                                        return el ? el.value.trim() : '';
                                    }"""
                                )) or ""
                            except Exception:
                                cep_val = ""
                                cidade_val = ""
                            cep_ok = len(cep_val) == 8
                            cidade_uf_ok = len(cidade_val) >= 3
                            if cep_ok and cidade_uf_ok:
                                break
                            await self._page.wait_for_timeout(500)

            else:
                # Modo normal: preencher CNPJ destinatário
                cnpj_ok = False
                for tentativa in range(4):
                    try:
                        loc = self._page.locator('#destinatarioInscricaoFiscalInput')
                        await loc.click(timeout=3000)
                        await loc.fill(cnpj_dest_digits)
                        await loc.press("Tab")
                        await self._page.wait_for_timeout(500)
                        val = await loc.input_value()
                        cnpj_ok = len(self._digits(val)) >= 14
                    except Exception:
                        cnpj_ok = False
                    if cnpj_ok:
                        break
                    logger.warning(
                        f"[{self.nome}] Tentativa {tentativa + 1}/4 de preencher CNPJ destinatário falhou; "
                        "aguardando e tentando novamente..."
                    )
                    await self._page.wait_for_timeout(700)

                if not cnpj_ok:
                    self.last_error = "TRD: não foi possível preencher CNPJ do destinatário na etapa 1"
                    logger.error(f"[{self.nome}] {self.last_error}")
                    return None

                # 1) Aguarda autocomplete natural do CEP/cidade/UF após CNPJ.
                cep_ok = False
                cidade_uf_ok = False
                for _ in range(24):
                    try:
                        cep_val = self._digits(await self._page.locator('#numeroCepEntregaInput').input_value())
                        cidade_val = (await self._page.evaluate(
                            """() => {
                                const el = document.querySelector('input[ng-model="vm.cotacao.cepEntregaCidadeEstado"]');
                                return el ? el.value.trim() : '';
                            }"""
                        )) or ""
                    except Exception:
                        cep_val = ""
                        cidade_val = ""
                    cep_ok = len(cep_val) == 8
                    cidade_uf_ok = len(cidade_val) >= 3
                    if cep_ok and cidade_uf_ok:
                        break
                    await self._page.wait_for_timeout(500)

                # 2) Fallback: preencher CEP manualmente se não completou sozinho.
                if not cep_ok:
                    logger.info(f"[{self.nome}] CEP entrega não completou sozinho; preenchendo manualmente...")
                    destino_digits = self._digits(destino)
                    if len(destino_digits) == 8:
                        try:
                            loc_cep = self._page.locator('#numeroCepEntregaInput')
                            await loc_cep.click(timeout=2000)
                            await loc_cep.fill(destino_digits)
                            await loc_cep.press("Tab")
                        except Exception:
                            pass
                        for _ in range(24):
                            try:
                                cep_val = self._digits(await self._page.locator('#numeroCepEntregaInput').input_value())
                                cidade_val = (await self._page.evaluate(
                                    """() => {
                                        const el = document.querySelector('input[ng-model="vm.cotacao.cepEntregaCidadeEstado"]');
                                        return el ? el.value.trim() : '';
                                    }"""
                                )) or ""
                            except Exception:
                                cep_val = ""
                                cidade_val = ""
                            cep_ok = len(cep_val) == 8
                            cidade_uf_ok = len(cidade_val) >= 3
                            if cep_ok and cidade_uf_ok:
                                break
                            await self._page.wait_for_timeout(500)

            if not (cep_ok and cidade_uf_ok):
                self.last_error = (
                    "TRD: após informar CNPJ/CEP, cidade/UF do destino não foi validada; "
                    "destino possivelmente não atendido"
                    f" [cep={cep_val} cidade={cidade_val}]"
                )
                logger.error(f"[{self.nome}] {self.last_error}")
                return None
             
            # Continuar
            await self._page.get_by_role("button", name="Continuar").click()
            await self._page.wait_for_timeout(1200)
            
            # Fecha modal se aparecer
            try:
                modal = self._page.locator('[role="dialog"], .modal').first
                if await modal.count() > 0:
                    await modal.get_by_role("button", name="Continuar").first.click()
                    await self._page.wait_for_timeout(800)
            except:
                pass

            # Garante transição para ETAPA 2.
            if not await self._aguardar_etapa2_pronta(timeout_ms=12000):
                # Tenta avançar novamente se o botão continuar ainda estiver visível.
                try:
                    btn_continuar = self._page.get_by_role("button", name="Continuar").first
                    if await btn_continuar.count() > 0 and await btn_continuar.is_visible():
                        await btn_continuar.click()
                        await self._page.wait_for_timeout(800)
                except Exception:
                    pass

            if not await self._aguardar_etapa2_pronta(timeout_ms=10000):
                alerts = await self._coletar_alertas_ui()
                extra_alert = f" ({'; '.join(alerts)})" if alerts else ""
                self.last_error = f"TRD: etapa 2 não carregou após clicar em Continuar{extra_alert}"
                logger.error(f"[{self.nome}] {self.last_error}")
                return None
            
            # ETAPA 2: DADOS DA CARGA
            logger.info(f"[{self.nome}] Preenchendo ETAPA 2...")
            
            # Valor da mercadoria
            valor_br = f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            try:
                loc_valor = self._page.locator('#valorMercadoriaInput')
                await loc_valor.click(timeout=3000)
                await loc_valor.fill(valor_br)
                await loc_valor.press("Tab")
            except Exception:
                self.last_error = "TRD: não foi possível preencher Valor da mercadoria"
                logger.error(f"[{self.nome}] {self.last_error}")
                return None
            await self._page.wait_for_timeout(250)
            
            # Peso
            peso_str = str(float(peso))
            try:
                loc_peso = self._page.locator('input[ng-model="vm.cotacao.quantidadePeso"]')
                await loc_peso.click(timeout=3000)
                await loc_peso.fill(peso_str)
                await loc_peso.press("Tab")
            except Exception:
                self.last_error = "TRD: não foi possível preencher o campo Peso"
                logger.error(f"[{self.nome}] {self.last_error}")
                return None
            await self._page.wait_for_timeout(300)
            
            # Cubagens reais do romaneio (uma linha por dimensão real)
            for cub in cubagens_m:
                await self._page.locator("#quantidadeVolumes").fill(str(int(cub["quantidade"])))
                await self._page.wait_for_timeout(300)
                await self._page.locator("#alturaInput").fill(str(float(cub["altura_m"])))
                await self._page.wait_for_timeout(300)
                await self._page.locator("#larguraInput").fill(str(float(cub["largura_m"])))
                await self._page.wait_for_timeout(300)
                await self._page.locator("#comprimentoInput").fill(str(float(cub["comprimento_m"])))
                await self._page.wait_for_timeout(500)
                await self._page.get_by_role("button", name="Adicionar").click()
                await self._page.wait_for_timeout(800)
            
            # Confirmar simulação (botão "Continuar" = vm.confirmaSimulacao())
            logger.info(f"[{self.nome}] Enviando cotação...")
            await self._page.locator('button[ng-click="vm.confirmaSimulacao()"]').click()
            await self._page.wait_for_timeout(1500)

            # Confirmar todos os modais sequenciais ("Continuar", "Enviar", etc.)
            modal_sel = '.modal-footer button[ng-click="ok()"]'
            for modal_idx in range(1, 6):
                try:
                    btn = self._page.locator(modal_sel)
                    await btn.wait_for(state="visible", timeout=10000)
                    await btn.click()
                    logger.info(f"[{self.nome}] Modal #{modal_idx} confirmado.")
                except Exception:
                    break
                # Aguardar spinner sumir entre modais
                try:
                    spinner = self._page.locator('spinner[name="mainSpinner"] .page-spinner-bar')
                    await spinner.wait_for(state="hidden", timeout=30000)
                except Exception:
                    pass
                await self._page.wait_for_timeout(2000)
            await self._page.wait_for_timeout(2000)
            
            # EXTRAÇÃO via status-card-js (HTML real)
            logger.info(f"[{self.nome}] Extraindo resultado...")

            # Aguardar resultado aparecer (status-card "Valor da prestação")
            for _ in range(30):
                has_result = await self._page.evaluate(
                    """() => {
                        const cards = document.querySelectorAll('status-card-js');
                        for (const c of cards) {
                            const title = (c.getAttribute('title') || '').toLowerCase();
                            if (title.includes('valor') && title.includes('presta')) {
                                const label = c.querySelector('.status-card-value label');
                                if (label && label.textContent.trim().length > 1) return true;
                            }
                        }
                        return false;
                    }"""
                )
                if has_result:
                    break
                await self._page.wait_for_timeout(1500)

            # Extrair valor da prestação e validade dos status-card-js
            result_cards = await self._page.evaluate(
                """() => {
                    const clean = (v) => (v || '').replace(/\\s+/g, ' ').trim();
                    const cards = document.querySelectorAll('status-card-js');
                    const out = {};
                    for (const c of cards) {
                        const title = clean(c.getAttribute('title') || '').toLowerCase();
                        const label = c.querySelector('.status-card-value label');
                        const value = label ? clean(label.textContent) : '';
                        if (!value) continue;
                        if (title.includes('valor') && title.includes('presta')) out.valorFrete = value;
                        if (title.includes('validade')) out.validade = value;
                        if (title.includes('simula')) out.simulacao = value;
                    }
                    return out;
                }"""
            )
            logger.info(f"[{self.nome}] Cards extraídos: {result_cards}")

            valor_frete = None
            texto = await self._page.inner_text('body')
            if isinstance(result_cards, dict) and result_cards.get('valorFrete'):
                valor_frete = self._parse_monetary_value(result_cards['valorFrete'])

            # Fallback: body text + regex
            if valor_frete is None:
                valor_frete = self._extrair_valor_frete(texto, valor)
            if valor_frete is None:
                valor_frete = self._extrair_valor_frete_da_payloads(network_payloads, valor)
                if valor_frete is not None:
                    logger.info(f"[{self.nome}] Valor extraído via respostas de rede: R$ {valor_frete:.2f}")
            if valor_frete is None:
                network_tail: list[dict[str, Any]] = []
                for sample in network_payloads[-8:]:
                    compact: dict[str, Any] = {
                        "url": str(sample.get("url", "")),
                        "status": sample.get("status"),
                        "content_type": sample.get("content_type"),
                    }
                    if "json" in sample:
                        try:
                            compact["json_excerpt"] = json.dumps(
                                sample.get("json"),
                                ensure_ascii=False,
                            )[:1800]
                        except Exception:
                            compact["json_excerpt"] = "<erro ao serializar json>"
                    elif "text" in sample:
                        compact["text_excerpt"] = str(sample.get("text", ""))[:1800]
                    network_tail.append(compact)

                diag = await self._capturar_diagnostico_etapa2(
                    "valor_resultado",
                    extra_data={
                        "network_payloads_count": len(network_payloads),
                        "network_payloads_tail": network_tail,
                        "body_excerpt": texto[:6000],
                    },
                )
                extra_diag = ""
                if diag.get("dir"):
                    extra_diag = f" (diagnóstico salvo em: {diag.get('dir')})"
                self.last_error = "Valor não encontrado no resultado TRD" + extra_diag
                logger.error(f"[{self.nome}] {self.last_error}")
                return None
            
            # Validade e simulação a partir dos cards
            validade = None
            numero_simulacao = None
            if isinstance(result_cards, dict):
                validade = result_cards.get('validade')
                numero_simulacao = result_cards.get('simulacao')
            if not numero_simulacao:
                texto_body = await self._page.inner_text('body')
                match_num = re.search(r'N[ºo]\s*Simula[çc][ãa]o\s*(\d+)', texto_body)
                numero_simulacao = match_num.group(1) if match_num else None
            
            # Prazo (busca em informações adicionais)
            prazo_dias = 5  # Padrão se não encontrar
            match_prazo = re.search(r'prazo.*?(\d+).*?dia', texto, re.IGNORECASE | re.DOTALL)
            if match_prazo:
                prazo_dias = int(match_prazo.group(1))
            
            logger.info(f"[{self.nome}] ✅ R$ {valor_frete:.2f} - {prazo_dias} dias (Simulação: {numero_simulacao})")
            
            restricoes = f"Simulação #{numero_simulacao}"
            if validade:
                restricoes += f" - Válida até {validade}"
            self.last_error = None
            
            return Cotacao(
                transportadora=self.nome,
                prazo_dias=prazo_dias,
                valor_frete=round(valor_frete, 2),
                restricoes=restricoes,
                timestamp=datetime.now()
            )
            
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"[{self.nome}] Erro na cotação: {e}")
            return None

    async def coteir(self, origem: str, destino: str, peso: float, valor: float,
                    volumes: int = 1, comprimento_cm: int = 0, largura_cm: int = 0,
                    altura_cm: int = 0, cubagens: Optional[list[dict]] = None,
                    cnpj_destinatario: Optional[str] = None,
                    cnpj_remetente: Optional[str] = None,
                    cep_remetente: Optional[str] = None) -> Optional[Cotacao]:
        """Compatível com ProviderBase: converte dimensões cm→m e delega."""
        cubagens_cm = self._normalizar_cubagens_cm(cubagens)
        if cubagens_cm:
            soma = sum(int(c["quantidade"]) for c in cubagens_cm)
            if int(volumes or 0) > 0 and soma != int(volumes):
                self.last_error = f"VOL ({volumes}) diverge da soma das cubagens ({soma})"
                logger.error(
                    f"[{self.nome}] Cotação bloqueada: VOL ({volumes}) diverge da soma das cubagens ({soma})"
                )
                return None
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
                f"Cubagem ausente/inválida (volumes={volumes}, "
                f"dims_cm={comprimento_cm}x{largura_cm}x{altura_cm})"
            )
            logger.error(
                f"[{self.nome}] Cotação bloqueada: cubagem ausente/inválida "
                f"(volumes={volumes}, dims_cm={comprimento_cm}x{largura_cm}x{altura_cm})"
            )
            return None

        cubagens_m = [
            {
                "quantidade": int(c["quantidade"]),
                "comprimento_m": int(c["comprimento_cm"]) / 100.0,
                "largura_m": int(c["largura_cm"]) / 100.0,
                "altura_m": int(c["altura_cm"]) / 100.0,
            }
            for c in cubagens_cm
        ]
        resultado = await self.cotear(
            origem=origem,
            destino=destino,
            peso=peso,
            valor=valor,
            cubagens=cubagens_m,
            cnpj_destinatario=cnpj_destinatario,
            cnpj_remetente=cnpj_remetente,
            cep_remetente=cep_remetente,
        )

        return resultado
