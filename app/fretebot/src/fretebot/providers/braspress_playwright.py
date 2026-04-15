"""Provider Braspress - Versão com Playwright para contornar antibot."""
from datetime import datetime
from typing import Optional
import re

from playwright.async_api import async_playwright

from fretebot.providers.base import ProviderBase
from fretebot.models import Cotacao
from fretebot.logging_conf import get_logger

logger = get_logger(__name__)


class BraspressPlaywrightProvider(ProviderBase):
    """Provider Braspress via Playwright para contornar antibot."""

    LOGIN_URL = "https://www.braspress.com.br/w/cliente/view"
    COTACAO_URL = "https://www.braspress.com.br/w/cotacao/view"

    def __init__(self, cnpj: str, senha: str, headless: bool = True):
        super().__init__(nome="Braspress")
        self.cnpj = self._digits(cnpj)
        self.senha = senha
        self.headless = headless
        self.last_error: str | None = None
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._logged_in = False

    @staticmethod
    def _digits(value: str) -> str:
        return re.sub(r"\D", "", value or "")

    @staticmethod
    def _fmt_decimal(value: float, decimals: int = 2) -> str:
        """Formata valor em formato brasileiro (vírgula como separador decimal)."""
        return f"{value:.{decimals}f}".replace(".", ",")

    @staticmethod
    def _parse_decimal_any(raw: str) -> float | None:
        txt = re.sub(r"[^\d,.\-]", "", str(raw or "").strip())
        if not txt:
            return None
        if "," in txt and "." in txt:
            # O separador decimal é o último símbolo entre vírgula/ponto.
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
    def _parse_int_any(raw: str) -> int:
        m = re.search(r"\d+", str(raw or ""))
        return int(m.group(0)) if m else 0

    @staticmethod
    def _normalizar_cubagens_cm(cubagens: Optional[list[dict]]) -> list[dict]:
        # Consolida por dimensão para só abrir nova linha quando houver tamanho diferente.
        agrupadas: dict[tuple[int, int, int], int] = {}
        if not isinstance(cubagens, list):
            return []
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
            key = (comp, larg, alt)
            agrupadas[key] = agrupadas.get(key, 0) + qtd

        return [
            {
                "quantidade": int(qtd),
                "comprimento_cm": int(comp),
                "largura_cm": int(larg),
                "altura_cm": int(alt),
            }
            for (comp, larg, alt), qtd in agrupadas.items()
        ]

    async def _fechar_alert_xml_modal(self, page):
        """Remove o modal #alertXmlModal e backdrop do DOM (incondicional)."""
        try:
            removed = await page.evaluate("""() => {
                const m = document.getElementById('alertXmlModal');
                if (!m) return false;
                m.remove();
                document.querySelectorAll('.modal-backdrop').forEach(b => b.remove());
                document.body.classList.remove('modal-open');
                document.body.style.removeProperty('overflow');
                document.body.style.removeProperty('padding-right');
                return true;
            }""")
            if removed:
                logger.info("[Braspress] Modal #alertXmlModal removido do DOM")
                await page.wait_for_timeout(200)
        except Exception:
            pass

    async def _init_browser(self):
        if self._browser:
            if self._browser.is_connected():
                return
            logger.warning("[Braspress] Browser desconectado, reinicializando...")
            await self.cleanup()
        from fretebot.providers.base import launch_browser_resilient
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

    async def _login(self) -> bool:
        if self._logged_in:
            return True
        logger.info("[Braspress] Fazendo login...")
        await self._page.goto(self.LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        await self._page.wait_for_timeout(1500)
        await self._page.locator('#inputLogin').fill(self.cnpj)
        await self._page.locator('input[name="pass"]').fill(self.senha)
        await self._page.locator('input[type="submit"][value="Acessar"]').click()
        await self._page.wait_for_timeout(3000)
        body = await self._page.inner_text("body")
        if "sair" not in body.lower():
            self.last_error = "Login falhou"
            logger.error("[Braspress] Login falhou")
            return False
        logger.info("[Braspress] Login OK")
        self._logged_in = True
        return True

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
            if self._browser and self._browser.is_connected():
                await self._browser.close()
        except Exception:
            pass
        self._browser = None
        self._page = None
        self._context = None
        self._logged_in = False

    async def cotar(
        self,
        origem: str,
        destino: str,
        peso: float,
        valor: float,
        cnpj_destinatario: str | None = None,
        volumes: int = 1,
        comprimento_cm: int = 0,
        largura_cm: int = 0,
        altura_cm: int = 0,
        cubagens: Optional[list[dict]] = None,
        cnpj_remetente: str | None = None,
        tipo_frete: str | None = None,
    ) -> Optional[Cotacao]:
        """Realiza cotação via Playwright."""
        try:
            cubagens_validas = self._normalizar_cubagens_cm(cubagens)
            if not cubagens_validas:
                if volumes > 0 and comprimento_cm > 0 and largura_cm > 0 and altura_cm > 0:
                    cubagens_validas = [
                        {
                            "quantidade": int(volumes),
                            "comprimento_cm": int(comprimento_cm),
                            "largura_cm": int(largura_cm),
                            "altura_cm": int(altura_cm),
                        }
                    ]
                else:
                    self.last_error = "Cubagem inválida: dimensões ausentes no romaneio"
                    logger.error(f"[Braspress] {self.last_error}")
                    return None

            volumes_total = sum(int(c["quantidade"]) for c in cubagens_validas)

            await self._init_browser()
            if not await self._login():
                return None

            # Criar nova página para cada cotação (mantém sessão via cookies do contexto)
            if self._logged_in:
                try:
                    await self._page.close()
                except Exception:
                    pass
                self._page = await self._context.new_page()
                self._page.set_default_timeout(30000)

            page = self._page

            # ── PÁGINA DE COTAÇÃO ──────────────────────────────────
            await page.goto(self.COTACAO_URL, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(500)

            # Verificar se sessão expirou (redirecionou para login)
            current_url = page.url.lower()
            if "cliente/view" in current_url or "login" in current_url:
                logger.warning("[Braspress] Sessão expirada, refazendo login...")
                self._logged_in = False
                if not await self._login():
                    return None
                await page.goto(self.COTACAO_URL, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(500)

            # ── FECHAR MODAL/POPUP ─────────────────────────────────
            for _ in range(3):
                try:
                    btn = page.locator("text=Fechar").first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.click()
                        await page.wait_for_timeout(400)
                    else:
                        break
                except Exception:
                    break

            # Fechar modal #alertXmlModal se estiver visível (bloqueia cliques)
            await self._fechar_alert_xml_modal(page)

            await page.wait_for_timeout(300)

            # ── 4. PREENCHER FORMULÁRIO ───────────────────────────────
            logger.info("[Braspress] Preenchendo formulário...")

            # Aguarda formulário ficar interativo antes de preencher
            try:
                await page.locator("#btnCalcular").wait_for(state="visible", timeout=10000)
            except Exception:
                logger.warning("[Braspress] Formulário não visível, aguardando networkidle...")
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass

            # Fechar #alertXmlModal se apareceu após carregamento
            await self._fechar_alert_xml_modal(page)

            # #modal e #tipoFrete só existem no frete fornecedor (FOB)
            if cnpj_remetente:
                # Aguarda select #modal ficar disponível antes de interagir
                try:
                    await page.locator("#modal").wait_for(state="attached", timeout=10000)
                except Exception:
                    logger.warning("[Braspress] Select #modal não encontrado, tentando reload...")
                    await page.goto(self.COTACAO_URL, wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(500)
                    # Fechar modais/popups pós-reload
                    for _ in range(3):
                        try:
                            btn = page.locator("text=Fechar").first
                            if await btn.count() > 0 and await btn.is_visible():
                                await btn.click()
                                await page.wait_for_timeout(400)
                            else:
                                break
                        except Exception:
                            break
                    try:
                        await page.locator("#modal").wait_for(state="attached", timeout=10000)
                    except Exception:
                        logger.warning("[Braspress] #modal ainda não encontrado, aguardando networkidle...")
                        try:
                            await page.wait_for_load_state("networkidle", timeout=5000)
                        except Exception:
                            pass
                        await page.locator("#modal").wait_for(state="attached", timeout=5000)

                # Selects (frete fornecedor)
                await page.select_option("#modal", "R")
                tipo_frete_val = tipo_frete if tipo_frete else "2"
                await page.select_option("#tipoFrete", tipo_frete_val)

            # Remover #alertXmlModal antes de interagir com o formulário
            await self._fechar_alert_xml_modal(page)

            # CNPJ Remetente (preencher quando fornecido, ex: modo FOB fornecedor)
            cnpj_rem = self._digits(cnpj_remetente) if cnpj_remetente else ""
            if cnpj_rem:
                await page.locator("#cnpjRemetente").click()
                await page.locator("#cnpjRemetente").press("Control+a")
                await page.locator("#cnpjRemetente").type(cnpj_rem, delay=50)
                await page.keyboard.press("Tab")
                await page.wait_for_timeout(500)

            # CNPJ Destinatário
            if cnpj_rem:
                cnpj_dest = self.cnpj
            else:
                cnpj_dest = self._digits(cnpj_destinatario) if cnpj_destinatario else ""
            if cnpj_dest:
                try:
                    await page.locator("#cnpjDestinatario").click(timeout=10000)
                    await page.locator("#cnpjDestinatario").press("Control+a")
                    await page.locator("#cnpjDestinatario").type(cnpj_dest, delay=50)
                    await page.keyboard.press("Tab")
                except Exception:
                    logger.warning("[Braspress] Click em #cnpjDestinatario falhou, usando JS")
                    await page.evaluate(f"""(cnpj) => {{
                        const el = document.getElementById('cnpjDestinatario');
                        if (!el) return;
                        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                        if (setter) setter.call(el, cnpj);
                        else el.value = cnpj;
                        el.dispatchEvent(new Event('input', {{bubbles: true}}));
                        el.dispatchEvent(new Event('change', {{bubbles: true}}));
                        el.dispatchEvent(new Event('blur', {{bubbles: true}}));
                    }}""", cnpj_dest)
                await page.wait_for_timeout(500)

            # CEPs: aguardar auto-preenchimento, preencher manualmente se necessário
            cep_orig_auto = ""
            cep_dest_auto = ""
            for _ in range(10):
                ceps_auto = await page.evaluate(
                    """() => ({
                        origem: (document.getElementById('cepOrigem')?.value || '').trim(),
                        destino: (document.getElementById('cepDestino')?.value || '').trim(),
                    })"""
                )
                cep_orig_auto = self._digits(str(ceps_auto.get("origem", "")))
                cep_dest_auto = self._digits(str(ceps_auto.get("destino", "")))
                if len(cep_orig_auto) == 8 and len(cep_dest_auto) == 8:
                    break
                await page.wait_for_timeout(400)

            # Preencher CEPs manualmente se não foram auto-preenchidos
            if len(cep_orig_auto) != 8:
                logger.info("[Braspress] CEP Origem não auto-preenchido, preenchendo manualmente...")
                try:
                    await page.locator("#cepOrigem").click(timeout=10000)
                    await page.locator("#cepOrigem").fill(self._digits(origem))
                    await page.keyboard.press("Tab")
                except Exception:
                    logger.warning("[Braspress] Click em #cepOrigem falhou, usando JS")
                    await page.evaluate("""(cep) => {
                        const el = document.getElementById('cepOrigem');
                        if (!el) return;
                        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                        if (setter) setter.call(el, cep);
                        else el.value = cep;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        el.dispatchEvent(new Event('blur', {bubbles: true}));
                    }""", self._digits(origem))
                await page.wait_for_timeout(500)
            if len(cep_dest_auto) != 8:
                logger.info("[Braspress] CEP Destino não auto-preenchido, preenchendo manualmente...")
                try:
                    await page.locator("#cepDestino").click(timeout=10000)
                    await page.locator("#cepDestino").fill(self._digits(destino))
                    await page.keyboard.press("Tab")
                except Exception:
                    logger.warning("[Braspress] Click em #cepDestino falhou, usando JS")
                    await page.evaluate("""(cep) => {
                        const el = document.getElementById('cepDestino');
                        if (!el) return;
                        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                        if (setter) setter.call(el, cep);
                        else el.value = cep;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        el.dispatchEvent(new Event('blur', {bubbles: true}));
                    }""", self._digits(destino))
                await page.wait_for_timeout(500)

            # Peso (formato BR: vírgula decimal)
            peso_str = self._fmt_decimal(peso)
            await page.locator("#peso").click()
            await page.locator("#peso").press("Control+a")
            await page.locator("#peso").type(peso_str, delay=100)
            await page.keyboard.press("Tab")
            await page.wait_for_timeout(300)

            # Volumes (type=number – usar nativeInputValueSetter)
            vol_str = str(volumes_total)
            await page.evaluate(
                """(vol) => {
                    const el = document.getElementById('volumes');
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    ).set;
                    setter.call(el, vol);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.dispatchEvent(new KeyboardEvent('keyup', {key: vol, bubbles: true}));
                }""",
                vol_str,
            )
            await page.wait_for_timeout(300)

            # Valor mercadoria
            valor_str = self._fmt_decimal(valor)
            await page.locator("#vlrMercadoria").click()
            await page.locator("#vlrMercadoria").press("Control+a")
            await page.locator("#vlrMercadoria").type(valor_str, delay=100)
            await page.keyboard.press("Tab")
            await page.wait_for_timeout(300)

            # Cubagens reais do romaneio (múltiplos tipos de volume).
            for idx, cub in enumerate(cubagens_validas):
                if idx > 0:
                    await page.locator("#btnAdd").click()
                    await page.wait_for_timeout(400)
                    final_count = await page.evaluate(
                        "() => document.querySelectorAll(\"input[id^='cubagem'][id$='comprimento']\").length"
                    )
                    if int(final_count or 0) < idx + 1:
                        self.last_error = f"Não foi possível adicionar linha de cubagem #{idx + 1} na Braspress"
                        logger.error(f"[Braspress] {self.last_error}")
                        return None

                cubagem_fields = {
                    f"cubagem{idx}comprimento": self._fmt_decimal(int(cub["comprimento_cm"]) / 100.0),
                    f"cubagem{idx}largura": self._fmt_decimal(int(cub["largura_cm"]) / 100.0),
                    f"cubagem{idx}altura": self._fmt_decimal(int(cub["altura_cm"]) / 100.0),
                    f"cubagem{idx}volumes": str(int(cub["quantidade"])),
                }
                for fid, val in cubagem_fields.items():
                    try:
                        loc = page.locator(f"#{fid}")
                        if await loc.count() > 0 and await loc.is_visible():
                            await loc.click()
                            await loc.press("Control+a")
                            await loc.type(val, delay=50)
                            await page.keyboard.press("Tab")
                            await page.wait_for_timeout(180)
                            continue
                    except Exception:
                        pass
                    await page.evaluate(
                        """({ fieldId, fieldValue }) => {
                            let el = document.getElementById(fieldId);
                            if (!el) {
                                const m = fieldId.match(/^cubagem(\\d+)(comprimento|largura|altura|volumes)$/);
                                if (m) {
                                    const rowIndex = Number(m[1]);
                                    const suffix = m[2];
                                    const candidatos = Array.from(
                                        document.querySelectorAll(`input[id^='cubagem'][id$='${suffix}']`)
                                    );
                                    if (candidatos.length > rowIndex) {
                                        el = candidatos[rowIndex];
                                    }
                                }
                            }
                            if (!el) return;
                            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                            if (setter) setter.call(el, fieldValue);
                            else el.value = fieldValue;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                            el.dispatchEvent(new Event('blur', { bubbles: true }));
                        }""",
                        {"fieldId": fid, "fieldValue": val},
                    )
                    await page.wait_for_timeout(180)

            # Log de verificação dos campos preenchidos
            form_values = await page.evaluate("""() => {
                const get = (id) => { const el = document.getElementById(id); return el ? el.value : 'N/A'; };
                return {
                    modal: get('modal'), tipoFrete: get('tipoFrete'),
                    cepOrigem: get('cepOrigem'), cepDestino: get('cepDestino'),
                    peso: get('peso'), volumes: get('volumes'), vlrMercadoria: get('vlrMercadoria'),
                    cubComp: get('cubagem0comprimento'), cubLarg: get('cubagem0largura'),
                    cubAlt: get('cubagem0altura'), cubVol: get('cubagem0volumes'),
                    linhasCubagem: document.querySelectorAll("input[id^='cubagem'][id$='comprimento']").length,
                };
            }""")
            logger.info(f"[Braspress] Campos do formulário: {form_values}")

            # ── 5. CALCULAR ───────────────────────────────────────────
            logger.info("[Braspress] Calculando...")

            # Verificar se os campos obrigatórios estão preenchidos
            form_check = await page.evaluate("""() => ({
                modal: document.getElementById('modal')?.value,
                cepOrig: document.getElementById('cepOrigem')?.value,
                cepDest: document.getElementById('cepDestino')?.value,
                peso: document.getElementById('peso')?.value,
            })""")
            if not form_check.get("peso"):
                self.last_error = "Campos do formulário não preenchidos (peso ausente)"
                logger.error(f"[Braspress] {self.last_error}: {form_check}")
                return None

            # Verificar e corrigir CEP Destino antes de calcular
            cep_dest_check = self._digits(str(form_check.get("cepDest", "")))
            if len(cep_dest_check) != 8:
                logger.warning(f"[Braspress] CEP Destino vazio/incorreto antes de calcular: '{form_check.get('cepDest')}', re-preenchendo...")
                dest_digits = self._digits(destino)
                try:
                    loc_dest = page.locator("#cepDestino")
                    await loc_dest.click(timeout=5000)
                    await loc_dest.press("Control+a")
                    await loc_dest.type(dest_digits, delay=50)
                    await page.keyboard.press("Tab")
                    await page.wait_for_timeout(500)
                except Exception:
                    await page.evaluate("""(cep) => {
                        const el = document.getElementById('cepDestino');
                        if (!el) return;
                        el.value = '';
                        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                        if (setter) setter.call(el, cep);
                        else el.value = cep;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        el.dispatchEvent(new Event('blur', {bubbles: true}));
                    }""", dest_digits)
                    await page.wait_for_timeout(500)
                # Verificar novamente
                cep_dest_recheck = await page.evaluate("() => document.getElementById('cepDestino')?.value || ''")
                if len(self._digits(str(cep_dest_recheck))) != 8:
                    self.last_error = f"CEP Destino não preenchido após retry: '{cep_dest_recheck}'"
                    logger.error(f"[Braspress] {self.last_error}")
                    return None

            # Verificar e corrigir CEP Origem
            cep_orig_check = self._digits(str(form_check.get("cepOrig", "")))
            if len(cep_orig_check) != 8:
                logger.warning(f"[Braspress] CEP Origem vazio/incorreto antes de calcular: '{form_check.get('cepOrig')}', re-preenchendo...")
                orig_digits = self._digits(origem)
                try:
                    loc_orig = page.locator("#cepOrigem")
                    await loc_orig.click(timeout=5000)
                    await loc_orig.press("Control+a")
                    await loc_orig.type(orig_digits, delay=50)
                    await page.keyboard.press("Tab")
                    await page.wait_for_timeout(500)
                except Exception:
                    await page.evaluate("""(cep) => {
                        const el = document.getElementById('cepOrigem');
                        if (!el) return;
                        el.value = '';
                        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                        if (setter) setter.call(el, cep);
                        else el.value = cep;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        el.dispatchEvent(new Event('blur', {bubbles: true}));
                    }""", orig_digits)
                    await page.wait_for_timeout(500)

            await page.locator("#btnCalcular").click()
            # Polling robusto: aguardar resultado aparecer em qualquer bloco da página.
            # Alguns cenários retornam tabela fora de #step4Result.
            resultado_pronto = False
            ultimo_status: dict | None = None
            for _poll in range(30):  # até ~40s
                await page.wait_for_timeout(800)
                status = await page.evaluate(
                    """() => {
                        const clean = (v) => (v || '').replace(/\\s+/g, ' ').trim();
                        const bodyText = clean(document.body?.innerText || '');
                        const hasTable = Array.from(document.querySelectorAll('table')).some((t) => {
                            const headers = Array.from(t.querySelectorAll('th'))
                                .map((th) => clean(th.innerText || th.textContent).toLowerCase());
                            const hasValor = headers.some(
                                (h) => h.includes('valor total frete') || (h.includes('valor') && h.includes('frete'))
                            );
                            const hasDias = headers.some((h) => h.includes('dias'));
                            return hasValor && hasDias;
                        });
                        const badges = Array.from(document.querySelectorAll('.badge, .label'))
                            .map((el) => clean(el.innerText || el.textContent))
                            .filter(Boolean);
                        const hasValorBadge = badges.some((b) => /\\d+[\\.,]\\d{2}/.test(b) || /^R\\$\\s*\\d/.test(b));
                        const hasSucesso = /cota[çc][aã]o realizada com sucesso/i.test(bodyText);
                        const hasResultadoTitulo = /resultado da cota[çc][aã]o/i.test(bodyText);
                        // Detectar erros visíveis na página
                        const errs = Array.from(document.querySelectorAll(
                            '.has-error .help-block, .error-message, .alert-danger, .alert-warning, .modal.in .modal-body'
                        )).map(e => clean(e.innerText)).filter(t => t.length > 0);
                        return {
                            stepLen: clean(document.getElementById('step4Result')?.innerText || '').length,
                            hasTable,
                            hasValorBadge,
                            hasSucesso,
                            hasResultadoTitulo,
                            errs,
                        };
                    }"""
                )
                ultimo_status = status if isinstance(status, dict) else None
                if isinstance(status, dict):
                    # Detectar erros da página cedo para não esperar 135s em vão
                    page_errs = status.get("errs") or []
                    # Filtrar mensagens de status/processamento (não são erros reais)
                    page_errs = [
                        e for e in page_errs
                        if not re.search(r'\b(VERIFICANDO|CARREGANDO|PROCESSANDO|AGUARD)\b', e, re.IGNORECASE)
                    ]
                    if page_errs and _poll >= 3:
                        self.last_error = f"Erro na página Braspress: {'; '.join(page_errs[:3])}"
                        logger.error(f"[Braspress] {self.last_error}")
                        return None
                    if (
                        bool(status.get("hasTable"))
                        or bool(status.get("hasValorBadge"))
                        or bool(status.get("hasSucesso"))
                        or bool(status.get("hasResultadoTitulo"))
                        or int(status.get("stepLen", 0) or 0) > 0
                    ):
                        resultado_pronto = True
                        break

            if not resultado_pronto:
                # Log estado atual para debug
                alerts = await page.evaluate("""() => {
                    const errs = document.querySelectorAll('.has-error .help-block, .error-message, .alert-danger, .alert-warning');
                    return Array.from(errs).map(e => e.innerText.trim()).filter(t => t.length > 0);
                }""")
                if alerts:
                    logger.warning(f"[Braspress] Alertas na página: {alerts}")
                if ultimo_status:
                    logger.warning(f"[Braspress] Resultado não detectado no polling: {ultimo_status}")

            # ── 6. EXTRAIR RESULTADO ──────────────────────────────────            # Via .label-result (ordem: previsão, dias, valor, data, nº, status)
            labels = await page.evaluate("""() => {
                const els = document.querySelectorAll('#step4Result .label-result');
                return Array.from(els).map(e => (e.innerText || e.textContent || '').trim());
            }""")
            logger.info(f"[Braspress] Labels do resultado: {labels}")

            if isinstance(labels, list) and len(labels) >= 3:
                _prazo = self._parse_int_any(labels[1])
                _valor = self._parse_decimal_any(labels[2])
                if _valor is not None:
                    logger.info(f"[Braspress] Cotação: R$ {_valor:.2f} - {_prazo} dias")
                    return Cotacao(
                        transportadora=self.nome,
                        prazo_dias=_prazo,
                        valor_frete=round(_valor, 2),
                        restricoes="Cotação portal Braspress",
                        timestamp=datetime.now(),
                    )

            # Fallback: extração por texto completo
            result_text = await page.evaluate(
                "() => { const el = document.getElementById('step4Result'); return el ? el.innerText : ''; }"
            )
            result_dom = await page.evaluate(
                """() => {
                    const clean = (v) => (v || '').replace(/\\s+/g, ' ').trim();
                    const lower = (v) => clean(v).toLowerCase();

                    const tableInfo = (table) => {
                        if (!table) return { headers: [], rows: [] };
                        const headers = Array.from(table.querySelectorAll('th'))
                            .map((th) => lower(th.innerText || th.textContent));
                        const rows = Array.from(table.querySelectorAll('tr'))
                            .map((tr) => Array.from(tr.querySelectorAll('td')).map((td) => clean(td.innerText || td.textContent)))
                            .filter((row) => row.length > 0);
                        return { headers, rows };
                    };

                    // 1) Tenta bloco tradicional
                    const rootStep4 = document.getElementById('step4Result');
                    let table = rootStep4 ? rootStep4.querySelector('table') : null;
                    let root = rootStep4;

                    // 2) Fallback: procura tabela de resultado em toda a página
                    if (!table) {
                        const allTables = Array.from(document.querySelectorAll('table'));
                        for (const t of allTables) {
                            const info = tableInfo(t);
                            const hasValor = info.headers.some(
                                (h) => h.includes('valor total frete') || (h.includes('valor') && h.includes('frete'))
                            );
                            const hasDias = info.headers.some((h) => h.includes('dias'));
                            if (hasValor && hasDias) {
                                table = t;
                                break;
                            }
                        }
                    }

                    if (!root && table) {
                        root =
                            table.closest('section') ||
                            table.closest('.panel') ||
                            table.closest('.panel-body') ||
                            table.closest('.container') ||
                            table.closest('form') ||
                            table.parentElement ||
                            document.body;
                    }
                    if (!root) root = document.body;

                    const info = tableInfo(table);
                    let valorText = '';
                    let prazoText = '';

                    if (info.headers.length && info.rows.length) {
                        const row = info.rows.find((r) => r.some((c) => /\\d/.test(c))) || info.rows[0];
                        const idxValor = info.headers.findIndex(
                            (h) => h.includes('valor total frete') || (h.includes('valor') && h.includes('frete'))
                        );
                        const idxPrazo = info.headers.findIndex((h) => h.includes('dias'));
                        if (idxValor >= 0 && idxValor < row.length) valorText = row[idxValor] || '';
                        if (idxPrazo >= 0 && idxPrazo < row.length) prazoText = row[idxPrazo] || '';
                    }

                    if (!valorText || !prazoText) {
                        const scope = table ? (table.parentElement || root) : root;
                        const badges = Array.from(scope.querySelectorAll('.badge, .label'))
                            .map((el) => clean(el.innerText || el.textContent))
                            .filter(Boolean);
                        if (!prazoText) {
                            const p = badges.find((b) => /^\\d{1,2}$/.test(b));
                            if (p) prazoText = p;
                        }
                        if (!valorText) {
                            const v = badges.find((b) => /\\d+[\\.,]\\d{2}/.test(b) || /^R\\$\\s*\\d/.test(b));
                            if (v) valorText = v;
                        }
                    }

                    return {
                        valorText,
                        prazoText,
                        text: clean((table ? table.innerText : root.innerText) || ''),
                        hasTable: !!table,
                    };
                }"""
            )
            if not result_text.strip():
                if isinstance(result_dom, dict):
                    result_text = str(result_dom.get("text", "") or "").strip()
                if not result_text:
                    result_text = await page.inner_text("body")

            logger.info(f"[Braspress] Resultado bruto (500 chars): {result_text[:500]}")
            if isinstance(result_dom, dict):
                logger.info(
                    "[Braspress] Resultado DOM: hasTable=%s valorText=%s prazoText=%s",
                    bool(result_dom.get("hasTable")),
                    str(result_dom.get("valorText", "") or ""),
                    str(result_dom.get("prazoText", "") or ""),
                )

            # Verificar sucesso
            if "sucesso" not in result_text.lower() and "valor" not in result_text.lower():
                erros_el = page.locator(".has-error .help-block, .error-message, .alert-danger")
                cnt = await erros_el.count()
                erros = []
                for i in range(min(cnt, 5)):
                    txt = await erros_el.nth(i).text_content()
                    if txt and txt.strip():
                        erros.append(txt.strip()[:100])
                self.last_error = "; ".join(erros) if erros else "Sem resultado"
                logger.error(f"[Braspress] {self.last_error}")
                return None

            # Extrair valor/prazo priorizando a tabela de resultado do step4.
            valor_frete = None
            prazo_dias = 0

            if isinstance(result_dom, dict):
                valor_frete = self._parse_decimal_any(str(result_dom.get("valorText", "") or ""))
                prazo_dias = self._parse_int_any(str(result_dom.get("prazoText", "") or ""))

            if valor_frete is None:
                # Fallback por rótulos e sequência numérica
                valor_label = re.search(r"valor\s*total\s*frete[^\d]*([\d.,]+)", result_text, re.IGNORECASE)
                if valor_label:
                    valor_frete = self._parse_decimal_any(valor_label.group(1))

            if prazo_dias <= 0:
                prazo_label = re.search(r"dias?\s*(?:úteis?)?[^\d]*(\d{1,3})", result_text, re.IGNORECASE)
                if prazo_label:
                    prazo_dias = int(prazo_label.group(1))

            if valor_frete is None:
                # Fallback antigo (sequência de colunas)
                valor_match = re.search(
                    r"(\d+)\s+([\d.,]+)\s+\d{2}/\d{2}/\d{4}\s+\d+\s+\w+",
                    result_text,
                )
                if valor_match:
                    prazo_dias = prazo_dias or int(valor_match.group(1))
                    valor_frete = self._parse_decimal_any(valor_match.group(2))

            if valor_frete is None:
                # Último fallback: qualquer decimal plausível no bloco de resultado.
                vals = re.findall(r"(\d[\d.,]*[.,]\d{2})", result_text)
                for v in vals:
                    vf = self._parse_decimal_any(v)
                    if vf is not None and 1 < vf < 100000:
                        valor_frete = vf
                        break

            if valor_frete is None:
                trecho = re.sub(r"\s+", " ", result_text).strip()[:220]
                dom_diag = ""
                if isinstance(result_dom, dict):
                    dom_diag = (
                        f" hasTable={bool(result_dom.get('hasTable'))}"
                        f" valorText={str(result_dom.get('valorText', '') or '')!r}"
                        f" prazoText={str(result_dom.get('prazoText', '') or '')!r}"
                    )
                self.last_error = f"Valor do frete não encontrado no resultado ({dom_diag} trecho: {trecho})"
                logger.error(f"[Braspress] {self.last_error}")
                return None

            logger.info(f"[Braspress] Cotação: R$ {valor_frete:.2f} - {prazo_dias} dias")
            return Cotacao(
                transportadora=self.nome,
                prazo_dias=prazo_dias,
                valor_frete=round(valor_frete, 2),
                restricoes="Cotação portal Braspress",
                timestamp=datetime.now(),
            )

        except Exception as e:
            self.last_error = str(e)
            logger.error(f"[Braspress] Erro: {e}")
            return None

    # Alias para compatibilidade
    coteir = cotar
