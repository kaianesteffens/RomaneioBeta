import json
import ssl
import sys
import time
from pathlib import Path
from urllib.error import URLError


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "app"))

import usage_reporter as ur


class FakeResponse:
    status = 201

    def __init__(self, payload=None):
        self.payload = payload or {"id": "evt_123"}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _prepare_identity(monkeypatch):
    monkeypatch.setattr(ur, "get_saved_license", lambda: "FBOT-ABCD-1234-EFGH-5678")
    monkeypatch.setattr(ur, "get_machine_id", lambda: "machine-123")
    monkeypatch.setattr(ur, "_get_app_version", lambda: "2.27")


def test_usage_metadata_sensitive_values_are_removed_before_send(monkeypatch):
    _prepare_identity(monkeypatch)
    captured = {}

    def fake_urlopen(req, timeout=10, context=None):
        assert isinstance(context, ssl.SSLContext)
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setenv("FRETIO_USAGE_API_URL", "https://usage.example.test/events")
    monkeypatch.setattr(ur, "urlopen", fake_urlopen)

    result = ur.report_usage_event(
        "carrier_quotation_result",
        module="quotation",
        provider="trd",
        status="ok",
        metadata={
            "uf_destino": "RS",
            "cnpj": "12.345.678/0001-90",
            "login": "cliente@example.com",
            "nested": {
                "Authorization": "Bearer secret-token",
                "volumes": 2,
            },
            "texto": "DATABASE_URL=postgres://user:pass@db/app",
        },
        wait=True,
    )

    assert result["sent"] is True
    serialized = json.dumps(captured["payload"], ensure_ascii=False)
    assert captured["payload"]["metadata"] == {"uf_destino": "RS", "nested": {"volumes": 2}}
    assert "12.345.678/0001-90" not in serialized
    assert "cliente@example.com" not in serialized
    assert "secret-token" not in serialized
    assert "DATABASE_URL" not in serialized


def test_usage_network_failure_does_not_raise(monkeypatch):
    _prepare_identity(monkeypatch)
    monkeypatch.setenv("FRETIO_USAGE_API_URL", "https://usage.example.test/events")
    monkeypatch.setattr(ur, "urlopen", lambda *a, **kw: (_ for _ in ()).throw(URLError("offline")))

    result = ur.report_usage_event("app_started", wait=True)

    assert result["sent"] is False
    assert result["status_code"] is None


def test_report_usage_event_builds_identity_payload(monkeypatch):
    _prepare_identity(monkeypatch)
    captured = {}

    def fake_urlopen(req, timeout=10, context=None):
        captured["url"] = req.full_url
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse({"id": "evt_identity"})

    monkeypatch.setenv("FRETIO_USAGE_API_URL", "https://usage.example.test/events")
    monkeypatch.setattr(ur, "urlopen", fake_urlopen)

    result = ur.report_usage_event("app_started", module="app", status="ok", wait=True)

    assert result["sent"] is True
    assert result["id"] == "evt_identity"
    assert captured["url"] == "https://usage.example.test/events"
    assert captured["payload"]["license_key"] == "FBOT-ABCD-1234-EFGH-5678"
    assert captured["payload"]["machine_id"] == "machine-123"
    assert captured["payload"]["app_version"] == "2.27"


def test_value_cents_is_integer_or_null(monkeypatch):
    _prepare_identity(monkeypatch)

    payload = ur._build_payload("carrier_quotation_result", value_cents="12345")
    invalid_payload = ur._build_payload("carrier_quotation_result", value_cents="valor")

    assert payload["value_cents"] == 12345
    assert isinstance(payload["value_cents"], int)
    assert invalid_payload["value_cents"] is None


def test_background_event_does_not_block(monkeypatch):
    _prepare_identity(monkeypatch)
    finished = []

    def fake_urlopen(req, timeout=10, context=None):
        time.sleep(0.25)
        finished.append(True)
        return FakeResponse()

    monkeypatch.setenv("FRETIO_USAGE_API_URL", "https://usage.example.test/events")
    monkeypatch.setattr(ur, "urlopen", fake_urlopen)

    started = time.monotonic()
    result = ur.report_usage_event("app_started", wait=False)
    elapsed = time.monotonic() - started

    assert result["queued"] is True
    assert elapsed < 0.15
    deadline = time.monotonic() + 1.0
    while not finished and time.monotonic() < deadline:
        time.sleep(0.02)
    assert finished == [True]


def test_usage_event_never_sends_traceback_metadata(monkeypatch):
    _prepare_identity(monkeypatch)
    captured = {}

    def fake_urlopen(req, timeout=10, context=None):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setenv("FRETIO_USAGE_API_URL", "https://usage.example.test/events")
    monkeypatch.setattr(ur, "urlopen", fake_urlopen)

    ur.report_usage_event(
        "quotation_finished",
        metadata={
            "traceback": "Traceback (most recent call last): senha=secret",
            "detalhe": "Traceback (most recent call last): token=abc123",
            "quantidade_transportadoras": 8,
        },
        wait=True,
    )

    serialized = json.dumps(captured["payload"], ensure_ascii=False)
    assert captured["payload"]["metadata"] == {"quantidade_transportadoras": 8}
    assert "Traceback" not in serialized
    assert "secret" not in serialized
    assert "abc123" not in serialized
