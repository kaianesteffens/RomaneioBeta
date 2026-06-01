import asyncio
import sys
from pathlib import Path

from types import ModuleType


def _install_playwright_test_stub():
    if "playwright.async_api" in sys.modules:
        return
    playwright_module = ModuleType("playwright")
    async_api_module = ModuleType("playwright.async_api")

    class PlaywrightTimeoutError(TimeoutError):
        pass

    def async_playwright():
        raise RuntimeError("Playwright não está instalado neste ambiente de teste")

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
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

import cotacao_transportadoras as ct
import remote_permissions as rp


def test_allow_cotacao_false_blocks_cotacao(monkeypatch):
    monkeypatch.setattr(rp, "is_feature_allowed", lambda feature: feature != "cotacao")

    assert rp.ensure_feature_allowed("cotacao") is False
    assert rp.feature_message("cotacao") == "Este módulo foi desabilitado pela configuração da licença."


def test_allow_rastreio_false_blocks_rastreio(monkeypatch):
    monkeypatch.setattr(rp, "is_feature_allowed", lambda feature: feature != "rastreio")

    assert rp.ensure_feature_allowed("rastreio") is False
    assert rp.feature_message("rastreio") == "Este módulo foi desabilitado pela configuração da licença."


def test_without_remote_cache_allows_everything(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("FRETIO_LICENSE_CONFIG_API_URL", raising=False)
    monkeypatch.delenv("FRETEBOT_LICENSE_CONFIG_API_URL", raising=False)

    assert rp.feature_allowed_or_default("cotacao") is True
    assert rp.feature_allowed_or_default("rastreio") is True
    assert rp.carrier_enabled_or_message("Braspress") == (True, "")


def test_remote_config_failure_does_not_break_permissions(monkeypatch):
    monkeypatch.setattr(rp, "is_feature_allowed", lambda feature: (_ for _ in ()).throw(OSError("cache falhou")))
    monkeypatch.setattr(rp, "is_carrier_enabled", lambda carrier: (_ for _ in ()).throw(OSError("cache falhou")))

    assert rp.ensure_feature_allowed("cotacao") is True
    assert rp.carrier_enabled_or_message("TRD") == (True, "")


def test_translovato_aliases_normalize_to_canonical_name():
    assert rp.normalize_carrier_name("Translovato") == "translovato"
    assert rp.normalize_carrier_name("trans lovato") == "translovato"
    assert rp.normalize_carrier_name("transportes translovato") == "translovato"


def test_disabled_carrier_is_skipped_before_provider_creation(monkeypatch):
    create_calls = []

    class FakeProvider:
        async def coteir(self, **kwargs):
            raise AssertionError("provider desabilitado nao deveria cotar")

    class FakeFactory:
        def __init__(self, config):
            self.config = config

        def is_available(self, nome):
            return nome == "braspress"

        def get_provider_config(self, nome):
            transportadoras = self.config.get("transportadoras", {})
            return dict(transportadoras.get(nome, {"habilitado": False}))

        def create(self, nome, **kwargs):
            create_calls.append((nome, kwargs))
            return FakeProvider()

    monkeypatch.setattr(ct, "ProviderFactory", FakeFactory)
    monkeypatch.setattr(
        ct,
        "carrier_enabled_or_message",
        lambda carrier: (
            False,
            ct.CARRIER_DISABLED_MESSAGE,
        )
        if str(carrier).lower() == "braspress"
        else (True, ""),
    )
    monkeypatch.setattr(ct, "_diag_log_enabled", lambda: False)

    config = {
        "fretio": {},
        "romaneio": {"cep_origem": "99740000"},
        "transportadoras": {
            "braspress": {
                "habilitado": True,
                "cnpj": "12345678000190",
                "senha": "secret",
                "ufs_atendidas": ["RS"],
            }
        },
    }
    dados = {
        "destino_cep": "90010123",
        "uf_destino": "RS",
        "cnpj_destinatario": "12345678000190",
        "peso": 3.3,
        "valor": 150.99,
        "volumes": 1,
        "cubagem_m3": 0.044,
        "cubagens": [
            {
                "quantidade": 1,
                "comprimento_cm": 45,
                "largura_cm": 31,
                "altura_cm": 31,
                "peso_por_volume_kg": 3.3,
            }
        ],
        "descricoes_itens": [],
    }

    resultados = asyncio.run(
        ct._executar_cotacoes_com_dados(
            config=config,
            dados=dados,
            cep_origem="99740000",
        )
    )

    assert create_calls == []
    assert len(resultados) == 1
    assert resultados[0].transportadora == "BRASPRESS"
    assert resultados[0].status == "desabilitada"
    assert resultados[0].detalhes == ct.CARRIER_DISABLED_MESSAGE


def test_known_carriers_includes_translovato():
    assert "translovato" in rp.KNOWN_CARRIERS


def test_remote_config_missing_translovato_key_allows_translovato(monkeypatch):
    monkeypatch.setattr(
        rp,
        "get_effective_remote_config",
        lambda: {"carriers_enabled": {"braspress": True}},
    )

    assert "translovato" in rp.enabled_carriers_from_config()
    assert "translovato" not in rp.disabled_carriers_from_config()


def test_orchestrator_includes_translovato_when_enabled_and_configured(monkeypatch):
    create_calls = []
    progress_events = []

    class FakeProvider:
        nome = "TRANSLOVATO"
        headless = True
        _logged_in = False

        async def coteir(self, **kwargs):
            return ct.ResultadoCotacao(
                transportadora="TRANSLOVATO",
                status="ok",
                valor_frete=123.45,
                prazo_dias=4,
            )

    class FakeFactory:
        def __init__(self, config):
            self.config = config

        def is_available(self, nome):
            return nome == "translovato"

        def get_provider_config(self, nome):
            transportadoras = self.config.get("transportadoras", {})
            return dict(transportadoras.get(nome, {"habilitado": False}))

        def validate_minimum_config(self, nome):
            from fretio.providers.factory import validate_provider_minimum_config

            return validate_provider_minimum_config(nome, self.get_provider_config(nome))

        def create(self, nome, **kwargs):
            create_calls.append((nome, kwargs))
            return FakeProvider()

    monkeypatch.setattr(ct, "ProviderFactory", FakeFactory)
    monkeypatch.setattr(ct, "carrier_enabled_or_message", lambda carrier: (True, ""))
    monkeypatch.setattr(ct, "_diag_log_enabled", lambda: False)

    config = {
        "fretio": {"max_paralelo": 1},
        "romaneio": {"cep_origem": "99740000"},
        "transportadoras": {
            "braspress": {"habilitado": False},
            "bauer": {"habilitado": False},
            "trd": {"habilitado": False},
            "agex": {"habilitado": False},
            "eucatur": {"habilitado": False},
            "rodonaves": {"habilitado": False},
            "alfa": {"habilitado": False},
            "coopex": {"habilitado": False},
            "translovato": {
                "habilitado": True,
                "cnpj": "12.345.678/0001-90",
                "usuario": "usuario_teste",
                "senha": "senha_teste",
                "cnpj_remetente": "98.765.432/0001-10",
                "produto": "CONFECCAO",
                "headless": True,
                "ufs_atendidas": ["RS"],
            },
        },
    }
    dados = {
        "destino_cep": "90010123",
        "uf_destino": "RS",
        "cnpj_destinatario": "12345678000190",
        "peso": 3.3,
        "valor": 150.99,
        "volumes": 1,
        "cubagem_m3": 0.044,
        "cubagens": [
            {
                "quantidade": 1,
                "comprimento_cm": 45,
                "largura_cm": 31,
                "altura_cm": 31,
                "peso_por_volume_kg": 3.3,
            }
        ],
        "descricoes_itens": [],
    }

    resultados = asyncio.run(
        ct._executar_cotacoes_com_dados(
            config=config,
            dados=dados,
            cep_origem="99740000",
            progresso_callback=progress_events.append,
        )
    )

    assert create_calls == [(
        "translovato",
        {
            "headless": True,
            "cnpj_remetente": "98765432000110",
            "produto": "CONFECCAO",
            "cotacao_url": "",
        },
    )]
    assert len(resultados) == 1
    assert resultados[0].transportadora == "TRANSLOVATO"
    assert resultados[0].status == "ok"
    assert any(
        event.get("provider") == "TRANSLOVATO" and event.get("status") == "aguardando"
        for event in progress_events
    )
    assert any(
        event.get("provider") == "TRANSLOVATO" and event.get("status") == "finalizada"
        for event in progress_events
    )
