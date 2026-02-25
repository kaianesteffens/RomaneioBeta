"""Provider AGEX Transportes - Automação via Playwright."""
from datetime import datetime
from typing import Optional
from decimal import Decimal, ROUND_HALF_UP
import re
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from fretebot.providers.base import ProviderBase
from fretebot.models import Cotacao
from fretebot.logging_conf import get_logger

logger = get_logger(__name__)


class AGEXProvider(ProviderBase):
    """Provider AGEX Transportes via Playwright."""

    LOGIN_URL = "https://cliente.agex.com.br/login"
    DEFAULT_TIMEOUT_MS = 30000

    def __init__(
        self,
        cnpj: str,
        senha: str,
        cnpj_remetente: Optional[str] = None,
        cnpj_destinatario: Optional[str] = None,
        cep_origem: Optional[str] = None,
        cep_destino: Optional[str] = None,
        descricao_mercadoria: str = "Mercadoria",
        tipo_produto: str = "Artigos Esportivos",
        volumes: int = 1,
        altura_m: float = 0.0,
        largura_m: float = 0.0,
        comprimento_m: float = 0.0,
        cubagens: Optional[list[dict]] = None,
        headless: bool = True,
    ) -> None:
        super().__init__(nome="AGEX")
        self.cnpj = cnpj
        self.senha = senha
        self.cnpj_remetente = cnpj_remetente or cnpj
        self.cnpj_destinatario = cnpj_destinatario
        self.cep_origem = self._digits(cep_origem or "")
        self.cep_destino = self._digits(cep_destino or "")
        self.descricao_mercadoria = descricao_mercadoria
        self.tipo_produto = tipo_produto
        self.volumes = int(volumes or 0)
        self.altura_m = altura_m
        self.largura_m = largura_m
        self.comprimento_m = comprimento_m
        self.cubagens = self._normalizar_cubagens(cubagens)
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._logged_in = False
        self.last_error: str | None = None

    def atualizar_carga(
        self,
        *,
        volumes: Optional[int] = None,
        altura_m: Optional[float] = None,
        largura_m: Optional[float] = None,
        comprimento_m: Optional[float] = None,
        cnpj_remetente: Optional[str] = None,
        cnpj_destinatario: Optional[str] = None,
        cep_origem: Optional[str] = None,
        cep_destino: Optional[str] = None,
        descricao_mercadoria: Optional[str] = None,
        tipo_produto: Optional[str] = None,
        cubagens: Optional[list[dict]] = None,
    ) -> None:
        """Atualiza dados dinâmicos da carga para a próxima cotação."""
        if volumes is not None:
            self.volumes = int(volumes)
        if altura_m is not None:
            self.altura_m = float(altura_m)
        if largura_m is not None:
            self.largura_m = float(largura_m)
        if comprimento_m is not None:
            self.comprimento_m = float(comprimento_m)
        if cnpj_remetente is not None:
            self.cnpj_remetente = cnpj_remetente
        if cnpj_destinatario is not None:
            self.cnpj_destinatario = cnpj_destinatario
        if cep_origem is not None:
            self.cep_origem = self._digits(cep_origem)
        if cep_destino is not None:
            self.cep_destino = self._digits(cep_destino)
        if descricao_mercadoria is not None:
            self.descricao_mercadoria = descricao_mercadoria
        if tipo_produto is not None:
            self.tipo_produto = tipo_produto
        if cubagens is not None:
            self.cubagens = self._normalizar_cubagens(cubagens)
        elif len(self.cubagens) <= 1:
            # Mantém coerência quando houver apenas uma linha de cubagem.
            self.cubagens = self._normalizar_cubagens(None)

    def _normalizar_cubagens(self, cubagens: Optional[list[dict]]) -> list[dict]:
        rows: list[dict] = []
        if isinstance(cubagens, list):
            for row in cubagens:
                if not isinstance(row, dict):
                    continue
                try:
                    qtd = int(row.get("quantidade", 0) or 0)
                    altura = float(row.get("altura_m", 0) or 0)
                    largura = float(row.get("largura_m", 0) or 0)
                    comp = float(row.get("comprimento_m", 0) or 0)
                except Exception:
                    continue
                if qtd <= 0 or altura <= 0 or largura <= 0 or comp <= 0:
                    continue
                rows.append(
                    {
                        "quantidade": qtd,
                        "altura_m": altura,
                        "largura_m": largura,
                        "comprimento_m": comp,
                    }
                )
        return rows

    @staticmethod
    def _format_decimal_br_2(valor: float, *, min_value: float | None = None) -> str:
        dec = Decimal(str(valor))
        if min_value is not None:
            dec = max(dec, Decimal(str(min_value)))
        dec = dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return f"{dec:.2f}".replace(".", ",")

    @staticmethod
    def _format_currency(valor: float) -> str:
        dec = Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        inteiro = int(dec)
        centavos = int((dec - inteiro) * 100)
        return f"R$ {inteiro:,}".replace(",", ".") + f",{centavos:02d}"

    @staticmethod
    def _format_weight(peso: float) -> str:
        return AGEXProvider._format_decimal_br_2(peso)

    @staticmethod
    def _format_dimension(valor: float) -> str:
        return AGEXProvider._format_decimal_br_2(valor)

    @staticmethod
    def _parse_brl(valor: str) -> float:
        return float(valor.replace(".", "").replace(",", "."))

    @staticmethod
    def _digits(value: str) -> str:
        return re.sub(r"\D", "", value or "")

    @classmethod
    def _extrair_valor_frete_do_texto(cls, texto: str) -> Optional[float]:
        txt = (texto or "").replace("\xa0", " ")

        # Padrões explícitos com rótulos observados no portal.
        for pattern in [
            r"(?i)\bfrete\b\s*[:\-]?\s*R\$\s*([\d.,]+)",
            r"(?i)\bvalor\s+do\s+frete\b\s*[:\-]?\s*R\$\s*([\d.,]+)",
            r"(?i)\btotal\s+(?:do\s+)?frete\b\s*[:\-]?\s*R\$\s*([\d.,]+)",
        ]:
            match = re.search(pattern, txt)
            if not match:
                continue
            try:
                valor = cls._parse_brl(match.group(1))
            except ValueError:
                continue
            if 1.0 < valor < 50000.0:
                return valor

        # Fallback: avalia todos os valores em R$ e prioriza contexto próximo de "frete".
        valor_mercadoria = None
        match_merc = re.search(
            r"(?i)\bvalor\s+(?:total|da\s+(?:mercadoria|NF|nota))\b.*?R\$\s*([\d.,]+)",
            txt,
            re.DOTALL,
        )
        if match_merc:
            try:
                valor_mercadoria = cls._parse_brl(match_merc.group(1))
            except ValueError:
                valor_mercadoria = None

        candidatos: list[tuple[int, int, float]] = []
        for match in re.finditer(r"R\$\s*([\d.,]+)", txt, re.IGNORECASE):
            val_str = match.group(1)
            try:
                valor = cls._parse_brl(val_str)
            except ValueError:
                continue
            if not (1.0 < valor < 50000.0):
                continue
            if valor_mercadoria is not None and abs(valor - valor_mercadoria) < 0.01:
                continue

            ini = max(0, match.start() - 90)
            fim = min(len(txt), match.end() + 90)
            janela = txt[ini:fim].lower()
            score = 0
            if "frete" in janela:
                score += 3
            if "total" in janela:
                score += 1
            if "mercadoria" in janela or "nota" in janela or " nf" in janela:
                score -= 3
            candidatos.append((score, -match.start(), valor))

        if not candidatos:
            return None
        candidatos.sort(reverse=True)
        return float(candidatos[0][2])

    async def _init_browser(self) -> None:
        if self._browser:
            if self._browser.is_connected():
                return
            logger.warning(f"[{self.nome}] Browser desconectado, reinicializando...")
            await self.cleanup()
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            channel="chrome",
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        self._page = await self._context.new_page()
        self._page.set_default_timeout(self.DEFAULT_TIMEOUT_MS)
        self._page.set_default_navigation_timeout(self.DEFAULT_TIMEOUT_MS)
        await self._page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

    async def _preencher_cnpj_e_aguardar_senha(self) -> bool:
        """Preenche CNPJ e tenta revelar campo de senha com múltiplas estratégias.

        Retorna True se o campo de senha ficou visível.
        """
        page = self._page
        cnpj_input = page.locator('input[name="document"]')
        senha_input = page.locator('input[type="password"]')

        # Estratégia 1: fill() padrão do Playwright
        await cnpj_input.fill(self.cnpj)
        try:
            await senha_input.wait_for(state="visible", timeout=8000)
            return True
        except Exception:
            pass

        # Estratégia 2: limpar e digitar caractere a caractere (aciona eventos React/Angular)
        logger.info(f"[{self.nome}] fill() não revelou senha, tentando type() char-a-char...")
        await cnpj_input.click()
        await cnpj_input.press("Control+a")
        await cnpj_input.press("Backspace")
        await cnpj_input.type(self.cnpj, delay=50)
        await page.wait_for_timeout(300)
        # Dispara blur/change que SPAs podem exigir
        await cnpj_input.press("Tab")
        await page.wait_for_timeout(1000)
        try:
            await senha_input.wait_for(state="visible", timeout=8000)
            return True
        except Exception:
            pass

        # Estratégia 3: forçar eventos via JS (input, change, blur)
        logger.info(f"[{self.nome}] type() não revelou senha, forçando eventos JS...")
        await page.evaluate("""(cnpj) => {
            const el = document.querySelector('input[name="document"]');
            if (!el) return;
            const nativeSet = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            ).set;
            nativeSet.call(el, cnpj);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));
        }""", self.cnpj)
        await page.wait_for_timeout(1000)
        try:
            await senha_input.wait_for(state="visible", timeout=8000)
            return True
        except Exception:
            pass

        # Estratégia 4: tentar avançar etapa via botões
        logger.info(f"[{self.nome}] Tentando avançar etapa via botão...")
        for btn_name in ["Próxima", "Próximo", "Next", "Continuar", "Avançar"]:
            try:
                proxima_btn = page.get_by_role("button", name=btn_name)
                if await proxima_btn.count() > 0 and await proxima_btn.is_visible():
                    await proxima_btn.click()
                    await page.wait_for_timeout(1000)
                    break
            except Exception:
                continue
        try:
            submit_btn = page.locator('button[type="submit"], input[type="submit"]').first
            if await submit_btn.count() > 0 and await submit_btn.is_visible():
                await submit_btn.click()
                await page.wait_for_timeout(1000)
        except Exception:
            pass
        # Enter no CNPJ como último recurso
        try:
            await cnpj_input.press("Enter")
            await page.wait_for_timeout(1000)
        except Exception:
            pass
        try:
            await senha_input.wait_for(state="visible", timeout=10000)
            return True
        except Exception:
            pass

        return False

    async def _login(self) -> bool:
        if self._logged_in:
            return True

        # Tenta login até 2x (page reload entre tentativas)
        for tentativa in range(1, 3):
            try:
                logger.info(f"[{self.nome}] Fazendo login (tentativa {tentativa})...")
                await self._page.goto(
                    self.LOGIN_URL, wait_until="domcontentloaded", timeout=60000
                )
                try:
                    await self._page.locator("path").nth(1).click(timeout=3000)
                except PlaywrightTimeoutError:
                    pass

                cnpj_input = self._page.locator('input[name="document"]')
                await cnpj_input.wait_for(timeout=self.DEFAULT_TIMEOUT_MS)

                if not await self._preencher_cnpj_e_aguardar_senha():
                    await self._salvar_debug(f"login_sem_senha_t{tentativa}")
                    logger.warning(
                        f"[{self.nome}] Campo de senha não apareceu (tentativa {tentativa})"
                    )
                    if tentativa < 2:
                        # Recarrega página para nova tentativa
                        continue
                    self.last_error = "Campo de senha não apareceu após 2 tentativas"
                    return False

                senha_input = self._page.locator('input[type="password"]')
                await senha_input.fill(self.senha)
                await self._page.wait_for_timeout(200)
                await self._page.get_by_role("button", name="Iniciar sessão").click()
                try:
                    await self._page.wait_for_url("**/inicio", timeout=self.DEFAULT_TIMEOUT_MS)
                except PlaywrightTimeoutError:
                    await self._page.wait_for_url(
                        "**/cotacao**", timeout=self.DEFAULT_TIMEOUT_MS
                    )
                logger.info(f"[{self.nome}] Login realizado com sucesso")
                self._logged_in = True
                return True
            except Exception as e:
                logger.warning(f"[{self.nome}] Login tentativa {tentativa} falhou: {e}")
                if tentativa < 2:
                    continue
                self.last_error = f"Erro no login: {e}"
                logger.error(f"[{self.nome}] Erro no login após 2 tentativas: {e}")
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

    async def _salvar_debug(self, sufixo: str) -> None:
        try:
            if self._page:
                import os
                debug_dir = os.path.join(os.environ.get("APPDATA", "."), "FreteBot")
                os.makedirs(debug_dir, exist_ok=True)
                await self._page.screenshot(
                    path=os.path.join(debug_dir, f"agex_{sufixo}.png"), full_page=True
                )
                html = await self._page.content()
                with open(os.path.join(debug_dir, f"agex_{sufixo}.html"), "w", encoding="utf-8") as f:
                    f.write(html)
        except Exception as e:
            logger.warning(f"[{self.nome}] Falha ao salvar debug: {e}")

    async def _preencher_cotacao(
        self, remetente: str, destinatario: str, peso: float, valor: float,
        tipo_pagador: str = "remetente",
    ) -> None:
        page = self._page
        etapa = "abrir_cotacoes"
        try:
            await page.goto(
                "https://cliente.agex.com.br/cotacao/cotar",
                wait_until="domcontentloaded",
                timeout=self.DEFAULT_TIMEOUT_MS,
            )
            await page.wait_for_timeout(500)

            # Verificar se foi redirecionado para login
            current_url = page.url
            logger.info(f"[{self.nome}] URL após goto cotação: {current_url}")
            if "login" in current_url.lower():
                logger.warning(f"[{self.nome}] Sessão expirou, refazendo login...")
                self._logged_in = False
                if not await self._login():
                    raise Exception("Re-login falhou")
                await page.goto(
                    "https://cliente.agex.com.br/cotacao/cotar",
                    wait_until="domcontentloaded",
                    timeout=self.DEFAULT_TIMEOUT_MS,
                )
                await page.wait_for_timeout(500)

            # ETAPA 1: Solicitante/Pagador
            etapa = "solicitante"
            await page.select_option("select[name=tipoPagador]", tipo_pagador)
            await page.wait_for_timeout(200)
            await page.get_by_role("button", name="Continuar").click()
            await page.wait_for_timeout(500)

            # ETAPA 2: Dados da origem (remetente)
            etapa = "remetente"
            await page.locator("input[name='cpfOuCnpjRemetente']").wait_for(
                timeout=self.DEFAULT_TIMEOUT_MS
            )
            await page.locator("input[name='cpfOuCnpjRemetente']").fill(remetente)
            await page.locator("input[name='cpfOuCnpjRemetente']").press("Tab")
            await page.wait_for_timeout(500)
            if len(self.cep_origem) == 8:
                cep_origem_loc = page.locator("input[name='enderecoOrigem.cep']")
                if await cep_origem_loc.count() > 0:
                    await cep_origem_loc.fill(self.cep_origem)
                    await cep_origem_loc.press("Tab")
                    await page.wait_for_timeout(400)
            await page.get_by_role("button", name="Continuar").click()
            await page.wait_for_timeout(500)

            # ETAPA 3: Dados do destino (destinatário)
            etapa = "destinatario"
            await page.locator("input[name='cpfOuCnpjDestinatario']").wait_for(
                timeout=self.DEFAULT_TIMEOUT_MS
            )
            await page.locator("input[name='cpfOuCnpjDestinatario']").fill(destinatario)
            await page.locator("input[name='cpfOuCnpjDestinatario']").press("Tab")
            await page.wait_for_timeout(500)

            if len(self.cep_destino) == 8:
                cep_dest_loc = page.locator("input[name='enderecoDestino.cep']")
                if await cep_dest_loc.count() > 0:
                    await cep_dest_loc.fill(self.cep_destino)
                    await cep_dest_loc.press("Tab")
                    await page.wait_for_timeout(400)

            # Avançar destino -> carga de forma robusta
            avancou = False
            for tentativa in range(1, 6):
                try:
                    btn_dest = page.locator("#enderecoDestino button[type='submit']").first
                    if await btn_dest.count() > 0:
                        await btn_dest.click()
                    else:
                        await page.get_by_role("button", name="Continuar").click()
                except Exception:
                    await page.get_by_role("button", name="Continuar").click()
                await page.wait_for_timeout(500)

                valor_total_loc = page.locator("input[name='valorTotal']")
                if await valor_total_loc.count() > 0:
                    try:
                        await valor_total_loc.first.wait_for(timeout=2500)
                        avancou = True
                        break
                    except Exception:
                        pass

                logger.info(f"[{self.nome}] Destino ainda não avançou para carga (tentativa {tentativa}/5)")

            if not avancou:
                estado_dest = await page.evaluate(
                    """() => {
                        const cnpj = document.querySelector("input[name='cpfOuCnpjDestinatario']")?.value || "";
                        const cep = document.querySelector("input[name='enderecoDestino.cep']")?.value || "";
                        const sujo = !!document.querySelector("#enderecoDestino .dirty");
                        const btn = document.querySelector("#enderecoDestino button[type='submit']");
                        const btnDisabled = btn ? !!btn.disabled : null;
                        return { cnpj, cep, sujo, btnDisabled };
                    }"""
                )
                logger.error(f"[{self.nome}] Destino não avançou para carga: {estado_dest}")
                raise Exception("Destino não avançou para etapa de carga no AGEX")

            # ETAPA 4: Carga
            etapa = "carga_valores"
            logger.info(f"[{self.nome}] Iniciando etapa 4 (carga), URL: {page.url}")
            await page.locator("input[name='valorTotal']").wait_for(
                timeout=self.DEFAULT_TIMEOUT_MS
            )
            await page.locator("input[name='valorTotal']").fill(
                self._format_currency(valor)
            )
            await page.wait_for_timeout(300)
            peso_loc = page.locator("input[name='pesoTotal']")
            await peso_loc.click()
            await peso_loc.fill("")
            peso_fmt = self._format_weight(peso)
            if not re.match(r"^\d+,\d{2}$", peso_fmt):
                raise Exception(f"Formato de peso inválido para AGEX: {peso_fmt}")
            await peso_loc.type(peso_fmt, delay=50)
            await peso_loc.press("Tab")
            await page.wait_for_timeout(300)

            # Garantir que o peso ficou de fato preenchido no campo mascarado.
            peso_valor_atual = (await peso_loc.input_value()).strip()
            if not re.search(r"\d", peso_valor_atual):
                await page.evaluate(
                    """(peso) => {
                        const el = document.querySelector("input[name='pesoTotal']");
                        if (!el) return;
                        const setter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype,
                            'value'
                        )?.set;
                        if (setter) setter.call(el, peso);
                        else el.value = peso;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        el.dispatchEvent(new Event('blur', { bubbles: true }));
                    }""",
                    peso_fmt,
                )
                await page.wait_for_timeout(300)
                peso_valor_atual = (await peso_loc.input_value()).strip()

            if not re.search(r"\d", peso_valor_atual):
                raise Exception("Campo Peso Total não foi preenchido no formulário AGEX")

            logger.info(f"[{self.nome}] Peso total preenchido: {peso_valor_atual}")

            # Cubagem - usa somente linhas vindas do romaneio.
            etapa = "carga_cubagem"
            cubagens = self._normalizar_cubagens(self.cubagens)
            if not cubagens:
                raise Exception("AGEX sem cubagens do romaneio (nenhum tamanho informado)")
            logger.info(f"[{self.nome}] Preenchendo cubagens do romaneio: {len(cubagens)} linha(s)")

            for idx, cub in enumerate(cubagens):
                if idx > 0:
                    # A próxima linha deve aparecer após clicar no botão verde "+".
                    row_ok = False
                    for _ in range(20):
                        row_ok = await page.evaluate(
                            """(rowIndex) => {
                                return !!(
                                    document.querySelector(`input[name="cubagem.${rowIndex}.altura"]`) ||
                                    document.querySelector(`input[name="cubagem.${rowIndex}.largura"]`) ||
                                    document.querySelector(`input[name="cubagem.${rowIndex}.comp"]`)
                                );
                            }""",
                            idx,
                        )
                        if row_ok:
                            break
                        await page.wait_for_timeout(250)
                    if not row_ok:
                        raise Exception(f"Não foi possível carregar a linha de cubagem #{idx + 1}")

                last_dim_loc = None
                for suffix, key in [
                    ("altura", "altura_m"),
                    ("largura", "largura_m"),
                    ("comp", "comprimento_m"),
                ]:
                    field_name = f"cubagem.{idx}.{suffix}"
                    loc = page.locator(f"input[name='{field_name}']").first
                    await loc.wait_for(timeout=self.DEFAULT_TIMEOUT_MS)
                    await loc.click()
                    await loc.fill("")
                    dim_fmt = self._format_dimension(float(cub[key]))
                    if not re.match(r"^\d+,\d{2}$", dim_fmt):
                        raise Exception(f"Formato de dimensão inválido para AGEX ({field_name}): {dim_fmt}")
                    await loc.type(dim_fmt, delay=50)
                    await page.wait_for_timeout(150)
                    last_dim_loc = loc

                # Regra: tamanho da caixa completo -> TAB -> quantidade.
                if last_dim_loc is not None:
                    await last_dim_loc.press("Tab")
                    await page.wait_for_timeout(150)

                qtd_fmt = str(int(cub["quantidade"]))
                qtd_ok = False
                qtd_locators = [
                    # Campo real observado no portal (sem atributo name).
                    page.locator("input[placeholder='Qtd.']").nth(idx),
                    page.locator(f"input[name='cubagem.{idx}.quantidade']").first,
                    page.locator(f"input[name='cubagem.{idx}.qtd']").first,
                    page.locator(f"input[name='cubagem.{idx}.qtde']").first,
                    page.locator(f"input[name='cubagem.{idx}.volumes']").first,
                ]
                for loc in qtd_locators:
                    try:
                        if await loc.count() <= 0:
                            continue
                        await loc.wait_for(timeout=4000)
                        await loc.click()
                        await loc.fill("")
                        await loc.type(qtd_fmt, delay=50)
                        val_atual = (await loc.input_value()).strip()
                        if re.search(r"\d+", val_atual):
                            qtd_ok = True
                            break
                    except Exception:
                        continue
                if not qtd_ok:
                    qtd_ok = await page.evaluate(
                        """({ rowIndex, qtd }) => {
                            const byPlaceholder = Array.from(
                                document.querySelectorAll("input[placeholder='Qtd.']")
                            );
                            let el = byPlaceholder[rowIndex] || null;

                            const byName = [
                                `input[name="cubagem.${rowIndex}.quantidade"]`,
                                `input[name="cubagem.${rowIndex}.qtd"]`,
                                `input[name="cubagem.${rowIndex}.qtde"]`,
                                `input[name="cubagem.${rowIndex}.volumes"]`,
                            ];
                            if (!el) {
                                for (const sel of byName) {
                                    el = document.querySelector(sel);
                                    if (el) break;
                                }
                            }
                            if (!el) return false;
                            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                            if (setter) setter.call(el, qtd);
                            else el.value = qtd;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                            el.dispatchEvent(new Event('blur', { bubbles: true }));
                            return /\\d+/.test(String(el.value || ''));
                        }""",
                        {"rowIndex": idx, "qtd": qtd_fmt},
                    )
                    if qtd_ok:
                        try:
                            qloc = page.locator("input[placeholder='Qtd.']").nth(idx)
                            if await qloc.count() <= 0:
                                qloc = page.locator(
                                    f"input[name='cubagem.{idx}.quantidade'], "
                                    f"input[name='cubagem.{idx}.qtd'], "
                                    f"input[name='cubagem.{idx}.qtde'], "
                                    f"input[name='cubagem.{idx}.volumes']"
                                ).first
                            await qloc.click()
                            await page.wait_for_timeout(150)
                        except Exception:
                            pass
                await page.wait_for_timeout(150)
                if not qtd_ok:
                    raise Exception(f"Não foi possível preencher a quantidade da cubagem #{idx + 1}")

                # Se houver outra caixa no romaneio, adiciona nova linha clicando no botão "+".
                if idx < len(cubagens) - 1:
                    add_clicked = False
                    add_btn = page.locator("button[name='adicionarVolume']").first
                    try:
                        if await add_btn.count() > 0:
                            await add_btn.wait_for(timeout=4000)
                            await add_btn.click()
                            add_clicked = True
                    except Exception:
                        add_clicked = False

                    if not add_clicked:
                        add_clicked = await page.evaluate(
                            """() => {
                                const btn = document.querySelector("button[name='adicionarVolume']");
                                if (!btn) return false;
                                btn.click();
                                return true;
                            }"""
                        )

                    if not add_clicked:
                        raise Exception(f"Não foi possível adicionar a linha de cubagem #{idx + 2}")

                    row_ok = False
                    for _ in range(24):
                        row_ok = await page.evaluate(
                            """(rowIndex) => {
                                return !!(
                                    document.querySelector(`input[name="cubagem.${rowIndex}.altura"]`) ||
                                    document.querySelector(`input[name="cubagem.${rowIndex}.largura"]`) ||
                                    document.querySelector(`input[name="cubagem.${rowIndex}.comp"]`)
                                );
                            }""",
                            idx + 1,
                        )
                        if row_ok:
                            break
                        await page.wait_for_timeout(250)
                    if not row_ok:
                        raise Exception(f"Não foi possível adicionar a linha de cubagem #{idx + 2}")

            # Tipo de produto (search/select)
            etapa = "tipo_produto"
            tipo_search = page.locator("input[placeholder='Selecione o tipo de produto']")
            await tipo_search.click()
            await page.wait_for_timeout(300)
            await tipo_search.fill(self.tipo_produto)
            await page.wait_for_timeout(500)
            options = await page.locator("[class*=option], [role=option]").all()
            if options:
                await options[0].click()
                await page.wait_for_timeout(300)

            # Tipo de embalagem (button inside div with <p>Caixa</p>)
            etapa = "embalagem"
            await page.evaluate("""() => {
                const paragraphs = document.querySelectorAll('p');
                for (const p of paragraphs) {
                    if (p.textContent.trim() === 'Caixa') {
                        const parent = p.parentElement;
                        if (parent) {
                            const btn = parent.querySelector('button');
                            if (btn) { btn.click(); return; }
                            parent.click();
                            return;
                        }
                    }
                }
            }""")
            await page.wait_for_timeout(300)

            # Descrição da mercadoria
            await page.locator("input[placeholder='Descrição da mercadoria']").fill(
                self.descricao_mercadoria
            )
            await page.wait_for_timeout(300)

            # Continuar para confirmação
            etapa = "continuar_confirmacao"
            await page.get_by_role("button", name="Continuar").click()
            await page.wait_for_timeout(500)

            # Confirmar cotação (botão "Confirmar e ver resultado")
            etapa = "confirmar"
            confirmar = page.get_by_role("button", name="Confirmar e ver resultado")
            await confirmar.wait_for(timeout=self.DEFAULT_TIMEOUT_MS)
            await confirmar.click()
        except Exception as e:
            if isinstance(e, PlaywrightTimeoutError):
                self.last_error = f"Timeout na etapa: {etapa}"
                logger.error(f"[{self.nome}] Timeout na etapa: {etapa}")
            else:
                self.last_error = f"Falha na etapa {etapa}: {e}"
                logger.error(f"[{self.nome}] Falha na etapa {etapa}: {e}")
            await self._salvar_debug(f"timeout_{etapa}")
            raise

    async def _extrair_resultado(self) -> Optional[tuple[float, int, str]]:
        page = self._page

        # Aguardar resultado ou indisponibilidade via polling JS no browser
        # (evita round-trips Python↔Browser a cada iteração)
        try:
            status = await page.evaluate("""() => new Promise((resolve) => {
                const check = () => {
                    const el = document.getElementById('resultado');
                    if (el && el.offsetParent !== null) return resolve('ok');
                    const txt = (document.body.innerText || '').toLowerCase();
                    if (txt.includes('não conseguimos buscar o preço') ||
                        txt.includes('nao conseguimos buscar o preco') ||
                        txt.includes('entre em contato com nosso comercial'))
                        return resolve('indisponivel');
                };
                check();
                const id = setInterval(check, 300);
                setTimeout(() => { clearInterval(id); resolve('timeout'); }, 20000);
            })""")
        except Exception:
            status = "timeout"

        if status == "indisponivel":
            self.last_error = "Cotação não disponível automaticamente para esta rota"
            logger.info(f"[{self.nome}] {self.last_error}")
            await self._salvar_debug("indisponivel")
            return None

        if status != "ok":
            self.last_error = "Seção #resultado não apareceu na página"
            logger.error(f"[{self.nome}] {self.last_error}")
            await self._salvar_debug("sem_resultado")
            return None

        # Extrair via seletores diretos do HTML real (#resultado spans)
        result_data = await page.evaluate(
            """() => {
                const section = document.getElementById('resultado');
                if (!section) return null;
                const spans = section.querySelectorAll('span');
                const out = {};
                const prevMap = {};
                for (const sp of spans) {
                    const parent = sp.closest('p') || sp.parentElement;
                    const parentText = (parent ? parent.textContent : '').toLowerCase();
                    const val = sp.textContent.trim();
                    if (!val) continue;
                    if (parentText.includes('frete')) out.frete = val;
                    else if (parentText.includes('número') || parentText.includes('numero')) out.numero = val;
                    else if (parentText.includes('data') && parentText.includes('entrega')) out.dataEntrega = val;
                }
                return out;
            }"""
        )
        logger.info(f"[{self.nome}] Dados extraídos de #resultado: {result_data}")

        valor_frete = None
        if isinstance(result_data, dict) and result_data.get('frete'):
            try:
                valor_frete = self._parse_brl(
                    result_data['frete'].replace('R$', '').strip()
                )
            except Exception:
                pass

        # Fallback: regex no body inteiro
        if valor_frete is None:
            texto = await page.inner_text("body")
            valor_frete = self._extrair_valor_frete_do_texto(texto)

        if valor_frete is None:
            self.last_error = "Valor do frete não encontrado na página de resultado"
            logger.error(f"[{self.nome}] {self.last_error}")
            await self._salvar_debug("sem_valor")
            return None

        numero = ""
        data_entrega = ""
        if isinstance(result_data, dict):
            numero = result_data.get('numero', '')
            data_entrega = result_data.get('dataEntrega', '')

        prazo_dias = 0
        if data_entrega:
            try:
                from datetime import datetime as dt
                entrega = dt.strptime(data_entrega, "%d/%m/%Y")
                prazo_dias = max(0, (entrega - dt.now()).days)
            except Exception:
                pass

        restricoes = f"Cotação #{numero}" if numero else "Cotação AGEX"
        if data_entrega:
            restricoes += f" - Entrega: {data_entrega}"

        self.last_error = None
        return valor_frete, prazo_dias, restricoes

    async def coteir(
        self, origem: str, destino: str, peso: float, valor: float,
        tipo_pagador: str = "remetente",
    ) -> Optional[Cotacao]:
        try:
            self.last_error = None
            await self._init_browser()

            if not self.cnpj_destinatario and not destino:
                self.last_error = "CNPJ/CPF do destinatário não informado"
                logger.error(f"[{self.nome}] {self.last_error}")
                return None

            if not await self._login():
                if not self.last_error:
                    self.last_error = "Falha no login AGEX"
                return None

            # AGEX é SPA — NÃO criar nova página (localStorage/sessionStorage perdem auth).
            # Navegar para /inicio primeiro para resetar estado do formulário.
            if self._logged_in:
                try:
                    await self._page.goto(
                        "https://cliente.agex.com.br/inicio",
                        wait_until="domcontentloaded",
                        timeout=self.DEFAULT_TIMEOUT_MS,
                    )
                    await self._page.wait_for_timeout(300)
                except Exception as nav_err:
                    logger.warning(f"[{self.nome}] Falha ao navegar para /inicio: {nav_err}")

            remetente = origem or self.cnpj_remetente
            destinatario = destino or self.cnpj_destinatario

            if int(self.volumes or 0) <= 0:
                self.last_error = "Cotação bloqueada: volumes inválidos no romaneio"
                logger.error(f"[{self.nome}] {self.last_error}")
                return None

            if not self._normalizar_cubagens(self.cubagens):
                self.last_error = "Cotação bloqueada: sem tamanhos do romaneio"
                logger.error(f"[{self.nome}] {self.last_error}")
                return None

            logger.info(f"[{self.nome}] Preenchendo cotação...")
            await self._preencher_cotacao(remetente, destinatario, peso, valor, tipo_pagador=tipo_pagador)

            logger.info(f"[{self.nome}] Extraindo resultado...")
            resultado = await self._extrair_resultado()
            if not resultado:
                if not self.last_error:
                    self.last_error = "AGEX sem resultado na extração"
                return None

            valor_frete, prazo_dias, restricoes = resultado
            logger.info(f"[{self.nome}] ✅ R$ {valor_frete:.2f} - {prazo_dias} dias")
            self.last_error = None
            return Cotacao(
                transportadora=self.nome,
                prazo_dias=prazo_dias,
                valor_frete=round(valor_frete, 2),
                restricoes=restricoes,
                timestamp=datetime.now(),
            )
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"[{self.nome}] Erro na cotação: {e}")
            return None

    async def cotear(
        self, origem: str, destino: str, peso: float, valor: float
    ) -> Optional[Cotacao]:
        return await self.coteir(origem, destino, peso, valor)
