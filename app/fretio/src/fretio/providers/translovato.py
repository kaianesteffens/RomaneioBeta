"""Provider Translovato via portal do cliente."""

from __future__ import annotations

import re
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from fretio.logging_conf import get_logger
from fretio.models import Cotacao
from fretio.providers.base import ProviderBase
from fretio.providers.provider_utils import _digits, _parse_decimal_any

logger = get_logger(__name__)


class TranslovatoProvider(ProviderBase):
    """Provider Translovato com cotação automatizada via Playwright."""

    BASE_URL = "https://www.translovato.com.br"
    LOGIN_URL = "https://www.translovato.com.br/fale-conosco/solicitacao-de-cotacao"
    DEFAULT_COTACAO_URL = (
        "https://www.translovato.com.br/fale-conosco/solicitacao-de-cotacao#portal-do-cliente"
    )
    MINHAS_COTACOES_URL = "https://www.translovato.com.br/portal-do-cliente/minhas-cotacoes"

    LOGIN_CNPJ_SELECTOR = "#cnpj"
    LOGIN_USER_SELECTOR = "#user"
    LOGIN_PASSWORD_SELECTOR = 'input[name="password"]'
    SENDER_CNPJ_SELECTOR = "#sender_cpnj"
    RECEIVER_CNPJ_SELECTOR = "#receiver_c"
    DELIVERY_ZIP_SELECTOR = "#cep_entrega"
    VALUE_SELECTOR = 'input[name="value[volume_nf]"]'
    WEIGHT_SELECTOR = 'input[name="value[volume_weigth]"]'
    QUANTITY_SELECTOR = 'input[name="cubing_qnt[]"]'
    HEIGHT_SELECTOR = 'input[name="cubing_height[]"]'
    LENGTH_SELECTOR = 'input[name="cubing_length[]"]'
    DEPTH_SELECTOR = 'input[name="cubing_depth[]"]'

    MAIN_SELECTORS = (
        LOGIN_CNPJ_SELECTOR,
        LOGIN_USER_SELECTOR,
        LOGIN_PASSWORD_SELECTOR,
        SENDER_CNPJ_SELECTOR,
        RECEIVER_CNPJ_SELECTOR,
        DELIVERY_ZIP_SELECTOR,
        VALUE_SELECTOR,
        WEIGHT_SELECTOR,
        QUANTITY_SELECTOR,
        HEIGHT_SELECTOR,
        LENGTH_SELECTOR,
        DEPTH_SELECTOR,
    )

    def __init__(
        self,
        cnpj: str,
        usuario: str,
        senha: str,
        cnpj_remetente: str = "",
        produto: str = "CONFECCAO",
        cotacao_url: str = "",
        headless: bool = True,
    ) -> None:
        super().__init__(nome="TRANSLOVATO")
        self.cnpj = _digits(cnpj)
        self.usuario = str(usuario or "").strip()
        self.senha = str(senha or "").strip()
        self.cnpj_remetente = _digits(cnpj_remetente)
        self.produto = str(produto or "CONFECCAO").strip() or "CONFECCAO"
        self.cotacao_url = str(cotacao_url or self.DEFAULT_COTACAO_URL).strip()
        self.headless = bool(headless)
        self.last_error: str | None = None
        self._passo_atual: str | None = None
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._logged_in = False

    @staticmethod
    def _mask_doc(value: str) -> str:
        digits = _digits(value)
        if len(digits) <= 6:
            return "***"
        return f"{digits[:4]}***{digits[-2:]}"

    @staticmethod
    def _format_cnpj(value: str) -> str:
        digits = _digits(value)
        if len(digits) != 14:
            return str(value or "").strip()
        return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"

    @staticmethod
    def _format_decimal_br(value: float, decimals: int = 2) -> str:
        return f"{float(value):.{decimals}f}".replace(".", ",")

    @classmethod
    def _cm_to_m_br(cls, value_cm: Any) -> str:
        try:
            meters = float(value_cm or 0) / 100.0
        except Exception:
            meters = 0.0
        text = cls._format_decimal_br(meters, 2)
        return re.sub(r",?0+$", "", text) if "," in text else text

    @staticmethod
    def _normalizar_cubagens_cm(cubagens: Any, *, volumes: int = 1) -> list[dict[str, int]]:
        validas: list[dict[str, int]] = []
        if isinstance(cubagens, list):
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
                if qtd > 0 and comp > 0 and larg > 0 and alt > 0:
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
    def _extract_valor(text: str) -> float | None:
        matches = re.findall(r"R\$\s*\d{1,3}(?:\.\d{3})*,\d{2}|R\$\s*\d+,\d{2}", str(text or ""))
        for raw in matches:
            value = _parse_decimal_any(raw)
            if value is not None and value > 0:
                return float(value)
        return None

    @staticmethod
    def _extract_prazo(text: str) -> int | None:
        patterns = (
            r"prazo(?:\s+de\s+entrega)?\D{0,30}(\d{1,3})\s*dias?",
            r"entrega\D{0,30}(\d{1,3})\s*dias?",
            r"(\d{1,3})\s*dias?\s+(?:úteis|uteis|corridos)?",
        )
        lowered = str(text or "").lower()
        for pattern in patterns:
            match = re.search(pattern, lowered, flags=re.IGNORECASE)
            if match:
                return int(match.group(1))
        return None

    async def _init_browser(self) -> None:
        if self._browser and self._browser.is_connected():
            return
        if self._browser:
            await self.cleanup()

        from fretio.providers.base import launch_browser_resilient

        self._browser = await launch_browser_resilient(
            headless=self.headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            locale="pt-BR",
        )
        self._page = await self._context.new_page()
        self._page.set_default_timeout(30000)

    async def _click_if_present(self, selector_or_text: str, *, by_text: bool = False, timeout: int = 2500) -> bool:
        page = self._page
        try:
            loc = page.get_by_text(selector_or_text, exact=True) if by_text else page.locator(selector_or_text)
            if await loc.count() == 0:
                return False
            first = loc.first
            if await first.is_visible(timeout=timeout):
                await first.click(timeout=timeout)
                return True
        except Exception:
            return False
        return False

    async def _aceitar_cookies(self) -> None:
        clicked = await self._click_if_present("Ok, entendi!", by_text=True, timeout=1500)
        if clicked:
            await self._page.wait_for_timeout(300)

    async def _login(self) -> bool:
        if self._logged_in:
            return True
        self._passo_atual = "login"
        page = self._page
        logger.info("[TRANSLOVATO] Fazendo login")

        await page.goto(self.LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        await self._aceitar_cookies()
        await page.goto(self.cotacao_url, wait_until="domcontentloaded", timeout=60000)
        await self._aceitar_cookies()

        await page.locator(self.LOGIN_CNPJ_SELECTOR).fill(self._format_cnpj(self.cnpj))
        await page.locator(self.LOGIN_USER_SELECTOR).fill(self.usuario)
        await page.locator(self.LOGIN_PASSWORD_SELECTOR).fill(self.senha)
        await page.evaluate(
            """() => {
                const buttons = Array.from(document.querySelectorAll('button'));
                const button = buttons.find((el) => (el.innerText || '').toLowerCase().includes('entrar'));
                if (!button) throw new Error('botao entrar nao encontrado');
                button.click();
            }"""
        )
        lowered = ""
        current_url = ""
        for _ in range(12):
            await page.wait_for_timeout(500)
            current_url = page.url.lower()
            try:
                body_text = await page.locator("body").inner_text(timeout=2500)
            except Exception:
                body_text = ""
            lowered = body_text.lower()
            if "minhas cotações" in lowered or "minhas cotacoes" in lowered or "sair" in lowered:
                break
        if "minhas-cotacoes" not in current_url and "solicitar nova cotação" not in lowered and "sair" not in lowered:
            self.last_error = "Login falhou ou portal não confirmou acesso"
            logger.warning("[TRANSLOVATO] %s", self.last_error)
            return False

        self._logged_in = True
        logger.info("[TRANSLOVATO] Login OK")
        return True

    async def pre_login(self):
        await self._init_browser()
        ok = await self._login()
        return ok

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
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._logged_in = False

    async def _abrir_nova_cotacao(self) -> None:
        self._passo_atual = "abrindo_nova_cotacao"
        page = self._page
        await page.goto(self.MINHAS_COTACOES_URL, wait_until="domcontentloaded", timeout=60000)
        await self._aceitar_cookies()
        link = page.get_by_role("link", name=re.compile(r"solicitar nova cota[çc][aã]o", re.I))
        try:
            await link.click(timeout=15000)
        except Exception:
            await page.get_by_text(re.compile(r"solicitar nova cota[çc][aã]o", re.I)).first.click(timeout=15000)
        await page.wait_for_load_state("domcontentloaded", timeout=30000)
        await page.wait_for_timeout(800)

    async def _fill_input(self, selector: str, value: str, *, index: int = 0, timeout: int = 12000) -> None:
        loc = self._page.locator(selector).nth(index)
        await loc.wait_for(state="visible", timeout=timeout)
        await loc.fill(str(value))
        await loc.dispatch_event("input")
        await loc.dispatch_event("change")
        await loc.dispatch_event("blur")

    async def _cep_entrega_if_needed(self, cep_destino: str) -> None:
        page = self._page
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(1800)
        try:
            loc = page.locator(self.DELIVERY_ZIP_SELECTOR)
            if await loc.count() == 0:
                return
            field = loc.first
            if not await field.is_enabled(timeout=1500):
                return
            current = (await field.input_value(timeout=1500)).strip()
            if not current:
                await field.fill(_digits(cep_destino))
                await field.dispatch_event("input")
                await field.dispatch_event("change")
                await field.dispatch_event("blur")
        except Exception:
            return

    async def _ensure_cubagem_rows(self, desired_rows: int) -> int:
        page = self._page
        desired_rows = max(1, int(desired_rows or 1))
        add_names = re.compile(r"adicionar\s+(linha|item|volume)|incluir\s+(linha|item|volume)", re.I)
        for _ in range(desired_rows - 1):
            current = await page.locator(self.QUANTITY_SELECTOR).count()
            if current >= desired_rows:
                break
            clicked = False
            for getter in (
                lambda: page.get_by_role("button", name=add_names),
                lambda: page.get_by_role("link", name=add_names),
                lambda: page.get_by_text(add_names),
            ):
                try:
                    loc = getter().first
                    if await loc.is_visible(timeout=1000):
                        await loc.click(timeout=1500)
                        await page.wait_for_timeout(500)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                break
        return max(1, await page.locator(self.QUANTITY_SELECTOR).count())

    async def _preencher_cubagens(self, cubagens: list[dict[str, int]]) -> str:
        supported_rows = await self._ensure_cubagem_rows(len(cubagens))
        detalhes = ""
        if len(cubagens) > supported_rows:
            detalhes = "Múltiplas cubagens parcialmente suportadas pelo portal; enviada a primeira linha disponível."
        for index, cub in enumerate(cubagens[:supported_rows]):
            await self._fill_input(self.QUANTITY_SELECTOR, str(int(cub["quantidade"])), index=index)
            await self._fill_input(self.HEIGHT_SELECTOR, self._cm_to_m_br(cub["altura_cm"]), index=index)
            await self._fill_input(self.LENGTH_SELECTOR, self._cm_to_m_br(cub["comprimento_cm"]), index=index)
            await self._fill_input(self.DEPTH_SELECTOR, self._cm_to_m_br(cub["largura_cm"]), index=index)
        return detalhes

    async def _preencher_formulario(
        self,
        *,
        origem: str,
        destino: str,
        peso: float,
        valor: float,
        volumes: int,
        cubagens: list[dict[str, int]],
        cnpj_destinatario: str,
        cnpj_remetente: str,
    ) -> str:
        self._passo_atual = "preenchendo_formulario"
        sender = _digits(cnpj_remetente or self.cnpj_remetente or self.cnpj)
        receiver = _digits(cnpj_destinatario)
        if len(sender) != 14:
            raise ValueError("CNPJ remetente inválido para TRANSLOVATO")
        if len(receiver) != 14:
            raise ValueError("CNPJ destinatário inválido para TRANSLOVATO")

        logger.info(
            "[TRANSLOVATO] Preenchendo cotação remetente=%s destinatario=%s",
            self._mask_doc(sender),
            self._mask_doc(receiver),
        )
        await self._fill_input(self.SENDER_CNPJ_SELECTOR, sender)
        await self._fill_input(self.RECEIVER_CNPJ_SELECTOR, receiver)
        await self._cep_entrega_if_needed(destino)
        await self._fill_input(self.VALUE_SELECTOR, self._format_decimal_br(valor, 2))
        await self._fill_input(self.WEIGHT_SELECTOR, self._format_decimal_br(peso, 3))
        return await self._preencher_cubagens(cubagens)

    async def _simular_e_extrair(self, detalhes_extra: str = "") -> Cotacao | None:
        self._passo_atual = "simulando_cotacao"
        page = self._page
        await page.get_by_role("button", name=re.compile(r"simular\s+cota[çc][aã]o", re.I)).click(timeout=20000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeoutError:
            pass

        self._passo_atual = "extraindo_resultado"
        text = ""
        valor = None
        prazo = None
        for _ in range(30):
            await page.wait_for_timeout(1000)
            text = await page.locator("body").inner_text(timeout=15000)
            valor = self._extract_valor(text)
            prazo = self._extract_prazo(text)
            if valor is not None and prazo is not None:
                break
        if valor is None or prazo is None:
            self.last_error = "Portal respondeu sem valor ou prazo de cotação"
            logger.warning("[TRANSLOVATO] %s", self.last_error)
            return None
        detalhes = detalhes_extra or None
        return Cotacao(
            transportadora="TRANSLOVATO",
            prazo_dias=int(prazo),
            valor_frete=float(valor),
            restricoes=detalhes,
        )

    async def coteir(
        self,
        origem: str,
        destino: str,
        peso: float,
        valor: float,
        volumes: int = 1,
        cubagem_m3: float = 0.0,
        cubagens: list[dict[str, Any]] | None = None,
        cnpj_destinatario: str = "",
        cnpj_remetente: str = "",
        cep_origem: str = "",
        cep_destino: str = "",
        uf_destino: str = "",
        **_kwargs: Any,
    ) -> Cotacao | None:
        try:
            self.last_error = None
            cubagens_validas = self._normalizar_cubagens_cm(cubagens, volumes=volumes)
            if not cubagens_validas:
                self.last_error = "Cubagem inválida: dimensões ausentes no romaneio"
                logger.warning("[TRANSLOVATO] %s", self.last_error)
                return None

            destino_cep = _digits(cep_destino or destino)
            origem_cep = _digits(cep_origem or origem)
            if len(destino_cep) != 8:
                raise ValueError("CEP de destino inválido para TRANSLOVATO")
            if origem_cep and len(origem_cep) != 8:
                raise ValueError("CEP de origem inválido para TRANSLOVATO")

            self._passo_atual = "init_browser"
            await self._init_browser()
            if not await self._login():
                return None
            await self._abrir_nova_cotacao()
            detalhes = await self._preencher_formulario(
                origem=origem_cep,
                destino=destino_cep,
                peso=float(peso or 0.0),
                valor=float(valor or 0.0),
                volumes=int(volumes or 1),
                cubagens=cubagens_validas,
                cnpj_destinatario=cnpj_destinatario,
                cnpj_remetente=cnpj_remetente,
            )
            return await self._simular_e_extrair(detalhes)
        except Exception as exc:
            self.last_error = f"Falha na cotação TRANSLOVATO ({self._passo_atual or 'desconhecido'}): {exc}"
            logger.warning("[TRANSLOVATO] %s", self.last_error)
            raise
