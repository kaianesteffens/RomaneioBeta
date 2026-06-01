import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

from fretio.providers.factory import ProviderFactory, _build_agex, _build_translovato, validate_provider_minimum_config


class DummyProvider:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def setup_function():
    ProviderFactory._class_cache.clear()


def test_get_provider_config_prefers_nested_transportadora_section():
    factory = ProviderFactory(
        config={
            "transportadoras": {"trd": {"email": "nested@example.com", "senha": "123"}},
            "trd": {"email": "legacy@example.com", "senha": "legacy"},
        }
    )

    assert factory.get_provider_config("trd") == {
        "email": "nested@example.com",
        "senha": "123",
    }


def test_get_provider_config_falls_back_to_legacy_section_when_needed():
    factory = ProviderFactory(
        config={
            "transportadoras": {"trd": {}},
            "trd": {"email": "legacy@example.com", "senha": "legacy"},
        }
    )

    assert factory.get_provider_config("trd") == {
        "email": "legacy@example.com",
        "senha": "legacy",
    }


def test_build_agex_applies_fallbacks_and_default_values():
    built = _build_agex(
        {
            "cnpj": "contato@example.com",
            "senha": "segredo",
            "volumes": "3",
            "altura_m": "1.5",
            "largura_m": "0.8",
            "comprimento_m": "2.1",
        }
    )

    assert built == {
        "cnpj": "contato@example.com",
        "email": "contato@example.com",
        "senha": "segredo",
        "cnpj_remetente": "contato@example.com",
        "cnpj_destinatario": None,
        "cep_origem": None,
        "cep_destino": None,
        "descricao_mercadoria": "Mercadoria",
        "tipo_produto": "Artigos Esportivos",
        "volumes": 3,
        "altura_m": 1.5,
        "largura_m": 0.8,
        "comprimento_m": 2.1,
        "cubagens": None,
        "headless": True,
    }


def test_get_provider_class_caches_resolved_imports():
    calls = {"count": 0}

    def fake_import(module_path):
        calls["count"] += 1
        assert module_path == "fretio.providers.trd"
        return SimpleNamespace(TRDProvider=DummyProvider)

    with patch("fretio.providers.factory.importlib.import_module", side_effect=fake_import):
        first = ProviderFactory.get_provider_class("trd")
        second = ProviderFactory.get_provider_class("trd")

    assert first is DummyProvider
    assert second is DummyProvider
    assert calls["count"] == 1


def test_create_respects_disabled_flag_and_merges_runtime_overrides():
    factory = ProviderFactory(
        config={
            "transportadoras": {
                "trd": {
                    "habilitado": False,
                    "email": "original@example.com",
                    "senha": "segredo",
                    "headless": True,
                }
            }
        }
    )

    with patch.object(ProviderFactory, "get_provider_class", return_value=DummyProvider):
        assert factory.create("trd") is None

        provider = factory.create(
            "trd",
            ignore_disabled=True,
            email="override@example.com",
            headless=False,
        )

    assert isinstance(provider, DummyProvider)
    assert provider.kwargs == {
        "email": "override@example.com",
        "senha": "segredo",
        "headless": False,
    }


def test_validate_provider_minimum_config_uses_provider_required_fields():
    valid_configs = {
        "braspress": {"habilitado": True, "cnpj": "123", "senha": "s"},
        "bauer": {
            "habilitado": True,
            "cotacao_url": "https://bauer.example/cotar",
            "cnpj_pagador": "1",
            "cnpj_remetente": "2",
            "cnpj_destinatario": "3",
        },
        "trd": {"habilitado": True, "email": "cliente@example.com", "senha": "s"},
        "agex": {"habilitado": True, "email": "cliente@example.com", "senha": "s"},
        "eucatur": {"habilitado": True, "dominio": "EUC", "usuario": "u", "senha": "s", "cnpj_pagador": "1"},
        "rodonaves": {"habilitado": True, "dominio": "RTE", "usuario": "u", "senha": "s", "cnpj_pagador": "1"},
        "alfa": {"habilitado": True, "login": "u", "senha": "s"},
        "coopex": {"habilitado": True, "dominio": "CLD", "usuario": "u", "senha": "s", "cnpj_pagador": "1"},
        "translovato": {"habilitado": True, "cnpj": "12345678000190", "usuario": "u", "senha": "s"},
    }

    for provider, config in valid_configs.items():
        assert validate_provider_minimum_config(provider, config).valid is True


