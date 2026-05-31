import sys
from pathlib import Path


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

from cotacao.error_context import report_provider_error, sanitize_context


def test_sanitize_context_redacts_credentials_documents_nfe_and_html():
    raw = {
        "senha": "segredo",
        "token": "tok_123",
        "login": "cliente@example.com",
        "cnpj_destinatario": "12.345.678/0001-90",
        "cpf": "123.456.789-01",
        "chave_nfe": "1" * 44,
        "html": "<html><body>segredo</body></html>",
        "observacao": "email cliente@example.com senha=abc cnpj 12345678000190 cpf 12345678901 nfe " + ("2" * 44),
    }

    sanitized = sanitize_context(raw)
    rendered = str(sanitized)

    assert sanitized["senha"] == "[DADO_SENSIVEL_REMOVIDO]"
    assert sanitized["token"] == "[DADO_SENSIVEL_REMOVIDO]"
    assert sanitized["login"] == "[DADO_SENSIVEL_REMOVIDO]"
    assert sanitized["html"] == "[HTML_REMOVIDO]"
    assert "segredo" not in rendered
    assert "tok_123" not in rendered
    assert "cliente@example.com" not in rendered
    assert "12.345.678/0001-90" not in rendered
    assert "12345678000190" not in rendered
    assert "123.456.789-01" not in rendered
    assert "12345678901" not in rendered
    assert "1" * 44 not in rendered
    assert "2" * 44 not in rendered
    assert "[CNPJ_REMOVIDO]" in rendered
    assert "[CPF_REMOVIDO]" in rendered
    assert "[CHAVE_NFE_REMOVIDA]" in rendered


def test_report_provider_error_builds_standard_payload_and_never_raises(monkeypatch):
    sent = []

    monkeypatch.setattr("cotacao.common.report_error_payload", lambda payload: sent.append(payload))

    try:
        raise RuntimeError("falha com senha=segredo e CNPJ 12.345.678/0001-90")
    except RuntimeError as exc:
        report_provider_error(
            "TRD",
            "prelogin",
            "Falha login cliente@example.com token=abc",
            exception=exc,
            context={
                "source": "prelogin_background",
                "carrier_enabled": True,
                "kwargs": {
                    "senha": "segredo",
                    "cnpj_destinatario": "12345678000190",
                },
                "browser_state": {
                    "html": "<html><body>raw</body></html>",
                },
            },
        )

    assert len(sent) == 1
    payload = sent[0]
    rendered = str(payload)
    assert payload["module"] == "cotacao"
    assert payload["provider"] == "trd"
    assert payload["stage"] == "pre_login"
    assert payload["event"] == "trd_pre_login_failed"
    assert payload["severity"] == "error"
    assert payload["source"] == "prelogin_background"
    assert payload["carrier_enabled"] is True
    assert payload["browser_state_json"]["html"] == "[HTML_REMOVIDO]"
    assert "segredo" not in rendered
    assert "cliente@example.com" not in rendered
    assert "12345678000190" not in rendered
