import json
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

    def fake_urlopen(req, timeout):
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


def test_license_service_backend_invalid_response_is_not_cached(monkeypatch):
    saved = []

    def fake_urlopen(req, timeout):
        return FakeResponse(
            {
                "valid": False,
                "message": "Chave de licença inválida.",
            }
        )

    monkeypatch.setattr(lic, "_get_license_api_url", lambda: "https://licenses.example.test/validate")
    monkeypatch.setattr(lic, "urlopen", fake_urlopen)
    monkeypatch.setattr(lic, "_save_validation_cache", lambda key, status: saved.append((key, status)))

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
    monkeypatch.setattr(lic, "urlopen", lambda req, timeout: (_ for _ in ()).throw(URLError("offline")))

    status = lic.validate_license("FBOT-CACHE", machine_id="MAQ-1")

    assert status == lic.LicenseStatus(
        valid=True,
        owner="Cliente Cache",
        message="Validado offline (sem conexão).",
        offline=True,
    )


def test_license_service_without_api_url_preserves_legacy_gist_validation(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(lic, "_get_license_api_url", lambda: "")
    monkeypatch.setattr(lic, "_get_gist_url", lambda: "https://example.test/licenses.json")
    monkeypatch.setattr(lic, "_fetch_licenses_fresh", lambda: (_ for _ in ()).throw(ValueError("sem token")))
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

    def fake_urlopen(req, timeout):
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
    monkeypatch.setattr(lic, "_get_gist_config", lambda: (_ for _ in ()).throw(AssertionError("gist legacy não deve ser usado")))
    monkeypatch.setattr(lic, "urlopen", fake_urlopen)

    status = lic.validate_license("fbot-api", machine_id="MAQ-1")

    assert status.valid is True
    assert len(requests) == 1
    body = requests[0].data.decode("utf-8")
    assert "ghp_SECRET_ERROR_REPORT_TOKEN" not in body
    assert requests[0].get_header("Authorization") is None
    assert requests[0].full_url == "https://licenses.example.test/validate"
