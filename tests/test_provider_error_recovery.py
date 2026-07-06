"""Regressões dos erros observados na v2.57 (log do cliente 2026-07-06).

Cobre dois defeitos:
- AGEX: etapa de carga mudou → seletor de tipo-produto/cubagem não encontrado.
  Agora há fallback para <select> nativo e diagnóstico dos campos reais.
- TRANSLOVATO: SweetAlert "Oops!" sobrepunha o botão "Simular cotação" e o
  clique era retentado cegamente por 20s. Agora falha rápido com a mensagem.
"""
import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _install_playwright_test_stub():
    if "playwright.async_api" in sys.modules:
        return
    try:
        if importlib.util.find_spec("playwright.async_api") is not None:
            return
    except (ImportError, ModuleNotFoundError, ValueError):
        pass
    playwright_module = ModuleType("playwright")
    playwright_module.__path__ = []
    async_api_module = ModuleType("playwright.async_api")

    class PlaywrightTimeoutError(TimeoutError):
        pass

    class _AsyncPlaywrightStub:
        async def start(self):
            return self

        async def stop(self):
            return None

    def async_playwright():
        return _AsyncPlaywrightStub()

    class Page:
        pass

    class Frame:
        pass

    async_api_module.TimeoutError = PlaywrightTimeoutError
    async_api_module.Page = Page
    async_api_module.Frame = Frame
    async_api_module.async_playwright = async_playwright
    playwright_module.async_api = async_api_module
    sys.modules.setdefault("playwright", playwright_module)
    sys.modules["playwright.async_api"] = async_api_module


_install_playwright_test_stub()

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

from fretio.providers.agex import AGEXProvider  # noqa: E402
from fretio.providers.translovato import TranslovatoProvider  # noqa: E402


# --------------------------------------------------------------------------- #
# TRANSLOVATO — SweetAlert "Oops!" interceptando o botão de simulação
# --------------------------------------------------------------------------- #
class _FakeBotao:
    def __init__(self):
        self.clicked = False

    async def click(self, timeout=None):  # pragma: no cover - não deve ser chamado
        self.clicked = True


class _FakeTranslovatoPage:
    """Simula um SweetAlert visível: o leitor devolve a mensagem do diálogo."""

    def __init__(self, alerta: str):
        self._alerta = alerta
        self.botao = _FakeBotao()
        self.waits = []

    def get_by_role(self, role, name=None):
        return self.botao

    async def evaluate(self, script, *args):
        if "filter(Boolean).join" in script:  # _ler_sweet_alert
            return self._alerta
        return None  # _fechar_sweet_alert

    async def wait_for_timeout(self, ms):
        self.waits.append(ms)


def test_translovato_falha_rapido_quando_sweet_alert_bloqueia_simulacao():
    async def run():
        page = _FakeTranslovatoPage("Oops! - Não foi possível calcular a cotação")
        provider = TranslovatoProvider(cnpj="12345678000190", usuario="u", senha="s")
        provider._page = page

        resultado = await provider._simular_e_extrair()

        assert resultado is None
        assert provider.last_error is not None
        assert provider.last_error.startswith("Portal exibiu erro na simulação:")
        assert "Oops!" in provider.last_error
        # Botão nunca foi clicado (falhou rápido antes do retry cego de 20s).
        assert page.botao.clicked is False

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# AGEX — fallback de tipo-produto para <select> nativo + diagnóstico de campos
# --------------------------------------------------------------------------- #
class _FakeAgexPage:
    def __init__(self, *, selects, campos_diag):
        self._selects = selects
        self._campos_diag = campos_diag
        self.set_calls = []
        self.waits = []

    async def evaluate(self, script, *args):
        if "s.options" in script:  # inspeção de <select> visíveis
            return self._selects
        if "HTMLSelectElement.prototype" in script:  # setter nativo
            self.set_calls.append(args)
            return True
        if "querySelectorAll('input, textarea')" in script:  # diagnóstico
            return self._campos_diag
        return None

    async def wait_for_timeout(self, ms):
        self.waits.append(ms)


def _agex_provider():
    return AGEXProvider(
        cnpj="12345678000190",
        email="a@b.com",
        senha="s",
        tipo_produto="Artigos Esportivos",
    )


