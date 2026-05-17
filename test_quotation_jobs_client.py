import json
import ssl
import sys
from pathlib import Path
from urllib.error import URLError


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app"))

import quotation_jobs_client as qj


class FakeResponse:
    status = 201

    def __init__(self, payload=None, status=201):
        self.payload = payload if payload is not None else {"job_id": 123}
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _prepare_identity(monkeypatch):
    qj.configure(None)
    monkeypatch.setattr(qj, "get_saved_license", lambda: "FBOT-ABCD-1234-EFGH-5678")
    monkeypatch.setattr(qj, "get_machine_id", lambda: "machine-123")
    monkeypatch.setattr(qj, "_get_app_version", lambda: "2.28")


def test_create_quotation_job_builds_identity_payload(monkeypatch):
    _prepare_identity(monkeypatch)
    captured = {}

    def fake_urlopen(req, timeout=8, context=None):
        assert isinstance(context, ssl.SSLContext)
        captured["url"] = req.full_url
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse({"job_id": 456})

    monkeypatch.setenv("FRETIO_QUOTATION_JOBS_API_URL", "https://jobs.example.test/api/quotations/jobs")
    monkeypatch.setattr(qj, "urlopen", fake_urlopen)

    result = qj.create_quotation_job("manual", payload={"modo": "romaneio_colado"}, wait=True)

    assert result["created"] is True
    assert result["job_id"] == 456
    assert captured["url"] == "https://jobs.example.test/api/quotations/jobs"
    assert captured["payload"]["license_key"] == "FBOT-ABCD-1234-EFGH-5678"
    assert captured["payload"]["machine_id"] == "machine-123"
    assert captured["payload"]["app_version"] == "2.28"
    assert captured["payload"]["source_type"] == "manual"
    assert captured["payload"]["payload"] == {"modo": "romaneio_colado"}


def test_quotation_jobs_url_can_come_from_config(monkeypatch, tmp_path):
    _prepare_identity(monkeypatch)
    monkeypatch.delenv("FRETIO_QUOTATION_JOBS_API_URL", raising=False)
    monkeypatch.delenv("FRETEBOT_QUOTATION_JOBS_API_URL", raising=False)
    config_path = tmp_path / "CONFIG.toml"
    config_path.write_text(
        "[fretio]\n"
        'quotation_jobs_api_url = "https://config.example.test/jobs"\n',
        encoding="utf-8",
    )
    qj.configure(config_path)
    captured = {}

    def fake_urlopen(req, timeout=8, context=None):
        captured["url"] = req.full_url
        return FakeResponse({"job_id": 789})

    monkeypatch.setattr(qj, "urlopen", fake_urlopen)

    result = qj.create_quotation_job("romaneio", payload={}, wait=True)

    assert result["job_id"] == 789
    assert captured["url"] == "https://config.example.test/jobs"


def test_payload_sensitive_fields_are_removed(monkeypatch):
    _prepare_identity(monkeypatch)
    captured = {}

    def fake_urlopen(req, timeout=8, context=None):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setenv("FRETIO_QUOTATION_JOBS_API_URL", "https://jobs.example.test")
    monkeypatch.setattr(qj, "urlopen", fake_urlopen)

    qj.create_quotation_job(
        "manual",
        payload={
            "modo": "romaneio_colado",
            "cnpj": "12.345.678/0001-90",
            "cpf": "123.456.789-00",
            "login": "cliente@example.com",
            "senha": "segredo",
            "token": "secret-token",
            "chave_nfe": "1" * 44,
            "traceback": "Traceback (most recent call last): senha=segredo",
            "nested": {"quantidade_linhas": 8, "authorization": "Bearer abc"},
        },
        wait=True,
    )

    serialized = json.dumps(captured["payload"], ensure_ascii=False)
    assert captured["payload"]["payload"] == {
        "modo": "romaneio_colado",
        "nested": {"quantidade_linhas": 8},
    }
    assert "12.345.678/0001-90" not in serialized
    assert "123.456.789-00" not in serialized
    assert "cliente@example.com" not in serialized
    assert "segredo" not in serialized
    assert "secret-token" not in serialized
    assert "Traceback" not in serialized


def test_network_failure_does_not_raise(monkeypatch):
    _prepare_identity(monkeypatch)
    monkeypatch.setenv("FRETIO_QUOTATION_JOBS_API_URL", "https://jobs.example.test")
    monkeypatch.setattr(qj, "urlopen", lambda *a, **kw: (_ for _ in ()).throw(URLError("offline")))

    result = qj.create_quotation_job("manual", payload={}, wait=True)

    assert result["created"] is False
    assert result["status_code"] is None


def test_update_quotation_job_result_sanitizes_result(monkeypatch):
    _prepare_identity(monkeypatch)
    captured = {}

    def fake_urlopen(req, timeout=8, context=None):
        captured["url"] = req.full_url
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse({"ok": True}, status=200)

    monkeypatch.setenv("FRETIO_QUOTATION_JOBS_API_URL", "https://jobs.example.test/api/quotations/jobs")
    monkeypatch.setattr(qj, "urlopen", fake_urlopen)

    result = qj.update_quotation_job_result(
        123,
        "finished",
        result={
            "summary": {"status": "ok", "total_providers": 1},
            "transportadoras": [
                {
                    "provider": "braspress",
                    "status": "ok",
                    "value_cents": 12345,
                    "login": "cliente@example.com",
                    "cnpj": "12.345.678/0001-90",
                }
            ],
        },
        error_message="Traceback (most recent call last): token=abc",
        wait=True,
    )

    serialized = json.dumps(captured["payload"], ensure_ascii=False)
    assert result["updated"] is True
    assert captured["url"] == "https://jobs.example.test/api/quotations/jobs/123/result"
    assert captured["payload"]["status"] == "finished"
    assert "error_message" not in captured["payload"]
    assert captured["payload"]["result"]["transportadoras"] == [
        {"provider": "braspress", "status": "ok", "value_cents": 12345}
    ]
    assert "cliente@example.com" not in serialized
    assert "12.345.678/0001-90" not in serialized
    assert "token=abc" not in serialized


def test_update_without_job_id_does_not_call_server(monkeypatch):
    _prepare_identity(monkeypatch)
    called = False

    def fake_urlopen(*args, **kwargs):
        nonlocal called
        called = True
        return FakeResponse()

    monkeypatch.setattr(qj, "urlopen", fake_urlopen)

    result = qj.update_quotation_job_result("", "finished", result={}, wait=True)

    assert result["skipped"] is True
    assert called is False