def test_validate_provider_minimum_config_reports_missing_fields_for_enabled_provider():
    result = validate_provider_minimum_config(
        "bauer",
        {
            "habilitado": True,
            "cotacao_url": "https://bauer.example/cotar",
            "cnpj_pagador": "",
            "cnpj_remetente": "2",
            "cnpj_destinatario": "",
        },
    )

    assert result.valid is False
    assert result.status == "Configuração incompleta"
    assert result.missing_fields == ("cnpj_pagador", "cnpj_destinatario")
    assert "CNPJ pagador" in result.user_message
    assert "CNPJ destinatário" in result.user_message


def test_validate_provider_minimum_config_does_not_require_disabled_provider():
    result = validate_provider_minimum_config("trd", {"habilitado": False})

    assert result.enabled is False
    assert result.valid is True


def test_validate_provider_minimum_config_keeps_agex_legacy_email_in_cnpj():
    result = validate_provider_minimum_config(
        "agex",
        {"habilitado": True, "cnpj": "cliente@example.com", "senha": "s"},
    )

    assert result.valid is True


def test_build_translovato_accepts_expected_optional_fields():
    built = _build_translovato(
        {
            "cnpj": "12.345.678/0001-90",
            "usuario": "user",
            "senha": "secret",
            "cnpj_remetente": "98.765.432/0001-10",
            "produto": "CONFECCAO",
            "cotacao_url": "https://example.invalid/cotacao",
            "headless": False,
        }
    )

    assert built == {
        "cnpj": "12.345.678/0001-90",
        "usuario": "user",
        "senha": "secret",
        "cnpj_remetente": "98.765.432/0001-10",
        "produto": "CONFECCAO",
        "cotacao_url": "https://example.invalid/cotacao",
        "headless": False,
    }


def test_translovato_provider_class_is_registered():
    provider_class = ProviderFactory.get_provider_class("translovato")

    assert provider_class is not None
    assert provider_class.__name__ == "TranslovatoProvider"


def test_create_translovato_with_valid_config_returns_provider_instance():
    factory = ProviderFactory(
        config={
            "transportadoras": {
                "translovato": {
                    "habilitado": True,
                    "cnpj": "12.345.678/0001-90",
                    "usuario": "usuario_teste",
                    "senha": "senha_teste",
                    "cnpj_remetente": "98.765.432/0001-10",
                    "produto": "CONFECCAO",
                    "cotacao_url": "https://example.invalid/cotacao",
                    "headless": True,
                }
            }
        }
    )

    provider = factory.create("translovato")

    assert provider is not None
    assert provider.__class__.__name__ == "TranslovatoProvider"
    assert provider.nome == "TRANSLOVATO"
    assert provider.cnpj == "12345678000190"
    assert provider.usuario == "usuario_teste"
    assert provider.cnpj_remetente == "98765432000110"


def test_validate_translovato_incomplete_config_reports_required_fields():
    result = validate_provider_minimum_config(
        "translovato",
        {"habilitado": True, "cnpj": "", "usuario": "", "senha": ""},
    )

    assert result.valid is False
    assert result.status == "Configuração incompleta"
    assert result.missing_fields == ("cnpj", "usuario", "senha")
    assert "CNPJ" in result.user_message
    assert "usuário" in result.user_message
    assert "senha" in result.user_message
