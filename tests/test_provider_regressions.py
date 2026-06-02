import asyncio
import sys
from pathlib import Path

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

from cotacao.common import ResultadoCotacao, carrier_login_indicator_from_progress_payload
from fretio.providers.coopex import CoopexProvider
from fretio.providers.eucatur import EucaturProvider
from fretio.providers.rodonaves import RodonavesProvider
from fretio.providers.trd import TRDProvider
from fretio.providers.translovato import TranslovatoProvider


class _FakePage:
    async def wait_for_timeout(self, _ms):
        return None


async def _run_translovato_auto_address(
    *, detected_zip="01301100", detected_city="SAO PAULO", detected_uf="SP", expected_city="São Paulo", expected_uf="SP"
):
    provider = TranslovatoProvider(cnpj="12345678000190", usuario="user", senha="senha")
    provider._page = _FakePage()

    async def read_zip():
        return detected_zip

    async def read_city_uf():
        raw = f"{detected_city}/{detected_uf}" if detected_city or detected_uf else ""
        return raw, detected_city, detected_uf

    async def validate_receiver(_expected, *, context):
        return None

    provider._read_delivery_zip_digits = read_zip
    provider._read_delivery_city_uf = read_city_uf
    provider._validate_receiver_cnpj = validate_receiver
    await provider._aguardar_e_validar_autopreenchimento_destino(
        expected_receiver="12345678000190",
        expected_cep="01415001",
        expected_city=expected_city,
        expected_uf=expected_uf,
    )


def test_translovato_accepts_different_auto_cep_when_cnpj_is_valid():
    asyncio.run(_run_translovato_auto_address())


def test_translovato_does_not_block_when_city_uf_are_not_detected():
    asyncio.run(_run_translovato_auto_address(detected_zip="01301100", detected_city="", detected_uf=""))


def test_translovato_blocks_clear_city_uf_divergence():
    with pytest.raises(ValueError, match="Cidade de entrega"):
        asyncio.run(_run_translovato_auto_address(detected_city="CAMPINAS", detected_uf="SP"))


def test_translovato_still_blocks_divergent_receiver_cnpj():
    provider = TranslovatoProvider(cnpj="12345678000190", usuario="user", senha="senha")

    async def read_receiver():
        return "00000000000000"

    async def diagnostic(**_kwargs):
        return {}

    provider._read_receiver_cnpj_digits = read_receiver
    provider._receiver_divergence_diagnostic = diagnostic
    with pytest.raises(ValueError, match="CNPJ destinatário no portal diverge"):
        asyncio.run(provider._validate_receiver_cnpj("12345678000190", context="teste"))


def test_rodonaves_valid_quote_neutralizes_prelogin_timeout_status():
    provider = RodonavesProvider("dom", "user", "senha", "12345678000190")
    provider.last_error = "Pre-login rodonaves timeout (60s) — login continuará na cotação"
    provider._set_login_status("login_falhou", True)

    provider._mark_valid_quote()

    assert provider.login_status["login_ok"] is True
    assert provider.login_status["cotacao_ok"] is True
    assert provider.login_status["login_falhou"] is False
    assert provider.last_error is None


def test_rodonaves_accepts_form_after_goto_commit_timeout():
    class Locator:
        def __init__(self):
            self.timeouts: list[int] = []

        async def wait_for(self, timeout):
            self.timeouts.append(timeout)
            if len(self.timeouts) == 1:
                raise RuntimeError("form not visible yet")
            return None

    class Page:
        def __init__(self):
            self.url = "https://cliente.rte.com.br/?showLogin=true"
            self._locator = Locator()

        def locator(self, _selector):
            return self._locator

    provider = RodonavesProvider("dom", "user", "senha", "12345678000190")
    page = Page()

    async def ensure_live_page_for_navigation(**_kwargs):
        return page

    async def goto_with_lifecycle_guard(*_args, **_kwargs):
        raise PlaywrightTimeoutError("Timeout 12000ms exceeded")

    provider._ensure_live_page_for_navigation = ensure_live_page_for_navigation
    provider._goto_with_lifecycle_guard = goto_with_lifecycle_guard

    asyncio.run(provider._navegar_cotacao(_from_login=True))

    assert page._locator.timeouts == [3000, 35000]


