import json
import ssl
import sys
import time
from pathlib import Path
from urllib.error import URLError


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app"))

import license as lic


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_license_service_backend_valid_response_is_mapped_and_cached(monkeypatch):
    requests = []
    saved = []
    remote_sync = []

    def fake_urlopen(req, timeout, context=None):
        assert isinstance(context, ssl.SSLContext)
        requests.append(req)
        return FakeResponse(
            {
                "valid": True,
                "owner": "Cliente Teste",
                "message": "Licença válida.",
                "blocked": False,
                "expires": "2026-12-31",
            }
        )

    monkeypatch.setattr(lic, "_get_license_api_url", lambda: "https://licenses.example.test/validate")
    monkeypatch.setattr(lic, "urlopen", fake_urlopen)
    monkeypatch.setattr(lic, "_save_validation_cache", lambda key, status: saved.append((key, status)))
    monkeypatch.setattr(
        lic,
        "_fetch_remote_config_after_validation",
        lambda **kwargs: remote_sync.append(kwargs),
    )

    status = lic.validate_license("fbot-ok", machine_id="MAQ-1")

    assert status == lic.LicenseStatus(
        valid=True,
        owner="Cliente Teste",
        message="Licença válida.",
        blocked=False,
        expires="2026-12-31",
    )
    assert len(requests) == 1
    assert requests[0].full_url == "https://licenses.example.test/validate"
    assert json.loads(requests[0].data.decode("utf-8")) == {
        "key": "FBOT-OK",
        "machine_id": "MAQ-1",
    }
    assert requests[0].get_header("Authorization") is None
    assert saved == [("FBOT-OK", status)]
    assert remote_sync == [
        {
            "key": "FBOT-OK",
            "machine_id": "MAQ-1",
            "validate_api_url": "https://licenses.example.test/validate",
        }
    ]


def test_license_service_backend_invalid_response_is_not_cached(monkeypatch):
    saved = []

    def fake_urlopen(req, timeout, context=None):
        assert isinstance(context, ssl.SSLContext)
        return FakeResponse(
            {
                "valid": False,
                "message": "Chave de licença inválida.",
            }
        )

    monkeypatch.setattr(lic, "_get_license_api_url", lambda: "https://licenses.example.test/validate")
    monkeypatch.setattr(lic, "urlopen", fake_urlopen)
    monkeypatch.setattr(lic, "_save_validation_cache", lambda key, status: saved.append((key, status)))
    monkeypatch.setattr(lic, "_fetch_remote_config_after_validation", lambda **kwargs: None)

    status = lic.validate_license("FBOT-RUIM", machine_id="MAQ-1")

    assert status == lic.LicenseStatus(
        valid=False,
        message="Chave de licença inválida.",
    )
    assert saved == []


