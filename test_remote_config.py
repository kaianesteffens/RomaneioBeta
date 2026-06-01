import json
import ssl
import sys
from pathlib import Path
from urllib.error import URLError


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app"))

import remote_config as rc


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _cache_file(appdata: Path) -> Path:
    return appdata / "Fretio" / "remote_config.json"


def test_remote_config_valid_response_saves_cache(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    requests = []

    def fake_urlopen(req, timeout, context=None):
        assert isinstance(context, ssl.SSLContext)
        assert context.verify_mode == ssl.CERT_REQUIRED
        requests.append(req)
        return FakeResponse(
            {
                "valid": True,
                "license": {"owner": "Cliente Teste", "key": "FBOT-OK"},
                "config": {
                    "force_update": True,
                    "allow_cotacao": False,
                    "carriers_enabled": {"braspress": False},
                },
            }
        )

    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("FRETIO_LICENSE_CONFIG_API_URL", "https://config.example.test/api")
    monkeypatch.delenv("FRETEBOT_LICENSE_CONFIG_API_URL", raising=False)
    monkeypatch.setattr(rc, "get_saved_license", lambda: "fbot-ok")
    monkeypatch.setattr(rc, "get_machine_id", lambda: "MAQ-1")
    monkeypatch.setattr(rc, "urlopen", fake_urlopen)

    result = rc.fetch_remote_config(wait=True)

    assert result["valid"] is True
    assert result["license"] == {"owner": "Cliente Teste"}
    assert result["config"]["force_update"] is True
    assert result["config"]["allow_cotacao"] is False
    assert result["config"]["carriers_enabled"]["braspress"] is False
    assert result["config"]["carriers_enabled"]["bauer"] is True
    assert len(requests) == 1
    assert requests[0].full_url == "https://config.example.test/api"
    assert requests[0].get_header("Authorization") is None
    assert json.loads(requests[0].data.decode("utf-8")) == {
        "key": "FBOT-OK",
        "machine_id": "MAQ-1",
    }

    cached = json.loads(_cache_file(appdata).read_text(encoding="utf-8"))
    assert cached["valid"] is True
    assert cached["license"] == {"owner": "Cliente Teste"}
    assert cached["config"]["allow_cotacao"] is False


def test_remote_config_network_failure_uses_existing_cache(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    cache_path = _cache_file(appdata)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            {
                "fetched_at": "2026-01-01T00:00:00Z",
                "valid": True,
                "license": {"owner": "Cliente Cache"},
                "config": {
                    "allow_cotacao": False,
                    "carriers_enabled": {"braspress": False},
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(rc, "get_saved_license", lambda: "FBOT-CACHE")
    monkeypatch.setattr(rc, "get_machine_id", lambda: "MAQ-1")
    monkeypatch.setattr(rc, "urlopen", lambda req, timeout, context=None: (_ for _ in ()).throw(URLError("offline")))

    result = rc.fetch_remote_config(wait=True)

    assert result["valid"] is True
    assert result["license"]["owner"] == "Cliente Cache"
    assert result["config"]["allow_cotacao"] is False
    assert result["config"]["carriers_enabled"]["braspress"] is False


def test_remote_config_without_cache_uses_local_defaults(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"

    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(rc, "get_saved_license", lambda: "FBOT-SEM-CACHE")
    monkeypatch.setattr(rc, "get_machine_id", lambda: "MAQ-1")
    monkeypatch.setattr(rc, "urlopen", lambda req, timeout, context=None: (_ for _ in ()).throw(URLError("offline")))

    result = rc.fetch_remote_config(wait=True)

    assert result["valid"] is False
    assert result["license"] == {}
    assert result["config"] == rc.DEFAULT_REMOTE_CONFIG


def test_remote_config_cache_does_not_persist_sensitive_fields(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"

    def fake_urlopen(req, timeout, context=None):
        return FakeResponse(
            {
                "valid": True,
                "license": {
                    "owner": "Cliente Seguro",
                    "key": "FBOT-SECRET-KEY",
                    "token": "TOKEN-SECRET",
                },
                "config": {
                    "ADMIN_TOKEN": "admin-secret",
                    "DATABASE_URL": "postgres://secret",
                    "Authorization": "Bearer secret",
                    "senha": "transport-pass",
                    "nested": {
                        "password": "nested-pass",
                        "url": "DATABASE_URL=postgres://secret",
                    },
                    "transportadoras": {
                        "braspress": {"senha": "carrier-pass"},
                    },
                    "carriers_enabled": {"braspress": True},
                },
            }
        )

    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(rc, "get_saved_license", lambda: "FBOT-OK")
    monkeypatch.setattr(rc, "get_machine_id", lambda: "MAQ-1")
    monkeypatch.setattr(rc, "urlopen", fake_urlopen)

    rc.fetch_remote_config(wait=True)

    cache_text = _cache_file(appdata).read_text(encoding="utf-8")
    for forbidden in (
        "ADMIN_TOKEN",
        "DATABASE_URL",
        "Authorization",
        "FBOT-SECRET-KEY",
        "TOKEN-SECRET",
        "admin-secret",
        "postgres://secret",
        "Bearer secret",
        "transport-pass",
        "nested-pass",
        "carrier-pass",
    ):
        assert forbidden not in cache_text


def test_is_carrier_enabled_uses_cached_known_carrier(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    cache_path = _cache_file(appdata)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            {
                "fetched_at": "2026-01-01T00:00:00Z",
                "valid": True,
                "license": {},
                "config": {"carriers_enabled": {"braspress": False}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA", str(appdata))

    assert rc.is_carrier_enabled("braspress") is False
    assert rc.is_carrier_enabled("bauer") is True


def test_is_feature_allowed_uses_cached_known_feature(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    cache_path = _cache_file(appdata)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            {
                "fetched_at": "2026-01-01T00:00:00Z",
                "valid": True,
                "license": {},
                "config": {"allow_cotacao": False},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA", str(appdata))

    assert rc.is_feature_allowed("cotacao") is False
    assert rc.is_feature_allowed("rastreio") is True


def test_license_config_api_url_can_be_loaded_from_config_toml(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    config_path = appdata / "Fretio" / "CONFIG.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "[fretio]\n"
        'license_config_api_url = "https://config.example.test/from-toml"\n',
        encoding="utf-8",
    )
    requests = []

    def fake_urlopen(req, timeout, context=None):
        requests.append(req)
        return FakeResponse({"valid": True, "license": {"owner": "Toml"}, "config": {}})

    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.delenv("FRETIO_LICENSE_CONFIG_API_URL", raising=False)
    monkeypatch.delenv("FRETEBOT_LICENSE_CONFIG_API_URL", raising=False)
    monkeypatch.setattr(rc, "get_saved_license", lambda: "FBOT-TOML")
    monkeypatch.setattr(rc, "get_machine_id", lambda: "MAQ-1")
    monkeypatch.setattr(rc, "urlopen", fake_urlopen)

    rc.fetch_remote_config(wait=True)

    assert requests[0].full_url == "https://config.example.test/from-toml"


def test_fetch_remote_config_for_license_derives_config_endpoint_from_validate_url(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    requests = []

    def fake_urlopen(req, timeout, context=None):
        requests.append(req)
        return FakeResponse({"valid": True, "license": {"owner": "Derivada"}, "config": {}})

    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(rc, "urlopen", fake_urlopen)

    rc.fetch_remote_config_for_license(
        key="FBOT-URL",
        machine_id="MAQ-1",
        validate_api_url="https://licenses.example.test/api/licenses/validate",
        wait=True,
    )

    assert requests[0].full_url == "https://licenses.example.test/api/licenses/config"


def test_apply_safe_runtime_overrides_does_not_overwrite_local_credentials(monkeypatch):
    local_config = {
        "fretio": {"fator_cubagem": 6000, "cache_dir": "cache"},
        "romaneio": {"cep_origem": "11111111"},
        "transportadoras": {
            "trd": {
                "habilitado": True,
                "email": "local@example.com",
                "senha": "SENHA_LOCAL",
            }
        },
    }
    original = json.loads(json.dumps(local_config))

    monkeypatch.setattr(
        rc,
        "get_effective_remote_config",
        lambda: {
            "cep_origem": "99.740-000",
            "fator_cubagem": "5500",
            "transportadoras": {
                "trd": {
                    "email": "server@example.com",
                    "senha": "SENHA_REMOTA",
                }
            },
        },
    )

    applied = rc.apply_safe_runtime_overrides(local_config)

    assert local_config == original
    assert applied["romaneio"]["cep_origem"] == "99740000"
    assert applied["fretio"]["fator_cubagem"] == 5500
    assert applied["transportadoras"] == original["transportadoras"]


def test_get_safe_runtime_overrides_ignores_invalid_values(monkeypatch):
    monkeypatch.setattr(
        rc,
        "get_effective_remote_config",
        lambda: {
            "cep_origem": "123",
            "fator_cubagem": "invalido",
        },
    )

    assert rc.get_safe_runtime_overrides() == {}


def test_default_remote_config_enables_translovato_by_default():
    assert rc.DEFAULT_REMOTE_CONFIG["carriers_enabled"]["translovato"] is True