def test_agex_seleciona_tipo_produto_em_select_nativo_ignorando_pagador():
    async def run():
        page = _FakeAgexPage(
            selects=[
                # select 0 = pagador (deve ser ignorado)
                {"idx": 0, "options": [
                    {"value": "remetente", "text": "Remetente (CIF)"},
                    {"value": "destinatario", "text": "Destinatario (FOB)"},
                ]},
                # select 1 = tipo de produto
                {"idx": 1, "options": [
                    {"value": "esp", "text": "Artigos Esportivos"},
                    {"value": "out", "text": "Outros"},
                ]},
            ],
            campos_diag=[],
        )
        provider = _agex_provider()

        ok = await provider._selecionar_tipo_produto_nativo(page)

        assert ok is True
        # Definiu o select de índice 1 (produto), não o de pagador.
        assert page.set_calls, "setter nativo não foi chamado"
        idx, valor = page.set_calls[0][0]
        assert idx == 1
        assert valor == "esp"

    asyncio.run(run())


def test_agex_tipo_produto_nativo_retorna_false_sem_select_de_produto():
    async def run():
        page = _FakeAgexPage(
            selects=[
                {"idx": 0, "options": [
                    {"value": "remetente", "text": "Remetente (CIF)"},
                ]},
            ],
            campos_diag=[],
        )
        provider = _agex_provider()

        ok = await provider._selecionar_tipo_produto_nativo(page)

        assert ok is False
        assert not page.set_calls

    asyncio.run(run())


class _FakeLoc:
    def __init__(self, page, kind):
        self.page = page
        self.kind = kind

    @property
    def first(self):
        return self

    async def count(self):
        return 1 if self.kind in ("combo", "trigger") else 0

    def locator(self, sel):
        return _FakeLoc(self.page, "trigger")

    async def click(self):
        if self.kind in ("combo", "trigger"):
            self.page.options_open = True


class _FakeOption:
    def __init__(self, page, text):
        self.page = page
        self.text = text

    async def inner_text(self):
        return self.text

    async def click(self):
        self.page.selected = self.text


class _FakeOptions:
    def __init__(self, page):
        self.page = page

    async def count(self):
        return len(self.page.opts) if self.page.options_open else 0

    def nth(self, i):
        return _FakeOption(self.page, self.page.opts[i])


class _FakeProdPage:
    def __init__(self, opts):
        self.opts = opts
        self.options_open = False
        self.selected = None

    def locator(self, sel):
        if "listbox" in sel and "option" in sel:
            return _FakeOptions(self)
        if "combobox" in sel or "produto" in sel:
            return _FakeLoc(self, "combo")
        return _FakeLoc(self, "none")

    def get_by_role(self, role):
        assert role == "option"
        return _FakeOptions(self)

    async def wait_for_timeout(self, ms):
        pass


_PRODUTOS_AGEX = [
    "Artigos Esportivos", "Artigos de Higiene e Limpeza", "Calçados",
    "Confecção, Vestuário, Tecidos e Fios", "Material Escolar/Escritório", "Outros",
]


def test_agex_seleciona_tipo_produto_no_combobox_input():
    async def run():
        page = _FakeProdPage(_PRODUTOS_AGEX)
        provider = _agex_provider()  # tipo_produto = "Artigos Esportivos"

        ok = await provider._selecionar_tipo_produto(page)

        assert ok is True
        assert page.selected == "Artigos Esportivos"

    asyncio.run(run())


def test_agex_tipo_produto_cai_para_outros_quando_nao_casa():
    async def run():
        page = _FakeProdPage(_PRODUTOS_AGEX)
        provider = AGEXProvider(
            cnpj="12345678000190", email="a@b.com", senha="s",
            tipo_produto="Categoria Inexistente XYZ",
        )

        ok = await provider._selecionar_tipo_produto(page)

        assert ok is True
        assert page.selected == "Outros"

    asyncio.run(run())


def test_agex_diagnostico_lista_campos_reais_da_carga():
    async def run():
        page = _FakeAgexPage(
            selects=[],
            campos_diag={
                "campos": [
                    {"placeholder": "Altura (cm)", "name": "", "aria": "", "visivel": True},
                    {"placeholder": "", "name": "totalWeight", "aria": "", "visivel": True},
                ],
                "comboboxes": 0,
                "selects": 1,
            },
        )
        provider = _agex_provider()

        diag = await provider._diagnostico_campos_carga(page)

        assert "comboboxes_radix=0" in diag
        assert "selects_nativos=1" in diag
        assert "Altura (cm)" in diag
        assert "totalWeight" in diag

    asyncio.run(run())
