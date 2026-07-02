import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

import cotacao_transportadoras as ct
import secure_credentials as sc
from fretio.config_manager import ConfigManager
from fretio.providers.factory import ProviderFactory


class DummyProvider:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def setup_function():
    ProviderFactory._class_cache.clear()
    ConfigManager._instances.clear()
    ConfigManager._config_cache.clear()
    ConfigManager._file_mtimes.clear()
    sc._memory_fallback.clear()


def teardown_function():
    ConfigManager._instances.clear()
    ConfigManager._config_cache.clear()
    ConfigManager._file_mtimes.clear()
    sc._memory_fallback.clear()


def test_set_and_get_credential_use_store_backend(monkeypatch):
    store = {}

    def fake_write(target, value):
        store[target] = value
        return True

    monkeypatch.setattr(sc, "_write_windows_credential", fake_write)
    monkeypatch.setattr(sc, "_read_windows_credential", lambda target: store.get(target))

    assert sc.set_credential("DARLU", "TRD", "senha", "segredo-novo") is True
    assert sc.get_credential("DARLU", "TRD", "senha") == "segredo-novo"


def test_legacy_toml_password_keeps_working_and_migration_does_not_destroy_data(monkeypatch):
    written = []
    config = {
        "transportadoras": {
            "trd": {
                "habilitado": True,
                "email": "cliente@example.com",
                "senha": "senha-antiga",
                "headless": True,
            }
        }
    }
    original = {
        "transportadoras": {
            "trd": {
                "habilitado": True,
                "email": "cliente@example.com",
                "senha": "senha-antiga",
                "headless": True,
            }
        }
    }

    monkeypatch.setattr(sc, "_write_windows_credential", lambda target, value: written.append((target, value)) or True)
    monkeypatch.setattr(sc, "_read_windows_credential", lambda target: None)

    migrated = sc.migrate_plaintext_credentials(config, "DARLU")
    overlaid = sc.overlay_secure_credentials(config, "DARLU")

    assert migrated is config
    assert config == original
    assert overlaid == original
    assert written == [("Fretio:darlu:trd:senha", "senha-antiga")]


def test_overlay_secure_credentials_fills_missing_password_from_store(monkeypatch):
    config = {
        "transportadoras": {
            "trd": {
                "habilitado": True,
                "email": "cliente@example.com",
                "headless": True,
            }
        }
    }

    monkeypatch.setattr(
        sc,
        "get_credential",
        lambda empresa, transportadora, campo: "senha-segura"
        if (empresa, transportadora, campo) == ("DARLU", "trd", "senha")
        else None,
    )

    overlaid = sc.overlay_secure_credentials(config, "DARLU")

    assert "senha" not in config["transportadoras"]["trd"]
    assert overlaid["transportadoras"]["trd"]["senha"] == "senha-segura"
    assert overlaid["transportadoras"]["trd"]["email"] == "cliente@example.com"


def test_carregar_config_applies_secure_overlay_for_company_config(monkeypatch, tmp_path):
    config_path = tmp_path / "Fretio" / "empresas" / "DARLU" / "CONFIG.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "[transportadoras.trd]\n"
        "habilitado = true\n"
        'email = "cliente@example.com"\n'
        "headless = true\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sc,
        "get_credential",
        lambda empresa, transportadora, campo: "senha-store"
        if (empresa, transportadora, campo) == ("DARLU", "trd", "senha")
        else None,
    )
    monkeypatch.setattr(sc, "migrate_plaintext_credentials", lambda config, empresa: config)

    config = ct._carregar_config(config_path=config_path)

    assert config["transportadoras"]["trd"] == {
        "habilitado": True,
        "email": "cliente@example.com",
        "headless": True,
        "senha": "senha-store",
    }


def test_config_manager_applies_secure_overlay_without_changing_public_api(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    config_path = appdata / "Fretio" / "empresas" / "DARLU" / "CONFIG.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "[transportadoras.trd]\n"
        "habilitado = true\n"
        'email = "cliente@example.com"\n'
        "headless = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.delenv("PROGRAMDATA", raising=False)
    monkeypatch.setattr(
        sc,
        "get_credential",
        lambda empresa, transportadora, campo: "senha-config-manager"
        if (empresa, transportadora, campo) == ("DARLU", "trd", "senha")
        else None,
    )
    monkeypatch.setattr(sc, "migrate_plaintext_credentials", lambda config, empresa: config)

    config = ConfigManager.get_instance("DARLU").load_config()

    assert config["transportadoras"]["trd"]["senha"] == "senha-config-manager"


def test_provider_factory_receives_same_kwargs_after_secure_overlay(monkeypatch):
    config = {
        "transportadoras": {
            "trd": {
                "habilitado": True,
                "email": "cliente@example.com",
                "headless": False,
            }
        }
    }
    monkeypatch.setattr(
        sc,
        "get_credential",
        lambda empresa, transportadora, campo: "senha-provider"
        if (empresa, transportadora, campo) == ("DARLU", "trd", "senha")
        else None,
    )
    effective_config = sc.overlay_secure_credentials(config, "DARLU")
    factory = ProviderFactory(config=effective_config)

    with patch.object(ProviderFactory, "get_provider_class", return_value=DummyProvider):
        provider = factory.create("trd")

    assert isinstance(provider, DummyProvider)
    assert provider.kwargs == {
        "email": "cliente@example.com",
        "senha": "senha-provider",
        "headless": False,
    }