def test_successful_quote_promotes_login_indicator_to_ok():
    resultado = ResultadoCotacao(
        transportadora="RODONAVES",
        status="ok",
        valor_frete=925.56,
        prazo_dias=3,
    )

    assert carrier_login_indicator_from_progress_payload({"resultado": resultado}) == ("RODONAVES", "ok")


class _FakeInputLocator:
    def __init__(self):
        self.fill_calls: list[str] = []
        self.dispatched: list[str] = []
        self.press_calls: list[str] = []

    @property
    def first(self):
        return self

    async def wait_for(self, **_kwargs):
        return None

    async def fill(self, value):
        self.fill_calls.append(value)

    async def dispatch_event(self, name):
        self.dispatched.append(name)

    async def press(self, key):
        self.press_calls.append(key)


class _FakeReceiverPage:
    def __init__(self, locator):
        self._locator = locator

    def locator(self, _selector):
        return self._locator


def test_translovato_preserves_already_correct_receiver_cnpj():
    provider = TranslovatoProvider(cnpj="12345678000190", usuario="user", senha="senha")
    locator = _FakeInputLocator()
    provider._page = _FakeReceiverPage(locator)

    async def read_receiver():
        return "12345678000190"

    validated_contexts: list[str] = []

    async def validate_receiver(_expected, *, context):
        validated_contexts.append(context)
        return None

    provider._read_receiver_cnpj_digits = read_receiver
    provider._validate_receiver_cnpj = validate_receiver

    asyncio.run(provider._preencher_cnpj_destinatario("12345678000190"))

    assert locator.fill_calls == []
    assert locator.press_calls == []
    assert validated_contexts == ["valor já preenchido"]


def test_translovato_calcula_resumo_cubagem_com_fator_padrao():
    resumo = TranslovatoProvider._calcular_resumo_cubagem(
        [
            {
                "quantidade": 2,
                "comprimento_cm": 100,
                "largura_cm": 50,
                "altura_cm": 40,
            }
        ],
        fator_produto=0,
    )

    assert resumo["linhas"] == [{"cubagem": "0,4000", "peso_cubado": "120,00"}]
    assert resumo["total_cubagem"] == "0,4000"
    assert resumo["total_peso_cubado"] == "120,00"
    assert resumo["fator_produto"] == 300.0


class _CloseRaises:
    def __init__(self, exc):
        self.exc = exc

    def is_closed(self):
        return False

    async def close(self):
        raise self.exc


def test_eucatur_cleanup_ignores_cancelled_and_timeout_errors():
    async def run_cleanup():
        provider = EucaturProvider("dom", "user", "senha")
        provider._page = _CloseRaises(asyncio.CancelledError("cancelado"))
        provider._context = _CloseRaises(TimeoutError("travado"))
        provider._browser = _CloseRaises(PlaywrightTimeoutError("timeout"))
        await provider.cleanup()
        assert provider._page is None
        assert provider._context is None
        assert provider._browser is None

    asyncio.run(run_cleanup())


def test_trd_goto_timeout_is_treated_as_portal_instability():
    class Page:
        async def goto(self, *args, **kwargs):
            raise PlaywrightTimeoutError("Timeout 30000ms exceeded")

    async def run_goto():
        provider = TRDProvider("email@example.com", "senha")
        provider._page = Page()
        with pytest.raises(RuntimeError, match="instabilidade do portal/rede"):
            await provider._goto_cotacao_tratavel()

    asyncio.run(run_goto())


def test_coopex_classifies_name_not_resolved_as_temporary_network_error():
    assert CoopexProvider._is_temporary_network_error("net::ERR_NAME_NOT_RESOLVED") is True
