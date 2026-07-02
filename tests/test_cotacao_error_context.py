import sys
from pathlib import Path


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

from cotacao.error_context import build_quotation_error_diagnostic, report_provider_error, sanitize_context


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

    monkeypatch.setattr("cotacao.error_context.report_error_payload", lambda payload: sent.append(payload))

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


def test_build_quotation_error_diagnostic_uses_safe_flags_and_job_id():
    diagnostic = build_quotation_error_diagnostic(
        provider="EUCATUR",
        stage="preenchendo_formulario",
        source_type="romaneio",
        quote_job_id="123",
        dados={
            "cep_origem": "99740-000",
            "destino_cep": "01001-000",
            "uf_destino": "SP",
            "cnpj_destinatario": "12.345.678/0001-90",
            "peso": 12.5,
            "valor": 1000,
            "volumes": 3,
            "cubagens": [
                {"quantidade": 1, "comprimento_cm": 50, "largura_cm": 40, "altura_cm": 30},
                {"quantidade": 2, "comprimento_cm": 30, "largura_cm": 20, "altura_cm": 10},
            ],
            "senha": "segredo",
        },
        provider_context={"headless": True},
        last_error="Timeout Playwright aguardando locator #cuba1",
    )

    assert diagnostic["diagnostic_version"] == 1
    assert diagnostic["flow"] == "cotacao"
    assert diagnostic["source_type"] == "romaneio"
    assert diagnostic["provider"] == "eucatur"
    assert diagnostic["stage"] == "preencher_cubagem"
    assert diagnostic["quote_job_id"] == 123
    assert diagnostic["data_flags"] == {
        "cep_origem_ok": True,
        "cep_destino_ok": True,
        "uf_destino_ok": True,
        "cnpj_destinatario_ok": True,
        "peso_ok": True,
        "valor_ok": True,
        "cubagens_count": 2,
        "volumes_total": 3,
        "has_cubagens": True,
    }
    assert diagnostic["provider_context"]["provider_key"] == "eucatur"
    assert diagnostic["provider_context"]["stage"] == "preencher_cubagem"
    assert diagnostic["provider_context"]["error_type"] == "selector_timeout"
    assert diagnostic["provider_context"]["last_error_kind"] == "playwright_timeout"
    assert diagnostic["safe_hints"]["headless"] is True

    rendered = str(diagnostic)
    assert "12.345.678/0001-90" not in rendered
    assert "12345678000190" not in rendered
    assert "segredo" not in rendered


def test_report_provider_error_embeds_standard_quotation_diagnostic(monkeypatch):
    sent = []
    monkeypatch.setattr("cotacao.error_context.report_error_payload", lambda payload: sent.append(payload))

    diagnostic = build_quotation_error_diagnostic(
        provider="COOPEX",
        stage="submetendo_cotacao",
        source_type="manual",
        quote_job_id=456,
        kwargs={
            "origem": "99740000",
            "destino": "01001000",
            "uf_destino": "SP",
            "cnpj_destinatario": "12345678000190",
            "peso": 5,
            "valor": 10,
            "volumes": 1,
            "cubagens": [{"quantidade": 1}],
        },
        last_error="Portal respondeu sem valor de frete",
    )
    report_provider_error(
        "COOPEX",
        "submetendo_cotacao",
        "Falha sem senha=abc",
        context={
            **diagnostic,
            "source": "cotacao_usuario",
            "carrier_enabled": True,
        },
    )

    payload = sent[0]
    context = payload["context_json"]
    assert payload["stage"] == "submeter_cotacao"
    assert context["diagnostic_version"] == 1
    assert context["source_type"] == "manual"
    assert context["quote_job_id"] == 456
    assert context["data_flags"]["cnpj_destinatario_ok"] is True
    assert context["provider_context"]["provider_key"] == "coopex"
    assert "abc" not in str(payload)
