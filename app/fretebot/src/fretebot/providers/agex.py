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
    COTACAO_URL = "https://cliente.agex.com.br/cotacao"
    DEFAULT_TIMEOUT_MS = 30000

    def __init__(
        self,
        cnpj: str,
        senha: str,
        email: str = "",
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
        self.email = email
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
        from fretebot.providers.base import launch_browser_resilient
        self._browser = await launch_browser_resilient(
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

    async def _fechar_popups(self) -> None:
        """Fecha qualquer alertdialog ou modal de aviso que esteja bloqueando a tela."""
        page = self._page
        try:
            # 1. Tentar Escape primeiro (fecha maioria dos dialogs Radix)
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(400)
        except Exception:
            pass

        # 2. Se ainda houver alertdialog aberto, clicar no primeiro botão dentro dele
        try:
            dialog = page.locator("[role='alertdialog'][data-state='open']")
            if await dialog.count() > 0:
                # Tentar botões conhecidos
                for name in ("Ok", "Entendi", "Fechar", "Confirmar", "Continuar", "Aceitar"):
                    btn = dialog.get_by_role("button", name=name)
                    if await btn.count() > 0:
                        await btn.first.click()
                        await page.wait_for_timeout(500)
                        return
                # Fallback: clicar no último botão do dialog (normalmente "Confirmar")
                btns = dialog.locator("button")
                cnt = await btns.count()
                if cnt > 0:
                    await btns.nth(cnt - 1).click()
                    await page.wait_for_timeout(500)
        except Exception:
            pass

    async def _buscar_cep_viacep(self, cep: str) -> dict:
        """Busca dados de endereço via ViaCEP. Retorna dict vazio em caso de falha."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"https://viacep.com.br/ws/{cep}/json/")
                if r.status_code == 200:
                    data = r.json()
                    if not data.get("erro"):
                        return data
        except Exception as e:
            logger.warning(f"[{self.nome}] ViaCEP lookup falhou: {e}")
        return {}

    async def _login(self) -> bool:
        """Login com e-mail e senha (site atualizado 2025)."""
        if self._logged_in:
            return True

        if not self.email:
            # sem email configurado: tenta cotacao publica
            return True

        for tentativa in range(1, 3):
            try:
                await self._page.goto(
                    self.LOGIN_URL, wait_until="domcontentloaded", timeout=60000
                )
                await self._page.wait_for_timeout(1000)
                # Fechar qualquer popup/alertdialog que apareça antes do login
                await self._fechar_popups()

                email_input = self._page.locator('input[name="email"]')
                await email_input.wait_for(timeout=self.DEFAULT_TIMEOUT_MS)
                await email_input.fill(self.email)
                senha_input = self._page.locator('input[name="password"]')
                await senha_input.wait_for(state="visible", timeout=8000)
                await senha_input.fill(self.senha)
                await self._page.get_by_role("button", name="Entrar").click()
                try:
                    await self._page.wait_for_url("**/inicio**", timeout=self.DEFAULT_TIMEOUT_MS)
                except Exception:
                    try:
                        await self._page.wait_for_url("**/cotacao**", timeout=10000)
                    except Exception:
                        if "login" in self._page.url.lower():
                            body_text = await self._page.inner_text("body")
                            if "inválid" in body_text.lower() or "incorrect" in body_text.lower():
                                self.last_error = "Credenciais AGEX invalidas (e-mail ou senha)"
                                return False
                            if tentativa < 2:
                                continue
                            self.last_error = "Login nao redirecionou apos preencher credenciais"
                            return False
                self._logged_in = True
                return True
            except Exception as e:
                if tentativa < 2:
                    continue
                self.last_error = f"Erro no login: {e}"
                return False
        return False


    async def pre_login(self):
        """Inicializa browser e faz login antecipadamente."""
        await self._init_browser()
        await self._login()

    async def cleanup(self):
        """Fecha o browser."""
        # Ordem: page → context → browser → playwright (Node.js driver)
        # Parar playwright por último evita EPIPE no Node.js
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
        self._browser = None
        self._context = None
        self._page = None
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

    async def _selecionar_pagador_radix(self, page, tipo_pagador: str) -> None:
        """Seleciona tipo pagador (SELECT nativo da interface atual do AGEX)."""
        # Interface atual usa <SELECT> nativo sem name/id visível na tela
        sel = page.locator("select").first
        if await sel.count() > 0:
            opts = await page.evaluate("""() => {
                const sel = document.querySelector('select');
                if (!sel) return [];
                return Array.from(sel.options).map(o => ({value: o.value, text: o.text.trim()}));
            }""")
            logger.info(f"[{self.nome}] AGEX SELECT options: {opts}")
            # Fragmentos para matching parcial (sem acentos, case-insensitive)
            fragmento_map = {
                "remetente": "emetente",
                "destinatario": "estinat",
                "terceiro": "erceiro",
            }
            fragmento = fragmento_map.get(tipo_pagador.lower(), "emetente")
            matched = next(
                (o for o in opts if fragmento.lower() in o["text"].lower()
                 or fragmento.lower() in o["value"].lower()),
                None
            )
            if matched and matched["value"]:
                await sel.select_option(value=matched["value"])
            elif matched:
                await sel.select_option(label=matched["text"])
            else:
                # Fallback: selecionar primeiro option não-vazio
                non_empty = next((o for o in opts if o["value"]), None)
                if non_empty:
                    await sel.select_option(value=non_empty["value"])
            await page.wait_for_timeout(200)
            return
        # Fallback: Radix Select (interface antiga ou futura)
        combo = page.locator("[id$='-form-item'][aria-expanded], button[role='combobox']").first
        if await combo.count() > 0:
            await combo.click()
            await page.wait_for_timeout(300)
            texto_map = {
                "remetente": "Remetente",
                "destinatario": "Destinatario",
                "terceiro": "Terceiro",
            }
            texto = texto_map.get(tipo_pagador.lower(), "Remetente")
            opt = page.locator("[role=option]").filter(has_text=texto).first
            if await opt.count() > 0:
                await opt.click()
                await page.wait_for_timeout(200)

    async def _preencher_endereco_cep(self, page, cep: str) -> None:
        """Preenche campo CEP e aguarda lookup de cidade/estado."""
        cep_loc = page.locator("input[name=cep]").first
        await cep_loc.fill(cep)
        await cep_loc.press("Tab")
        # Aguardar lookup de CEP (até 5s)
        for _ in range(20):
            city_val = await page.evaluate(
                "() => (document.querySelector('input[name=city]') || {}).value || ''"
            )
            if city_val and len(city_val) > 1:
                break
            await page.wait_for_timeout(250)

    async def _preencher_cotacao(
        self, remetente: str, destinatario: str, peso: float, valor: float,
        tipo_pagador: str = "remetente",
    ) -> None:
        page = self._page
        etapa = "abrir_cotacoes"
        try:
            # Nova interface usa /cotacao (não /cotacao/cotar)
            await page.goto(
                "https://cliente.agex.com.br/cotacao",
                wait_until="domcontentloaded",
                timeout=self.DEFAULT_TIMEOUT_MS,
            )
            await page.wait_for_timeout(800)

            # Verificar se foi redirecionado para login
            current_url = page.url
            logger.info(f"[{self.nome}] URL após goto cotação: {current_url}")
            if "login" in current_url.lower():
                logger.warning(f"[{self.nome}] Sessão expirou, refazendo login...")
                self._logged_in = False
                if not await self._login():
                    raise Exception("Re-login falhou")
                await page.goto(
                    "https://cliente.agex.com.br/cotacao",
                    wait_until="domcontentloaded",
                    timeout=self.DEFAULT_TIMEOUT_MS,
                )
                await page.wait_for_timeout(800)

            # ETAPA 1: Tipo Pagador (nova interface: Radix Select)
            etapa = "solicitante"
            await self._fechar_popups()
            await self._selecionar_pagador_radix(page, tipo_pagador)
            # CNPJ do pagador (input[name=document]) — já deve vir preenchido,
            # mas garantimos que está correto.
            doc_loc = page.locator("input[name=document]").first
            if await doc_loc.count() > 0:
                val_atual = await doc_loc.input_value()
                if not val_atual:
                    await doc_loc.fill(remetente)
            await self._fechar_popups()
            await page.locator("button[type=submit]").first.click()
            await page.wait_for_timeout(1500)

            # ETAPA 2: Dados da origem (remetente)
            # O endereço do remetente já vem pré-preenchido do perfil da empresa.
            # Basta aguardar a tela aparecer e clicar "Próximo".
            etapa = "remetente"
            await page.locator("input[name=cep]").first.wait_for(timeout=self.DEFAULT_TIMEOUT_MS)
            city_val = await page.evaluate(
                "() => (document.querySelector('input[name=city]') || {}).value || ''"
            )
            logger.info(f"[{self.nome}] Step 2 (remetente) carregado: cidade={city_val!r}")
            await self._fechar_popups()
            await page.locator("button[type=submit]").first.click()
            await page.wait_for_timeout(2000)

            # ETAPA 3: Dados do destino (destinatário)
            etapa = "destinatario"
            await page.locator("input[name=document]").first.wait_for(
                timeout=self.DEFAULT_TIMEOUT_MS
            )
            dest_doc_loc = page.locator("input[name=document]").first
            if await dest_doc_loc.is_enabled():
                # fill() despacha eventos React corretamente mesmo em masked inputs
                await dest_doc_loc.click()
                await dest_doc_loc.fill(destinatario)
                await page.wait_for_timeout(400)
                await dest_doc_loc.press("Tab")
                await page.wait_for_timeout(300)
            if len(self.cep_destino) == 8:
                cep_dest_loc = page.locator("input[name=cep]").first
                await cep_dest_loc.click()
                # Limpar e digitar char a char para ativar a máscara e o lookup React
                await cep_dest_loc.fill("")
                await page.keyboard.type(self.cep_destino, delay=60)
                await cep_dest_loc.press("Tab")
                # Aguardar lookup automático da página
                city_val = ""
                for _ in range(30):
                    city_val = await page.evaluate(
                        "() => (document.querySelector('input[placeholder=\"Cidade\"]') || {}).value || ''"
                    )
                    if city_val and len(city_val) > 1:
                        break
                    await page.wait_for_timeout(250)
                logger.info(f"[{self.nome}] CEP destino lookup: cidade={city_val!r}")
                # Fallback: se o lookup não preencheu a cidade, buscar via ViaCEP e preencher manualmente
                if not city_val:
                    logger.info(f"[{self.nome}] Lookup CEP falhou, preenchendo via ViaCEP")
                    dados_cep = await self._buscar_cep_viacep(self.cep_destino)
                    if dados_cep:
                        campos_addr = [
                            ("Cidade", dados_cep.get("localidade", "")),
                            ("Estado (UF)", dados_cep.get("uf", "")),
                            ("Bairro", dados_cep.get("bairro", "") or "Centro"),
                            ("Rua", dados_cep.get("logradouro", "") or "S/N"),
                        ]
                        for placeholder, valor in campos_addr:
                            if not valor:
                                continue
                            await page.evaluate(
                                """([ph, val]) => {
                                    const input = document.querySelector('input[placeholder="' + ph + '"]');
                                    if (!input) return;
                                    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                                    setter.call(input, val);
                                    input.dispatchEvent(new Event('input', { bubbles: true }));
                                    input.dispatchEvent(new Event('change', { bubbles: true }));
                                }""",
                                [placeholder, valor],
                            )
                            await page.wait_for_timeout(100)
                        logger.info(f"[{self.nome}] Endereço preenchido via ViaCEP: {dados_cep.get('localidade')}/{dados_cep.get('uf')}")
            await self._fechar_popups()
            await page.locator("button[type=submit]").first.click()
            await page.wait_for_timeout(2000)

            # ETAPA 4: Carga
            # Campos confirmados: totalMerchandiseValue, cubageValues.0.height/width/length/weight/quantity,
            # totalCubage, amount, totalWeight
            etapa = "carga_valores"
            logger.info(f"[{self.nome}] Iniciando etapa 4 (carga), URL: {page.url}")
            await page.locator("input[name='totalMerchandiseValue']").wait_for(
                timeout=self.DEFAULT_TIMEOUT_MS
            )

            # Selecionar tipo de produto (Radix combobox)
            combo_produto = page.locator("button[role=combobox]").first
            if await combo_produto.count() > 0:
                await combo_produto.click()
                await page.wait_for_timeout(600)
                # Selecionar "Carga Geral" (primeira e mais genérica opção)
                opt = page.locator("[role=option]").filter(has_text="Carga Geral").first
                if await opt.count() > 0:
                    await opt.click()
                else:
                    # Fallback: primeira opção disponível
                    await page.locator("[role=option]").first.click()
                await page.wait_for_timeout(300)

            # Valor da nota fiscal
            # A máscara do campo strips o ponto — enviar 2 casas decimais: "34.65" → digits "3465" → "34,65"
            valor_loc = page.locator("input[name='totalMerchandiseValue']")
            await valor_loc.fill(f"{valor:.2f}")
            await valor_loc.press("Tab")
            await page.wait_for_timeout(300)

            # Cubagem - usa somente linhas vindas do romaneio.
            etapa = "carga_cubagem"
            cubagens = self._normalizar_cubagens(self.cubagens)
            if not cubagens:
                raise Exception("AGEX sem cubagens do romaneio (nenhum tamanho informado)")
            logger.info(f"[{self.nome}] Preenchendo {len(cubagens)} linha(s) de cubagem")

            for idx, cub in enumerate(cubagens):
                if idx > 0:
                    # Aguardar nova linha aparecer (botão "+" deve ter sido clicado)
                    for _ in range(20):
                        if await page.locator(f"input[name='cubageValues.{idx}.height']").count() > 0:
                            break
                        await page.wait_for_timeout(250)

                altura_m = float(cub["altura_m"])
                largura_m = float(cub["largura_m"])
                comp_m = float(cub["comprimento_m"])
                peso_unit = peso / int(cub.get("quantidade", 1))

                for field, value in [
                    (f"cubageValues.{idx}.height", int(round(altura_m * 100))),   # cm inteiro
                    (f"cubageValues.{idx}.width",  int(round(largura_m * 100))),
                    (f"cubageValues.{idx}.length", int(round(comp_m * 100))),
                    (f"cubageValues.{idx}.weight", peso_unit),
                    (f"cubageValues.{idx}.quantity", int(cub.get("quantidade", 1))),
                ]:
                    loc = page.locator(f"input[name='{field}']").first
                    if await loc.count() == 0:
                        continue
                    # Cm e quantidade: inteiro; peso: 2 casas decimais (máscara strips ponto)
                    if field.endswith("weight"):
                        val_str = f"{value:.2f}"
                    else:
                        val_str = str(int(value))
                    await loc.fill(val_str)
                    await loc.press("Tab")
                    await page.wait_for_timeout(100)

                # Adicionar linha se houver mais volumes
                if idx < len(cubagens) - 1:
                    # Clicar no botão "+" (ícone de adicionar volume)
                    added = await page.evaluate("""(nextIdx) => {
                        // procurar botão de adicionar linha de cubagem
                        const btns = Array.from(document.querySelectorAll("button"));
                        for (const b of btns) {
                            const t = (b.textContent||'').trim();
                            if (t === '+' || t === 'Adicionar volume' || b.getAttribute('aria-label') === 'Adicionar volume') {
                                b.click(); return true;
                            }
                        }
                        // fallback: svgs com plus
                        const svg = document.querySelector("button svg[data-lucide='plus'], button svg.lucide-plus");
                        if (svg) { svg.closest('button').click(); return true; }
                        return false;
                    }""", idx)
                    await page.wait_for_timeout(500)
                    if not added:
                        logger.warning(f"[{self.nome}] Não encontrou botão '+' para linha {idx+2}")

            # Quantidade de volumes (campo separado do romaneio)
            total_volumes = sum(int(c.get("quantidade", 1)) for c in cubagens)
            amount_loc = page.locator("input[name='amount']")
            if await amount_loc.count() > 0 and await amount_loc.is_enabled():
                await amount_loc.fill(str(total_volumes))
                await amount_loc.press("Tab")
                await page.wait_for_timeout(200)

            # Peso total (campo pode estar disabled se calculado automaticamente)
            total_peso_loc = page.locator("input[name='totalWeight']")
            if await total_peso_loc.count() > 0 and await total_peso_loc.is_enabled():
                await total_peso_loc.fill(f"{peso:.2f}")
                await total_peso_loc.press("Tab")
                await page.wait_for_timeout(200)

            # Continuar para confirmação
            etapa = "confirmar"
            await page.locator("button[type=submit]").first.click()
            await page.wait_for_timeout(2000)
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

        # Screenshot imediato para diagnóstico do estado pós-submit etapa 4
        await self._salvar_debug("pos_submit_etapa4")

        # Etapa 5 opcional: página de confirmação/revisão antes do resultado
        # Se existir botão de confirmação, clicar para enviar a cotação de fato
        try:
            import re as _re
            confirm_loc = page.locator("button").filter(
                has_text=_re.compile(
                    r"confirmar\s+cota[çc][aã]o|solicitar\s+cota[çc][aã]o|confirmar|enviar\s+cota[çc][aã]o",
                    _re.IGNORECASE,
                )
            )
            if await confirm_loc.count() > 0:
                logger.info(f"[{self.nome}] Etapa de confirmação detectada — clicando para confirmar")
                await confirm_loc.first.click()
                await page.wait_for_timeout(3000)
                await self._salvar_debug("pos_confirmar")
        except Exception as _e:
            logger.warning(f"[{self.nome}] Erro ao tentar clicar confirmação: {_e}")

        # Aguardar resultado no layout antigo (#resultado) OU no layout novo
        try:
            status = await page.evaluate("""() => new Promise((resolve) => {
                const check = () => {
                    const el = document.getElementById('resultado');
                    if (el && el.offsetParent !== null) return resolve('ok');
                    const txt = (document.body.innerText || '').toLowerCase();
                    if (txt.includes('resultado da cota\u00e7\u00e3o') ||
                        txt.includes('resultado da cotacao') ||
                        txt.includes('valor do frete') ||
                        txt.includes('pre\u00e7o do frete') ||
                        txt.includes('total do frete') ||
                        txt.includes('valor frete'))
                        return resolve('ok_new');
                    if (txt.includes('n\u00e3o conseguimos buscar o pre\u00e7o') ||
                        txt.includes('nao conseguimos buscar o preco') ||
                        txt.includes('entre em contato com nosso comercial') ||
                        txt.includes('n\u00e3o atendemos') ||
                        txt.includes('cep n\u00e3o atendido') ||
                        txt.includes('fora da \u00e1rea'))
                        return resolve('indisponivel');
                };
                check();
                const id = setInterval(check, 300);
                setTimeout(() => { clearInterval(id); resolve('timeout'); }, 40000);
            })""")
        except Exception:
            status = "timeout"

        if status == "indisponivel":
            self.last_error = "Cota\u00e7\u00e3o n\u00e3o dispon\u00edvel automaticamente para esta rota"
            logger.info(f"[{self.nome}] {self.last_error}")
            await self._salvar_debug("indisponivel")
            return None

        if status not in ("ok", "ok_new"):
            self.last_error = "P\u00e1gina de resultado da AGEX n\u00e3o apareceu"
            logger.error(f"[{self.nome}] {self.last_error}")
            await self._salvar_debug("sem_resultado")
            return None

        result_data = await page.evaluate("""() => {
            const out = {};
            const section = document.getElementById('resultado');
            if (section) {
                const spans = section.querySelectorAll('span');
                for (const sp of spans) {
                    const parent = sp.closest('p') || sp.parentElement;
                    const parentText = (parent ? parent.textContent : '').toLowerCase();
                    const val = (sp.textContent || '').trim();
                    if (!val) continue;
                    if (parentText.includes('frete')) out.frete = val;
                    else if (parentText.includes('n\u00famero') || parentText.includes('numero')) out.numero = val;
                    else if (parentText.includes('data') && parentText.includes('entrega')) out.dataEntrega = val;
                }
            }
            const bodyText = (document.body && document.body.innerText) ? document.body.innerText : '';
            if (!out.bodyText) out.bodyText = bodyText;
            return out;
        }""")
        logger.info(f"[{self.nome}] Dados extra\u00eddos de #resultado: {result_data}")

        valor_frete = None
        if isinstance(result_data, dict) and result_data.get('frete'):
            try:
                valor_frete = self._parse_brl(result_data['frete'].replace('R$', '').strip())
            except Exception:
                pass

        if valor_frete is None:
            texto = ""
            if isinstance(result_data, dict):
                texto = str(result_data.get('bodyText') or "")
            if not texto:
                texto = await page.inner_text("body")
            valor_frete = self._extrair_valor_frete_do_texto(texto)

        if valor_frete is None:
            self.last_error = "Valor do frete n\u00e3o encontrado na p\u00e1gina de resultado"
            logger.error(f"[{self.nome}] {self.last_error}")
            await self._salvar_debug("sem_valor")
            return None

        numero = ""
        data_entrega = ""
        if isinstance(result_data, dict):
            numero = result_data.get('numero', '')
            data_entrega = result_data.get('dataEntrega', '')

        body_text = str(result_data.get('bodyText') or "") if isinstance(result_data, dict) else ""
        if body_text:
            if not numero:
                m_num = re.search(r"(?i)cota\u00e7[a\u00e3]o\s*:\s*(\d+)", body_text)
                if m_num:
                    numero = m_num.group(1)
            if not data_entrega:
                m_entrega = re.search(r"(?i)previs[a\u00e3]o\s+de\s+entrega\s*(\d{2}/\d{2}/\d{2,4})", body_text)
                if m_entrega:
                    data_entrega = m_entrega.group(1)

        prazo_dias = 0
        if data_entrega:
            try:
                from datetime import datetime as dt
                if re.match(r"^\d{2}/\d{2}/\d{2}$", data_entrega):
                    entrega = dt.strptime(data_entrega, "%d/%m/%y")
                else:
                    entrega = dt.strptime(data_entrega, "%d/%m/%Y")
                prazo_dias = max(0, (entrega - dt.now()).days)
            except Exception:
                pass

        restricoes = f"Cota\u00e7\u00e3o #{numero}" if numero else "Cota\u00e7\u00e3o AGEX"
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

            remetente = self.cnpj_remetente or origem
            destinatario = self.cnpj_destinatario or destino

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

