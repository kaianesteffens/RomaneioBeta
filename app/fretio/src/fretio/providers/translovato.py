"""Provider Translovato via portal do cliente."""

from __future__ import annotations

import re
import unicodedata
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
    CUBING_SELECTOR = 'input[name="cubing[]"]'
    CUBING_WEIGHT_SELECTOR = 'input[name="cubing_weigth[]"]'
    CUBING_TOTAL_SELECTOR = 'input[name="cubing_total"]'
    CUBING_WEIGHT_TOTAL_SELECTOR = 'input[name="cubing_weigth_total"]'

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
        self._last_receiver_diagnostic: dict[str, Any] = {}

    @staticmethod
    def _mask_doc(value: str) -> str:
        digits = _digits(value)
        if len(digits) <= 6:
            return "***"
        return f"{digits[:4]}***{digits[-2:]}"


    @staticmethod
    def _normalizar_texto_comparacao(value: str) -> str:
        text = unicodedata.normalize("NFKD", str(value or ""))
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = re.sub(r"[^A-Za-z0-9]+", " ", text).strip().upper()
        return re.sub(r"\s+", " ", text)

    @classmethod
    def _parse_cidade_uf(cls, value: str) -> tuple[str, str]:
        text = re.sub(r"\s+", " ", str(value or "").strip())
        if not text:
            return "", ""
        uf = ""
        city = text
        match = re.search(r"(.+?)(?:\s*/\s*|\s+-\s+|\s+)([A-Za-z]{2})$", text)
        if match:
            city = str(match.group(1) or "").strip(" -/")
            uf = str(match.group(2) or "").strip().upper()
        return cls._normalizar_texto_comparacao(city), uf

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

    @classmethod
    def _calcular_resumo_cubagem(
        cls,
        cubagens: list[dict[str, int]],
        *,
        fator_produto: float = 300.0,
    ) -> dict[str, Any]:
        fator = float(fator_produto or 0.0)
        if fator <= 0:
            fator = 300.0

        linhas: list[dict[str, str]] = []
        total_cubagem = 0.0
        total_peso_cubado = 0.0
        for cub in cubagens:
            qtd = int(cub.get("quantidade", 0) or 0)
            altura_m = float(cub.get("altura_cm", 0) or 0) / 100.0
            comprimento_m = float(cub.get("comprimento_cm", 0) or 0) / 100.0
            largura_m = float(cub.get("largura_cm", 0) or 0) / 100.0
            cubagem_linha = max(0.0, float(qtd) * altura_m * comprimento_m * largura_m)
            peso_cubado_linha = max(0.0, cubagem_linha * fator)
            total_cubagem += cubagem_linha
            total_peso_cubado += peso_cubado_linha
            linhas.append(
                {
                    "cubagem": cls._format_decimal_br(cubagem_linha, 4),
                    "peso_cubado": cls._format_decimal_br(peso_cubado_linha, 2),
                }
            )

        return {
            "linhas": linhas,
            "total_cubagem": cls._format_decimal_br(total_cubagem, 4),
            "total_peso_cubado": cls._format_decimal_br(total_peso_cubado, 2),
            "fator_produto": fator,
        }

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
        await loc.evaluate(
            """(el) => {
                el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'Tab' }));
            }"""
        )
        await loc.dispatch_event("blur")

    async def _read_receiver_cnpj_digits(self) -> str:
        try:
            return _digits(await self._page.locator(self.RECEIVER_CNPJ_SELECTOR).first.input_value(timeout=1500))
        except Exception:
            return ""

    async def _receiver_divergence_diagnostic(
        self,
        *,
        stage: str,
        expected: str,
        found: str,
        selector: str | None = None,
    ) -> dict[str, Any]:
        raw_city = ""
        city = ""
        uf = ""
        zip_digits = ""
        autocomplete_present = False
        try:
            raw_city, city, uf = await self._read_delivery_city_uf()
        except Exception:
            pass
        try:
            zip_digits = await self._read_delivery_zip_digits()
        except Exception:
            pass
        try:
            autocomplete_present = bool(await self._page.evaluate(r"""() => {
                const visible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                return Array.from(document.querySelectorAll(
                    '[role="listbox"], [role="option"], .autocomplete, .ui-autocomplete, .select2-results, .dropdown-menu, ul li'
                )).some(visible);
            }"""))
        except Exception:
            autocomplete_present = False
        diag = {
            "stage": str(stage or "")[:80],
            "expected_masked": self._mask_doc(expected),
            "found_masked": self._mask_doc(found),
            "selector": selector or self.RECEIVER_CNPJ_SELECTOR,
            "autocomplete_present": autocomplete_present,
            "cep_detected_masked": f"{zip_digits[:5]}-***" if len(zip_digits) == 8 else "",
            "cidade_detectada": raw_city[:80],
            "cidade_norm": city[:80],
            "uf_detectada": uf[:2],
        }
        self._last_receiver_diagnostic = diag
        logger.warning(
            "[TRANSLOVATO] Diagnóstico divergência CNPJ destinatário: etapa=%s esperado=%s encontrado=%s seletor=%s autocomplete=%s cep=%s cidade_uf=%s",
            diag["stage"],
            diag["expected_masked"],
            diag["found_masked"],
            diag["selector"],
            diag["autocomplete_present"],
            diag["cep_detected_masked"] or "?",
            diag["cidade_detectada"] or "?",
        )
        return diag

    def _receiver_divergence_message(self, *, stage: str, expected: str, found: str) -> str:
        return (
            "CNPJ destinatário no portal diverge do romaneio após "
            f"{stage}: esperado {self._mask_doc(expected)}, encontrado {self._mask_doc(found)}. "
            "O portal alterou o CNPJ destinatário após blur; cotação bloqueada para evitar destinatário incorreto."
        )

    async def _validate_receiver_cnpj(self, expected: str, *, context: str) -> None:
        found = await self._read_receiver_cnpj_digits()
        logger.info(
            "[TRANSLOVATO] Validação CNPJ destinatário (%s): esperado=%s encontrado=%s",
            context,
            self._mask_doc(expected),
            self._mask_doc(found),
        )
        if found != expected:
            await self._receiver_divergence_diagnostic(
                stage=context,
                expected=expected,
                found=found,
                selector=self.RECEIVER_CNPJ_SELECTOR,
            )
            raise ValueError(self._receiver_divergence_message(stage=context, expected=expected, found=found))

    async def _select_receiver_autocomplete_match(self, receiver: str) -> bool:
        try:
            return bool(await self._page.evaluate(r"""(expected) => {
                const digits = (value) => String(value || '').replace(/\D+/g, '');
                const visible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const nodes = Array.from(document.querySelectorAll(
                    '[role="option"], [role="listbox"] *, .autocomplete *, .ui-autocomplete *, .select2-results__option, .dropdown-menu *, ul li'
                ));
                const match = nodes.find((el) => visible(el) && digits(el.innerText || el.textContent).includes(expected));
                if (!match) return false;
                match.click();
                return true;
            }""", receiver))
        except Exception:
            return False

    async def _rewrite_receiver_cnpj_fallback(self, receiver: str, *, reason_context: str) -> None:
        loc = self._page.locator(self.RECEIVER_CNPJ_SELECTOR).first
        logger.warning(
            "[TRANSLOVATO] CNPJ destinatário divergente após %s; limpando e preenchendo novamente %s",
            reason_context,
            self._mask_doc(receiver),
        )
        await loc.fill("")
        await loc.dispatch_event("input")
        await loc.dispatch_event("change")
        await loc.fill(receiver)
        await loc.dispatch_event("input")
        await loc.dispatch_event("change")
        await loc.dispatch_event("blur")
        await self._page.wait_for_timeout(800)
        await self._select_receiver_autocomplete_match(receiver)
        await self._page.wait_for_timeout(700)
        await self._validate_receiver_cnpj(receiver, context=f"fallback seguro após {reason_context}")

    async def _preencher_cnpj_destinatario(self, receiver: str) -> None:
        loc = self._page.locator(self.RECEIVER_CNPJ_SELECTOR).first
        await loc.wait_for(state="visible", timeout=12000)
        if await self._read_receiver_cnpj_digits() == receiver:
            logger.info(
                "[TRANSLOVATO] CNPJ destinatário já estava correto no portal; preservando valor automático %s",
                self._mask_doc(receiver),
            )
            await self._validate_receiver_cnpj(receiver, context="valor já preenchido")
            return
        await loc.fill(receiver)
        await loc.dispatch_event("input")
        await loc.dispatch_event("change")
        await loc.dispatch_event("blur")
        try:
            await self._validate_receiver_cnpj(receiver, context="blur")
        except ValueError:
            await self._rewrite_receiver_cnpj_fallback(receiver, reason_context="blur")
        await loc.press("Tab")
        await self._page.wait_for_timeout(500)
        await self._validate_receiver_cnpj(receiver, context="tabulação")

    async def _read_delivery_zip_digits(self) -> str:
        try:
            loc = self._page.locator(self.DELIVERY_ZIP_SELECTOR)
            if await loc.count() == 0:
                return ""
            return _digits(await loc.first.input_value(timeout=1500))
        except Exception:
            return ""

    async def _read_delivery_city_uf(self) -> tuple[str, str, str]:
        script = r"""() => {
            const visible = (el) => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style && style.visibility !== 'hidden' && style.display !== 'none' && rect.width >= 0 && rect.height >= 0;
            };
            const fields = Array.from(document.querySelectorAll('input, select, textarea'));
            const labelFor = (el) => {
                const id = el.getAttribute('id') || '';
                const direct = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
                const parent = el.closest('label');
                return [direct && direct.innerText, parent && parent.innerText].filter(Boolean).join(' ');
            };
            const textOf = (el) => [
                el.getAttribute('id'),
                el.getAttribute('name'),
                el.getAttribute('placeholder'),
                el.getAttribute('aria-label'),
                labelFor(el),
            ].filter(Boolean).join(' ').toLowerCase();
            const valueOf = (el) => {
                if (el.tagName === 'SELECT') {
                    return (el.options[el.selectedIndex]?.text || el.value || '').trim();
                }
                return (el.value || '').trim();
            };
            const candidates = fields
                .filter(visible)
                .map((el) => ({ el, haystack: textOf(el), value: valueOf(el) }))
                .filter((item) => item.value);
            const cityUf = candidates.find((item) =>
                /(cidade|municipio|munic[ií]pio).*(uf|estado)|cidade.*estado|cep.*cidade|entrega.*cidade|destino.*cidade/.test(item.haystack)
            );
            if (cityUf) return cityUf.value;
            const city = candidates.find((item) => /(cidade|municipio|munic[ií]pio)/.test(item.haystack));
            const uf = candidates.find((item) => /(^|[^a-z])(uf|estado)([^a-z]|$)/.test(item.haystack));
            return [city && city.value, uf && uf.value].filter(Boolean).join('/');
        }"""
        try:
            raw = str(await self._page.evaluate(script) or "").strip()
        except Exception:
            raw = ""
        city, uf = self._parse_cidade_uf(raw)
        return raw, city, uf

    async def _aguardar_e_validar_autopreenchimento_destino(
        self,
        *,
        expected_receiver: str,
        expected_cep: str,
        expected_city: str = "",
        expected_uf: str = "",
    ) -> None:
        expected_city_norm = self._normalizar_texto_comparacao(expected_city)
        expected_uf_norm = str(expected_uf or "").strip().upper()
        detected_zip = ""
        detected_raw = ""
        detected_city = ""
        detected_uf = ""

        for _ in range(24):
            await self._page.wait_for_timeout(300)
            detected_zip = await self._read_delivery_zip_digits()
            detected_raw, detected_city, detected_uf = await self._read_delivery_city_uf()
            if (not expected_cep or detected_zip == expected_cep) and (detected_city or detected_uf):
                break

        await self._validate_receiver_cnpj(expected_receiver, context="autopreenchimento do endereço")
        logger.info(
            "[TRANSLOVATO] Diagnóstico endereço destino: CNPJ esperado=%s CNPJ campo=%s cidade/UF esperada=%s/%s cidade/UF detectada=%s",
            self._mask_doc(expected_receiver),
            self._mask_doc(await self._read_receiver_cnpj_digits()),
            expected_city_norm or "?",
            expected_uf_norm or "?",
            detected_raw or "?",
        )

        if expected_cep and not detected_zip:
            logger.warning(
                "[TRANSLOVATO] Portal não expôs CEP de entrega após CNPJ válido; seguindo com endereço automático. CNPJ=%s",
                self._mask_doc(expected_receiver),
            )
        elif expected_cep and detected_zip != expected_cep:
            logger.warning(
                "[TRANSLOVATO] CEP automático do portal diverge do romaneio; seguindo porque CNPJ final está correto. esperado=%s detectado=%s CNPJ=%s",
                f"{expected_cep[:5]}-***" if len(expected_cep) == 8 else "?",
                f"{detected_zip[:5]}-***" if len(detected_zip) == 8 else "?",
                self._mask_doc(expected_receiver),
            )
        if not detected_city and not detected_uf:
            logger.warning(
                "[TRANSLOVATO] Cidade/UF do destino não detectadas com segurança; seguindo com endereço automático do portal. CNPJ=%s",
                self._mask_doc(expected_receiver),
            )
            return
        if expected_uf_norm and detected_uf and detected_uf != expected_uf_norm:
            raise ValueError(
                "UF de entrega preenchida pelo portal diverge do romaneio: "
                f"esperada {expected_uf_norm}, detectada {detected_uf}."
            )
        if expected_city_norm and detected_city and detected_city != expected_city_norm:
            raise ValueError(
                "Cidade de entrega preenchida pelo portal diverge do romaneio: "
                f"esperada {expected_city_norm}, detectada {detected_city}."
            )

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

    async def _read_cubagem_resumo_portal(self) -> dict[str, str]:
        page = self._page
        script = r"""() => {
            const valueOf = (selector, index = 0) => {
                const nodes = Array.from(document.querySelectorAll(selector));
                const el = nodes[index];
                return el ? String(el.value || '').trim() : '';
            };
            return {
                cubagem_linha: valueOf('input[name="cubing[]"]'),
                peso_cubado_linha: valueOf('input[name="cubing_weigth[]"]'),
                cubagem_total: valueOf('input[name="cubing_total"]'),
                peso_cubado_total: valueOf('input[name="cubing_weigth_total"]'),
            };
        }"""
        return dict(await page.evaluate(script) or {})

    async def _fallback_preencher_resumo_cubagem(self, cubagens: list[dict[str, int]]) -> dict[str, Any]:
        page = self._page
        factor_script = r"""() => {
            const factor = document.querySelector('#product-factor');
            return factor ? String(factor.value || '').trim() : '';
        }"""
        factor_raw = str(await page.evaluate(factor_script) or "").replace(",", ".").strip()
        try:
            factor = float(factor_raw or 0.0)
        except Exception:
            factor = 0.0
        resumo = self._calcular_resumo_cubagem(cubagens, fator_produto=factor or 300.0)
        payload = {
            "linhas": resumo["linhas"],
            "totalCubagem": resumo["total_cubagem"],
            "totalPeso": resumo["total_peso_cubado"],
        }
        await page.evaluate(
            r"""(data) => {
                const setValue = (el, value, key = 'Tab') => {
                    if (!el) return;
                    el.value = String(value || '');
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                };
                const cubagemInputs = Array.from(document.querySelectorAll('input[name="cubing[]"]'));
                const pesoInputs = Array.from(document.querySelectorAll('input[name="cubing_weigth[]"]'));
                data.linhas.forEach((linha, index) => {
                    setValue(cubagemInputs[index], linha.cubagem);
                    setValue(pesoInputs[index], linha.peso_cubado);
                });
                setValue(document.querySelector('input[name="cubing_total"]'), data.totalCubagem);
                setValue(document.querySelector('input[name="cubing_weigth_total"]'), data.totalPeso);
            }""",
            payload,
        )
        return resumo

    async def _garantir_resumo_cubagem_calculado(self, cubagens: list[dict[str, int]]) -> None:
        await self._page.wait_for_timeout(700)
        resumo = await self._read_cubagem_resumo_portal()
        if any(str(resumo.get(key) or "").strip() not in {"", "0", "0,0", "0,00", "0,000", "0,0000"} for key in ("cubagem_total", "peso_cubado_total")):
            logger.info(
                "[TRANSLOVATO] Portal calculou cubagem automaticamente: cubagem_total=%s peso_cubado_total=%s",
                resumo.get("cubagem_total") or "?",
                resumo.get("peso_cubado_total") or "?",
            )
            return

        fallback = await self._fallback_preencher_resumo_cubagem(cubagens)
        await self._page.wait_for_timeout(300)
        resumo_fim = await self._read_cubagem_resumo_portal()
        logger.warning(
            "[TRANSLOVATO] Portal não calculou cubagem automaticamente; fallback aplicado. cubagem_total=%s peso_cubado_total=%s fator=%s",
            resumo_fim.get("cubagem_total") or fallback.get("total_cubagem") or "?",
            resumo_fim.get("peso_cubado_total") or fallback.get("total_peso_cubado") or "?",
            fallback.get("fator_produto") or "?",
        )

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
        cidade_destino: str = "",
        uf_destino: str = "",
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
        await self._preencher_cnpj_destinatario(receiver)
        await self._aguardar_e_validar_autopreenchimento_destino(
            expected_receiver=receiver,
            expected_cep=_digits(destino),
            expected_city=cidade_destino,
            expected_uf=uf_destino,
        )
        await self._fill_input(self.VALUE_SELECTOR, self._format_decimal_br(valor, 2))
        await self._fill_input(self.WEIGHT_SELECTOR, self._format_decimal_br(peso, 3))
        detalhes = await self._preencher_cubagens(cubagens)
        await self._garantir_resumo_cubagem_calculado(cubagens)
        await self._validate_receiver_cnpj(receiver, context="preenchimento final da cotação")
        return detalhes

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
        cidade_destino: str = "",
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
                cidade_destino=cidade_destino,
                uf_destino=uf_destino,
            )
            return await self._simular_e_extrair(detalhes)
        except Exception as exc:
            self.last_error = f"Falha na cotação TRANSLOVATO ({self._passo_atual or 'desconhecido'}): {exc}"
            logger.warning("[TRANSLOVATO] %s", self.last_error)
            raise
