"""Provider <Nome> - Automação via Playwright.

Esqueleto inicial. Para criar uma nova transportadora:
1. Copie este arquivo para app/Fretio/src/Fretio/providers/<nome>.py
2. Renomeie a classe (ex: MinhaTransportadoraProvider).
3. Ajuste LOGIN_URL e COTACAO_URL.
4. Implemente _login, _preencher_cotacao e _extrair_resultado de acordo com
   os HTML capturados em app/<NOME>/*.txt.
5. Registre em app/Fretio/src/Fretio/providers/__init__.py,
   app/CONFIG.example.toml e installer/Fretio.spec (hiddenimports).
"""
from datetime import datetime
from typing import Optional
import re

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

from Fretio.providers.base import ProviderBase, launch_browser_resilient
from Fretio.models import Cotacao
from Fretio.logging_conf import get_logger

logger = get_logger(__name__)


class TemplateProvider(ProviderBase):
    """Provider <Nome> via Playwright."""

    LOGIN_URL = "https://exemplo.com.br/login"
    COTACAO_URL = "https://exemplo.com.br/cotacao"
    DEFAULT_TIMEOUT_MS = 30000

    def __init__(
        self,
        cnpj: str = "",
        senha: str = "",
        cnpj_remetente: Optional[str] = None,
        cnpj_destinatario: Optional[str] = None,
        cep_origem: Optional[str] = None,
        cep_destino: Optional[str] = None,
        volumes: int = 1,
        altura_m: float = 0.0,
        largura_m: float = 0.0,
        comprimento_m: float = 0.0,
        cubagens: Optional[list[dict]] = None,
        headless: bool = True,
        ufs_atendidas: Optional[list[str]] = None,
    ) -> None:
        super().__init__(nome="Template")
        self.cnpj = self._digits(cnpj)
        self.senha = senha
        self.cnpj_remetente = self._digits(cnpj_remetente or cnpj)
        self.cnpj_destinatario = self._digits(cnpj_destinatario or "")
        self.cep_origem = self._digits(cep_origem or "")
        self.cep_destino = self._digits(cep_destino or "")
        self.volumes = int(volumes or 0)
        self.altura_m = float(altura_m or 0.0)
        self.largura_m = float(largura_m or 0.0)
        self.comprimento_m = float(comprimento_m or 0.0)
        self.cubagens = cubagens or []
        self.headless = headless
        self.ufs_atendidas = [u.upper() for u in (ufs_atendidas or [])]

        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._logged_in = False
        self.last_error: Optional[str] = None

    # --- helpers ----------------------------------------------------------

    @staticmethod
    def _digits(value: str) -> str:
        return re.sub(r"\D", "", value or "")

    @staticmethod
    def _parse_decimal_any(raw: str) -> Optional[float]:
        """Aceita 1.234,56 e 1,234.56."""
        txt = re.sub(r"[^\d,.\-]", "", str(raw or "").strip())
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
    def _format_decimal_br(valor: float, decimals: int = 2) -> str:
        return f"{valor:.{decimals}f}".replace(".", ",")

    # --- ciclo de vida do browser ----------------------------------------

    async def _init_browser(self) -> None:
        if self._browser is not None:
            return
        self._pw = await async_playwright().start()
        self._browser = await launch_browser_resilient(self._pw, headless=self.headless)
        self._context = await self._browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
        )
        self._page = await self._context.new_page()
        self._page.set_default_timeout(self.DEFAULT_TIMEOUT_MS)

    async def cleanup(self) -> None:
        for closer, attr in (
            (self._page, "_page"),
            (self._context, "_context"),
            (self._browser, "_browser"),
            (self._pw, "_pw"),
        ):
            if closer is None:
                continue
            try:
                if attr == "_pw":
                    await closer.stop()
                else:
                    await closer.close()
            except Exception:
                pass
            setattr(self, attr, None)
        self._logged_in = False

    # --- login ------------------------------------------------------------

    async def _login(self) -> bool:
        if self._logged_in:
            return True
        try:
            await self._page.goto(
                self.LOGIN_URL,
                wait_until="domcontentloaded",
                timeout=self.DEFAULT_TIMEOUT_MS,
            )
            # TODO: ajustar seletores conforme app/<NOME>/LOGIN.txt.
            await self._page.locator('input[name="cnpj"]').fill(self.cnpj)
            await self._page.locator('input[type="password"]').fill(self.senha)
            await self._page.get_by_role(
                "button", name=re.compile("Entrar|Acessar|Iniciar", re.I)
            ).click()

            # Aguardar indicador inequívoco de login OK.
            await self._page.wait_for_url(
                re.compile(r".*(painel|inicio|dashboard).*"),
                timeout=15000,
            )
            self._logged_in = True
            logger.info(f"[{self.nome}] login OK")
            return True
        except Exception as exc:
            self.last_error = f"Falha no login {self.nome}: {exc}"
            logger.error(self.last_error)
            return False

    # --- preencher cotação ------------------------------------------------

    async def _preencher_cotacao(
        self,
        origem: str,
        destino: str,
        peso: float,
        valor: float,
    ) -> bool:
        try:
            page = self._page
            # Em SPA: NÃO chame page.goto() pra COTACAO_URL se já está logado;
            # navegue pelo menu interno pra preservar localStorage.
            if self.COTACAO_URL not in page.url:
                await page.goto(
                    self.COTACAO_URL,
                    wait_until="domcontentloaded",
                    timeout=self.DEFAULT_TIMEOUT_MS,
                )

            # TODO: ajustar conforme app/<NOME>/COTAÇAO.txt.
            await page.locator('input[name="cep_origem"]').fill(self._digits(origem))
            await page.locator('input[name="cep_destino"]').fill(self._digits(destino))
            await page.locator('input[name="peso"]').fill(self._format_decimal_br(peso))
            await page.locator('input[name="valor"]').fill(self._format_decimal_br(valor))

            await page.get_by_role(
                "button", name=re.compile("Cotar|Calcular|Continuar", re.I)
            ).click()
            return True
        except Exception as exc:
            self.last_error = f"Falha ao preencher cotação: {exc}"
            logger.error(self.last_error)
            return False

    # --- extrair resultado ------------------------------------------------

    async def _extrair_resultado(self) -> Optional[tuple[float, int, str]]:
        """Retorna (valor_frete, prazo_dias, restricoes)."""
        try:
            page = self._page

            # Aguardar painel de resultado renderizar.
            await page.locator('#resultado').wait_for(
                state="visible", timeout=self.DEFAULT_TIMEOUT_MS
            )

            texto = await page.locator('#resultado').inner_text()

            # TODO: ajustar regexes conforme app/<NOME>/RESULTADO COTAÇAO.txt.
            m_valor = re.search(r"R\$\s*([\d\.,]+)", texto)
            m_prazo = re.search(r"(\d+)\s*dia", texto, re.I)
            if not m_valor:
                self.last_error = "Resultado sem valor de frete"
                return None
            valor = self._parse_decimal_any(m_valor.group(1)) or 0.0
            prazo = int(m_prazo.group(1)) if m_prazo else 0
            return valor, prazo, ""
        except PlaywrightTimeoutError:
            self.last_error = "Timeout esperando resultado"
            return None
        except Exception as exc:
            self.last_error = f"Falha ao extrair resultado: {exc}"
            logger.error(self.last_error)
            return None

    # --- método público ---------------------------------------------------

    async def coteir(
        self,
        origem: str,
        destino: str,
        peso: float,
        valor: float,
    ) -> Optional[Cotacao]:
        try:
            self.last_error = None

            if self.ufs_atendidas:
                # Opcional: bloqueio antes de abrir browser quando UF não atende.
                pass

            await self._init_browser()
            if not await self._login():
                return None
            if not await self._preencher_cotacao(origem, destino, peso, valor):
                return None
            resultado = await self._extrair_resultado()
            if not resultado:
                self.last_error = self.last_error or f"{self.nome} sem resultado"
                return None

            valor_frete, prazo_dias, restricoes = resultado
            logger.info(
                f"[{self.nome}] OK R$ {valor_frete:.2f} - {prazo_dias} dias"
            )
            return Cotacao(
                transportadora=self.nome,
                prazo_dias=prazo_dias,
                valor_frete=round(valor_frete, 2),
                restricoes=restricoes or None,
                timestamp=datetime.now(),
            )
        except Exception as exc:
            self.last_error = str(exc)
            logger.error(f"[{self.nome}] Erro na cotação: {exc}", exc_info=True)
            return None

    # Alias usado por algumas partes do app.
    async def cotear(self, *args, **kwargs):
        return await self.coteir(*args, **kwargs)
