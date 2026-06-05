"""Provider Rodonaves (RTE) – seletores extraidos da gravação Playwright."""
from typing import Any, Optional
import asyncio
import json
import os
import re
import socket
import subprocess
import time
from playwright.async_api import async_playwright
from fretio.providers.base import ProviderBase, find_chrome, _find_free_port, _kill_proc, _register_owned_proc
from fretio.providers._win_taskbar import (
    ocultar_taskbar_por_pagina,
    posicionar_janela_por_pagina,
    posicionar_janela_por_pid,
    trazer_janela_frente,
)
from fretio.providers.provider_utils import _digits, get_stealth_script
from fretio.models import Cotacao
from fretio.quotation_contract import QuoteRequest, QuoteResponse
from fretio.logging_conf import get_logger

logger = get_logger(__name__)


import random

# User-Agent completo de Chrome real (evita sinal de bot no reCAPTCHA)
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)

# Script de stealth injetado antes de qualquer página carregar.
# Remove sinais de automação que o reCAPTCHA usa para escalar dificuldade.
_STEALTH_JS = get_stealth_script()


class RodonavesProvider(ProviderBase):
    """Provider Rodonaves via portal cliente.rte.com.br (seletores gravados)."""

    PORTAL_URL = "https://cliente.rte.com.br/Quotation"
    BASE_URL = "https://cliente.rte.com.br"
    CAPTCHA_MAX_WAIT_S = 45
    _digits = staticmethod(_digits)

    @property
    def portal_entry_url(self) -> str:
        return self.login_url or f"{self.BASE_URL}/?showLogin=true"

    @property
    def quotation_url(self) -> str:
        return self.cotacao_url or self.PORTAL_URL

    def __init__(
        self,
        dominio: str,
        usuario: str,
        senha: str,
        cnpj_pagador: str,
        login_url: str = "",
        cotacao_url: str = "",
        headless: bool = True,
    ):
        super().__init__(nome="RODONAVES")
        self.dominio = str(dominio or "").strip()
        self.usuario = str(usuario or "").strip()
        self.senha = str(senha or "").strip()
        self.cnpj_pagador = self._digits(cnpj_pagador)
        self.login_url = str(login_url or "").strip()
        self.cotacao_url = str(cotacao_url or "").strip()
        self.headless = bool(headless)
        self._effective_headless = False
        self.last_error: str | None = None
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._logged_in = False
        self._cdp_session = None
        self._window_id = None
        self._chrome_proc = None
        self._active_user_data_dir = ""
        self._passo_atual: str = "inicio"
        self._diagnostic_context: dict[str, Any] = {}
        self._stage_started_at: float | None = None
        self._login_status: dict[str, bool] = {
            "login_ok": False,
            "aguardando_captcha": False,
            "captcha_resolvido": False,
            "cotacao_ok": False,
            "login_falhou": False,
        }

    def _set_login_status(self, state: str, value: bool = True) -> None:
        """Mantém estados de login/captcha/cotação separados para a UI/orquestrador."""
        if state not in self._login_status:
            self._login_status[state] = bool(value)
        else:
            self._login_status[state] = bool(value)
        if state in {"login_ok", "cotacao_ok"} and value:
            self._logged_in = True
            self._login_status["login_falhou"] = False
        if state == "login_falhou" and value and self._login_status.get("cotacao_ok"):
            self._login_status["login_falhou"] = False

    def _start_stage(self, stage: str) -> float:
        self._passo_atual = stage
        started_at = time.monotonic()
        self._stage_started_at = started_at
        logger.info("[%s] Etapa iniciada: %s", self.nome, stage)
        return started_at

    def _finish_stage(self, stage: str, started_at: float, *, details: str = "") -> None:
        elapsed = time.monotonic() - started_at
        suffix = f" ({details})" if details else ""
        logger.info("[%s] Etapa concluída: %s em %.2fs%s", self.nome, stage, elapsed, suffix)

    def _mark_login_failed(self) -> None:
        if self._login_status.get("cotacao_ok"):
            return
        self._logged_in = False
        self._set_login_status("login_falhou", True)
        self._login_status["login_ok"] = False

    def _mark_valid_quote(self) -> None:
        self._set_login_status("login_ok", True)
        self._set_login_status("cotacao_ok", True)
        self._login_status["login_falhou"] = False
        if self.last_error and "pre-login" in self.last_error.lower() and "timeout" in self.last_error.lower():
            logger.info(
                "[%s] Timeout anterior de pre-login neutralizado após cotação válida",
                self.nome,
            )
            self.last_error = None

    @property
    def login_status(self) -> dict[str, bool]:
        return dict(self._login_status)

    @staticmethod
    def _is_retryable_navigation_error(error: BaseException | str) -> bool:
        text = str(error or "")
        if not text:
            return False
        upper = text.upper()
        if "TIMEOUT" in upper or "TIMED OUT" in upper:
            return True
        return any(
            token in upper
            for token in (
                "ERR_ABORTED",
                "ERR_CONNECTION_RESET",
                "ERR_CONNECTION_CLOSED",
                "ERR_CONNECTION_TIMED_OUT",
                "ERR_NAME_NOT_RESOLVED",
                "ERR_NETWORK_CHANGED",
                "ERR_INTERNET_DISCONNECTED",
                "ERR_ADDRESS_UNREACHABLE",
                "ERR_HTTP2_PROTOCOL_ERROR",
            )
        )


    @staticmethod
    def _is_playwright_lifecycle_error(error: BaseException | str) -> bool:
        text = str(error or "").lower()
        return any(
            token in text
            for token in (
                "target page, context or browser has been closed",
                "target closed",
                "browser has been closed",
                "context has been closed",
                "page has been closed",
                "browser closed",
            )
        )

    def _safe_current_url(self) -> str:
        page = self._page
        if page is None:
            return "(sem page)"
        try:
            if page.is_closed():
                return "(page fechada)"
        except Exception:
            return "(page sem status)"
        try:
            return str(getattr(page, "url", "") or "(sem URL)")
        except Exception:
            return "(URL indisponivel)"

    def _browser_is_connected(self) -> bool:
        try:
            return bool(self._browser and self._browser.is_connected())
        except Exception:
            return False

    def _page_is_closed(self, page: Any | None = None) -> bool:
        page = self._page if page is None else page
        if page is None:
            return True
        try:
            return bool(page.is_closed())
        except Exception:
            return True

    def _lifecycle_closed_reason(self) -> str | None:
        if not self._browser:
            return "browser ausente"
        if not self._browser_is_connected():
            return "browser desconectado"
        if self._context is None:
            return "context ausente"
        if self._page is None:
            return "page ausente"
        if self._page_is_closed():
            return "page fechada"
        return None

    def _record_lifecycle_diagnostic(
        self,
        *,
        stage: str,
        target_url: str,
        reason: str,
        previous_stage: str | None = None,
        error: BaseException | None = None,
    ) -> None:
        detail = (
            "Lifecycle Playwright RODONAVES: "
            f"stage={stage}; previous_stage={previous_stage or self._passo_atual}; "
            f"target_url={target_url}; current_url={self._safe_current_url()}; "
            f"reason={reason}; headless={self._effective_headless}"
        )
        self._diagnostic_context.update({
            "rodonaves_stage": stage,
            "rodonaves_previous_stage": previous_stage or self._passo_atual,
            "rodonaves_target_url": target_url,
            "rodonaves_current_url": self._safe_current_url(),
            "rodonaves_close_reason": reason,
            "rodonaves_effective_headless": self._effective_headless,
        })
        if error is not None:
            detail = f"{detail}; error={error}"
        self.last_error = detail
        logger.warning(f"[{self.nome}] {detail}")

    async def _ensure_live_page_for_navigation(
        self,
        *,
        stage: str,
        target_url: str,
        recreate_session: bool = True,
    ):
        """Garante browser/context/page vivos antes de usar page.goto()."""
        reason = self._lifecycle_closed_reason()
        if reason is None:
            return self._page

        self._record_lifecycle_diagnostic(
            stage=stage,
            target_url=target_url,
            reason=reason,
        )

        if not recreate_session:
            raise RuntimeError(self.last_error or f"Sessao Playwright indisponivel: {reason}")

        if reason in {"browser ausente", "browser desconectado", "context ausente"}:
            await self.cleanup()
            await self._init_browser()
            await self._sync_active_page()
        else:
            try:
                if self._context is not None:
                    self._page = await self._context.new_page()
                else:
                    await self.cleanup()
                    await self._init_browser()
                    await self._sync_active_page()
            except Exception as exc:
                self._record_lifecycle_diagnostic(
                    stage=stage,
                    target_url=target_url,
                    reason="falha ao recriar page; reiniciando sessao",
                    error=exc,
                )
                await self.cleanup()
                await self._init_browser()
                await self._sync_active_page()

        reason_after = self._lifecycle_closed_reason()
        if reason_after is not None:
            self._record_lifecycle_diagnostic(
                stage=stage,
                target_url=target_url,
                reason=f"sessao continua indisponivel apos recriacao: {reason_after}",
            )
            raise RuntimeError(self.last_error or "Sessao Playwright indisponivel apos recriacao")
        self._logged_in = False
        return self._page

    async def _goto_with_lifecycle_guard(
        self,
        target_url: str,
        *,
        stage: str,
        wait_until: str,
        timeout: int,
        attempts: int = 3,
    ):
        last_error: BaseException | None = None
        for attempt in range(max(1, attempts)):
            page = await self._ensure_live_page_for_navigation(stage=stage, target_url=target_url)
            try:
                await page.goto(target_url, wait_until=wait_until, timeout=timeout)
                return page
            except Exception as goto_err:
                last_error = goto_err
                previous_stage = self._passo_atual
                if self._is_playwright_lifecycle_error(goto_err) and attempt < attempts - 1:
                    self._record_lifecycle_diagnostic(
                        stage=stage,
                        target_url=target_url,
                        reason="page/context/browser fechou durante goto; recriando sessao",
                        previous_stage=previous_stage,
                        error=goto_err,
                    )
                    await self.cleanup()
                    await self._init_browser()
                    await asyncio.sleep(0.5)
                    continue
                if self._is_retryable_navigation_error(goto_err) and attempt < attempts - 1:
                    logger.warning(
                        f"[{self.nome}] goto {target_url} transitório, retry {attempt + 1}/{attempts}: {goto_err}"
                    )
                    await asyncio.sleep(1)
                    continue
                if self._is_playwright_lifecycle_error(goto_err):
                    self._record_lifecycle_diagnostic(
                        stage=stage,
                        target_url=target_url,
                        reason="falha de lifecycle persistente durante goto",
                        previous_stage=previous_stage,
                        error=goto_err,
                    )
                raise
        raise RuntimeError(str(last_error) if last_error else f"Falha ao navegar para {target_url}")

    async def _wait_for_quotation_form(self, page: Any, *, timeout: int, success_message: str) -> bool:
        deadline = time.monotonic() + max(timeout, 0) / 1000
        while True:
            receiver_visible = await self._locator_looks_ready(page, "#ReceiverTaxId")
            destination_visible = await self._locator_looks_ready(page, "#destinationZipCode")
            calculate_visible = await self._locator_looks_ready(page, "#calculateQuotationBtn")
            if calculate_visible and (receiver_visible or destination_visible):
                logger.info(f"[{self.nome}] {success_message}")
                return True
            if time.monotonic() >= deadline:
                return False
            if hasattr(page, "wait_for_timeout"):
                await page.wait_for_timeout(250)
            else:
                await asyncio.sleep(0.25)

    async def _locator_looks_ready(self, page: Any, selector: str) -> bool:
        locator = page.locator(selector)
        is_visible = getattr(locator, "is_visible", None)
        if callable(is_visible):
            try:
                return bool(await is_visible(timeout=250))
            except TypeError:
                try:
                    return bool(await is_visible())
                except Exception:
                    pass
            except Exception:
                pass
        count = getattr(locator, "count", None)
        if callable(count):
            try:
                if int(await count()) <= 0:
                    return False
            except Exception:
                pass
        wait_for = getattr(locator, "wait_for", None)
        if callable(wait_for):
            try:
                await wait_for(timeout=250)
                return True
            except Exception:
                return False
        return False

    async def _has_login_prompt(self, page: Any) -> bool:
        return await self._locator_looks_ready(page, "#cpfcnp")

    async def _open_portal_entrypoint(self):
        target_url = self.portal_entry_url
        page = await self._ensure_live_page_for_navigation(
            stage="abrindo_portal",
            target_url=target_url,
        )
        if await self._wait_for_quotation_form(
            page,
            timeout=1000,
            success_message="Formulário de cotação já visível no entrypoint",
        ):
            return page

        last_error: Exception | None = None
        for candidate_url in (target_url, self.BASE_URL):
            try:
                page = await self._goto_with_lifecycle_guard(
                    candidate_url,
                    stage="abrindo_portal",
                    wait_until="domcontentloaded",
                    timeout=15000,
                    attempts=1,
                )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    f"[{self.nome}] Entry point {candidate_url} não carregou de forma limpa: {exc}"
                )
                page = await self._ensure_live_page_for_navigation(
                    stage="abrindo_portal",
                    target_url=candidate_url,
                )
            if await self._wait_for_quotation_form(
                page,
                timeout=3000,
                success_message=f"Entry point {candidate_url} abriu direto a cotação",
            ):
                return page
            if await self._has_login_prompt(page):
                logger.info(f"[{self.nome}] Entry point carregado com tela de login: {candidate_url}")
                return page

        if last_error is not None:
            raise RuntimeError(
                f"Entry point Rodonaves não carregou corretamente (URL: {self._safe_current_url()})"
            ) from last_error
        raise RuntimeError(
            f"Entry point Rodonaves não exibiu login nem formulário de cotação (URL: {self._safe_current_url()})"
        )

    async def _perform_ajax_login(self, page: Any) -> None:
        logger.info(f"[{self.nome}] Iniciando login...")
        for _jquery_attempt in range(2):
            try:
                await page.wait_for_function(
                    "typeof jQuery !== 'undefined' && typeof jQuery.ajax === 'function'",
                    timeout=10000,
                )
                break
            except Exception:
                if _jquery_attempt == 0:
                    logger.warning(f"[{self.nome}] jQuery não carregou, recarregando página...")
                    try:
                        await page.reload(wait_until="domcontentloaded", timeout=10000)
                    except Exception:
                        pass
                    continue
                raise RuntimeError(f"Login Rodonaves falhou — jQuery não carregou (URL: {page.url})")

        login_doc = self._digits(self.usuario) or self._digits(self.dominio) or self.cnpj_pagador
        logger.info(f"[{self.nome}] Login AJAX com doc={login_doc[:4]}***{login_doc[-2:]}")

        result = await page.evaluate("""({cpfcnp, password}) => {
            return new Promise((resolve) => {
                const root = typeof rootPath !== 'undefined' ? rootPath : '';
                jQuery.ajax({
                    type: "POST",
                    url: root + "/CustomerAccount/LogIn",
                    dataType: "json",
                    contentType: "application/json; charset=utf-8",
                    data: JSON.stringify({ Cpfcnp: cpfcnp, Password: password }),
                    success: function(r) { resolve(r); },
                    error: function(xhr, status, err) {
                        resolve({ Success: false, ErrorMessage: err || status || 'AJAX error' });
                    }
                });
            });
        }""", {"cpfcnp": login_doc, "password": self.senha})

        if not result or not result.get("Success"):
            self._mark_login_failed()
            error_msg = (
                (result or {}).get("WarningMessage")
                or (result or {}).get("ErrorMessage")
                or "resposta inesperada do servidor"
            )
            raise RuntimeError(f"Login Rodonaves falhou — {error_msg}")

        self._set_login_status("login_ok", True)

    async def _go_to_quotation_after_login(self):
        """Vai para a cotação após login e aceita sucesso por elementos reais do formulário."""
        quotation_url = self.quotation_url
        page = await self._ensure_live_page_for_navigation(
            stage="navegando_cotacao",
            target_url=quotation_url,
        )

        if await self._wait_for_quotation_form(
            page,
            timeout=3000,
            success_message="Formulário já visível após login",
        ):
            return page

        logger.info(f"[{self.nome}] Navegando para cotação... URL atual: {page.url}")

        goto_error: Exception | None = None
        try:
            page = await self._goto_with_lifecycle_guard(
                quotation_url,
                stage="navegando_cotacao",
                wait_until="commit",
                timeout=12000,
                attempts=1,
            )
        except Exception as exc:
            goto_error = exc
            logger.warning(
                f"[{self.nome}] goto {quotation_url} não confirmou commit imediatamente: {exc}. "
                "Aguardando formulário da cotação na mesma página..."
            )
            page = await self._ensure_live_page_for_navigation(
                stage="navegando_cotacao",
                target_url=quotation_url,
            )

        if await self._wait_for_quotation_form(
            page,
            timeout=35000,
            success_message="Formulário visível após navegação da cotação",
        ):
            return page

        current_url = (getattr(page, "url", "") or "").lower()
        if await self._has_login_prompt(page) or "showlogin" in current_url:
            raise RuntimeError(
                f"Cotação Rodonaves voltou para login/entrypoint sem formulário (URL: {page.url})"
            )
        if goto_error is not None:
            raise RuntimeError(
                f"Formulário de cotação não carregou após navegação pendente (URL: {page.url})"
            ) from goto_error
        raise RuntimeError(f"Formulário de cotação não carregou (URL: {page.url})")

    # ── helpers ────────────────────────────────────────────────────────

    async def _simular_interacao_humana(self, page) -> None:
        """Simula mouse/scroll rápidos para acumular score no reCAPTCHA."""
        vw = 820
        vh = 720

        # Curva Bézier compacta entre dois pontos
        async def _bezier_move(x1, y1, x2, y2):
            steps = random.randint(8, 14)
            cx = (x1 + x2) / 2 + random.randint(-60, 60)
            cy = (y1 + y2) / 2 + random.randint(-40, 40)
            for i in range(steps + 1):
                t = i / steps
                x = (1 - t)**2 * x1 + 2 * (1 - t) * t * cx + t**2 * x2
                y = (1 - t)**2 * y1 + 2 * (1 - t) * t * cy + t**2 * y2
                await page.mouse.move(x, y)
                await page.wait_for_timeout(random.randint(3, 10))

        # Move mouse por 2 pontos aleatórios (reduzido de 4)
        px, py = random.randint(100, 300), random.randint(50, 150)
        await page.mouse.move(px, py)
        await page.wait_for_timeout(random.randint(100, 250))

        pontos = [
            (random.randint(200, vw - 200), random.randint(100, 300)),
            (random.randint(100, vw - 100), random.randint(300, vh - 100)),
        ]
        for x, y in pontos:
            await _bezier_move(px, py, x, y)
            px, py = x, y
            await page.wait_for_timeout(random.randint(80, 200))

        # Scroll rápido para baixo e volta
        for _ in range(random.randint(1, 2)):
            await page.mouse.wheel(0, random.randint(40, 80))
            await page.wait_for_timeout(random.randint(50, 150))
        await page.mouse.wheel(0, -random.randint(20, 50))
        await page.wait_for_timeout(random.randint(200, 500))

    async def _fill_field(self, page, field_id: str, value: str) -> None:
        """Fill campo por ID via Playwright, com fallback JS se overlay bloquear."""
        timeout_ms = 1200 if self._effective_headless else 5000
        try:
            await page.locator(f"#{field_id}").fill(value, timeout=timeout_ms)
        except Exception:
            logger.warning(f"[{self.nome}] fill #{field_id} bloqueado, usando JS")
            await page.evaluate(
                """({fieldId, val}) => {
                    const el = document.getElementById(fieldId);
                    if (!el) return;
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    )?.set;
                    if (setter) setter.call(el, val);
                    else el.value = val;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.dispatchEvent(new Event('blur', {bubbles: true}));
                }""",
                {"fieldId": field_id, "val": value},
            )

    @staticmethod
    def _format_cnpj(digits: str) -> str:
        d = re.sub(r"\D", "", str(digits or ""))
        if len(d) == 14:
            return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
        return d

    @staticmethod
    def _format_cep(digits: str) -> str:
        d = re.sub(r"\D", "", str(digits or ""))
        if len(d) == 8:
            return f"{d[:5]}-{d[5:]}"
        return d

    @staticmethod
    def _extrair_de_json(data) -> tuple:
        """Extrai (valor_frete, prazo_dias) de resposta JSON da API Rodonaves."""
        valor_frete = None
        prazo_dias = 0

        if isinstance(data, dict):
            html_data = data.get("Data") or data.get("data") or ""
            if isinstance(html_data, str) and len(html_data) > 20:
                for m in re.finditer(r"R\$\s*([\d.]+,\d{2})", html_data):
                    trecho = html_data[max(0, m.start() - 200): m.end() + 200].lower()
                    if any(kw in trecho for kw in (
                        "frete", "cotacao", "total geral",
                        "valor total", "prazo", "freight", "total",
                    )):
                        valor_frete = float(m.group(1).replace(".", "").replace(",", "."))
                        break
                if valor_frete is not None:
                    m_prazo = re.search(r"(\d+)\s*(?:dias?|day)", html_data, re.IGNORECASE)
                    if m_prazo:
                        prazo_dias = int(m_prazo.group(1))
                    return valor_frete, prazo_dias

        def _buscar(obj):
            nonlocal valor_frete, prazo_dias
            if isinstance(obj, dict):
                for key, val in obj.items():
                    kl = key.lower()
                    if valor_frete is None and isinstance(val, (int, float)) and val > 0:
                        if any(kw in kl for kw in (
                            "frete", "freight", "freighttotal", "totalfreight",
                            "valor", "value", "total", "price", "vlrfrete",
                            "valorfrete", "totalfrete", "vlrtotal",
                        )):
                            valor_frete = round(float(val), 2)
                    if prazo_dias == 0 and isinstance(val, (int, float)) and val > 0:
                        if any(kw in kl for kw in (
                            "prazo", "deadline", "days", "dias",
                            "deliverytime", "transittime", "leadtime",
                        )):
                            prazo_dias = int(val)
                    if isinstance(val, (dict, list)):
                        _buscar(val)
            elif isinstance(obj, list):
                for item in obj:
                    _buscar(item)

        _buscar(data)
        return valor_frete, prazo_dias


    @staticmethod
    def _peso_str(cub: dict) -> str:
        """Peso por volume em kg (mínimo 1), arredondado para inteiro."""
        try:
            v = float(cub.get("peso_por_volume_kg") or 0.0)
            if v > 0:
                return str(max(1, round(v)))
        except Exception:
            pass
        raise RuntimeError("Peso por volume ausente no romaneio")

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
            peso_por_volume_kg = None
            try:
                peso_raw = row.get("peso_por_volume_kg", None)
                if peso_raw is not None:
                    peso_val = float(peso_raw)
                    if peso_val > 0:
                        peso_por_volume_kg = peso_val
            except Exception:
                peso_por_volume_kg = None
            validas.append(
                {
                    "quantidade": qtd,
                    "comprimento_cm": comp,
                    "largura_cm": larg,
                    "altura_cm": alt,
                    "peso_por_volume_kg": peso_por_volume_kg,
                }
            )
        return validas

    # ── browser lifecycle ──────────────────────────────────────────────

    @staticmethod
    def _user_data_dir() -> str:
        """Diretório persistente exclusivo da Rodonaves para a sessão CDP."""
        base = os.path.join(os.path.expanduser("~"), ".fretio", "rodonaves_browser_data")
        os.makedirs(base, exist_ok=True)
        return base

    @staticmethod
    def _launcher_exit_can_still_spawn_browser(exit_code: int | None) -> bool:
        return exit_code == 0

    @classmethod
    def _is_internal_browser_url(cls, url: str | None) -> bool:
        lowered = str(url or "").strip().lower()
        if not lowered or lowered == "about:blank":
            return True
        return lowered.startswith((
            "about:",
            "chrome://",
            "chrome-extension://",
            "devtools://",
            "edge://",
        ))

    def _score_page_url(self, url: str | None) -> tuple[int, str]:
        lowered = str(url or "").strip().lower()
        if f"{self.BASE_URL.lower()}/quotation" in lowered:
            return (0, lowered)
        if self.BASE_URL.lower() in lowered:
            return (1, lowered)
        if lowered.startswith(("http://", "https://")):
            return (2, lowered)
        if self._is_internal_browser_url(lowered):
            return (4, lowered)
        return (3, lowered)

    async def _select_best_context_page(self) -> tuple[object | None, object | None]:
        best_context = None
        best_page = None
        best_score = None
        for context in getattr(self._browser, "contexts", []) or []:
            for page in getattr(context, "pages", []) or []:
                score = self._score_page_url(getattr(page, "url", ""))
                if best_score is None or score < best_score:
                    best_context = context
                    best_page = page
                    best_score = score
        return best_context, best_page

    async def _sync_active_page(self) -> None:
        if not self._browser:
            return
        best_context, best_page = await self._select_best_context_page()
        if best_context is not None:
            self._context = best_context
        elif self._context is None:
            self._context = self._browser.contexts[0] if self._browser.contexts else None
        if best_page is not None:
            self._page = best_page
        elif self._context is not None and self._page is None:
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()

    @staticmethod
    def _captcha_window_bounds(screen_w: int, screen_h: int) -> tuple[int, int, int, int]:
        w, h = 820, 720
        left = max(0, (int(screen_w) - w) // 2)
        top = max(0, (int(screen_h) - h) // 2)
        return left, top, w, h

    async def _reposicionar_janela_win32(
        self,
        *,
        left: int,
        top: int,
        width: int,
        height: int,
        bring_to_front: bool,
    ) -> bool:
        moved = await posicionar_janela_por_pagina(
            self._page,
            left=left,
            top=top,
            width=width,
            height=height,
            bring_to_front=bring_to_front,
        )
        if moved:
            return True
        for pid in self._candidate_window_pids():
            if posicionar_janela_por_pid(
                pid,
                left=left,
                top=top,
                width=width,
                height=height,
                bring_to_front=bring_to_front,
            ):
                logger.debug(f"[{self.nome}] Janela reposicionada via PID %d", pid)
                return True
        return False

    def _candidate_window_pids(self) -> list[int]:
        pids: list[int] = []
        pid_launcher = getattr(self._chrome_proc, "pid", 0) or 0
        if pid_launcher > 0:
            pids.append(int(pid_launcher))
        for pid in self._listar_pids_chrome_por_user_data_dir(self._active_user_data_dir):
            if pid > 0 and pid not in pids:
                pids.append(pid)
        return pids

    @staticmethod
    def _listar_pids_chrome_por_user_data_dir(user_data_dir: str) -> list[int]:
        if os.name != "nt":
            return []
        norm = str(user_data_dir or "").replace("/", "\\").rstrip("\\").lower()
        if not norm:
            return []
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                    "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }",
                ],
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as e:
            logger.debug(f"[{RodonavesProvider.__name__}] Falha ao listar PIDs do Chrome: {e}")
            return []

        pids: list[int] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            pid_str, cmd = line.split("|", 1)
            if norm not in cmd.lower():
                continue
            try:
                pid = int(pid_str.strip())
            except ValueError:
                continue
            if pid > 0 and pid not in pids:
                pids.append(pid)
        return pids

    def _has_live_browser_session(self) -> bool:
        try:
            return bool(self._context and self._browser and self._browser.is_connected())
        except Exception:
            return False

    @staticmethod
    def _fix_preferences(user_data_dir: str) -> None:
        """Marca exit_type como Normal e limpa sessão para evitar 'Restaurar páginas'."""
        import shutil as _shutil
        default_dir = os.path.join(user_data_dir, "Default")
        os.makedirs(default_dir, exist_ok=True)

        # Remove arquivos de sessão que disparam "Restaurar páginas?"
        for fname in ("Current Session", "Current Tabs", "Last Session", "Last Tabs"):
            fpath = os.path.join(default_dir, fname)
            try:
                if os.path.isfile(fpath):
                    os.remove(fpath)
            except Exception:
                pass
        sessions_dir = os.path.join(default_dir, "Sessions")
        if os.path.isdir(sessions_dir):
            _shutil.rmtree(sessions_dir, ignore_errors=True)

        prefs_path = os.path.join(default_dir, "Preferences")
        prefs = {}
        if os.path.isfile(prefs_path):
            try:
                with open(prefs_path, "r", encoding="utf-8") as f:
                    prefs = json.load(f)
            except Exception:
                prefs = {}
        prefs.setdefault("profile", {})["exit_type"] = "Normal"
        prefs["profile"]["exited_cleanly"] = True
        prefs.setdefault("session", {})["restore_on_startup"] = 5
        try:
            with open(prefs_path, "w", encoding="utf-8") as f:
                json.dump(prefs, f)
        except Exception:
            pass

    @staticmethod
    def _kill_stale_chrome(user_data_dir: str) -> None:
        """Mata processos Chrome órfãos que usem o mesmo user-data-dir."""
        import sys as _sys
        import subprocess as _subprocess
        if _sys.platform != "win32":
            return
        try:
            result = _subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                 "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"],
                capture_output=True, text=True, timeout=15,
                creationflags=_subprocess.CREATE_NO_WINDOW,
            )
            norm = user_data_dir.replace("/", "\\").rstrip("\\").lower()
            for line in result.stdout.splitlines():
                line = line.strip()
                if "|" not in line:
                    continue
                pid_str, cmd = line.split("|", 1)
                if norm in cmd.lower():
                    try:
                        pid = int(pid_str.strip())
                        os.kill(pid, 9)
                        logger.debug("[RODONAVES] Matou Chrome órfão PID=%d", pid)
                    except (ValueError, OSError):
                        pass
        except Exception as e:
            logger.debug("[RODONAVES] _kill_stale_chrome falhou: %s", e)

    async def _init_browser(self):
        if self._context:
            # Verifica se o processo Chrome ainda está vivo
            if self._chrome_proc and self._chrome_proc.poll() is not None:
                if self._has_live_browser_session():
                    logger.info(f"[{self.nome}] Launcher saiu, mas sessão CDP segue ativa; reutilizando browser")
                    await self._ocultar_janela()
                    return
                logger.warning(f"[{self.nome}] Chrome process morreu (exit={self._chrome_proc.returncode}), reinicializando...")
                await self.cleanup()
            elif self._browser and not self._browser.is_connected():
                logger.warning(f"[{self.nome}] Browser desconectado, reinicializando...")
                await self.cleanup()
            else:
                await self._ocultar_janela()
                return
        await self._init_browser_inner()

    async def _init_browser_inner(self):
        """Lanca Chrome headful/off-screen e conecta via CDP na mesma sessao persistente."""
        import shutil as _shutil
        chrome_path = find_chrome()

        for launch_attempt in range(2):
            port = _find_free_port()
            udd = self._user_data_dir()
            self._kill_stale_chrome(udd)
            self._fix_preferences(udd)
            self._profile_tmp = None

            launch_args = [
                chrome_path,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={udd}",
                "--no-first-run",
                "--no-default-browser-check",
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--window-position=-32000,-32000",
                "--window-size=1920,1080",
                "--disable-session-crashed-bubble",
                "--disable-features=InfiniteSessionRestore",
                "--hide-crash-restore-bubble",
                "--noerrdialogs",
                "--disable-infobars",
                "--enable-features=NetworkService,NetworkServiceInProcess",
                "--disable-features=IsolateOrigins,site-per-process,TranslateUI",
                "--disable-component-extensions-with-background-pages",
            ]

            self._chrome_proc = subprocess.Popen(
                launch_args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            _register_owned_proc(self._chrome_proc, source="rodonaves")

            chrome_ok = False
            for _ in range(50):
                await asyncio.sleep(0.1)
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                        chrome_ok = True
                        break
                except (ConnectionRefusedError, OSError):
                    exit_code = self._chrome_proc.poll()
                    if exit_code is None:
                        continue
                    if self._launcher_exit_can_still_spawn_browser(exit_code):
                        continue
                    break

            if chrome_ok:
                self._effective_headless = False
                self._active_user_data_dir = udd
                break

            exit_code = self._chrome_proc.returncode if self._chrome_proc.poll() is not None else None
            _kill_proc(self._chrome_proc)
            self._chrome_proc = None

            if launch_attempt == 0:
                logger.warning(
                    f"[{self.nome}] Chrome crashou (exit {exit_code}), "
                    f"limpando perfil e tentando novamente..."
                )
                try:
                    _shutil.rmtree(udd, ignore_errors=True)
                except Exception:
                    pass
                await asyncio.sleep(0.5)
                continue

            raise RuntimeError(
                f"Chrome (Rodonaves) encerrou inesperadamente (exit {exit_code})"
            )

        # Conecta Playwright via CDP com retry (driver Node.js pode crashar)
        last_err = None
        for _attempt in range(3):
            try:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                logger.info(f"[{self.nome}] Chrome conectado via CDP porta {port} (headless={self._effective_headless})")
                break
            except Exception as e:
                last_err = e
                logger.warning(f"[{self.nome}] CDP tentativa {_attempt+1}/3 falhou: {e}")
                try:
                    if self._playwright:
                        await self._playwright.stop()
                        self._playwright = None
                except Exception:
                    pass
                if _attempt < 2:
                    await asyncio.sleep(1 + _attempt)
        else:
            _kill_proc(self._chrome_proc)
            raise RuntimeError(f"Falha ao conectar CDP apos 3 tentativas: {last_err}")

        self._context = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=_CHROME_UA,
            locale="pt-BR",
        )
        await self._sync_active_page()
        if self._page is None:
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        try:
            self._cdp_session = await self._context.new_cdp_session(self._page)
            resp = await self._cdp_session.send("Browser.getWindowForTarget")
            self._window_id = resp.get("windowId")
            if self._window_id:
                await self._cdp_session.send("Browser.setWindowBounds", {
                    "windowId": self._window_id,
                    "bounds": {"left": -32000, "top": -32000, "width": 1920, "height": 1080},
                })
        except Exception as e:
            logger.debug(f"[{self.nome}] Falha ao mover janela off-screen via CDP: {e}")
        await ocultar_taskbar_por_pagina(self._page)
        # Stealth script: injetar em todas as paginas (funciona tambem para persistent context)
        await self._context.add_init_script(_STEALTH_JS)
        # Injetar no primeiro page ja existente (add_init_script so afeta navegacoes futuras)
        try:
            await self._page.evaluate(_STEALTH_JS)
        except Exception:
            pass

    async def _ocultar_janela(self):
        """Move a janela para coordenadas fora da tela (invisível)."""
        try:
            await self._sync_active_page()
            if not self._cdp_session:
                self._cdp_session = await self._context.new_cdp_session(self._page)
            if not self._window_id:
                resp = await self._cdp_session.send("Browser.getWindowForTarget")
                self._window_id = resp.get("windowId")
            if self._window_id:
                await self._cdp_session.send("Browser.setWindowBounds", {
                    "windowId": self._window_id,
                    "bounds": {"left": -32000, "top": -32000, "width": 1920, "height": 1080},
                })
                logger.debug(f"[{self.nome}] Janela movida off-screen")
            await ocultar_taskbar_por_pagina(self._page)
        except Exception as e:
            logger.warning(f"[{self.nome}] Falha ao ocultar janela: {e}")

    async def _mostrar_janela(self):
        """Mostra janela compacta centralizada na tela, só para o CAPTCHA."""
        await self._sync_active_page()
        shown = False
        try:
            if not self._cdp_session:
                self._cdp_session = await self._context.new_cdp_session(self._page)
            if not self._window_id:
                resp = await self._cdp_session.send("Browser.getWindowForTarget")
                self._window_id = resp.get("windowId")
            if self._window_id:
                # Obtém resolução real da tela via JS
                try:
                    screen_info = await self._page.evaluate(
                        "() => ({ w: screen.width, h: screen.height })"
                    )
                    screen_w = int(screen_info.get("w", 1920))
                    screen_h = int(screen_info.get("h", 1080))
                except Exception:
                    screen_w, screen_h = 1920, 1080

                left, top, w, h = self._captcha_window_bounds(screen_w, screen_h)
                await self._cdp_session.send("Browser.setWindowBounds", {
                    "windowId": self._window_id,
                    "bounds": {"windowState": "normal"},
                })
                await self._cdp_session.send("Browser.setWindowBounds", {
                    "windowId": self._window_id,
                    "bounds": {"left": left, "top": top, "width": w, "height": h},
                })
                await self._page.bring_to_front()
                await ocultar_taskbar_por_pagina(self._page)
                moved = await posicionar_janela_por_pagina(
                    self._page,
                    left=left,
                    top=top,
                    width=w,
                    height=h,
                    bring_to_front=True,
                )
                await trazer_janela_frente(self._page)
                logger.debug(
                    f"[{self.nome}] Janela compacta (CAPTCHA) visível em ({left},{top}) tela {screen_w}x{screen_h}"
                )
                shown = True

            # Scroll para o captcha/botão Calcular ficar visível
            try:
                await self._page.locator("#calculateQuotationBtn").scroll_into_view_if_needed()
            except Exception:
                pass
            try:
                captcha_iframe = self._page.locator("iframe[title*='reCAPTCHA'], iframe[src*='recaptcha']")
                if await captcha_iframe.count() > 0:
                    await captcha_iframe.first.scroll_into_view_if_needed()
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"[{self.nome}] Falha ao mostrar janela via CDP: {e}")

        if shown:
            return True

        try:
            screen_info = await self._page.evaluate("() => ({ w: screen.width, h: screen.height })")
            screen_w = int(screen_info.get("w", 1920))
            screen_h = int(screen_info.get("h", 1080))
        except Exception:
            screen_w, screen_h = 1920, 1080

        left, top, w, h = self._captcha_window_bounds(screen_w, screen_h)
        shown = await self._reposicionar_janela_win32(
            left=left,
            top=top,
            width=w,
            height=h,
            bring_to_front=True,
        )
        if shown:
            await ocultar_taskbar_por_pagina(self._page)
            logger.debug(f"[{self.nome}] Janela compacta (CAPTCHA) visível via Win32 em ({left},{top}) tela {screen_w}x{screen_h}")
            return True
        return False

    async def cleanup(self):
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
        _kill_proc(self._chrome_proc)
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        # Marca sessão como limpa para evitar "Restaurar páginas?" na próxima abertura
        try:
            profile_dir = self._active_user_data_dir or self._user_data_dir()
            self._fix_preferences(profile_dir)
        except Exception:
            pass
        if getattr(self, "_profile_tmp", None):
            import shutil as _shutil
            _shutil.rmtree(self._profile_tmp, ignore_errors=True)
            self._profile_tmp = None
        self._context = None
        self._browser = None
        self._page = None
        self._playwright = None
        self._logged_in = False
        self._cdp_session = None
        self._window_id = None
        self._chrome_proc = None
        self._active_user_data_dir = ""

    @staticmethod
    def _safe_diagnostic_excerpt(value: Any, *, limit: int = 900) -> str:
        text = str(value or "")
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
        text = re.sub(r"\b\d{14}\b", "***", text)
        text = re.sub(r"\b\d{11}\b", "***", text)
        text = re.sub(r"\b\d{5}-?\d{3}\b", "***", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > limit:
            return text[:limit].rstrip() + "..."
        return text

    def _set_last_error_with_diagnostic(self, message: str, *, stage: str | None = None) -> None:
        stage_name = stage or self._passo_atual
        snapshot = self._diagnostic_context.get("rodonaves_snapshot")
        suffix = ""
        if isinstance(snapshot, dict):
            flags = []
            for key in (
                "url",
                "title",
                "recaptcha_frames",
                "captcha_token_len",
                "form_present",
                "result_present",
                "alert_excerpt",
                "body_excerpt",
            ):
                value = snapshot.get(key)
                if value not in (None, "", []):
                    flags.append(f"{key}={value}")
            if flags:
                suffix = " | diagnostico: " + "; ".join(flags[:8])
        self.last_error = f"{message} (stage={stage_name}; headless={self._effective_headless}){suffix}"

    async def _capture_safe_diagnostic_snapshot(
        self,
        *,
        reason: str,
        stage: str | None = None,
        api_result: dict | None = None,
    ) -> dict[str, Any]:
        page = self._page
        snapshot: dict[str, Any] = {
            "reason": str(reason or "")[:120],
            "stage": stage or self._passo_atual,
            "headless": self.headless,
            "effective_headless": self._effective_headless,
            "logged_in": self._logged_in,
        }
        if page is None:
            snapshot["page_state"] = "ausente"
            self._diagnostic_context["rodonaves_snapshot"] = snapshot
            return snapshot
        try:
            snapshot["page_closed"] = bool(page.is_closed())
        except Exception:
            snapshot["page_closed"] = None
        try:
            snapshot["url"] = str(getattr(page, "url", "") or "")[:240]
        except Exception:
            snapshot["url"] = ""
        if snapshot.get("page_closed"):
            self._diagnostic_context["rodonaves_snapshot"] = snapshot
            return snapshot
        try:
            snapshot["title"] = self._safe_diagnostic_excerpt(await page.title(), limit=120)
        except Exception:
            pass
        try:
            dom_state = await page.evaluate(r"""() => {
                const text = (document.body && document.body.innerText || '').replace(/\s+/g, ' ').trim();
                const alertText = Array.from(document.querySelectorAll('.alert, .validation-summary-errors, [role="alert"], .field-validation-error'))
                    .map(el => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim())
                    .filter(Boolean)
                    .join(' | ');
                const visible = (selector) => {
                    const el = document.querySelector(selector);
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const captchaToken = document.querySelector('textarea[name="g-recaptcha-response"]');
                return {
                    bodyText: text.slice(0, 2500),
                    alertText: alertText.slice(0, 1000),
                    formPresent: !!document.querySelector('#ReceiverTaxId'),
                    calculateButtonPresent: !!document.querySelector('#calculateQuotationBtn'),
                    calculateButtonVisible: visible('#calculateQuotationBtn'),
                    resultPresent: !!document.querySelector('td.col-result, #quotationResult'),
                    recaptchaFrames: document.querySelectorAll('iframe[title*="reCAPTCHA"], iframe[src*="recaptcha"]').length,
                    captchaTokenLen: captchaToken && captchaToken.value ? captchaToken.value.length : 0,
                };
            }""")
            if isinstance(dom_state, dict):
                snapshot.update({
                    "form_present": bool(dom_state.get("formPresent")),
                    "calculate_button_present": bool(dom_state.get("calculateButtonPresent")),
                    "calculate_button_visible": bool(dom_state.get("calculateButtonVisible")),
                    "result_present": bool(dom_state.get("resultPresent")),
                    "recaptcha_frames": int(dom_state.get("recaptchaFrames") or 0),
                    "captcha_token_len": int(dom_state.get("captchaTokenLen") or 0),
                    "alert_excerpt": self._safe_diagnostic_excerpt(dom_state.get("alertText"), limit=500),
                    "body_excerpt": self._safe_diagnostic_excerpt(dom_state.get("bodyText"), limit=900),
                })
        except Exception as exc:
            snapshot["snapshot_error"] = self._safe_diagnostic_excerpt(exc, limit=180)
        if api_result:
            snapshot["api_url"] = str(api_result.get("url") or "")[:240]
            if "json" in api_result:
                snapshot["api_kind"] = "json"
                snapshot["api_excerpt"] = self._safe_diagnostic_excerpt(json.dumps(api_result.get("json"), ensure_ascii=False), limit=700)
            elif "text" in api_result:
                snapshot["api_kind"] = "text"
                snapshot["api_excerpt"] = self._safe_diagnostic_excerpt(api_result.get("text"), limit=700)
        self._diagnostic_context["rodonaves_snapshot"] = snapshot
        logger.info(f"[{self.nome}] Diagnóstico seguro Rodonaves ({reason}): {snapshot}")
        return snapshot

    async def _reset_page_for_retry(self, error: Exception) -> None:
        """Recria a página (mas não o browser) para erros transitórios de frame/page."""
        err_str = str(error).lower()
        is_page_error = any(t in err_str for t in (
            "frame was detached",
            "target page, context or browser has been closed",
            "target closed",
            "net::err_aborted",
            "net::err_connection",
            "net::err_timed_out",
            "timeout",
        ))
        if not is_page_error:
            return
        logger.info(f"[{self.nome}] Erro transitório de página, recriando page para retry...")
        try:
            if self._page and not self._page.is_closed():
                await self._page.close()
        except Exception:
            pass
        self._page = None
        self._mark_login_failed()
        if self._context:
            try:
                self._page = await self._context.new_page()
            except Exception:
                self._page = None

    # ── login ──────────────────────────────────────────────────────────

    async def _navegar_cotacao(self, _from_login: bool = False):
        """Navegação pós-login para a cotação; nunca inicia sessão nova em /Quotation."""
        if not _from_login and not self._logged_in:
            await self._login()
            return
        await self._go_to_quotation_after_login()

    async def _login(self):
        if self._logged_in:
            return
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                page = await self._open_portal_entrypoint()
                if not await self._wait_for_quotation_form(
                    page,
                    timeout=1000,
                    success_message="Formulário visível sem precisar reenviar login",
                ):
                    await self._perform_ajax_login(page)
                    logger.info(f"[{self.nome}] Login AJAX OK, confirmando sessão e indo para cotação...")
                else:
                    self._set_login_status("login_ok", True)

                await self._go_to_quotation_after_login()
                self._set_login_status("login_ok", True)
                logger.info(f"[{self.nome}] Login OK – formulário visível")
                return
            except Exception as exc:
                last_error = exc
                self._mark_login_failed()
                if attempt == 0:
                    logger.warning(
                        f"[{self.nome}] Fluxo pós-login/cotação falhou ({exc}). "
                        "Reabrindo a entrada inicial do portal e tentando mais uma vez..."
                    )
                    continue
                raise
        if last_error is not None:
            raise last_error

    async def pre_login(self):
        self._passo_atual = "pre_login"
        await self._init_browser()
        try:
            await self._login()
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"[{self.nome}] Pre-login falhou: {e}")
            return False

    # ── preenchimento do formulário (seletores por ID do HTML real) ───

    async def _preencher_cotacao(
        self,
        valor: float,
        cubagens: list[dict],
        cnpj_destinatario: str,
        cep_destino: str,
        cep_origem: str = "",
    ) -> None:
        page = self._page

        # Remove overlays que interceptam cliques (cookie banner, navbar fixa, modais,
        # e elementos com position:fixed/absolute via CSS — não só inline style)
        try:
            await page.evaluate("""() => {
                const cookieBtn = document.getElementById('adopt-controller-button');
                if (cookieBtn) cookieBtn.remove();
                const cookieBanner = document.getElementById('cookie-banner');
                if (cookieBanner) cookieBanner.remove();
                const nav = document.getElementById('mainNav');
                if (nav) nav.style.position = 'relative';
                // Remove qualquer modal-backdrop residual
                document.querySelectorAll('.modal-backdrop, .overlay, #overlay, [id*="overlay"], [class*="overlay"], [class*="modal"]').forEach(el => {
                    if (el.tagName.toLowerCase() !== 'html' && el.tagName.toLowerCase() !== 'body') {
                        el.remove();
                    }
                });
                document.body.classList.remove('modal-open');
                document.body.style.overflow = '';
                document.body.style.paddingRight = '';
                // Remove elementos fixos/absolutos que cobrem a página (via computedStyle, não só inline)
                for (const el of document.querySelectorAll('*')) {
                    const tag = el.tagName.toLowerCase();
                    if (tag === 'html' || tag === 'body' || tag === 'script' || tag === 'style') continue;
                    if (el.id && el.id.includes('recaptcha')) continue;
                    const cs = window.getComputedStyle(el);
                    const pos = cs.position;
                    if (pos !== 'fixed' && pos !== 'sticky') continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 200 && rect.height > 50) {
                        el.style.display = 'none';
                    }
                }
            }""")
        except Exception:
            pass

        # ─── Contato ───
        await page.locator("#contactName").fill("DARLU IND")
        await page.locator("#contactPhoneNumber").fill("(54) 99999-9999")

        # ─── CEP origem ───
        if cep_origem:
            origin_zip = page.locator("#originZipCode")
            if await origin_zip.count() > 0:
                await origin_zip.fill(self._format_cep(cep_origem))
                await origin_zip.press("Tab")
                for _ in range(20):
                    await page.wait_for_timeout(500)
                    try:
                        city_val = await page.locator("#originCity").input_value()
                        if city_val.strip():
                            break
                    except Exception:
                        break
                await page.wait_for_timeout(500)

        # ─── Destinatário ───
        try:
            await page.locator("#ReceiverTaxId").fill(self._format_cnpj(cnpj_destinatario))
        except Exception:
            logger.warning(f"[{self.nome}] fill #ReceiverTaxId falhou, usando JS")
            await page.evaluate(f"""() => {{
                const el = document.getElementById('ReceiverTaxId');
                if (!el) return;
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                if (setter) setter.call(el, '{self._format_cnpj(cnpj_destinatario)}');
                else el.value = '{self._format_cnpj(cnpj_destinatario)}';
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                el.dispatchEvent(new Event('blur', {{bubbles: true}}));
            }}""")

        # ─── CEP destino ───
        try:
            await page.locator("#destinationZipCode").fill(self._format_cep(cep_destino))
            await page.locator("#destinationZipCode").press("Tab")
        except Exception:
            logger.warning(f"[{self.nome}] fill #destinationZipCode falhou, usando JS")
            await page.evaluate(f"""() => {{
                const el = document.getElementById('destinationZipCode');
                if (!el) return;
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                if (setter) setter.call(el, '{self._format_cep(cep_destino)}');
                else el.value = '{self._format_cep(cep_destino)}';
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                el.dispatchEvent(new Event('blur', {{bubbles: true}}));
            }}""")
        # Aguarda auto-preenchimento de cidade/estado/bairro via API do CEP
        for _ in range(20):
            await page.wait_for_timeout(500)
            try:
                city_val = await page.locator("#destinationCity").input_value()
                if city_val.strip():
                    break
            except Exception:
                break
        await page.wait_for_timeout(500)

        # Preencher Bairro se ficou vazio (campo obrigatório: DestinationAddress.District)
        district_loc = page.locator("#destinationDistrict")
        if await district_loc.count() > 0:
            district_val = (await district_loc.input_value()).strip()
            if not district_val:
                await district_loc.fill("Centro")
                logger.info(f"[{self.nome}] Bairro destino vazio, preenchido 'Centro'")

        # ─── Número destino ───
        try:
            await page.locator("#destinationNumber").fill("1")
        except Exception:
            logger.warning(f"[{self.nome}] fill #destinationNumber falhou, usando JS")
            await page.evaluate("""() => {
                const el = document.getElementById('destinationNumber');
                if (!el) return;
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                if (setter) setter.call(el, '1');
                else el.value = '1';
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('blur', {bubbles: true}));
            }""")

        # ─── Valor NF ───
        # Campo #eletronicInvoiceValue tem máscara jQuery currency —
        # fill() seta value diretamente sem acionar a máscara, causando
        # interpretação incorreta. Usar type() digita char-a-char,
        # permitindo que a máscara processe corretamente (centavos à direita).
        nf_loc = page.locator("#eletronicInvoiceValue")
        nf_centavos = str(int(round(float(valor) * 100)))  # ex: 1500.50 → "150050"
        try:
            await nf_loc.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        try:
            await nf_loc.click(timeout=5000)
            await nf_loc.press("Control+a")
            await nf_loc.type(nf_centavos, delay=30)
        except Exception:
            # Fallback: preencher via JS quando overlay bloqueia clique
            logger.warning(f"[{self.nome}] Click em #eletronicInvoiceValue bloqueado, usando JS")
            await page.evaluate(f"""() => {{
                const el = document.getElementById('eletronicInvoiceValue');
                if (!el) return;
                el.focus();
                el.value = '';
                el.dispatchEvent(new Event("focus", {{bubbles: true}}));
                for (const ch of '{nf_centavos}') {{
                    el.value += ch;
                    el.dispatchEvent(new Event("input", {{bubbles: true}}));
                }}
                el.dispatchEvent(new Event("change", {{bubbles: true}}));
                el.dispatchEvent(new Event("blur", {{bubbles: true}}));
            }}""")

        # ─── Tipo embalagem ───
        try:
            await page.locator(
                "#packageType"
            ).select_option("1", timeout=1500 if self._effective_headless else 10000)
        except Exception:
            logger.warning(f"[{self.nome}] select_option #packageType falhou, usando JS")
            await page.evaluate("""() => {
                const el = document.getElementById('packageType');
                if (!el) return;
                el.value = '1';
                el.dispatchEvent(new Event('change', {bubbles: true}));
                // Fallback para jQuery/Angular se presente
                if (window.jQuery) { window.jQuery(el).val('1').trigger('change'); }
            }""")

        # ─── Primeira linha de volume (IDs fixos: amountPacks1, height1, ...) ───
        # Usa _fill_field com fallback JS: overlays (cookie banner, navbar fixa)
        # podem interceptar cliques do Playwright, causando timeout no fill().
        primeiro = cubagens[0]
        await self._fill_field(page, "amountPacks1", str(primeiro["quantidade"]))
        await self._fill_field(page, "height1", str(primeiro["altura_cm"]))
        await self._fill_field(page, "width1", str(primeiro["largura_cm"]))
        await self._fill_field(page, "length1", str(primeiro["comprimento_cm"]))
        await self._fill_field(page, "weight1", self._peso_str(primeiro))

        # ─── Linhas adicionais de volume ───
        for idx, linha in enumerate(cubagens[1:], start=2):
            add_pack = page.locator("#addPack")
            try:
                await add_pack.wait_for(state="visible", timeout=5000)
                await add_pack.scroll_into_view_if_needed(timeout=3000)
                await add_pack.click(timeout=5000)
            except Exception:
                logger.warning(f"[{self.nome}] Click em #addPack bloqueado, usando JS")
                await page.evaluate("""() => {
                    const el = document.getElementById('addPack');
                    if (!el) return;
                    el.click();
                    if (window.jQuery) { window.jQuery(el).trigger('click'); }
                }""")
            await page.wait_for_timeout(800)

            # Novos campos seguem padrão: amountPacks{n}, height{n}, ...
            await self._fill_field(page, f"amountPacks{idx}", str(linha["quantidade"]))
            await self._fill_field(page, f"height{idx}", str(linha["altura_cm"]))
            await self._fill_field(page, f"width{idx}", str(linha["largura_cm"]))
            await self._fill_field(page, f"length{idx}", str(linha["comprimento_cm"]))
            await self._fill_field(page, f"weight{idx}", self._peso_str(linha))

        # Reforça número destino (autocomplete pode sobrescrever)
        try:
            await page.locator("#destinationNumber").fill("1")
        except Exception:
            await page.evaluate("""() => {
                const el = document.getElementById('destinationNumber');
                if (el) { el.value = '1'; el.dispatchEvent(new Event('change', {bubbles: true})); }
            }""")
        await page.wait_for_timeout(300)

        # Simula pequenas interações naturais ao terminar de preencher o formulário.
        # Acumula score de comportamento humano que o reCAPTCHA analisa.
        try:
            await page.mouse.move(
                random.randint(300, 600), random.randint(200, 400),
                steps=random.randint(5, 12),
            )
            await page.wait_for_timeout(random.randint(300, 800))
            await page.mouse.wheel(0, random.randint(40, 100))
            await page.wait_for_timeout(random.randint(200, 600))
        except Exception:
            pass

    async def _click_calcular_via_js(self, page) -> bool:
        return bool(await page.evaluate("""() => {
            const el = document.getElementById('calculateQuotationBtn');
            if (!el) return false;
            el.click();
            return true;
        }"""))

    # ── submissão e extração de resultado ──────────────────────────────

    async def _submeter_e_extrair(self) -> Optional[Cotacao]:
        stage_started = self._start_stage("submetendo_e_extraindo")
        page = self._page
        self.last_error = None
        await self._capture_safe_diagnostic_snapshot(reason="inicio_submissao", stage="submetendo_cotacao")
        api_result: dict[str, Any] = {}
        submit_started = False

        def _is_quotation_api_url(url: str | None) -> bool:
            lowered = str(url or "").lower()
            return "quotation" in lowered or "calculate" in lowered or "cotacao" in lowered

        async def _capture_quotation_request(request):
            nonlocal submit_started
            try:
                if _is_quotation_api_url(getattr(request, "url", "")):
                    submit_started = True
            except Exception:
                pass

        async def _capture_quotation_response(response):
            nonlocal submit_started
            try:
                url = response.url.lower()
                if response.status != 200:
                    return
                if _is_quotation_api_url(url):
                    submit_started = True
                    ct = response.headers.get("content-type", "")
                    if "json" in ct:
                        try:
                            data = await response.json()
                            api_result["json"] = data
                            api_result["url"] = response.url
                        except Exception:
                            pass
                    elif "text" in ct or "html" in ct:
                        try:
                            body = await response.text()
                            if any(kw in body.lower() for kw in ("frete", "valor", "prazo", "freight", "price")):
                                api_result["text"] = body
                                api_result["url"] = response.url
                        except Exception:
                            pass
            except Exception:
                pass

        request_handler = lambda r: asyncio.ensure_future(_capture_quotation_request(r))
        response_handler = lambda r: asyncio.ensure_future(_capture_quotation_response(r))
        page.on("request", request_handler)
        page.on("response", response_handler)

        try:
            try:
                cb = page.locator("#cbSendEmail")
                await cb.scroll_into_view_if_needed(timeout=3000)
                await cb.click(timeout=3000)
            except Exception:
                try:
                    await page.evaluate("document.getElementById('cbSendEmail')?.click()")
                except Exception:
                    logger.warning(f"[{self.nome}] Não conseguiu clicar no checkbox de e-mail")

            janela_visivel = await self._mostrar_janela()
            await page.wait_for_timeout(300)
            try:
                await page.locator("#calculateQuotationBtn").scroll_into_view_if_needed()
                await page.wait_for_timeout(300)
            except Exception:
                pass
            if janela_visivel:
                logger.info(f"[{self.nome}] Janela compacta visível para CAPTCHA")
            else:
                logger.warning(f"[{self.nome}] Não foi possível tornar a janela visível para o CAPTCHA")

            try:
                await self._simular_interacao_humana(page)
            except Exception:
                pass

            try:
                captcha_frame = page.frame_locator("iframe[title*='reCAPTCHA'], iframe[src*='recaptcha']")
                chk_captcha = captcha_frame.get_by_role("checkbox", name="Não sou um robô")
                if await chk_captcha.count() > 0:
                    try:
                        box = await chk_captcha.bounding_box()
                        if box:
                            start_x = random.randint(200, 500)
                            start_y = random.randint(200, 400)
                            await page.mouse.move(start_x, start_y, steps=random.randint(8, 15))
                            await page.wait_for_timeout(random.randint(200, 500))
                            target_x = box["x"] + box["width"] / 2 + random.randint(-3, 3)
                            target_y = box["y"] + box["height"] / 2 + random.randint(-3, 3)
                            await page.mouse.move(target_x, target_y, steps=random.randint(15, 30))
                            await page.wait_for_timeout(random.randint(300, 800))
                    except Exception:
                        pass
                    await chk_captcha.click(timeout=5000)
                    await page.wait_for_timeout(random.randint(1000, 2500))
            except Exception:
                pass

            async def _captcha_token() -> str:
                try:
                    return str(await page.evaluate("""() => {
                        const el = document.querySelector('textarea[name="g-recaptcha-response"]');
                        if (el && el.value) return String(el.value);
                        if (typeof grecaptcha !== 'undefined') {
                            try { const r = grecaptcha.getResponse(); if (r) return String(r); } catch(e) {}
                        }
                        return '';
                    }""") or "")
                except Exception:
                    return ""

            async def _resultado_ou_submit_ja_apareceu() -> bool:
                if submit_started:
                    return True
                if api_result:
                    return True
                try:
                    detected = await page.evaluate("""() => {
                        if (document.querySelectorAll('td.col-result').length > 0) return true;
                        const qr = document.getElementById('quotationResult');
                        return !!(qr && qr.innerHTML.trim().length > 50);
                    }""")
                    return bool(detected)
                except Exception:
                    return False

            self._passo_atual = "aguardando_captcha"
            self._set_login_status("aguardando_captcha", True)
            token = await _captcha_token()
            recaptcha_frames = 0
            try:
                recaptcha_frames = await page.locator("iframe[title*='reCAPTCHA'], iframe[src*='recaptcha']").count()
            except Exception:
                recaptcha_frames = 0

            manual_submit_detected = False
            if recaptcha_frames > 0 and not token.strip():
                logger.warning(
                    f"[{self.nome}] reCAPTCHA: resolva manualmente e clique em Calcular na mesma janela; aguardando até {self.CAPTCHA_MAX_WAIT_S}s..."
                )
                for _ in range(self.CAPTCHA_MAX_WAIT_S):
                    await page.wait_for_timeout(1000)
                    token = await _captcha_token()
                    if token.strip():
                        break
                    if await _resultado_ou_submit_ja_apareceu():
                        manual_submit_detected = True
                        break
                if not token.strip() and not manual_submit_detected:
                    logger.warning(f"[{self.nome}] reCAPTCHA: timeout {self.CAPTCHA_MAX_WAIT_S}s — tentando submeter na mesma sessão")

            if token.strip():
                self._set_login_status("captcha_resolvido", True)
                logger.info(f"[{self.nome}] CAPTCHA resolvido")
                if await _resultado_ou_submit_ja_apareceu():
                    manual_submit_detected = True
            elif manual_submit_detected:
                logger.info(f"[{self.nome}] CAPTCHA/submissão concluídos manualmente na mesma sessão")
            else:
                logger.info(f"[{self.nome}] CAPTCHA nao confirmado, tentando submeter mesmo assim")

            try:
                form_state = await page.evaluate("""() => {
                    const fields = {};
                    const ids = ['contactName', 'ReceiverTaxId', 'destinationZipCode', 'destinationNumber'];
                    for (const id of ids) {
                        const el = document.getElementById(id);
                        fields[id] = el ? el.value : '(not found)';
                    }
                    const cap = document.querySelector('textarea[name="g-recaptcha-response"]');
                    fields['captcha_token_len'] = cap && cap.value ? cap.value.length : 0;
                    return fields;
                }""")
                logger.info(f"[{self.nome}] Estado do formulário antes de Calcular: {form_state}")
            except Exception as e:
                logger.warning(f"[{self.nome}] Não foi possível logar estado do formulário: {e}")

            calc_btn = page.locator("#calculateQuotationBtn")
            if not manual_submit_detected:
                click_succeeded = False
                try:
                    await calc_btn.wait_for(state="visible", timeout=15000)
                except Exception:
                    logger.warning(f"[{self.nome}] Botao Calcular nao visivel, aguardando...")
                    await page.wait_for_timeout(3000)
                for _click_attempt in range(3):
                    try:
                        await calc_btn.click(timeout=15000)
                        click_succeeded = True
                        break
                    except Exception as click_err:
                        if _click_attempt == 2:
                            break
                        logger.warning(f"[{self.nome}] Click no Calcular falhou (tentativa {_click_attempt+1}): {click_err}")
                        await page.wait_for_timeout(2000)
                if not click_succeeded:
                    try:
                        await calc_btn.click(timeout=15000, force=True)
                        click_succeeded = True
                    except Exception as force_click_err:
                        logger.warning(f"[{self.nome}] Click forçado no Calcular falhou: {force_click_err}")
                if not click_succeeded:
                    logger.warning(f"[{self.nome}] Click no Calcular bloqueado, usando JS")
                    try:
                        click_succeeded = await self._click_calcular_via_js(page)
                    except Exception as js_click_err:
                        raise RuntimeError(f"Falha ao acionar Calcular via JS: {js_click_err}") from js_click_err
                    if not click_succeeded:
                        raise RuntimeError("Falha ao acionar Calcular via JS: botão Calcular não encontrado")
            await self._ocultar_janela()
            self._passo_atual = "aguardando_resultado_api"
            if manual_submit_detected:
                logger.info(f"[{self.nome}] Resultado em andamento após interação manual, aguardando retorno...")
            else:
                logger.info(f"[{self.nome}] Botao Calcular clicado, aguardando resultado...")

            for _poll in range(120):
                if api_result:
                    logger.info(f"[{self.nome}] Resultado capturado via API ({api_result.get('url', '?')})")
                    break
                try:
                    has_result = await page.evaluate("""() => {
                        if (document.querySelectorAll('td.col-result').length > 0) return 'col-result';
                        const qr = document.getElementById('quotationResult');
                        if (qr && qr.innerHTML.trim().length > 50) return 'quotationResult';
                        return '';
                    }""")
                    if has_result:
                        logger.info(f"[{self.nome}] Resultado detectado no DOM ({has_result})")
                        break
                except Exception:
                    pass
                await page.wait_for_timeout(250)
            else:
                logger.warning(f"[{self.nome}] Timeout aguardando resultado (30s)")
                await self._capture_safe_diagnostic_snapshot(
                    reason="timeout_aguardando_resultado",
                    stage="aguardando_resultado_api",
                    api_result=api_result,
                )

            if api_result:
                await page.wait_for_timeout(500)

            valor_frete = None
            prazo_dias = 0

            if "json" in api_result:
                try:
                    valor_frete, prazo_dias = self._extrair_de_json(api_result["json"])
                    if valor_frete is not None:
                        logger.info(f"[{self.nome}] Extracao via JSON API: R${valor_frete:.2f}, {prazo_dias} dias")
                except Exception as e:
                    logger.debug(f"[{self.nome}] Extracao JSON falhou: {e}")

            if valor_frete is None:
                try:
                    result_data = await page.evaluate("""() => {
                        const texts = [];
                        const cells = document.querySelectorAll('td.col-result');
                        for (const cell of cells) {
                            texts.push(cell.innerText.trim());
                        }
                        if (texts.length === 0) {
                            const qr = document.getElementById('quotationResult');
                            if (qr && qr.innerText.trim()) {
                                texts.push(qr.innerText.trim());
                            }
                        }
                        return texts;
                    }""")
                    for txt in (result_data or []):
                        if valor_frete is None:
                            m_val = re.search(r"R\$\s*([\d.]+,\d{2})", txt)
                            if m_val:
                                valor_frete = float(m_val.group(1).replace(".", "").replace(",", "."))
                                continue
                        if prazo_dias == 0:
                            m_prazo = re.search(r"(\d+)\s*dias?", txt, re.IGNORECASE)
                            if m_prazo:
                                prazo_dias = int(m_prazo.group(1))
                    if valor_frete is not None:
                        logger.info(f"[{self.nome}] Extracao via td.col-result (batch): R${valor_frete:.2f}")
                except Exception as e:
                    logger.debug(f"[{self.nome}] Extracao batch td.col-result falhou: {e}")

            if valor_frete is None and "text" in api_result:
                try:
                    api_text = api_result["text"]
                    for m in re.finditer(r"R\$\s*([\d.]+,\d{2})", api_text):
                        trecho = api_text[max(0, m.start() - 120): m.end() + 120].lower()
                        if any(kw in trecho for kw in ("frete", "cotacao", "total geral", "valor total", "prazo")):
                            valor_frete = float(m.group(1).replace(".", "").replace(",", "."))
                            break
                    if valor_frete is not None:
                        logger.info(f"[{self.nome}] Extracao via texto API: R${valor_frete:.2f}")
                except Exception as e:
                    logger.debug(f"[{self.nome}] Extracao texto API falhou: {e}")

            if valor_frete is None:
                try:
                    body_txt = await page.inner_text("body")
                    body_norm = (body_txt or "").replace("\xa0", " ")

                    erro_validacao = re.search(r'Alerta\s*\{.*?"errors".*?\}', body_norm, re.DOTALL)
                    if erro_validacao:
                        await self._capture_safe_diagnostic_snapshot(
                            reason="erro_validacao_portal",
                            stage="ler_resultado",
                            api_result=api_result,
                        )
                        self._set_last_error_with_diagnostic(
                            f"Rodonaves: erro de validacao - {self._safe_diagnostic_excerpt(erro_validacao.group(0), limit=300)}",
                            stage="ler_resultado",
                        )
                        logger.error(f"[{self.nome}] {self.last_error}")
                        return None

                    for m in re.finditer(r"R\$\s*([\d.]+,\d{2})", body_norm):
                        trecho = body_norm[max(0, m.start() - 120): m.end() + 120].lower()
                        if any(kw in trecho for kw in ("frete", "cotacao", "total geral", "valor total", "prazo")):
                            valor_frete = float(m.group(1).replace(".", "").replace(",", "."))
                            break

                    if prazo_dias == 0 and body_norm:
                        m_prazo = re.search(r"(\d+)\s*dias?", body_norm, re.IGNORECASE)
                        if m_prazo:
                            prazo_dias = int(m_prazo.group(1))

                    if valor_frete is not None:
                        logger.info(f"[{self.nome}] Extracao via body fallback: R${valor_frete:.2f}")
                except Exception as e:
                    logger.debug(f"[{self.nome}] Extracao body fallback falhou: {e}")

            # Estratégia 5: aguardar mais tempo se o DOM ainda pode estar carregando
            if valor_frete is None and not page.is_closed() and not api_result:
                logger.info(f"[{self.nome}] Portal externo lento; nenhum resultado após %.2fs. Aguardando mais 10s...", time.monotonic() - stage_started)
                for _extra_poll in range(20):
                    await page.wait_for_timeout(500)
                    try:
                        has_result = await page.evaluate("""() => {
                            const cells = document.querySelectorAll('td.col-result');
                            if (cells.length > 0) return true;
                            const qr = document.getElementById('quotationResult');
                            if (qr && qr.innerHTML.trim().length > 50) return true;
                            return false;
                        }""")
                        if has_result:
                            result_data = await page.evaluate("""() => {
                                const texts = [];
                                const cells = document.querySelectorAll('td.col-result');
                                for (const cell of cells) texts.push(cell.innerText.trim());
                                if (texts.length === 0) {
                                    const qr = document.getElementById('quotationResult');
                                    if (qr && qr.innerText.trim()) texts.push(qr.innerText.trim());
                                }
                                return texts;
                            }""")
                            for txt in (result_data or []):
                                if valor_frete is None:
                                    m_val = re.search(r"R\$\s*([\d.]+,\d{2})", txt)
                                    if m_val:
                                        valor_frete = float(m_val.group(1).replace(".", "").replace(",", "."))
                                if prazo_dias == 0:
                                    m_prazo = re.search(r"(\d+)\s*dias?", txt, re.IGNORECASE)
                                    if m_prazo:
                                        prazo_dias = int(m_prazo.group(1))
                            if valor_frete is not None:
                                logger.info(f"[{self.nome}] Extracao via polling extra: R${valor_frete:.2f}")
                                break
                    except Exception:
                        break

            if valor_frete is None:
                await self._capture_safe_diagnostic_snapshot(
                    reason="valor_frete_nao_encontrado",
                    stage="ler_resultado",
                    api_result=api_result,
                )
                snapshot = self._diagnostic_context.get("rodonaves_snapshot", {})
                if isinstance(snapshot, dict) and snapshot.get("recaptcha_frames") and not snapshot.get("captcha_token_len"):
                    msg = "Rodonaves: reCAPTCHA não resolvido ou bloqueio antifraude impediu a cotação"
                elif api_result:
                    msg = "Rodonaves: resposta da API/portal recebida, mas valor de frete não foi encontrado"
                else:
                    msg = "Rodonaves: portal não retornou resultado de cotação dentro do tempo esperado"
                self._set_last_error_with_diagnostic(msg, stage="ler_resultado")
                logger.warning(f"[{self.nome}] {self.last_error}")
                return None

            self._mark_valid_quote()
            self._finish_stage(
                "submetendo_e_extraindo",
                stage_started,
                details=f"valor={float(valor_frete):.2f} prazo={prazo_dias}d",
            )
            return Cotacao(
                transportadora=self.nome,
                prazo_dias=prazo_dias,
                valor_frete=round(float(valor_frete), 2),
                restricoes="Cotacao via portal cliente.rte.com.br",
            )
        finally:
            page.remove_listener("request", request_handler)
            page.remove_listener("response", response_handler)


    # ── orquestração ───────────────────────────────────────────────────

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
        preencher_cep_origem: bool = False,
    ) -> Optional[Cotacao]:
        try:
            self.last_error = None
            cubagens_cm = self._normalizar_cubagens_cm(cubagens)
            if cubagens_cm:
                soma = sum(int(c["quantidade"]) for c in cubagens_cm)
                if int(volumes or 0) > 0 and int(volumes) != soma:
                    self.last_error = f"VOL ({volumes}) diverge da soma das cubagens ({soma})"
                    logger.error(f"[{self.nome}] {self.last_error}")
                    return None
                volumes = soma
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
                    f"Cubagens ausentes/inválidas (volumes={volumes}, "
                    f"dims_cm={comprimento_cm}x{largura_cm}x{altura_cm})"
                )
                logger.error(f"[{self.nome}] {self.last_error}")
                return None

            cnpj_dest = self._digits(cnpj_destinatario)
            cep_dest = self._digits(destino)
            if len(cnpj_dest) != 14:
                raise RuntimeError("CNPJ do destinatário inválido")
            if len(cep_dest) != 8:
                raise RuntimeError("CEP de destino inválido")

            stage_start = self._start_stage("init_browser")
            await self._init_browser()
            self._finish_stage("init_browser", stage_start, details=f"headless={self._effective_headless}")
            stage_start = self._start_stage("login")
            await self._login()
            self._finish_stage("login", stage_start)

            # Na segunda cotação em diante, navega de volta ao formulário
            stage_start = self._start_stage("navegando_cotacao")
            await self._navegar_cotacao()
            self._finish_stage("navegando_cotacao", stage_start)

            stage_start = self._start_stage("preenchendo_formulario")
            cep_orig = self._digits(origem) if preencher_cep_origem else ""
            await self._preencher_cotacao(
                valor=valor,
                cubagens=cubagens_cm,
                cnpj_destinatario=cnpj_dest,
                cep_destino=cep_dest,
                cep_origem=cep_orig,
            )
            self._finish_stage("preenchendo_formulario", stage_start, details=f"linhas_cubagem={len(cubagens_cm)}")
            self._passo_atual = "submetendo_cotacao"
            resultado = await self._submeter_e_extrair()
            if resultado is not None:
                self._mark_valid_quote()
            return resultado
        except Exception as error:
            self.last_error = str(error)
            if not self._login_status.get("cotacao_ok"):
                self._mark_login_failed()
            logger.error(f"[{self.nome}] Erro na cotação: {error}")
            # Detectar browser morto e resetar para próxima tentativa
            browser_morto = False
            if self._browser and not self._browser.is_connected():
                browser_morto = True
            elif self._context and not self._browser and self._page:
                try:
                    await self._page.evaluate("1")
                except Exception:
                    browser_morto = True
            if browser_morto:
                try:
                    await self.cleanup()
                except Exception:
                    pass
            else:
                # Para erros transitórios de página/frame (sem matar o browser),
                # recria a página para deixar o contexto limpo para o próximo retry.
                await self._reset_page_for_retry(error)
            return None

    async def cotar(self, request: QuoteRequest) -> QuoteResponse:
        return await super().cotar(request)
