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
        cnpj: str = "",
        senha: str = "",
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

    async def _clicar_botao_fluxo(self, page, labels: tuple[str, ...] = ("Próximo", "Proximo", "Continuar")) -> bool:
        """Clica no botão principal da etapa, com fallbacks por texto e submit."""
        for label in labels:
            try:
                btn = page.get_by_role("button", name=re.compile(label, re.IGNORECASE)).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click()
                    return True
            except Exception:
                continue

        for sel in ("button[type=submit]", "form button[type=submit]"):
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click()
                    return True
            except Exception:
                continue
        return False

    async def _login(self) -> bool:
        """Login AGEX via e-mail corporativo + senha."""
        if self._logged_in:
            return True

        email_cfg = (self.email or "").strip()
        senha_cfg = (self.senha or "").strip()

        if not email_cfg or not senha_cfg:
            self.last_error = "AGEX exige email e senha no CONFIG"
            return False

        for tentativa in range(1, 3):
            try:
                await self._page.goto(
                    self.LOGIN_URL, wait_until="domcontentloaded", timeout=60000
                )
                await self._page.wait_for_timeout(1000)
                await self._fechar_popups()

                if "login" not in (self._page.url or "").lower():
                    self._logged_in = True
                    return True

                email_input = self._page.locator(
                    'input[name="email"], input[type="email"], input[placeholder*="mail" i]'
                ).first
                if await email_input.count() == 0:
                    self.last_error = "Campo de e-mail nao encontrado no login AGEX"
                    await self._salvar_debug("login_sem_email")
                    return False

                senha_input = self._page.locator(
                    'input[name="password"], input[type="password"], input[placeholder*="senha" i]'
                ).first
                await email_input.wait_for(timeout=self.DEFAULT_TIMEOUT_MS)
                await email_input.fill(email_cfg)
                await senha_input.wait_for(state="visible", timeout=12000)
                await senha_input.fill(senha_cfg)

                clicou_login = False
                for label in ("Entrar", "Iniciar sessão", "Iniciar sessao", "Acessar", "Login"):
                    try:
                        btn = self._page.get_by_role("button", name=re.compile(label, re.IGNORECASE)).first
                        if await btn.count() > 0 and await btn.is_visible():
                            await btn.click()
                            clicou_login = True
                            break
                    except Exception:
                        continue
                if not clicou_login:
                    submit_btn = self._page.locator('button[type="submit"], input[type="submit"]').first
                    if await submit_btn.count() > 0:
                        await submit_btn.click()
                        clicou_login = True
                if not clicou_login:
                    await senha_input.press("Enter")

                try:
                    await self._page.wait_for_url("**/dashboard**", timeout=self.DEFAULT_TIMEOUT_MS)
                except Exception:
                    try:
                        await self._page.wait_for_url("**/inicio**", timeout=10000)
                    except Exception:
                        try:
                            await self._page.wait_for_url("**/cotacao**", timeout=10000)
                        except Exception:
                            pass

                if "login" in (self._page.url or "").lower():
                    body_lower = (await self._page.inner_text("body")).lower()
                    if (
                        "inválid" in body_lower
                        or "inval" in body_lower
                        or "incorreta" in body_lower
                        or "incorreto" in body_lower
                        or "não autorizado" in body_lower
                        or "nao autorizado" in body_lower
                    ):
                        self.last_error = "Credenciais AGEX invalidas"
                        return False
                    if tentativa < 2:
                        continue
                    self.last_error = "Login nao redirecionou apos preencher credenciais"
                    await self._salvar_debug("login_sem_redirect")
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

    async def _set_react_select(self, page, value: str, selector: str = "select") -> bool:
        """Define valor em <select> React via native setter para disparar onChange."""
        try:
            result = await page.evaluate(
                """([sel, val]) => {
                    const selects = Array.from(document.querySelectorAll(sel));
                    const visible = selects.filter(s => s.offsetParent !== null);
                    if (!visible.length) return false;
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLSelectElement.prototype, 'value'
                    ).set;
                    setter.call(visible[0], val);
                    visible[0].dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }""",
                [selector, value],
            )
            return bool(result)
        except Exception as e:
            logger.warning(f"[{self.nome}] _set_react_select falhou: {e}")
            return False

    async def _set_react_input(self, page, selector: str, value: str) -> bool:
        """Define valor em <input> React via native setter para disparar onChange."""
        try:
            result = await page.evaluate(
                """([sel, val]) => {
                    const input = document.querySelector(sel);
                    if (!input) return false;
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    ).set;
                    setter.call(input, val);
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }""",
                [selector, value],
            )
            return bool(result)
        except Exception as e:
            logger.warning(f"[{self.nome}] _set_react_input({selector}) falhou: {e}")
            return False

    async def _selecionar_pagador(self, page, tipo_pagador: str) -> None:
        """Seleciona tipo pagador no <select> nativo do portal AGEX."""
        # Portal novo: <select> sem name/id; primeiro select visível da página
        fragmento_map = {
            "remetente": "emetente",
            "destinatario": "estinat",
            "terceiro": "erceiro",
        }
        fragmento = fragmento_map.get(tipo_pagador.lower(), "emetente")

        opts = await page.evaluate("""() => {
            const selects = Array.from(document.querySelectorAll('select'))
                .filter(s => s.offsetParent !== null);
            if (!selects.length) return [];
            return Array.from(selects[0].options).map(o => ({
                value: o.value, text: o.text.trim()
            }));
        }""")
        logger.info(f"[{self.nome}] Pagador SELECT options: {opts}")

        matched = next(
            (o for o in opts
             if fragmento.lower() in o["text"].lower() or fragmento.lower() in o["value"].lower()),
            None,
        )
        target = matched["value"] if matched and matched["value"] else (
            matched["text"] if matched else None
        )
        if not target:
            # Fallback: primeiro option não-vazio
            non_empty = next((o for o in opts if o["value"]), None)
            target = non_empty["value"] if non_empty else None

        if target:
            await self._set_react_select(page, target)
        await page.wait_for_timeout(200)

    async def _preencher_cotacao(
        self, remetente: str, destinatario: str, peso: float, valor: float,
        tipo_pagador: str = "remetente",
    ) -> None:
        page = self._page
        etapa = "abrir_cotacao"
        try:
            # ── Navegar para /cotacao ──────────────────────────────────────────
            await page.goto(
                self.COTACAO_URL,
                wait_until="domcontentloaded",
                timeout=self.DEFAULT_TIMEOUT_MS,
            )
            await page.wait_for_timeout(800)

            if "login" in (page.url or "").lower():
                logger.warning(f"[{self.nome}] Sessão expirou, refazendo login...")
                self._logged_in = False
                if not await self._login():
                    raise Exception("Re-login falhou")
                await page.goto(
                    self.COTACAO_URL,
                    wait_until="domcontentloaded",
                    timeout=self.DEFAULT_TIMEOUT_MS,
                )
                await page.wait_for_timeout(800)

            # ── ETAPA 1: Tipo Pagador ─────────────────────────────────────────
            etapa = "tipo_pagador"
            await self._fechar_popups()
            # Aguardar o select de pagador ficar visível
            try:
                await page.locator("select").first.wait_for(
                    state="visible", timeout=self.DEFAULT_TIMEOUT_MS
                )
            except PlaywrightTimeoutError:
                logger.warning(f"[{self.nome}] Select pagador não apareceu; tentando continuar")

            await self._selecionar_pagador(page, tipo_pagador)
            await page.wait_for_timeout(300)

            if not await self._clicar_botao_fluxo(page, ("Próximo", "Proximo", "Continuar")):
                raise Exception("Botão Próximo não encontrado na etapa tipo_pagador")
            await page.wait_for_timeout(1200)

            # ── ETAPA 2: Dados da Origem (endereço pré-preenchido) ────────────
            # A etapa 2 exibe o endereço do remetente como texto read-only.
            # Não há campos para preencher — apenas clicar Próximo.
            etapa = "dados_origem"
            await page.wait_for_timeout(800)  # aguardar accordion abrir
            await self._fechar_popups()
            if not await self._clicar_botao_fluxo(page, ("Próximo", "Proximo", "Continuar")):
                raise Exception("Botão Próximo não encontrado na etapa dados_origem")
            await page.wait_for_timeout(1500)

            # ── ETAPA 3: Dados do Destino ─────────────────────────────────────
            etapa = "dados_destino"
            # Aguardar campo CNPJ do destinatário
            dest_cnpj_loc = page.locator('input[name="document"]').first
            try:
                await dest_cnpj_loc.wait_for(state="visible", timeout=self.DEFAULT_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                # Fallback por placeholder
                dest_cnpj_loc = page.locator(
                    'input[placeholder*="CNPJ" i], input[placeholder*="CPF" i], '
                    'input[placeholder*="Destinat" i]'
                ).first
                await dest_cnpj_loc.wait_for(state="visible", timeout=10000)

            await dest_cnpj_loc.click()
            await dest_cnpj_loc.fill(destinatario)
            await page.wait_for_timeout(400)
            await dest_cnpj_loc.press("Tab")

            # Aguardar auto-preenchimento via lookup de CNPJ (até 5s)
            city_autofill = ""
            for _ in range(20):
                city_autofill = await page.evaluate(
                    "() => (document.querySelector('input[name=\"city\"]') || {}).value || ''"
                )
                if city_autofill and len(city_autofill) > 1:
                    break
                await page.wait_for_timeout(250)

            if city_autofill:
                logger.info(f"[{self.nome}] CNPJ lookup preencheu cidade: {city_autofill!r}")
            else:
                # Lookup não preencheu → tentar via CEP
                logger.info(f"[{self.nome}] CNPJ lookup sem resultado; tentando CEP")
                cep_dest = self._digits(self.cep_destino or "")
                if len(cep_dest) == 8:
                    cep_loc = page.locator('input[name="cep"]').first
                    if await cep_loc.count() > 0 and await cep_loc.is_visible():
                        await cep_loc.click()
                        await cep_loc.fill("")
                        await page.keyboard.type(cep_dest, delay=60)
                        await cep_loc.press("Tab")
                        # Aguardar lookup CEP
                        for _ in range(24):
                            city_autofill = await page.evaluate(
                                "() => (document.querySelector('input[name=\"city\"]') || {}).value || ''"
                            )
                            if city_autofill and len(city_autofill) > 1:
                                break
                            await page.wait_for_timeout(250)
                        logger.info(f"[{self.nome}] CEP lookup cidade: {city_autofill!r}")

                # Se ainda vazio, preencher manualmente via ViaCEP
                if not city_autofill and len(cep_dest) == 8:
                    dados_cep = await self._buscar_cep_viacep(cep_dest)
                    if dados_cep:
                        campos = [
                            ("city",         dados_cep.get("localidade", "")),
                            ("state",        dados_cep.get("uf", "")),
                            ("neighborhood", dados_cep.get("bairro", "") or "Centro"),
                            ("address",      dados_cep.get("logradouro", "") or "S/N"),
                        ]
                        for name_attr, val in campos:
                            if not val:
                                continue
                            await self._set_react_input(page, f'input[name="{name_attr}"]', val)
                            await page.wait_for_timeout(80)
                        logger.info(f"[{self.nome}] Endereço preenchido via ViaCEP: "
                                    f"{dados_cep.get('localidade')}/{dados_cep.get('uf')}")

            await self._fechar_popups()
            if not await self._clicar_botao_fluxo(page, ("Próximo", "Proximo", "Continuar")):
                raise Exception("Botão Próximo não encontrado na etapa dados_destino")
            await page.wait_for_timeout(1500)

            # ── ETAPA 4: Carga ────────────────────────────────────────────────
            etapa = "carga_nf"
            logger.info(f"[{self.nome}] Etapa carga, URL: {page.url}")

            # Aguardar o campo de valor da NF (novo portal)
            nf_loc = page.locator('input[placeholder="Valor total da nota fiscal"]').first
            try:
                await nf_loc.wait_for(state="visible", timeout=self.DEFAULT_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                # Fallback: campo por name (layout futuro)
                nf_loc = page.locator(
                    'input[name="totalMerchandiseValue"], '
                    'input[placeholder*="nota fiscal" i], '
                    'input[placeholder*="valor" i]'
                ).first
                await nf_loc.wait_for(state="visible", timeout=10000)

            # Preencher valor NF — campo pode ter máscara de moeda, usar keyboard.type
            await nf_loc.click()
            await nf_loc.fill("")
            await page.keyboard.type(str(int(round(valor))), delay=30)
            await nf_loc.press("Tab")
            await page.wait_for_timeout(200)

            # ── Tipo de Produto (Radix Combobox — NÃO é <select> nativo) ────────
            etapa = "carga_tipo_produto"
            try:
                # O portal usa button[role="combobox"] do Radix UI, não <select>
                opened = await page.evaluate(r"""() => {
                    const btn = Array.from(document.querySelectorAll('button[role="combobox"]'))
                        .find(b => b.offsetParent !== null);
                    if (!btn) return false;
                    btn.click();
                    return true;
                }""")
                if opened:
                    await page.wait_for_timeout(700)
                    # As opções aparecem em [role="option"] dentro do popover Radix
                    tipo_opts = await page.evaluate(r"""() => {
                        return Array.from(document.querySelectorAll('[role="option"]'))
                            .map(o => o.textContent.trim())
                            .filter(t => t.length > 0);
                    }""")
                    logger.info(f"[{self.nome}] Tipo produto options (radix): {tipo_opts}")

                    target_text = self.tipo_produto.lower()
                    matched_tp = next(
                        (t for t in tipo_opts if target_text in t.lower()),
                        None,
                    )
                    if not matched_tp:
                        matched_tp = next(
                            (t for t in tipo_opts if t.lower() in target_text),
                            None,
                        )
                    if not matched_tp and tipo_opts:
                        matched_tp = tipo_opts[0]  # fallback: primeiro disponível

                    if matched_tp:
                        await page.evaluate(r"""(text) => {
                            const opt = Array.from(document.querySelectorAll('[role="option"]'))
                                .find(o => o.textContent.trim() === text);
                            if (opt) opt.click();
                        }""", matched_tp)
                        await page.wait_for_timeout(400)
                        logger.info(f"[{self.nome}] Tipo produto selecionado: '{matched_tp}'")
                    else:
                        logger.warning(f"[{self.nome}] Tipo produto: nenhuma opção encontrada no combobox")
                        # Fechar o combobox se ficou aberto
                        await page.keyboard.press("Escape")
                        await page.wait_for_timeout(200)
                else:
                    logger.warning(f"[{self.nome}] Combobox tipo produto não encontrado na página")
            except Exception as e:
                logger.warning(f"[{self.nome}] Erro ao selecionar tipo produto: {e}")

            # ── Cubagem (linhas de volume) ─────────────────────────────────────
            etapa = "carga_cubagem"
            cubagens = self._normalizar_cubagens(self.cubagens)
            if not cubagens:
                raise Exception("AGEX: nenhuma cubagem informada no romaneio")
            logger.info(f"[{self.nome}] Preenchendo {len(cubagens)} linha(s) de cubagem")

            for idx, cub in enumerate(cubagens):
                altura_cm = int(round(float(cub["altura_m"]) * 100))
                largura_cm = int(round(float(cub["largura_m"]) * 100))
                comp_cm = int(round(float(cub["comprimento_m"]) * 100))
                qtd = int(cub.get("quantidade", 1))
                # Peso por unidade desta linha
                peso_unit = peso / qtd if qtd > 0 else peso

                # Campos da linha pelo placeholder (novo portal)
                campos_cubagem = [
                    ("Altura",        str(altura_cm)),
                    ("Largura",       str(largura_cm)),
                    ("Comp.",         str(comp_cm)),
                    ("Peso unitário", f"{peso_unit:.2f}"),
                    ("Quantidade",    str(qtd)),
                ]
                for ph, val_str in campos_cubagem:
                    filled = await page.evaluate(
                        """([ph, val, i]) => {
                            const inputs = Array.from(
                                document.querySelectorAll('input[placeholder="' + ph + '"]')
                            );
                            const input = inputs[i] !== undefined ? inputs[i] : inputs[0];
                            if (!input) return false;
                            const setter = Object.getOwnPropertyDescriptor(
                                window.HTMLInputElement.prototype, 'value'
                            ).set;
                            setter.call(input, val);
                            input.dispatchEvent(new Event('input', { bubbles: true }));
                            input.dispatchEvent(new Event('change', { bubbles: true }));
                            return true;
                        }""",
                        [ph, val_str, idx],
                    )
                    if not filled:
                        logger.warning(f"[{self.nome}] Campo '{ph}' não encontrado (linha {idx})")
                    await page.wait_for_timeout(80)

                # Adicionar nova linha se ainda há volumes
                if idx < len(cubagens) - 1:
                    added = await page.evaluate("""() => {
                        const btns = Array.from(document.querySelectorAll('button'));
                        for (const b of btns) {
                            const t = (b.textContent || '').trim();
                            if (t.includes('Adicionar') || t === '+') {
                                b.click();
                                return true;
                            }
                        }
                        // Fallback: botão com ícone plus
                        const svg = document.querySelector(
                            "button svg[data-lucide='plus'], button svg.lucide-plus"
                        );
                        if (svg) { svg.closest('button').click(); return true; }
                        return false;
                    }""")
                    await page.wait_for_timeout(600)
                    if not added:
                        logger.warning(f"[{self.nome}] Botão 'Adicionar' não encontrado para linha {idx+2}")

            # ── Totais ────────────────────────────────────────────────────────
            etapa = "carga_totais"
            total_volumes = sum(int(c.get("quantidade", 1)) for c in cubagens)

            # Quantidade de volumes
            amount_loc = page.locator("input[name='amount']").first
            if await amount_loc.count() > 0:
                try:
                    if await amount_loc.is_enabled():
                        await self._set_react_input(page, "input[name='amount']", str(total_volumes))
                        await page.wait_for_timeout(150)
                except Exception:
                    pass

            # Peso total
            peso_loc = page.locator("input[name='totalWeight']").first
            if await peso_loc.count() > 0:
                try:
                    if await peso_loc.is_enabled():
                        await self._set_react_input(page, "input[name='totalWeight']", f"{peso:.2f}")
                        await page.wait_for_timeout(150)
                except Exception:
                    pass

            # ── Submeter carga ────────────────────────────────────────────────
            etapa = "submit_carga"
            if not await self._clicar_botao_fluxo(page, ("Próximo", "Proximo", "Continuar", "Confirmar")):
                raise Exception("Botão Próximo não encontrado na etapa carga")
            await page.wait_for_timeout(2000)

        except Exception as e:
            if isinstance(e, PlaywrightTimeoutError):
                self.last_error = f"Timeout na etapa: {etapa}"
                logger.error(f"[{self.nome}] Timeout na etapa: {etapa}")
            else:
                self.last_error = f"Falha na etapa {etapa}: {e}"
                logger.error(f"[{self.nome}] Falha na etapa {etapa}: {e}")
            await self._salvar_debug(f"erro_{etapa}")
            raise

    async def _extrair_resultado(self) -> Optional[tuple[float, int, str]]:
        page = self._page
        await self._salvar_debug("pos_submit_etapa4")

        # Portal novo redireciona para /cotacao/resultado/{numero} após submeter carga.
        # Aguardar a URL mudar (timeout generoso — servidor pode demorar).
        try:
            await page.wait_for_url("**/cotacao/resultado/**", timeout=60000)
        except PlaywrightTimeoutError:
            # Verificar se já estamos na página de resultado
            if "/cotacao/resultado/" not in (page.url or ""):
                body_lower = (await page.inner_text("body")).lower()
                for phrase in [
                    "não atend", "nao atend", "fora da área", "fora de cobertura",
                    "cep não atendido", "cep nao atendido", "não atendemos",
                ]:
                    if phrase in body_lower:
                        self.last_error = "Rota não atendida pela AGEX"
                        logger.info(f"[{self.nome}] {self.last_error}")
                        await self._salvar_debug("indisponivel")
                        return None
                self.last_error = "Página de resultado não apareceu (timeout)"
                logger.error(f"[{self.nome}] {self.last_error}")
                await self._salvar_debug("sem_resultado")
                return None

        await self._salvar_debug("resultado_page")
        logger.info(f"[{self.nome}] Resultado URL: {page.url}")

        # Extrair número da cotação direto da URL: /cotacao/resultado/2197136
        url = page.url or ""
        numero = ""
        m_url = re.search(r"/cotacao/resultado/(\d+)", url)
        if m_url:
            numero = m_url.group(1)

        # Extrair valor do frete e data de entrega dos spans
        result_data = await page.evaluate("""() => {
            const out = {frete: null, previsao: null};
            const spans = Array.from(document.querySelectorAll('span'));

            // Frete: span cujo texto é exatamente "R$ X,XX"
            const freteSpan = spans.find(s =>
                /^R\\$\\s*[\\d.,]+$/.test(s.textContent.trim())
            );
            if (freteSpan) out.frete = freteSpan.textContent.trim();

            // Previsão de entrega: primeiro span com data DD/MM/YY ou DD/MM/YYYY
            const dateSpan = spans.find(s =>
                /^\\d{2}\\/\\d{2}\\/\\d{2,4}$/.test(s.textContent.trim())
            );
            if (dateSpan) out.previsao = dateSpan.textContent.trim();

            return out;
        }""")
        logger.info(f"[{self.nome}] Dados extraídos: {result_data}")

        # Parsear valor do frete
        valor_frete = None
        if result_data and result_data.get("frete"):
            try:
                valor_frete = self._parse_brl(
                    result_data["frete"].replace("R$", "").strip()
                )
            except Exception:
                pass

        if valor_frete is None:
            # Fallback: extrair do texto completo da página
            body_text = await page.inner_text("body")
            valor_frete = self._extrair_valor_frete_do_texto(body_text)

        if valor_frete is None:
            self.last_error = "Valor do frete não encontrado na página de resultado"
            logger.error(f"[{self.nome}] {self.last_error}")
            await self._salvar_debug("sem_valor")
            return None

        # Calcular prazo em dias
        data_entrega = (result_data or {}).get("previsao") or ""
        prazo_dias = 0
        if data_entrega:
            try:
                fmt = "%d/%m/%y" if re.match(r"^\d{2}/\d{2}/\d{2}$", data_entrega) else "%d/%m/%Y"
                entrega = datetime.strptime(data_entrega, fmt)
                prazo_dias = max(0, (entrega - datetime.now()).days)
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

            # AGEX é SPA — NÃO criar nova página (perde localStorage/auth).
            # Navegar para /dashboard para resetar estado antes da cotação.
            if self._logged_in:
                try:
                    await self._page.goto(
                        "https://cliente.agex.com.br/dashboard",
                        wait_until="domcontentloaded",
                        timeout=self.DEFAULT_TIMEOUT_MS,
                    )
                    await self._page.wait_for_timeout(300)
                except Exception as nav_err:
                    logger.warning(f"[{self.nome}] Falha ao navegar para /dashboard: {nav_err}")

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
