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
