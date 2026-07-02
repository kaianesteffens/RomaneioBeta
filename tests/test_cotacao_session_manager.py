import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

from cotacao.session_manager import _ProviderSessionRegistry, TransportadoraSession
from cotacao.session_manager import CHROME_MISSING_USER_MESSAGE


class DummyProvider:
    def __init__(self):
        self.cleaned = False

    async def cleanup(self):
        self.cleaned = True


def test_provider_session_registry_registers_and_pops_provider():
    async def run():
        registry = _ProviderSessionRegistry()
        provider = DummyProvider()
        assert await registry.register("trd", provider) is None
        assert await registry.get("trd") is provider
        assert await registry.pop("trd") is provider
        assert await registry.get("trd") is None

    asyncio.run(run())


def test_provider_session_registry_idle_cleanup_selection():
    async def run():
        registry = _ProviderSessionRegistry()
        provider = DummyProvider()
        await registry.register("trd", provider)
        removidos = await registry.pop_idle(0)
        assert [(nome, prov) for nome, prov, _idle in removidos] == [("trd", provider)]

    asyncio.run(run())


def test_transportadora_session_cleanup_does_not_open_playwright(monkeypatch):
    monkeypatch.setattr("cotacao.session_manager._carregar_config", lambda config_path=None: {})
    session = TransportadoraSession()

    async def run():
        provider = DummyProvider()
        await session.registrar_provider("fake", provider)
        await session.cleanup()
        assert provider.cleaned is True
        assert session.providers == {}
        assert session.pronto is False

    asyncio.run(run())


def test_transportadora_session_chrome_missing_cancels_prelogin_once(monkeypatch):
    reports = []
    created = []
    messages = []

    class FakeFactory:
        def __init__(self, config):
            pass

        def preload(self):
            pass

        def get_provider_config(self, nome):
            return {"habilitado": True}

        def is_available(self, nome):
            return True

        def create(self, nome, **kwargs):
            created.append(nome)
            return DummyProvider()

    def missing_chrome():
        raise FileNotFoundError("Google Chrome nao encontrado. Instale o Chrome para usar o Fretio.")

    monkeypatch.setattr("cotacao.session_manager._carregar_config", lambda config_path=None: {})
    monkeypatch.setattr("cotacao.deps.ProviderFactory", FakeFactory)
    monkeypatch.setattr("cotacao.session_manager._kill_orphan_Fretio_chromes", lambda: None)
    monkeypatch.setattr("fretio.providers.base.find_chrome", missing_chrome)
    monkeypatch.setattr(
        "cotacao.session_manager.report_provider_error",
        lambda provider, stage, message, **kw: reports.append((provider, stage, kw)),
    )

    session = TransportadoraSession()

    async def run():
        await session.inicializar(callback=messages.append)
        await session.inicializar(callback=messages.append)

    asyncio.run(run())

    assert created == []
    assert session.chrome_missing is True
    assert session.pronto is False
    assert messages == [CHROME_MISSING_USER_MESSAGE, CHROME_MISSING_USER_MESSAGE]
    assert len(reports) == 1
    assert reports[0][0] == "chrome"
    assert reports[0][1] == "pre_login"
    assert reports[0][2]["context"]["event"] == "chrome_missing"
