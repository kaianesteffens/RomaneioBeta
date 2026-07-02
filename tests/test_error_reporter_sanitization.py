import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parent.parent
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


def test_sanitize_extra_value_sanitiza_dict_list_str():
    extra = {
        "cnpj": "12.345.678/0001-90",
        "aninhado": {"email": "cliente@example.com"},
        "lista": ["senha=segredo", "ok"],
        "numero": 42,
    }

    out = er._sanitize_extra_value(extra)

    assert out["cnpj"] == "[CNPJ_REDACTED]"
    assert out["aninhado"]["email"] == "[EMAIL_REDACTED]"
    assert out["lista"][0] == "senha=[TOKEN_REDACTED]"
    assert out["lista"][1] == "ok"
    assert out["numero"] == 42


def test_traceback_is_test_originated_detects_test_frames():
    assert er._traceback_is_test_originated(
        '  File "test_cotacao_transportadoras.py", line 218, in create'
    )
    assert er._traceback_is_test_originated(
        '  File "C:\\\\Users\\\\dev\\\\RomaneioBeta\\\\test_x.py", line 1, in f'
    )
    assert er._traceback_is_test_originated(
        '  File "/home/u/work/RomaneioBeta/tests/test_provider_regressions.py", line 5, in g'
    )
    assert er._traceback_is_test_originated('  File "conftest.py", line 1, in h')
    # Caminhos de produção não devem ser detectados como teste.
    assert not er._traceback_is_test_originated(
        '  File "app/cotacao/orchestrator.py", line 1101, in _executar_cotacoes_com_dados'
    )
    assert not er._traceback_is_test_originated("")
