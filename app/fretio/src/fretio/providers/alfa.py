"""Provider Alfa Transportes via Playwright com login manual (Turnstile)."""
from __future__ import annotations

from datetime import datetime
import os
import re
import base64
import json
import shutil
import socket
import struct
import subprocess
import asyncio
import urllib.parse
import urllib.request
from typing import Optional, Any

from playwright.async_api import async_playwright

from fretio.providers.base import ProviderBase, _kill_proc, _register_owned_proc
from fretio.providers.provider_utils import (
    _digits, _fmt_decimal, _parse_decimal_any, _parse_int_any
)
from fretio.providers.alfa_browser import AlfaBrowserMixin
from fretio.models import Cotacao
from fretio.quotation_contract import QuoteRequest, QuoteResponse
from fretio.logging_conf import get_logger

logger = get_logger(__name__)


class AlfaProvider(AlfaBrowserMixin, ProviderBase):
    """Provider Alfa com login manual e cotacao automatizada."""

    BASE_URL = "https://arearestrita.alfatransportes.com.br"
    LOGIN_URL = "https://arearestrita.alfatransportes.com.br/login/"
    COTACAO_URL = "https://arearestrita.alfatransportes.com.br/cotacao/"
    COTACAO_API_URL = "https://arearestrita.alfatransportes.com.br/cotacao/api/"
    LOGIN_MAX_WAIT_S = 120
    _digits = staticmethod(_digits)
    _fmt_decimal = staticmethod(_fmt_decimal)
    _parse_decimal_any = staticmethod(_parse_decimal_any)
    _parse_int_any = staticmethod(_parse_int_any)

    def __init__(
        self,
        login: str,
        senha: str,
        login_url: str = "",
        cotacao_url: str = "",
        headless: bool = False,
    ) -> None:
        super().__init__(nome="ALFA")
        self.login = str(login or "").strip()
        self.senha = str(senha or "").strip()
        self.headless = bool(headless)
        self.login_url = str(login_url or self.LOGIN_URL).strip()

        if cotacao_url and "/api/" in str(cotacao_url):
            self.cotacao_api_url = str(cotacao_url).strip()
            self.cotacao_url = self.COTACAO_URL
        else:
            self.cotacao_url = str(cotacao_url or self.COTACAO_URL).strip()
            self.cotacao_api_url = self.COTACAO_API_URL

        self.last_error: str | None = None
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._logged_in = False
        self._cdp_session = None
        self._window_id = None
        self._chrome_proc = None
        self._debug_port = 0

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _format_doc(value: str) -> str:
        digits = re.sub(r"\D", "", str(value or ""))
        if len(digits) == 11:
            return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
        if len(digits) == 14:
            return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
        return digits

    @staticmethod
    def _calc_cubagem_m3(cubagens: Optional[list[dict]]) -> float:
        total = 0.0
        if not isinstance(cubagens, list):
            return total
        for row in cubagens:
            if not isinstance(row, dict):
                continue
            try:
                qtd = int(row.get("quantidade", 0) or 0)
                comp = float(row.get("comprimento_cm", 0) or 0)
                larg = float(row.get("largura_cm", 0) or 0)
                alt = float(row.get("altura_cm", 0) or 0)
            except Exception:
                continue
            if qtd <= 0 or comp <= 0 or larg <= 0 or alt <= 0:
                continue
            total += (comp * larg * alt / 1_000_000.0) * qtd
        return total

    @staticmethod
    def _sum_volumes(cubagens: Optional[list[dict]], fallback: int) -> int:
        if not isinstance(cubagens, list):
            return int(fallback or 0)
        total = 0
        for row in cubagens:
            if not isinstance(row, dict):
                continue
            try:
                qtd = int(row.get("quantidade", 0) or 0)
            except Exception:
                qtd = 0
            total += max(qtd, 0)
        return total if total > 0 else int(fallback or 0)

    @staticmethod
    def _today_str() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    # ── login ─────────────────────────────────────────────────────────

    _DEBUG_SCREENSHOT_RETENTION = 20

    async def _save_debug_screenshot(self, suffix: str = "") -> None:
        """Salva screenshot para diagnóstico em ~/.fretio/alfa_debug/ (só com FRETIO_DEBUG_DUMP)."""
        # Screenshot full-page expõe CNPJ/endereço; só captura sob flag explícita.
        if not os.environ.get("FRETIO_DEBUG_DUMP"):
            return
        try:
            debug_dir = os.path.join(os.path.expanduser("~"), ".fretio", "alfa_debug")
            os.makedirs(debug_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"alfa_{ts}_{suffix}.png" if suffix else f"alfa_{ts}.png"
            fpath = os.path.join(debug_dir, fname)
            if self._page:
                await self._page.screenshot(path=fpath, full_page=True)
                logger.info("[ALFA] Screenshot salvo: %s", fpath)
                self._prune_debug_screenshots(debug_dir)
        except Exception as e:
            logger.debug("[ALFA] Falha ao salvar screenshot: %s", e)

    @classmethod
    def _prune_debug_screenshots(cls, debug_dir: str) -> None:
        try:
            arquivos = [
                os.path.join(debug_dir, n)
                for n in os.listdir(debug_dir)
                if n.startswith("alfa_") and n.endswith(".png")
            ]
            arquivos.sort(key=os.path.getmtime, reverse=True)
            for antigo in arquivos[cls._DEBUG_SCREENSHOT_RETENTION:]:
                try:
                    os.remove(antigo)
                except OSError:
                    pass
        except OSError:
            pass

    async def _navegar_para_cotacao(self) -> bool:
        """Navega para o formulário de cotação: card 'Painel de Cotações' → botão 'Nova Cotação'."""
        page = self._page
        try:
            current = page.url.lower()
            logger.info("[ALFA] _navegar_para_cotacao — URL atual: %s", page.url)

            # Se já está na página com o formulário, não precisa navegar
            if "cotacao" in current and "login" not in current:
                try:
                    await page.wait_for_selector("#tipoPagador", timeout=2000)
                    logger.info("[ALFA] Já estava na página de cotação com formulário pronto")
                    return True
                except Exception:
                    logger.debug("[ALFA] URL contém 'cotacao' mas #tipoPagador não encontrado")

            # Após cotação anterior, pode ter botão "Fazer outra Cotação"
            try:
                btn_outra = page.locator("a[href='/cotacao/api/']").first
                if await btn_outra.is_visible(timeout=1500):
                    await btn_outra.click()
                    logger.info("[ALFA] Clicou em 'Fazer outra Cotação'")
                    try:
                        await page.wait_for_selector("#tipoPagador", timeout=8000)
                        return True
                    except Exception:
                        pass
            except Exception:
                pass

            # Tenta navegação direta pela URL da cotação (mais rápido que menu)
            logger.info("[ALFA] Navegação direta para %s", self.cotacao_api_url)
            try:
                await page.goto(self.cotacao_api_url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(800)
                try:
                    await page.wait_for_selector("#tipoPagador", timeout=8000)
                    logger.info("[ALFA] Formulário encontrado via URL direta")
                    return True
                except Exception:
                    logger.debug("[ALFA] URL direta não encontrou formulário, tentando via menu...")
            except Exception as e:
                logger.debug("[ALFA] Navegação direta falhou: %s", e)

            # Fallback: volta à página base e navega via menu de cards
            await page.goto(self.BASE_URL, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1000)

            # Clicar no card "Painel de Cotações" via JS (combina todas as tentativas)
            card_clicked = await page.evaluate("""() => {
                // Tenta card com class 'opcoes'
                const cards = document.querySelectorAll('div.opcoes');
                for (const c of cards) {
                    const txt = (c.textContent || '').toLowerCase();
                    if (txt.includes('painel') && txt.includes('cota')) {
                        c.click();
                        return true;
                    }
                }
                // Fallback: qualquer link/botão contendo cotação
                const links = document.querySelectorAll('a, button, div[onclick], div.opcoes');
                for (const el of links) {
                    const txt = (el.textContent || '').toLowerCase();
                    if (txt.includes('cota') && (txt.includes('painel') || txt.includes('frete'))) {
                        el.click();
                        return true;
                    }
                }
                return false;
            }""")

            if not card_clicked:
                logger.warning("[ALFA] Não encontrou card 'Painel de Cotações'")
                await self._save_debug_screenshot("card_nao_encontrado")
                return False

            logger.info("[ALFA] Clicou no card 'Painel de Cotações'")
            await page.wait_for_timeout(1000)

            # Clicar em "Nova Cotação" via JS
            nova_clicked = await page.evaluate("""() => {
                const a = document.querySelector('a[href*="/cotacao/api"]');
                if (a) { a.click(); return true; }
                const links = document.querySelectorAll('a');
                for (const el of links) {
                    const txt = (el.textContent || '').toLowerCase();
                    if (txt.includes('nova') && txt.includes('cota')) {
                        el.click();
                        return true;
                    }
                }
                return false;
            }""")

            if not nova_clicked:
                logger.warning("[ALFA] Não encontrou botão 'Nova Cotação'")
                await self._save_debug_screenshot("nova_cotacao_nao_encontrado")
                return False

            logger.info("[ALFA] Clicou em 'Nova Cotação'")

            # Espera formulário renderizar
            try:
                await page.wait_for_selector("#tipoPagador", timeout=10000)
                return True
            except Exception:
                logger.warning("[ALFA] Formulário não renderizou após navegação por menu")
                await self._save_debug_screenshot("formulario_nao_renderizou")
                return False
        except Exception as e:
            logger.warning("[ALFA] _navegar_para_cotacao falhou com exceção: %s", e)
            return False

    async def _is_logged_in(self) -> bool:
        """Verifica se está autenticado navegando até cotação via menu."""
        page = self._page
        try:
            current_url = page.url.lower()
            logger.info("[ALFA] _is_logged_in — URL atual: %s", page.url)
            if "login" in current_url:
                logger.info("[ALFA] Ainda na página de login")
                return False
            return await self._navegar_para_cotacao()
        except Exception as e:
            logger.warning("[ALFA] _is_logged_in check falhou: %s", e)
            return False

    async def _login(self) -> bool:
        if self._logged_in:
            return True

        await self._init_browser()

        # ── Headless: login direto via Playwright (Turnstile não funciona) ──
        if self.headless:
            page = self._page
            await page.goto(self.login_url, wait_until="domcontentloaded", timeout=60000)
            await page.locator("#username").fill(self.login)
            await page.locator("#password").fill(self.senha)
            try:
                await page.locator("#btn-enviar").click(timeout=5000)
            except Exception:
                pass
            await page.wait_for_timeout(5000)
            if "login" not in page.url.lower():
                self._logged_in = True
                return True
            self.last_error = "Login Alfa falhou (headless, Turnstile bloqueou)"
            return False

        # ── Não-headless ──
        # Espera a página carregar (apenas no primeiro launch)
        chrome_was_running = self._chrome_proc is not None and self._chrome_proc.poll() is None
        if not chrome_was_running:
            await asyncio.sleep(2)

        # Verifica se já está logado (sessão persistente do user-data-dir)
        current_url = self._get_page_url_sync()
        if current_url and self.BASE_URL.lower() in current_url.lower() and "login" not in current_url.lower():
            await self._connect_playwright()
            if await self._is_logged_in():
                self._logged_in = True
                await self._ocultar_janela()
                self._set_taskbar_visible(False)
                return True

        # Turnstile necessário: desconecta Playwright
        await self._disconnect_playwright()

        # Preenche login/senha via CDP bruto (sem Playwright = sem detecção)
        fill_js = (
            "(function(){"
            f"var u=document.querySelector('#username');"
            f"var p=document.querySelector('#password');"
            f"if(u){{u.value={json.dumps(self.login)};u.dispatchEvent(new Event('input',{{bubbles:true}}));}}"
            f"if(p){{p.value={json.dumps(self.senha)};p.dispatchEvent(new Event('input',{{bubbles:true}}));}}"
            "})();"
        )
        await self._cdp_eval_raw(fill_js)
        logger.info("[ALFA] Credenciais preenchidas via CDP direto (sem Playwright)")

        # Tenta clicar submit (Turnstile pode auto-resolver para browsers conhecidos)
        click_js = "(function(){var b=document.querySelector('#btn-enviar');if(b){b.click();}})();"
        await self._cdp_eval_raw(click_js)

        # Aguarda até 15s por auto-pass do Turnstile (janela fica oculta)
        logger.info("[ALFA] Tentando auto-login (sem janela)...")
        auto_pass = False
        for _ in range(30):
            await asyncio.sleep(0.5)
            url = self._get_page_url_sync()
            if url and self.BASE_URL.lower() in url.lower() and "login" not in url.lower():
                auto_pass = True
                break

        if not auto_pass:
            # Auto-pass falhou — mostra janela para resolução manual do Turnstile
            self._ensure_chrome_visible()
            self._set_taskbar_visible(True)
            logger.info("[ALFA] Aguardando usuario resolver Turnstile e clicar Continuar...")

            for _ in range(self.LOGIN_MAX_WAIT_S):
                await asyncio.sleep(0.5)
                url = self._get_page_url_sync()
                if url and self.BASE_URL.lower() in url.lower() and "login" not in url.lower():
                    break
            else:
                self.last_error = "Login Alfa timeout (aguardando login manual)"
                logger.error(f"[ALFA] {self.last_error}")
                return False

        # Login OK — espera a página estabilizar antes de conectar Playwright
        logger.info("[ALFA] Login detectado! Aguardando p\u00e1gina estabilizar...")
        await asyncio.sleep(1)

        # Conecta Playwright (Turnstile já passou)
        await self._connect_playwright()
        # Oculta janela e taskbar após login
        await self._ocultar_janela()
        self._set_taskbar_visible(False)

        # Verifica se o formulário de cotação renderizou (com retry)
        for attempt in range(2):
            if await self._is_logged_in():
                self._logged_in = True
                logger.info("[ALFA] Login OK — Playwright conectado após Turnstile")
                return True
            logger.info(f"[ALFA] Formul\u00e1rio n\u00e3o renderizou (tentativa {attempt+1}/2), aguardando...")
            await asyncio.sleep(1)

        self._logged_in = False
        self.last_error = "Login realizado mas formulário não renderizou após 2 tentativas"
        logger.error(f"[ALFA] {self.last_error}")
        return False

    async def pre_login(self) -> None:
        try:
            await self._login()
        except Exception as e:
            logger.warning(f"[ALFA] Pre-login falhou: {e}")

    # ── cotacao ───────────────────────────────────────────────────────

    async def _preencher_formulario(
        self,
        *,
        cnpj_remetente: str,
        cnpj_destinatario: str,
        cep_remetente: str,
        cep_destinatario: str,
        peso: float,
        valor: float,
        volumes: int,
        cubagem_m3: float,
        tipo_pagador: str = "1",
    ) -> None:
        page = self._page

        # Navega para cotação via menu (não por URL direta)
        logger.info("[ALFA] Iniciando navegação para formulário de cotação...")
        if not await self._navegar_para_cotacao():
            # Se caiu na tela de login, precisa relogar
            if "login" in page.url.lower():
                logger.info("[ALFA] Sessão expirou, refazendo login...")
                self._logged_in = False
                if not await self._login():
                    raise RuntimeError("Login Alfa falhou")
                # Garante janela oculta após re-login por sessão expirada
                if not self.headless:
                    await self._ocultar_janela()
                    self._set_taskbar_visible(False)

            if not await self._navegar_para_cotacao():
                logger.error("[ALFA] Navegação para cotação falhou")
                await self._save_debug_screenshot("navegacao_falhou")
                raise RuntimeError("Não conseguiu navegar para cotação")

        # Espera o formulário renderizar
        await page.wait_for_selector("#tipoPagador", timeout=10000)

        await page.select_option("#tipoPagador", tipo_pagador)
        await page.wait_for_timeout(400)

        # Preenche todos os campos via JS de uma vez (mais rápido que fills individuais)
        await page.evaluate(
            """(data) => {
                function setVal(sel, val) {
                    const el = document.querySelector(sel);
                    if (!el) return;
                    el.value = val;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }
                function setSelect(sel, val) {
                    const el = document.querySelector(sel);
                    if (!el) return;
                    el.value = val;
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }
                setVal('#pesoMercadoria', data.peso);
                setVal('#valorMercadoria', data.valor);
                setVal('#dataInicialColeta', data.data);
                setVal('#totalVolumes', data.volumes);
                setVal('#totalCubagem', data.cubagem);
                setSelect('#tipoCarga', '0');
                setSelect('#tipoZona', '0');
            }""",
            {
                "peso": self._fmt_decimal(peso, 3, comma=True),
                "valor": self._fmt_decimal(valor, 2, comma=True),
                "data": self._today_str(),
                "volumes": str(int(volumes or 0)),
                "cubagem": self._fmt_decimal(cubagem_m3, 3, comma=False),
            },
        )

        # Preenche CNPJs e CEPs por último para não ser sobrescrito pelo Angular
        await page.wait_for_timeout(300)
        await page.evaluate(
            """(data) => {
                function setVal(sel, val) {
                    const el = document.querySelector(sel);
                    if (!el) return;
                    el.value = val;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.dispatchEvent(new Event('blur', {bubbles: true}));
                }
                setVal('#cnpjRemetente', data.cnpjRem);
                setVal('#cepRemetente', data.cepRem);
                setVal('#cnpjDestinatario', data.cnpjDest);
                setVal('#cepDestinatario', data.cepDest);
            }""",
            {
                "cnpjRem": self._format_doc(cnpj_remetente),
                "cepRem": self._digits(cep_remetente),
                "cnpjDest": self._format_doc(cnpj_destinatario),
                "cepDest": self._digits(cep_destinatario),
            },
        )

    async def _do_submit_click(self, submit_btn) -> None:
        """Clica no botão submit com fallbacks."""
        try:
            await submit_btn.click(timeout=8000)
        except Exception:
            logger.warning("[ALFA] Click normal falhou, tentando via JS...")
            await self._page.evaluate("document.querySelector(\"button[type='submit']\")?.click()")

    async def _extrair_resultado(self, api_response=None) -> Optional[Cotacao]:
        page = self._page

        valor_frete = None
        prazo_dias = 0

        # Tenta extrair da response da API (já capturada durante o click)
        if api_response is not None and asyncio.iscoroutine(api_response):
            api_response = await api_response
        if api_response is not None and api_response.ok:
            try:
                data = await api_response.json()
                valor_frete = self._find_json_value(data, ["frete", "valor", "total"])
                prazo_dias = int(self._find_json_value(data, ["prazo", "dia", "dias"]) or 0)
            except Exception:
                valor_frete = None
                prazo_dias = 0

        # Fallback: extrai do DOM (resultado já está renderizado)
        if valor_frete is None:
            await page.wait_for_timeout(500)
            body_txt = await page.inner_text("body")
            body_txt = (body_txt or "").replace("\xa0", " ")
            m_val = re.search(r"R\$\s*([\d.]+,\d{2})", body_txt)
            if m_val:
                valor_frete = self._parse_decimal_any(m_val.group(1))
            m_prazo = re.search(r"(\d+)\s*dias?", body_txt, re.IGNORECASE)
            if m_prazo:
                prazo_dias = int(m_prazo.group(1))

        if valor_frete is None:
            self.last_error = "ALFA: valor de frete nao encontrado"
            return None

        return Cotacao(
            transportadora=self.nome,
            prazo_dias=int(prazo_dias or 0),
            valor_frete=round(float(valor_frete), 2),
            restricoes="Cotacao via portal Alfa",
            timestamp=datetime.now(),
        )

    def _find_json_value(self, data: Any, keys: list[str]) -> float | None:
        if isinstance(data, dict):
            for k, v in data.items():
                k_low = str(k).lower()
                if any(key in k_low for key in keys):
                    parsed = self._parse_decimal_any(v)
                    if parsed is not None:
                        return parsed
                nested = self._find_json_value(v, keys)
                if nested is not None:
                    return nested
        elif isinstance(data, list):
            for item in data:
                nested = self._find_json_value(item, keys)
                if nested is not None:
                    return nested
        return None

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
        tipo_pagador: str = "1",
    ) -> Optional[Cotacao]:
        try:
            self.last_error = None
            if not await self._login():
                return None

            # Garante que a janela está oculta após login (mesmo que Turnstile tenha sido resolvido)
            if not self.headless:
                await self._ocultar_janela()
                self._set_taskbar_visible(False)

            vol_total = self._sum_volumes(cubagens, volumes)
            cub_total = self._calc_cubagem_m3(cubagens)
            if cub_total <= 0:
                if cubagem_m3 and float(cubagem_m3) > 0:
                    cub_total = float(cubagem_m3)
                elif comprimento_cm > 0 and largura_cm > 0 and altura_cm > 0 and vol_total > 0:
                    cub_total = (float(comprimento_cm) * float(largura_cm) * float(altura_cm) / 1_000_000.0) * vol_total

            await self._preencher_formulario(
                cnpj_remetente=cnpj_remetente,
                cnpj_destinatario=cnpj_destinatario,
                cep_remetente=origem,
                cep_destinatario=destino,
                peso=peso,
                valor=valor,
                volumes=vol_total,
                cubagem_m3=cub_total,
                tipo_pagador=tipo_pagador,
            )

            submit_btn = self._page.locator("button[type='submit']")
            try:
                await submit_btn.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass

            # Captura a response da API durante o click para não perder
            # respostas rápidas (causa raiz do delay de 30s)
            api_response = None
            try:
                async with self._page.expect_response(
                    lambda r: self.cotacao_api_url in r.url and r.request.method.upper() in {"POST", "GET"},
                    timeout=15000,
                ) as response_info:
                    await self._do_submit_click(submit_btn)
                api_response = response_info.value
                if asyncio.iscoroutine(api_response) or asyncio.isfuture(api_response):
                    api_response = await api_response
            except Exception:
                # Se expect_response falhar (timeout ou click falhou antes),
                # continua sem response — fallback DOM será usado
                pass

            return await self._extrair_resultado(api_response)

        except Exception as e:
            self.last_error = str(e)
            logger.error(f"[ALFA] Erro na cotacao: {e}")
            return None

    async def cotar(self, request: QuoteRequest) -> QuoteResponse:
        return await super().cotar(request)
