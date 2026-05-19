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
from fretio.providers.base import ProviderBase
from fretio.providers.provider_utils import _digits, _fmt_peso, get_stealth_script
from fretio.models import Cotacao
from fretio.logging_conf import get_logger

logger = get_logger(__name__)


class TRDProvider(ProviderBase):
    """Provider TRD Transportes usando Playwright."""
    
    LOGIN_URL = "https://platform.senior.com.br/login/?redirectTo=https%3A%2F%2Fplatform.senior.com.br%2Fsenior-x%2F&tenant=trdtransportes.com"
    COTACAO_URL = "https://platform.senior.com.br/logistica-documentos/tms/documentos-frontend/#/cotacao/adicao"
    _digits = staticmethod(_digits)

    @staticmethod
    def _fmt_peso_3casas(value: float) -> str:
        """Compatibilidade: usa _fmt_peso com 3 casas decimais."""
        return _fmt_peso(value, decimals=3)

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
        self._login_frame: Frame | None = None
        self._passo_atual: str = "inicio"

    _STEALTH_JS = get_stealth_script(preserve_eval=False)

    @staticmethod
    def _format_cnpj(value: str) -> str:
        digits = _digits(value)
        if len(digits) == 14:
            return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
        return digits

    @classmethod
    def _document_candidate_values(cls, value: str) -> list[str]:
        digits = cls._digits(value)
        formatted = cls._format_cnpj(digits)
        values: list[str] = []
        for item in (digits, formatted):
            if item and item not in values:
                values.append(item)
        return values

    @staticmethod
    def _etapa1_document_selectors(kind: str) -> list[str]:
        kind = str(kind or "").strip().lower()
        if kind == "remetente":
            return [
                "#remetenteInscricaoFiscalInput",
                "input[name*='remetente' i][name*='inscricao' i]",
                "input[id*='remetente' i][id*='inscricao' i]",
                "input[name*='remetente' i][name*='cnpj' i]",
                "input[id*='remetente' i][id*='cnpj' i]",
                "input[ng-model*='remetente' i][ng-model*='inscricao' i]",
                "input[ng-model*='remetente' i][ng-model*='cnpj' i]",
                "input[formcontrolname*='remetente' i][formcontrolname*='inscricao' i]",
                "input[formcontrolname*='remetente' i][formcontrolname*='cnpj' i]",
                "input[aria-label*='remet' i][aria-label*='cnpj' i]",
                "input[placeholder*='remet' i][placeholder*='cnpj' i]",
            ]
        return [
            "#destinatarioInscricaoFiscalInput",
            "input[name*='inscricaofiscal' i]",
            "input[id*='inscricaofiscal' i]",
            "input[name*='document' i]",
            "input[id*='document' i]",
            "input[name*='cpfoucnpjdestinatario' i]",
            "input[id*='cpfoucnpjdestinatario' i]",
            "input[name*='destinatario' i][name*='inscricao' i]",
            "input[id*='destinatario' i][id*='inscricao' i]",
            "input[name*='destinatario' i][name*='cnpj' i]",
            "input[id*='destinatario' i][id*='cnpj' i]",
            "input[name*='destinat' i][name*='cpf' i]",
            "input[id*='destinat' i][id*='cpf' i]",
            "input[ng-model*='inscricaofiscal' i]",
            "input[ng-model*='document' i]",
            "input[ng-model*='destinatario' i][ng-model*='inscricao' i]",
            "input[ng-model*='destinatario' i][ng-model*='cnpj' i]",
            "input[ng-model*='destinatario' i][ng-model*='cpf' i]",
            "input[formcontrolname*='inscricaofiscal' i]",
            "input[formcontrolname*='document' i]",
            "input[formcontrolname*='destinatario' i][formcontrolname*='inscricao' i]",
            "input[formcontrolname*='destinatario' i][formcontrolname*='cnpj' i]",
            "input[formcontrolname*='destinatario' i][formcontrolname*='cpf' i]",
            "input[aria-label*='destinat' i][aria-label*='cnpj' i]",
            "input[aria-label*='destinat' i][aria-label*='cpf' i]",
            "input[placeholder*='destinat' i][placeholder*='cnpj' i]",
            "input[placeholder*='destinat' i][placeholder*='cpf' i]",
        ]

    async def _wait_for_any_selector(self, selectors: list[str], timeout_ms: int = 5000) -> bool:
        if not self._page:
            return False
        deadline = time.monotonic() + (max(timeout_ms, 500) / 1000.0)
        while time.monotonic() < deadline:
            for sel in selectors:
                try:
                    loc = self._page.locator(sel).first
                    if await loc.count() > 0:
                        await loc.wait_for(state="visible", timeout=500)
                        return True
                except Exception:
                    continue
            await self._page.wait_for_timeout(250)
        return False

    async def _fill_locator_with_values(self, loc, values: list[str], *, min_digits: int = 14) -> bool:
        for value in values:
            try:
                await loc.wait_for(state="visible", timeout=1200)
                await loc.scroll_into_view_if_needed(timeout=1200)
            except Exception:
                pass
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
                await loc.fill(value, timeout=1800)
                typed = True
            except Exception:
                try:
                    await loc.type(value, delay=35, timeout=1800)
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
                value_now = await loc.input_value(timeout=800)
            except Exception:
                value_now = ""

            if len(self._digits(value_now)) >= min_digits:
                return True
        return False

    async def _init_browser(self):
        """Inicializa browser Playwright."""
        if self._browser:
            if self._browser.is_connected():
                return
            logger.warning(f"[{self.nome}] Browser desconectado, reinicializando...")
            await self.cleanup()
        
        from fretio.providers.base import launch_browser_resilient
        self._browser = await launch_browser_resilient(
            headless=self.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--enable-features=NetworkService,NetworkServiceInProcess',
                '--disable-features=IsolateOrigins,site-per-process,TranslateUI',
            ],
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
        self._login_frame = None
        await self._page.add_init_script(self._STEALTH_JS)

    @staticmethod
    def _is_logged_in_url(url: Optional[str]) -> bool:
        current_url = str(url or "").lower()
        if not current_url or "platform.senior.com.br" not in current_url:
            return False
        if "/login" in current_url:
            return False
        return any(token in current_url for token in ("senior-x/#/", "/senior-x/", "documentos-frontend/#/"))

    @staticmethod
    def _is_transient_sso_url(url: Optional[str]) -> bool:
        current_url = str(url or "").lower()
        if not current_url:
            return False
        return any(
            token in current_url
            for token in (
                "login-actions/authenticate",
                "login-actions/required-action",
                "sso.senior.com.br/realms/senior-x",
                "platform.senior.com.br/login",
            )
        )

    async def _recreate_page(self) -> None:
        self._login_frame = None
        try:
            if self._page and not self._page.is_closed():
                await self._page.close()
        except Exception:
            pass
        self._page = await self._context.new_page()
        await self._page.add_init_script(self._STEALTH_JS)

    async def _coletar_feedback_login(self, login_context) -> str | None:
        selectors = (
            "#input-error",
            ".alert-error",
            ".alert-danger",
            ".pf-c-alert__title",
            ".kc-feedback-text",
            ".invalid-feedback",
            '[role="alert"]',
        )
        contexts = []
        if login_context is not None:
            contexts.append(login_context)
        if self._page is not None and self._page not in contexts:
            contexts.append(self._page)

        for ctx in contexts:
            for sel in selectors:
                try:
                    loc = ctx.locator(sel).first
                    if await loc.count() == 0 or not await loc.is_visible():
                        continue
                    txt = re.sub(r"\s+", " ", (await loc.inner_text()).strip())
                    if txt:
                        return txt[:220]
                except Exception:
                    continue

        for ctx in contexts:
            try:
                body_text = re.sub(r"\s+", " ", (await ctx.locator("body").inner_text()).strip())
            except Exception:
                continue
            for marker in (
                "usuário ou senha",
                "usuario ou senha",
                "credenciais inválidas",
                "credenciais invalidas",
                "erro ao autenticar",
                "falha na autenticação",
                "falha na autenticacao",
            ):
                idx = body_text.lower().find(marker)
                if idx >= 0:
                    return body_text[idx:idx + 220]
        return None

    async def _aguardar_conclusao_login(self, login_context, timeout_ms: int = 20000) -> tuple[str, str | None]:
        deadline = time.monotonic() + (timeout_ms / 1000)
        while time.monotonic() < deadline:
            current_url = self._page.url
            if self._is_logged_in_url(current_url):
                return "ok", None

            feedback = await self._coletar_feedback_login(login_context)
            if feedback:
                return "erro", f"Login falhou: {feedback} (URL: {current_url})"

            try:
                await self._page.wait_for_load_state('networkidle', timeout=1000)
            except Exception:
                pass

            await self._page.wait_for_timeout(500)

        current_url = self._page.url
        if self._is_logged_in_url(current_url):
            return "ok", None

        feedback = await self._coletar_feedback_login(login_context)
        if feedback:
            return "erro", f"Login falhou: {feedback} (URL: {current_url})"
        if self._is_transient_sso_url(current_url):
            return "transient", f"SSO da TRD não concluiu autenticação (URL: {current_url})"
        return "erro", f"Timeout aguardando conclusão do login TRD (URL: {current_url})"
    
    async def _login(self):
        """Faz login na plataforma Senior X (até 2 tentativas p/ erros de rede)."""
        if self._logged_in:
            return True

        max_tentativas = 2
        for tentativa in range(1, max_tentativas + 1):
            try:
                logger.info(f"[{self.nome}] Fazendo login (tentativa {tentativa})...")
                self._login_frame = None
                await self._page.goto(self.LOGIN_URL, wait_until='domcontentloaded', timeout=60000)

                # Aguarda SSO (Keycloak) completar a cadeia de redirecionamentos antes de buscar campos
                try:
                    await self._page.wait_for_load_state('networkidle', timeout=15000)
                except Exception:
                    pass  # timeout aceitável; prossegue com verificação dos elementos

                # Verificar se já estamos logados (redirecionou para o dashboard)
                await self._page.wait_for_timeout(500)
                current_url = self._page.url
                if self._is_logged_in_url(current_url):
                    logger.info(f"[{self.nome}] Já logado (redirecionou para dashboard: {current_url})")
                    self._logged_in = True
                    return True

                # Etapa 1: Email + clicar "Próximo"
                # Tenta seletor direto primeiro, depois busca em iframes
                email_selectors = [
                    '#username-input-field',
                    'input[id*="username"]',
                    'input[id*="user"]',
                    'input[type="email"]',
                    'input[name="username"]',
                    'input[name="login"]',
                    'input[placeholder*="mail"]',
                    'input[placeholder*="suário"]',
                    'input[placeholder*="usuário"]',
                    'input[placeholder*="login"]',
                ]
                email_loc = None
                # Tenta no contexto principal
                for sel in email_selectors:
                    try:
                        loc = self._page.locator(sel).first
                        await loc.wait_for(state='visible', timeout=5000)
                        email_loc = loc
                        logger.info(f"[{self.nome}] Campo de email encontrado via: {sel}")
                        break
                    except Exception:
                        continue
                # Fallback: procurar em iframes
                if email_loc is None:
                    logger.info(f"[{self.nome}] Campo de email não encontrado na página principal, buscando em iframes...")
                    for frame in self._page.frames:
                        for sel in email_selectors:
                            try:
                                frame_loc = frame.locator(sel).first
                                if await frame_loc.count() > 0:
                                    await frame_loc.wait_for(state='visible', timeout=5000)
                                    email_loc = frame_loc
                                    self._login_frame = frame
                                    logger.info(f"[{self.nome}] Campo de email encontrado em iframe via: {sel}")
                                    break
                            except Exception:
                                continue
                        if email_loc is not None:
                            break
                if email_loc is None:
                    # Último cheque: talvez a página tenha carregado mas demorou
                    await self._page.wait_for_timeout(5000)
                    # Re-check se redirecionou enquanto esperávamos
                    if self._is_logged_in_url(self._page.url):
                        logger.info(f"[{self.nome}] Já logado (detectado após espera)")
                        self._logged_in = True
                        return True
                    try:
                        body_text = await self._page.inner_text("body")
                        logger.warning(f"[{self.nome}] Página de login (500 chars): {body_text[:500]}")
                    except Exception:
                        pass
                    raise TimeoutError(f"Timeout aguardando campo de login TRD (URL: {self._page.url})")
                await self._page.wait_for_timeout(500)
                await email_loc.fill(self.email)
                # Botão "Próximo" - tenta no frame se encontrou lá
                login_context = self._login_frame or self._page
                next_btn = login_context.locator('#nextBtn')
                try:
                    await next_btn.click(timeout=5000)
                except Exception:
                    # Fallback: qualquer botão submit/next na página
                    await self._page.locator('button[type="submit"], input[type="submit"]').first.click(timeout=5000)
                await self._page.wait_for_timeout(2000)

                # Etapa 2: Senha + clicar "Autenticar"
                login_context = self._login_frame or self._page
                pwd = login_context.locator('#password-input-field')
                await pwd.wait_for(state='visible', timeout=15000)
                await pwd.fill(self.senha)
                await self._page.wait_for_timeout(300)
                login_btn = login_context.locator('#loginbtn')
                try:
                    await login_btn.click(timeout=5000)
                except Exception:
                    await login_context.locator('button[type="submit"], input[type="submit"]').first.click(timeout=5000)

                status_login, detalhe_login = await self._aguardar_conclusao_login(login_context)
                if status_login == "ok":
                    logger.info(f"[{self.nome}] Login realizado com sucesso")
                    self._logged_in = True
                    return True

                if status_login == "transient" and tentativa < max_tentativas:
                    logger.warning(f"[{self.nome}] Login tentativa {tentativa} presa no SSO, recriando página: {detalhe_login}")
                    await asyncio.sleep(3 * tentativa)
                    await self._recreate_page()
                    continue

                self.last_error = detalhe_login or f"Login falhou - URL: {self._page.url}"
                logger.error(f"[{self.nome}] {self.last_error}")
                return False

            except Exception as e:
                current_url = str(getattr(self._page, 'url', '') or '')
                is_retryable = any(k in str(e) for k in (
                    "ERR_CONNECTION", "ERR_NAME", "ERR_TIMED_OUT", "net::", "Timeout",
                )) or self._is_transient_sso_url(current_url)
                if is_retryable and tentativa < max_tentativas:
                    logger.warning(f"[{self.nome}] Login tentativa {tentativa} falhou (retry): {e}")
                    await asyncio.sleep(3 * tentativa)
                    await self._recreate_page()
                    continue
                self.last_error = f"Erro no login: {e}"
                logger.error(f"[{self.nome}] Erro no login após {tentativa} tentativa(s): {e}")
                return False
        return False
    
    async def pre_login(self):
        """Inicializa browser e faz login antecipadamente."""
        await self._init_browser()
        ok = await self._login()
        if not ok:
            raise RuntimeError(self.last_error or "Falha no login TRD")
        return True

    async def cleanup(self):
        """Fecha o browser."""
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
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        self._browser = None
        self._context = None
        self._page = None
        self._login_frame = None
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

        _V = r'([\d]{1,3}(?:\.\d{3})*[.,]\d{2}|[\d]+[.,]\d{2})'
        for pat in [
            rf'(?i)valor\s+(?:total\s+da\s+)?presta[çc][ãa]o\s*(?:R\$\s*)?{_V}',
            rf'(?i)valor\s+do\s+frete\s*(?:R\$\s*)?{_V}',
            # "Valor Frete:" / "Valor Frete R$" sem "do"
            rf'(?i)valor\s+frete\s*[:\-]?\s*(?:R\$\s*)?{_V}',
            # "Total Frete:" label
            rf'(?i)total\s+frete\s*[:\-]?\s*(?:R\$\s*)?{_V}',
            # "Valor total do frete"
            rf'(?i)valor\s+total\s+(?:do\s+)?frete\s*[:\-]?\s*(?:R\$\s*)?{_V}',
            rf'(?i)\bfrete\b[^\n\r]{{0,90}}?(?:R\$\s*)?{_V}',
            # "Total: R$ ..." or "Valor total: R$ ..." — só quando R$ explícito (evita confusão)
            rf'(?i)(?:valor\s+)?total\s*[:\-]\s*R\$\s*{_V}',
        ]:
            m = re.search(pat, txt)
            if not m:
                continue
            val = TRDProvider._parse_monetary_value(m.group(1))
            if val is None:
                continue
            if 1 < val < 100000 and abs(val - float(valor_mercadoria or 0.0)) > 0.01:
                return val

        def _score_janela(janela: str, *, has_rs: bool = False) -> int:
            score = 0
            if "presta" in janela:
                score += 3 if not has_rs else 3
            if "frete" in janela:
                score += 2
            if has_rs and "total" in janela and "frete" not in janela and "presta" not in janela:
                score += 1
            # Penaliza contextos irrelevantes para frete
            if "mercadoria" in janela:
                score -= 3
            if "peso" in janela and "frete" not in janela:
                score -= 2
            if any(w in janela for w in ("volume", "cubagem")) and "frete" not in janela:
                score -= 2
            if any(w in janela for w in ("imposto", "icms", "ipi")):
                score -= 3
            if "prazo" in janela and "frete" not in janela:
                score -= 2
            return score

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
            score = _score_janela(janela, has_rs=True)
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
            score = _score_janela(janela)
            if "valor" in janela:
                score += 2
            if "nota" in janela:
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

    async def _preencher_documento_fiscal_js(self, frame: Frame, kind: str, doc_digits: str) -> bool:
        """Fallback robusto para localizar/preencher documento fiscal na Etapa 1."""
        try:
            result = await frame.evaluate(
                """(payload) => {
                    const clean = (v) => (v || '').replace(/\\s+/g, ' ').trim();
                    const digits = (v) => String(v || '').replace(/\\D/g, '');
                    const toLower = (v) => clean(v).toLowerCase();
                    const kind = String(payload?.kind || 'destinatario').toLowerCase();
                    const values = Array.isArray(payload?.values) ? payload.values.filter(Boolean) : [];
                    const targetTokens = kind === 'remetente'
                        ? ['remet', 'origem', 'embarque', 'fornecedor']
                        : ['destinat', 'destino', 'consignat', 'recebed'];
                    const negativeTokens = kind === 'remetente'
                        ? ['destinat', 'destino', 'consignat', 'recebed', 'pagador', 'tomador']
                        : ['remet', 'origem', 'pagador', 'tomador', 'fornecedor'];
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

                        trigger(el, 'keyup');
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
                            `${el.getAttribute('aria-label') || ''} ${el.className || ''} ${el.getAttribute('data-testid') || ''} ` +
                            `${el.getAttribute('ng-model') || ''} ${el.getAttribute('formcontrolname') || ''} ` +
                            `${el.getAttribute('title') || ''} ${el.getAttribute('autocomplete') || ''}`
                        );
                        const wrapper = el.closest('div,section,form,tr,td,mat-form-field') || el.parentElement;
                        const wrapperTxt = toLower((wrapper?.innerText || '').slice(0, 320));
                        const labelTxt = toLower(associatedLabelText(el));
                        const txt = `${attrs} ${wrapperTxt} ${labelTxt}`;

                        let score = 0;
                        if (txt.includes('cpfoucnpj')) score += 5;
                        if (txt.includes('cnpj') || txt.includes('cpf')) score += 4;
                        if (txt.includes('inscricao fiscal') || txt.includes('documento')) score += 4;
                        if (targetTokens.some((token) => txt.includes(token))) score += 9;
                        if (negativeTokens.some((token) => txt.includes(token))) score -= 10;
                        if ((el.maxLength || 0) >= 14 && (el.maxLength || 0) <= 18) score += 2;

                        return score;
                    };

                    const candidates = collectInputs().filter((el) => isVisible(el));
                    if (!candidates.length) return { ok: false, reason: 'no_visible_inputs' };

                    // Tentativa 1: seletores explícitos conhecidos.
                    const explicitSelectors = kind === 'remetente' ? [
                        '#remetenteInscricaoFiscalInput',
                        'input[name*="remetente" i][name*="inscricao" i]',
                        'input[id*="remetente" i][id*="inscricao" i]',
                        'input[name*="remetente" i][name*="cnpj" i]',
                        'input[id*="remetente" i][id*="cnpj" i]',
                        'input[ng-model*="remetente" i][ng-model*="cnpj" i]',
                        'input[ng-model*="remetente" i][ng-model*="inscricao" i]',
                    ] : [
                        '#destinatarioInscricaoFiscalInput',
                        'input[name*="inscricaofiscal" i]',
                        'input[id*="inscricaofiscal" i]',
                        'input[name*="document" i]',
                        'input[id*="document" i]',
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
                        'input[ng-model*="inscricaofiscal" i]',
                        'input[ng-model*="document" i]',
                        'input[ng-model*="destinatario" i][ng-model*="cnpj" i]',
                        'input[ng-model*="destinatario" i][ng-model*="inscricao" i]',
                    ];
                    for (const sel of explicitSelectors) {
                        const node = document.querySelector(sel);
                        if (node && isVisible(node)) {
                            for (const value of values) {
                                if (setValueSafely(node, value)) {
                                    return { ok: true, reason: 'explicit_selector', selector: sel };
                                }
                            }
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
                    let ok = false;
                    for (const value of values) {
                        ok = setValueSafely(top.el, value);
                        if (ok) break;
                    }
                    return {
                        ok,
                        reason: 'ranked',
                        bestScore: top.score,
                        valueLen: digits(top.el.value).length,
                    };
                }""",
                {"kind": kind, "values": self._document_candidate_values(doc_digits)},
            )
            return bool(isinstance(result, dict) and result.get("ok"))
        except Exception:
            return False

    async def _preencher_documento_fiscal_etapa1(self, kind: str, doc_digits: str) -> bool:
        """Preenche documento fiscal do remetente/destinatário com seletores e fallback JS."""
        if not self._page:
            return False

        values = self._document_candidate_values(doc_digits)
        selectors = self._etapa1_document_selectors(kind)
        frames: list[Frame] = []
        seen: set[int] = set()
        for frame in [self._page.main_frame, *self._page.frames]:
            fid = id(frame)
            if fid in seen:
                continue
            seen.add(fid)
            frames.append(frame)

        for frame in frames:
            for sel in selectors:
                loc = frame.locator(sel).first
                try:
                    if await loc.count() == 0:
                        continue
                except Exception:
                    continue
                if await self._fill_locator_with_values(loc, values):
                    return True

            if await self._preencher_documento_fiscal_js(frame, kind, doc_digits):
                return True

        return False

    async def _preencher_cnpj_destinatario_etapa1(self, cnpj_digits: str) -> bool:
        """Preenche o CNPJ destinatário tentando locator + fallback JS em todos os frames."""
        return await self._preencher_documento_fiscal_etapa1("destinatario", cnpj_digits)

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
        base_dir = Path(tempfile.gettempdir()) / "fretio_trd_debug"
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
            
            self._passo_atual = "login"
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

            def _response_handler(response):
                asyncio.create_task(_capturar_resposta_rede(response))

            def _registrar_listener():
                self._page.on("response", _response_handler)

            def _remover_listener():
                try:
                    self._page.remove_listener("response", _response_handler)
                except Exception:
                    pass

            _registrar_listener()

            self._passo_atual = "navegando_cotacao"
            logger.info(f"[{self.nome}] Navegando para cotação...")
            await self._page.goto(self.COTACAO_URL, wait_until='domcontentloaded', timeout=30000)
            await self._page.wait_for_timeout(500)

            # Verificar se sessão expirou (redirecionou para login ou não carregou o form)
            current_url = self._page.url
            session_expired = '/login' in current_url.lower()
            if not session_expired:
                # Verifica se o formulário da etapa 1 está presente
                try:
                    etapa1_ok = await self._wait_for_any_selector(
                        self._etapa1_document_selectors("destinatario"),
                        timeout_ms=7000,
                    )
                    if not etapa1_ok:
                        raise TimeoutError("Campos da etapa 1 não renderizaram")
                except Exception:
                    session_expired = True
            if session_expired:
                logger.warning(f"[{self.nome}] Sessão expirada (URL: {current_url}), refazendo login...")
                self._logged_in = False
                _remover_listener()
                if not await self._login():
                    return None
                # Recriar página após re-login (cookies do contexto mantêm sessão)
                try:
                    await self._page.close()
                except Exception:
                    pass
                self._page = await self._context.new_page()
                await self._page.add_init_script(self._STEALTH_JS)
                _registrar_listener()
                await self._page.goto(self.COTACAO_URL, wait_until='domcontentloaded', timeout=30000)
                await self._page.wait_for_timeout(500)

            # ETAPA 1: DADOS DO FRETE
            self._passo_atual = "etapa1_cnpj_cep"
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
                    await self._page.wait_for_timeout(300)
                except Exception as e:
                    logger.warning(f"[{self.nome}] Falha ao trocar pagador para DESTINATARIO: {e}")

            try:
                await self._page.wait_for_timeout(200)
            except Exception:
                pass

            if modo_fornecedor:
                # Preencher CNPJ do remetente (fornecedor)
                cnpj_rem_ok = False
                for tentativa in range(4):
                    try:
                        cnpj_rem_ok = await self._preencher_documento_fiscal_etapa1("remetente", cnpj_rem_digits)
                    except Exception:
                        cnpj_rem_ok = False
                    if cnpj_rem_ok:
                        break
                    logger.warning(
                        f"[{self.nome}] Tentativa {tentativa + 1}/4 de preencher CNPJ remetente falhou"
                    )
                    await self._page.wait_for_timeout(500)

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
                    await self._page.wait_for_timeout(300)

                # Fallback: preencher CEP coleta manualmente
                if not cep_coleta_ok and len(cep_rem_digits) == 8:
                    logger.info(f"[{self.nome}] CEP coleta não completou; preenchendo manualmente...")
                    try:
                        loc_cep_col = self._page.locator('#numeroCepColetaInput')
                        await loc_cep_col.click(timeout=2000)
                        await loc_cep_col.fill(cep_rem_digits)
                        await loc_cep_col.press("Tab")
                        await self._page.wait_for_timeout(300)
                    except Exception:
                        pass

                # No modo fornecedor com pagador=DESTINATARIO, o CNPJ destinatário
                # é auto-preenchido (empresa logada). Aguardar CEP entrega auto-completar.
                cep_ok = False
                cidade_uf_ok = False
                for _ in range(16):
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
                    await self._page.wait_for_timeout(300)

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
                        for _ in range(16):
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
                            await self._page.wait_for_timeout(300)

            else:
                # Modo normal: preencher CNPJ destinatário
                cnpj_ok = False
                for tentativa in range(4):
                    try:
                        cnpj_ok = await self._preencher_documento_fiscal_etapa1("destinatario", cnpj_dest_digits)
                    except Exception:
                        cnpj_ok = False
                    if cnpj_ok:
                        break
                    logger.warning(
                        f"[{self.nome}] Tentativa {tentativa + 1}/4 de preencher CNPJ destinatário falhou; "
                        "aguardando e tentando novamente..."
                    )
                    await self._page.wait_for_timeout(500)

                if not cnpj_ok:
                    # Fallback: usar método robusto com múltiplos seletores + JS
                    logger.warning(f"[{self.nome}] Loop simples falhou, tentando _preencher_cnpj_destinatario_etapa1...")
                    cnpj_ok = await self._preencher_cnpj_destinatario_etapa1(cnpj_dest_digits)

                if not cnpj_ok:
                    self.last_error = "TRD: não foi possível preencher CNPJ do destinatário na etapa 1"
                    logger.error(f"[{self.nome}] {self.last_error}")
                    return None

                # 1) Aguarda autocomplete natural do CEP/cidade/UF após CNPJ.
                cep_ok = False
                cidade_uf_ok = False
                for _ in range(16):
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
                    await self._page.wait_for_timeout(300)

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
                        for _ in range(16):
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
                            await self._page.wait_for_timeout(300)

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
            await self._page.wait_for_timeout(300)
            
            # Fecha modal se aparecer
            try:
                modal = self._page.locator('[role="dialog"], .modal').first
                if await modal.count() > 0:
                    await modal.get_by_role("button", name="Continuar").first.click()
                    await self._page.wait_for_timeout(300)
            except Exception:
                pass

            # Garante transição para ETAPA 2.
            if not await self._aguardar_etapa2_pronta(timeout_ms=8000):
                # Tenta avançar novamente se o botão continuar ainda estiver visível.
                try:
                    btn_continuar = self._page.get_by_role("button", name="Continuar").first
                    if await btn_continuar.count() > 0 and await btn_continuar.is_visible():
                        await btn_continuar.click()
                        await self._page.wait_for_timeout(300)
                except Exception:
                    pass

            if not await self._aguardar_etapa2_pronta(timeout_ms=8000):
                alerts = await self._coletar_alertas_ui()
                extra_alert = f" ({'; '.join(alerts)})" if alerts else ""
                self.last_error = f"TRD: etapa 2 não carregou após clicar em Continuar{extra_alert}"
                logger.error(f"[{self.nome}] {self.last_error}")
                return None
            
            # ETAPA 2: DADOS DA CARGA
            self._passo_atual = "etapa2_valor_peso_cubagem"
            logger.info(f"[{self.nome}] Preenchendo ETAPA 2...")
            
            # Valor da mercadoria (com retry e fallback robusto)
            await self._page.wait_for_timeout(500)
            if not await self._preencher_valor_mercadoria_etapa2(valor):
                logger.warning(f"[{self.nome}] Valor mercadoria falhou no 1º try, retentando após 2s...")
                await self._page.wait_for_timeout(2000)
                if not await self._preencher_valor_mercadoria_etapa2(valor):
                    alerts = await self._coletar_alertas_ui()
                    extra_alert = f" ({'; '.join(alerts)})" if alerts else ""
                    self.last_error = f"TRD: não foi possível preencher Valor da mercadoria{extra_alert}"
                    logger.error(f"[{self.nome}] {self.last_error}")
                    diag = await self._capturar_diagnostico_etapa2("valor_mercadoria_falhou")
                    if diag:
                        logger.info(f"[{self.nome}] Diagnóstico salvo: {diag}")
                    return None
            await self._page.wait_for_timeout(100)
            
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
            await self._page.wait_for_timeout(100)
            
            # Cubagens reais do romaneio (uma linha por dimensão real)
            for cub in cubagens_m:
                await self._page.locator("#quantidadeVolumes").fill(str(int(cub["quantidade"])))
                await self._page.locator("#alturaInput").fill(str(float(cub["altura_m"])))
                await self._page.locator("#larguraInput").fill(str(float(cub["largura_m"])))
                await self._page.locator("#comprimentoInput").fill(str(float(cub["comprimento_m"])))
                await self._page.wait_for_timeout(200)
                await self._page.get_by_role("button", name="Adicionar").click()
                await self._page.wait_for_timeout(300)
            
            # Confirmar simulação (botão "Continuar" = vm.confirmaSimulacao())
            self._passo_atual = "confirmando_simulacao"
            logger.info(f"[{self.nome}] Enviando cotação...")
            await self._page.locator('button[ng-click="vm.confirmaSimulacao()"]').click()
            await self._page.wait_for_timeout(500)

            # Confirmar todos os modais sequenciais ("Continuar", "Enviar", etc.)
            modal_sel = '.modal-footer button[ng-click="ok()"]'
            for modal_idx in range(1, 6):
                try:
                    btn = self._page.locator(modal_sel)
                    await btn.wait_for(state="visible", timeout=5000)
                    await btn.click()
                    logger.info(f"[{self.nome}] Modal #{modal_idx} confirmado.")
                except Exception:
                    break
                # Aguardar spinner sumir entre modais
                try:
                    spinner = self._page.locator('spinner[name="mainSpinner"] .page-spinner-bar')
                    await spinner.wait_for(state="hidden", timeout=15000)
                except Exception:
                    pass
                await self._page.wait_for_timeout(500)
            await self._page.wait_for_timeout(500)
            
            # EXTRAÇÃO via status-card-js (HTML real)
            self._passo_atual = "extraindo_resultado"
            logger.info(f"[{self.nome}] Extraindo resultado...")

            # Aguardar resultado aparecer (status-card "Valor da prestação")
            for _ in range(20):
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
                await self._page.wait_for_timeout(800)

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
        finally:
            try:
                _remover_listener()
            except NameError:
                pass

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
