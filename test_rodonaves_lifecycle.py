import asyncio
import sys
from pathlib import Path
from types import ModuleType


def _install_playwright_test_stub():
    if "playwright.async_api" in sys.modules:
        return
    playwright_module = ModuleType("playwright")
    async_api_module = ModuleType("playwright.async_api")

    def async_playwright():
        raise RuntimeError("Playwright não está instalado neste ambiente de teste")

    class PlaywrightTimeoutError(TimeoutError):
        pass

    class Page:
        pass

    class Frame:
        pass

    async_api_module.TimeoutError = PlaywrightTimeoutError
    async_api_module.Page = Page
    async_api_module.Frame = Frame
    async_api_module.async_playwright = async_playwright
    sys.modules.setdefault("playwright", playwright_module)
    sys.modules["playwright.async_api"] = async_api_module


_install_playwright_test_stub()

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

from fretio.models import Cotacao
from fretio.providers.rodonaves import RodonavesProvider


class FakePage:
    def __init__(self, url="about:blank", closed=False, goto_error=None):
        self.url = url
        self.closed = closed
        self.goto_error = goto_error
        self.gotos = []

    def is_closed(self):
        return self.closed

    async def goto(self, url, **kwargs):
        self.gotos.append((url, kwargs))
        if self.goto_error is not None:
            error = self.goto_error
            self.goto_error = None
            raise error
        self.url = url

    async def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, pages):
        self.pages = list(pages)
        self.created_pages = []

    async def new_page(self):
        page = FakePage(url="about:blank")
        self.pages.append(page)
        self.created_pages.append(page)
        return page

    async def close(self):
        for page in self.pages:
            page.closed = True


class FakeBrowser:
    def __init__(self, context, connected=True):
        self.contexts = [context]
        self.connected = connected

    def is_connected(self):
        return self.connected

    async def close(self):
        self.connected = False


def _provider():
    return RodonavesProvider(
        dominio="RTE",
        usuario="12345678000190",
        senha="senha_teste",
        cnpj_pagador="12345678000190",
    )


def test_rodonaves_recreates_page_when_closed_before_goto():
    async def run():
        provider = _provider()
        closed_page = FakePage(url="https://cliente.rte.com.br/?showLogin=true", closed=True)
        context = FakeContext([closed_page])
        provider._context = context
        provider._browser = FakeBrowser(context)
        provider._page = closed_page
        provider._passo_atual = "login"

        page = await provider._goto_with_lifecycle_guard(
            "https://cliente.rte.com.br/?showLogin=true",
            stage="login",
            wait_until="domcontentloaded",
            timeout=15000,
        )

        assert page is context.created_pages[0]
        assert closed_page.gotos == []
        assert page.gotos[0][0] == "https://cliente.rte.com.br/?showLogin=true"
        assert "Lifecycle Playwright RODONAVES" in provider.last_error
        assert "target_url=https://cliente.rte.com.br/?showLogin=true" in provider.last_error
        assert "reason=page fechada" in provider.last_error

    asyncio.run(run())


def test_rodonaves_restarts_session_when_browser_closes_during_goto(monkeypatch):
    async def run():
        provider = _provider()
        first_page = FakePage(
            url="about:blank",
            goto_error=RuntimeError("Page.goto: Target page, context or browser has been closed"),
        )
        first_context = FakeContext([first_page])
        provider._context = first_context
        provider._browser = FakeBrowser(first_context)
        provider._page = first_page
        provider._passo_atual = "login"
        cleanup_calls = []
        second_page = FakePage(url="about:blank")
        second_context = FakeContext([second_page])

        async def fake_cleanup():
            cleanup_calls.append(True)
            provider._context = None
            provider._browser = None
            provider._page = None

        async def fake_init_browser():
            provider._context = second_context
            provider._browser = FakeBrowser(second_context)
            provider._page = second_page

        monkeypatch.setattr(provider, "cleanup", fake_cleanup)
        monkeypatch.setattr(provider, "_init_browser", fake_init_browser)

        page = await provider._goto_with_lifecycle_guard(
            "https://cliente.rte.com.br/?showLogin=true",
            stage="login",
            wait_until="domcontentloaded",
            timeout=15000,
        )

        assert cleanup_calls == [True]
        assert first_page.gotos[0][0] == "https://cliente.rte.com.br/?showLogin=true"
        assert page is second_page
        assert second_page.gotos[0][0] == "https://cliente.rte.com.br/?showLogin=true"
        assert "page/context/browser fechou durante goto" in provider.last_error
        assert "stage=login" in provider.last_error

    asyncio.run(run())


class FakeDiagnosticPage:
    url = "https://cliente.rte.com.br/Quotation"

    def is_closed(self):
        return False

    async def title(self):
        return "Cotação RTE"

    async def evaluate(self, _script):
        return {
            "bodyText": "Erro para CNPJ 12345678000190 no CEP 99999-000 sem valor",
            "alertText": "CNPJ 12345678000190 inválido",
            "formPresent": True,
            "calculateButtonPresent": True,
            "calculateButtonVisible": True,
            "resultPresent": False,
            "recaptchaFrames": 1,
            "captchaTokenLen": 0,
        }


