import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app"))

import error_reporter as er


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
