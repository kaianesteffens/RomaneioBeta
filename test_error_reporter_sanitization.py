import json
import sys
from pathlib import Path
from urllib.error import URLError

import pytest


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app"))

import error_reporter as er


@pytest.fixture(autouse=True)
def _reset_error_reporter_state(monkeypatch):
    er._error_api_url = ""
    er._gist_id = ""
    er._token = ""
    er._initialized = False
    er._recent_errors.clear()
    er._invalid_token_fingerprints.clear()
    for env_name in (
        "FRETIO_ERROR_API_URL",
        "FRETEBOT_ERROR_API_URL",
        "FRETIO_ERROR_GIST_ID",
        "FRETEBOT_ERROR_GIST_ID",
        "FRETIO_ERROR_REPORT_TOKEN",
        "FRETEBOT_ERROR_REPORT_TOKEN",
    ):
        monkeypatch.delenv(env_name, raising=False)
    yield
    er._error_api_url = ""
    er._gist_id = ""
    er._token = ""
    er._initialized = False
    er._recent_errors.clear()


@pytest.mark.parametrize(
    ("raw", "marker", "secret"),
    [
        ("CNPJ 12.345.678/0001-90", "[CNPJ_REDACTED]", "12.345.678/0001-90"),
        ("CNPJ 12345678000190", "[CNPJ_REDACTED]", "12345678000190"),
        ("CPF 123.456.789-01", "[CPF_REDACTED]", "123.456.789-01"),
        ("CPF 12345678901", "[CPF_REDACTED]", "12345678901"),
        ("CEP 90010-123", "[CEP_REDACTED]", "90010-123"),
        ("CEP 90010123", "[CEP_REDACTED]", "90010123"),
        ("email cliente.teste+nf@example.com", "[EMAIL_REDACTED]", "cliente.teste+nf@example.com"),
        ("token ghp_abcdefghijklmnopqrstuvwxyz123456", "[TOKEN_REDACTED]", "ghp_abcdefghijklmnopqrstuvwxyz123456"),
        (
            "token github_pat_11ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
            "[TOKEN_REDACTED]",
            "github_pat_11ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
        ),
        ("Authorization: Bearer abc.def-123_456", "Bearer [TOKEN_REDACTED]", "abc.def-123_456"),
        ("senha = \"segredo123\"", "[TOKEN_REDACTED]", "segredo123"),
        ("password: pass-123", "[TOKEN_REDACTED]", "pass-123"),
        ("ADMIN_TOKEN=admin-secret", "[TOKEN_REDACTED]", "admin-secret"),
        ("DATABASE_URL=postgres://user:pass@db/app", "[TOKEN_REDACTED]", "postgres://user:pass@db/app"),
        ("token=tok_123456", "[TOKEN_REDACTED]", "tok_123456"),
        ("error_report_token = ghp_abcdefghijklmnopqrstuvwxyz123456", "[TOKEN_REDACTED]", "ghp_abcdefghijklmnopqrstuvwxyz123456"),
        ("licença parcial FBOT-ABCD", "[LICENSE_REDACTED]", "FBOT-ABCD"),
        ("license_key = FBOT-ABCD-1234-EFGH-5678", "[LICENSE_REDACTED]", "FBOT-ABCD-1234-EFGH-5678"),
        (
            "url https://example.test/hook?token=abc123&cliente=1",
            "[URL_REDACTED]",
            "https://example.test/hook?token=abc123&cliente=1",
        ),
    ],
)
def test_sanitize_error_payload_redacts_sensitive_values(raw, marker, secret):
    sanitized = er.sanitize_error_payload(raw)

    assert marker in sanitized
    assert secret not in sanitized


def test_sanitize_error_payload_preserves_report_structure_and_diagnostics():
    raw = (
        "## RuntimeError: falha\n"
        "| Contexto | `cotacao_TRD` |\n"
        "| Fingerprint | `abc123def4567890` |\n"
        "Traceback (most recent call last):\n"
        "RuntimeError: CNPJ 12.345.678/0001-90 senha=abc123\n"
    )

    sanitized = er.sanitize_error_payload(raw)

    assert "## RuntimeError: falha" in sanitized
    assert "| Contexto | `cotacao_TRD` |" in sanitized
    assert "| Fingerprint | `abc123def4567890` |" in sanitized
    assert "Traceback (most recent call last):" in sanitized
    assert "[CNPJ_REDACTED]" in sanitized
    assert "senha=[TOKEN_REDACTED]" in sanitized
    assert "12.345.678/0001-90" not in sanitized
    assert "abc123\n" not in sanitized