def test_rodonaves_safe_diagnostic_snapshot_redacts_documents():
    async def run():
        provider = _provider()
        provider._page = FakeDiagnosticPage()
        provider._passo_atual = "ler_resultado"

        snapshot = await provider._capture_safe_diagnostic_snapshot(
            reason="valor_frete_nao_encontrado",
            stage="ler_resultado",
            api_result={"text": "CNPJ 12345678000190 valor ausente", "url": "https://cliente.rte.com.br/Quotation"},
        )

        assert snapshot["form_present"] is True
        assert snapshot["recaptcha_frames"] == 1
        assert "12345678000190" not in snapshot["body_excerpt"]
        assert "12345678000190" not in snapshot["alert_excerpt"]
        assert "12345678000190" not in snapshot["api_excerpt"]
        assert "***" in snapshot["body_excerpt"]
        assert provider._diagnostic_context["rodonaves_snapshot"] is snapshot

    asyncio.run(run())


def test_rodonaves_retries_visible_when_headless_recaptcha_blocks(monkeypatch):
    async def run():
        provider = _provider()
        provider._effective_headless = True
        calls = []

        async def fake_init_browser():
            calls.append("init")

        async def fake_login():
            calls.append("login")

        async def fake_navegar():
            calls.append("navegar")

        async def fake_preencher(**_kwargs):
            calls.append("preencher")

        async def fake_submeter():
            calls.append("submeter")
            provider.last_error = "Rodonaves: reCAPTCHA pendente em headless; será necessário refazer em modo visível"
            return None

        async def fake_retry_visible(**kwargs):
            calls.append("retry_visible")
            assert kwargs["volumes"] == 23
            assert kwargs["preencher_cep_origem"] is True
            return Cotacao(transportadora="RODONAVES", prazo_dias=3, valor_frete=123.45)

        monkeypatch.setattr(provider, "_init_browser", fake_init_browser)
        monkeypatch.setattr(provider, "_login", fake_login)
        monkeypatch.setattr(provider, "_navegar_cotacao", fake_navegar)
        monkeypatch.setattr(provider, "_preencher_cotacao", fake_preencher)
        monkeypatch.setattr(provider, "_submeter_e_extrair", fake_submeter)
        monkeypatch.setattr(provider, "_retry_visible_after_headless_captcha", fake_retry_visible)

        result = await provider.coteir(
            origem="01001000",
            destino="02002000",
            peso=100,
            valor=1500.0,
            volumes=23,
            comprimento_cm=0,
            largura_cm=0,
            altura_cm=0,
            cnpj_destinatario="12345678000190",
            cubagens=[
                {"quantidade": 10, "comprimento_cm": 40, "largura_cm": 30, "altura_cm": 20, "peso_por_volume_kg": 4.3},
                {"quantidade": 13, "comprimento_cm": 50, "largura_cm": 35, "altura_cm": 25, "peso_por_volume_kg": 4.4},
            ],
            preencher_cep_origem=True,
        )

        assert result is not None
        assert result.valor_frete == 123.45
        assert calls == ["init", "login", "navegar", "preencher", "submeter", "retry_visible"]

    asyncio.run(run())


def test_rodonaves_valid_quote_forces_coherent_login_status(monkeypatch):
    async def run():
        provider = _provider()
        calls = []

        async def fake_init_browser():
            calls.append("init")

        async def fake_login():
            calls.append("login")
            provider._set_login_status("login_ok", True)

        async def fake_navegar():
            calls.append("navegar")

        async def fake_preencher(**_kwargs):
            calls.append("preencher")

        async def fake_submeter():
            calls.append("submeter")
            provider._set_login_status("aguardando_captcha", True)
            provider._set_login_status("captcha_resolvido", True)
            provider._logged_in = False  # simula sincronização antiga incoerente após resultado válido
            provider._set_login_status("login_falhou", True)
            return Cotacao(transportadora="RODONAVES", prazo_dias=3, valor_frete=925.56)

        monkeypatch.setattr(provider, "_init_browser", fake_init_browser)
        monkeypatch.setattr(provider, "_login", fake_login)
        monkeypatch.setattr(provider, "_navegar_cotacao", fake_navegar)
        monkeypatch.setattr(provider, "_preencher_cotacao", fake_preencher)
        monkeypatch.setattr(provider, "_submeter_e_extrair", fake_submeter)

        result = await provider.coteir(
            origem="01001000",
            destino="02002000",
            peso=100,
            valor=1500.0,
            volumes=1,
            cnpj_destinatario="12345678000190",
            cubagens=[{"quantidade": 1, "comprimento_cm": 40, "largura_cm": 30, "altura_cm": 20}],
        )

        assert result is not None
        assert result.valor_frete == 925.56
        assert provider._logged_in is True
        assert provider.login_status["login_ok"] is True
        assert provider.login_status["aguardando_captcha"] is True
        assert provider.login_status["captcha_resolvido"] is True
        assert provider.login_status["cotacao_ok"] is True
        assert provider.login_status["login_falhou"] is False
        assert calls == ["init", "login", "navegar", "preencher", "submeter"]

    asyncio.run(run())