def test_license_service_backend_offline_uses_existing_validation_cache(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    cache_path = appdata / "Fretio" / ".license_cache"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            {
                "key": "FBOT-CACHE",
                "valid": True,
                "owner": "Cliente Cache",
                "blocked": False,
                "timestamp": time.time(),
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(lic, "_get_license_api_url", lambda: "https://licenses.example.test/validate")
    monkeypatch.setattr(lic, "urlopen", lambda req, timeout, context=None: (_ for _ in ()).throw(URLError("offline")))

    status = lic.validate_license("FBOT-CACHE", machine_id="MAQ-1")

    assert status == lic.LicenseStatus(
        valid=True,
        owner="Cliente Cache",
        message="Servidor indisponível, usando validação offline.",
        offline=True,
    )


def test_license_service_backend_offline_without_cache_returns_friendly_message(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(lic, "_get_license_api_url", lambda: "https://licenses.example.test/validate")
    monkeypatch.setattr(lic, "urlopen", lambda req, timeout, context=None: (_ for _ in ()).throw(URLError("offline")))

    status = lic.validate_license("FBOT-SEM-CACHE", machine_id="MAQ-1")

    assert status == lic.LicenseStatus(
        valid=False,
        message="Servidor indisponível e sem validação offline válida.",
    )


def test_license_service_blocked_cache_never_unlocks_license(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    cache_path = appdata / "Fretio" / ".license_cache"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            {
                "key": "FBOT-BLOCKED",
                "valid": False,
                "owner": "Cliente Bloqueado",
                "blocked": True,
                "timestamp": time.time(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(lic, "_get_license_api_url", lambda: "https://licenses.example.test/validate")
    monkeypatch.setattr(lic, "urlopen", lambda req, timeout, context=None: (_ for _ in ()).throw(URLError("offline")))

    status = lic.validate_license("FBOT-BLOCKED", machine_id="MAQ-1")

    assert status == lic.LicenseStatus(
        valid=False,
        blocked=True,
        message="Licença revogada.",
    )


def test_license_service_without_api_url_preserves_legacy_gist_validation(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(lic, "_get_license_api_url", lambda: "")
    monkeypatch.setattr(lic, "_get_gist_url", lambda: "https://example.test/licenses.json")
    monkeypatch.setattr(
        lic,
        "_fetch_licenses",
        lambda gist_url: {
            "licenses": {
                "FBOT-LEGADO": {
                    "owner": "Cliente Legado",
                    "active": True,
                    "machines": ["MAQ-1"],
                    "max_machines": 1,
                    "expires": "",
                }
            },
            "blocked_keys": [],
            "blocked_machines": [],
        },
    )

    status = lic.validate_license("fbot-legado", machine_id="MAQ-1")

    assert status == lic.LicenseStatus(
        valid=True,
        owner="Cliente Legado",
        message="Licença válida.",
    )


def test_license_service_uses_gist_fallback_only_without_server_url(monkeypatch):
    monkeypatch.setattr(lic, "_get_license_api_url", lambda: "")
    called = []
    monkeypatch.setattr(
        lic.LicenseService,
        "_validate_legacy_gist",
        lambda self, key, machine_id: called.append((key, machine_id)) or lic.LicenseStatus(valid=True, message="ok"),
    )

    lic.validate_license("fbot-legado", machine_id="MAQ-1")

    assert called == [("FBOT-LEGADO", "MAQ-1")]


def test_license_api_url_does_not_use_or_send_error_report_token(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    config_path = appdata / "Fretio" / "CONFIG.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "[fretio]\n"
        'license_api_url = "https://licenses.example.test/validate"\n'
        'license_url = "https://gist.githubusercontent.com/user/gist-id/raw/licenses.json"\n'
        'error_report_token = "ghp_SECRET_ERROR_REPORT_TOKEN"\n',
        encoding="utf-8",
    )
    requests = []

    def fake_urlopen(req, timeout, context=None):
        assert isinstance(context, ssl.SSLContext)
        requests.append(req)
        return FakeResponse({"valid": True, "owner": "Cliente API", "message": "Licença válida."})

    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.delenv("FRETIO_LICENSE_API_URL", raising=False)
    monkeypatch.delenv("FRETEBOT_LICENSE_API_URL", raising=False)
    monkeypatch.delenv("Fretio_LICENSE_API_URL", raising=False)
    monkeypatch.setattr(
        lic,
        "_load_toml_file",
        lambda path: {
            "fretio": {
                "license_api_url": "https://licenses.example.test/validate",
                "license_url": "https://gist.githubusercontent.com/user/gist-id/raw/licenses.json",
                "error_report_token": "ghp_SECRET_ERROR_REPORT_TOKEN",
            }
        },
    )
    monkeypatch.setattr(lic, "urlopen", fake_urlopen)
    monkeypatch.setattr(lic, "_fetch_remote_config_after_validation", lambda **kwargs: None)

    status = lic.validate_license("fbot-api", machine_id="MAQ-1")

    assert status.valid is True
    assert len(requests) == 1
    body = requests[0].data.decode("utf-8")
    assert "ghp_SECRET_ERROR_REPORT_TOKEN" not in body
    assert requests[0].get_header("Authorization") is None
    assert requests[0].full_url == "https://licenses.example.test/validate"


def test_ssl_context_returns_secure_context():
    context = lic._ssl_context()

    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_license_module_does_not_use_unverified_ssl_context():
    source = Path(lic.__file__).read_text(encoding="utf-8")

    assert "_create_unverified_context" not in source


def test_validate_license_uses_env_api_url_when_defined(monkeypatch, tmp_path):
    requests = []

    def fake_urlopen(req, timeout, context=None):
        assert isinstance(context, ssl.SSLContext)
        requests.append(req)
        return FakeResponse({"valid": True, "message": "Licença válida."})

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("FRETEBOT_LICENSE_API_URL", raising=False)
    monkeypatch.delenv("Fretio_LICENSE_API_URL", raising=False)
    monkeypatch.setenv("FRETIO_LICENSE_API_URL", "https://licenses.example.test/env-validate")
    monkeypatch.setattr(lic, "urlopen", fake_urlopen)
    monkeypatch.setattr(lic, "_save_validation_cache", lambda key, status: None)
    monkeypatch.setattr(lic, "_fetch_remote_config_after_validation", lambda **kwargs: None)

    status = lic.validate_license("fbot-env", machine_id="MAQ-ENV")

    assert status.valid is True
    assert len(requests) == 1
    assert requests[0].full_url == "https://licenses.example.test/env-validate"
    assert json.loads(requests[0].data.decode("utf-8")) == {
        "key": "FBOT-ENV",
        "machine_id": "MAQ-ENV",
    }


def test_get_license_api_url_normalizes_validate_endpoint(monkeypatch):
    # delenv before setenv: on Windows, env keys are case-insensitive, so
    # delenv("Fretio_LICENSE_API_URL") would delete the same key we just set.
    monkeypatch.delenv("FRETEBOT_LICENSE_API_URL", raising=False)
    monkeypatch.delenv("Fretio_LICENSE_API_URL", raising=False)
    monkeypatch.setenv("FRETIO_LICENSE_API_URL", "https://licenses.example.test/api/licenses")

    assert lic._get_license_api_url() == "https://licenses.example.test/api/licenses/validate"