def test_report_error_message_sanitizes_body_before_send(monkeypatch):
    sent = {}

    class _ImmediateThread:
        def __init__(self, target, args=(), daemon=None):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

        def join(self, timeout=None):
            return None

    er._gist_id = "gist"
    er._token = "token"
    er._initialized = True
    er._recent_errors.clear()
    monkeypatch.setattr(er.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(er, "_read_recent_diag_log", lambda: "cliente@example.com token=tok_123")
    monkeypatch.setattr(er, "_send_to_gist", lambda body, label="": sent.setdefault("body", body) or True)

    er.report_error_message(
        "Falha cliente 12.345.678/0001-90 email cliente@example.com senha=segredo",
        context="cotacao_TRD",
        wait=True,
    )

    body = sent["body"]
    assert "### Stack de Chamada" in body
    assert "| Contexto | `cotacao_TRD` |" in body
    assert "[CNPJ_REDACTED]" in body
    assert "[EMAIL_REDACTED]" in body
    assert "senha=[TOKEN_REDACTED]" in body
    assert "token=[TOKEN_REDACTED]" in body
    assert "12.345.678/0001-90" not in body
    assert "cliente@example.com" not in body
    assert "segredo" not in body
    assert "tok_123" not in body


def test_report_error_sanitizes_traceback_before_send(monkeypatch):
    sent = {}

    class _ImmediateThread:
        def __init__(self, target, args=(), daemon=None):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

        def join(self, timeout=None):
            return None

    er._gist_id = "gist"
    er._token = "token"
    er._initialized = True
    er._recent_errors.clear()
    monkeypatch.setattr(er.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(er, "_get_license_key", lambda: "FBOT-ABCD-1234-EFGH-5678")
    monkeypatch.setattr(er, "_read_recent_diag_log", lambda: "Bearer ghp_abcdefghijklmnopqrstuvwxyz123456")
    monkeypatch.setattr(er, "_send_to_gist", lambda body, label="": sent.setdefault("body", body) or True)

    try:
        raise RuntimeError(
            "cpf 123.456.789-01 cep 90010-123 github_pat_11ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
        )
    except RuntimeError:
        er.report_error(context="licenca_cliente", wait=True)

    body = sent["body"]
    assert "### Traceback" in body
    assert "| Contexto | `licenca_cliente` |" in body
    assert "| Fingerprint | `" in body
    assert "[CPF_REDACTED]" in body
    assert "[CEP_REDACTED]" in body
    assert "[TOKEN_REDACTED]" in body
    assert "[LICENSE_REDACTED]" in body
    assert "123.456.789-01" not in body
    assert "90010-123" not in body
    assert "github_pat_11ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890" not in body
    assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in body
    assert "FBOT-ABCD-1234-EFGH-5678" not in body


def test_report_error_posts_to_error_api_when_configured(monkeypatch):
    requests = []

    class _ImmediateThread:
        def __init__(self, target, args=(), daemon=None):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

        def join(self, timeout=None):
            return None

    class _Response:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(req, timeout=15):
        requests.append(req)
        return _Response()

    er._error_api_url = "https://errors.example.test/api/errors"
    er._initialized = True
    monkeypatch.setattr(er.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(er, "urlopen", fake_urlopen)
    monkeypatch.setattr(er, "_get_saved_license_key", lambda: "FBOT-ABCD-1234-EFGH-5678")
    monkeypatch.setattr(er, "_get_machine_id_for_report", lambda: "machine-123")
    monkeypatch.setattr(er, "_get_version", lambda: "9.9")

    try:
        raise RuntimeError(
            "falha senha=segredo ADMIN_TOKEN=adm DATABASE_URL=postgres://user:pass@db/app "
            "ghp_abcdefghijklmnopqrstuvwxyz123456"
        )
    except RuntimeError:
        er.report_error(context="cotacao_TRD", wait=True)

    assert len(requests) == 1
    assert requests[0].full_url == "https://errors.example.test/api/errors"
    assert requests[0].get_header("Authorization") is None
    payload = json.loads(requests[0].data.decode("utf-8"))
    assert payload["license_key"] == "FBOT-ABCD-1234-EFGH-5678"
    assert payload["machine_id"] == "machine-123"
    assert payload["app_version"] == "9.9"
    assert payload["module"] == "cotacao_TRD"
    assert payload["provider"] == ""
    assert payload["message"].startswith("RuntimeError: falha")
    assert "Traceback (most recent call last)" in payload["traceback"]
    serialized = json.dumps(payload)
    assert "segredo" not in serialized
    assert "ADMIN_TOKEN" not in serialized
    assert "DATABASE_URL" not in serialized
    assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in serialized


def test_report_error_falls_back_to_gist_without_error_api_url(monkeypatch):
    sent = {}

    class _ImmediateThread:
        def __init__(self, target, args=(), daemon=None):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

        def join(self, timeout=None):
            return None

    er._gist_id = "gist"
    er._token = "token"
    er._initialized = True
    monkeypatch.setattr(er.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        er,
        "_send_to_error_api",
        lambda payload, label="": (_ for _ in ()).throw(AssertionError("API não deve ser usada")),
    )
    monkeypatch.setattr(er, "_send_to_gist", lambda body, label="": sent.setdefault("label", label) or True)

    er.report_error_message("falha operacional", context="cotacao_RODONAVES", wait=True)

    assert sent["label"] == "msg/cotacao_RODONAVES"


def test_report_error_api_failure_does_not_raise(monkeypatch):
    calls = []

    class _ImmediateThread:
        def __init__(self, target, args=(), daemon=None):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

        def join(self, timeout=None):
            return None

    def fake_urlopen(req, timeout=15):
        calls.append(req.full_url)
        raise URLError("offline")

    er._error_api_url = "https://errors.example.test/api/errors"
    er._initialized = True
    monkeypatch.setattr(er.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(er, "urlopen", fake_urlopen)

    er.report_error_message("falha offline", context="teste_api", wait=True)

    assert calls == ["https://errors.example.test/api/errors"]


def test_error_reporter_loads_error_api_url_from_config(monkeypatch, tmp_path):
    config_path = tmp_path / "CONFIG.toml"
    config_path.write_text(
        "[romaneio]\n"
        'error_api_url = "https://errors.example.test/api/errors"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(er, "_iter_config_candidates", lambda: [config_path])

    er._load_config()

    assert er._error_api_url == "https://errors.example.test/api/errors"
