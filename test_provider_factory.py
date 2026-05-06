import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

from fretio.providers.factory import ProviderFactory, _build_agex


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
