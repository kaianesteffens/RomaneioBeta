"""Testes para fretio.quotation_contract."""

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

import pytest
from fretio.quotation_contract import (
    QuoteRequest,
    QuoteResponse,
    normalize_quote_status,
    sanitize_raw_payload,
    quote_request_from_legacy_kwargs,
    cotacao_legada_to_quote_response,
    resultado_cotacao_to_quote_response,
    quote_response_to_resultado_cotacao,
)


# ---------------------------------------------------------------------------
# TestNormalizeStatus
# ---------------------------------------------------------------------------

class TestNormalizeStatus:
    def test_ok_returns_ok(self):
        assert normalize_quote_status("ok") == "ok"

    def test_erro_returns_erro(self):
        assert normalize_quote_status("erro") == "erro"

    def test_sem_cotacao_returns_sem_cotacao(self):
        assert normalize_quote_status("sem_cotacao") == "sem_cotacao"

    def test_nao_atendido_returns_nao_atendido(self):
        assert normalize_quote_status("nao_atendido") == "nao_atendido"

    def test_desabilitada_returns_desabilitada(self):
        assert normalize_quote_status("desabilitada") == "desabilitada"

    # Aliases
    def test_alias_error_returns_erro(self):
        assert normalize_quote_status("error") == "erro"

    def test_alias_disabled_returns_desabilitada(self):
        assert normalize_quote_status("disabled") == "desabilitada"

    def test_alias_no_quote_returns_sem_cotacao(self):
        assert normalize_quote_status("no_quote") == "sem_cotacao"

    def test_alias_desativada_returns_desabilitada(self):
        assert normalize_quote_status("desativada") == "desabilitada"

    def test_alias_desabilitado_returns_desabilitada(self):
        assert normalize_quote_status("desabilitado") == "desabilitada"

    def test_alias_sem_resultado_returns_sem_cotacao(self):
        assert normalize_quote_status("sem_resultado") == "sem_cotacao"

    def test_alias_not_served_returns_nao_atendido(self):
        assert normalize_quote_status("not_served") == "nao_atendido"

    def test_alias_not_supported_returns_nao_atendido(self):
        assert normalize_quote_status("not_supported") == "nao_atendido"

    # Prefix rule
    def test_starts_with_erro_returns_erro(self):
        assert normalize_quote_status("erro_de_negocio") == "erro"

    def test_starts_with_erro_exact_match_wins(self):
        # "erro" itself is in ALLOWED_QUOTE_STATUS, so exact match path
        assert normalize_quote_status("erro") == "erro"

    # Case insensitivity
    def test_uppercase_ok_returns_ok(self):
        assert normalize_quote_status("OK") == "ok"

    def test_mixed_case_erro_returns_erro(self):
        assert normalize_quote_status("Erro") == "erro"

    # Whitespace handling
    def test_status_with_leading_trailing_spaces(self):
        assert normalize_quote_status("  ok  ") == "ok"

    # Invalid / unknown
    def test_unknown_status_returns_none(self):
        assert normalize_quote_status("totally_unknown") is None

    def test_empty_string_returns_none(self):
        assert normalize_quote_status("") is None

    def test_none_returns_none(self):
        assert normalize_quote_status(None) is None

    def test_integer_unknown_returns_none(self):
        assert normalize_quote_status(99) is None


# ---------------------------------------------------------------------------
# TestSanitize
# ---------------------------------------------------------------------------

class TestSanitize:
    def test_senha_is_redacted(self):
        result = sanitize_raw_payload({"senha": "minha_senha"})
        assert result["senha"] == "***"

    def test_password_is_redacted(self):
        result = sanitize_raw_payload({"password": "abc123"})
        assert result["password"] == "***"

    def test_token_is_redacted(self):
        result = sanitize_raw_payload({"token": "abc-token"})
        assert result["token"] == "***"

    def test_cnpj_is_redacted(self):
        result = sanitize_raw_payload({"cnpj": "12345678000190"})
        assert result["cnpj"] == "***"

    def test_cookie_is_redacted(self):
        result = sanitize_raw_payload({"cookie": "session=abc"})
        assert result["cookie"] == "***"

    def test_secret_is_redacted(self):
        result = sanitize_raw_payload({"secret": "s3cr3t"})
        assert result["secret"] == "***"

    def test_api_key_is_redacted(self):
        result = sanitize_raw_payload({"api_key": "k123"})
        assert result["api_key"] == "***"

    def test_authorization_is_redacted(self):
        result = sanitize_raw_payload({"authorization": "Bearer xyz"})
        assert result["authorization"] == "***"

    def test_bearer_is_redacted(self):
        result = sanitize_raw_payload({"bearer": "token123"})
        assert result["bearer"] == "***"

    def test_cpf_is_redacted(self):
        result = sanitize_raw_payload({"cpf": "12345678901"})
        assert result["cpf"] == "***"

    def test_apikey_is_redacted(self):
        result = sanitize_raw_payload({"apikey": "k456"})
        assert result["apikey"] == "***"

    def test_non_sensitive_provider_preserved(self):
        result = sanitize_raw_payload({"provider": "JADLOG"})
        assert result["provider"] == "JADLOG"

    def test_non_sensitive_valor_preserved(self):
        result = sanitize_raw_payload({"valor": 150.00})
        assert result["valor"] == 150.00

    def test_mixed_payload_selectively_redacts(self):
        payload = {"provider": "ABC", "senha": "s3cr3t", "valor": 50.0}
        result = sanitize_raw_payload(payload)
        assert result["provider"] == "ABC"
        assert result["senha"] == "***"
        assert result["valor"] == 50.0

    def test_nested_dict_sensitive_field_redacted(self):
        payload = {"data": {"token": "abc", "name": "teste"}}
        result = sanitize_raw_payload(payload)
        assert result["data"]["token"] == "***"
        assert result["data"]["name"] == "teste"

    def test_nested_dict_multiple_levels(self):
        payload = {"outer": {"inner": {"senha": "deep"}}}
        result = sanitize_raw_payload(payload)
        assert result["outer"]["inner"]["senha"] == "***"

    def test_list_of_dicts_sanitized(self):
        payload = [{"senha": "s1"}, {"token": "t1"}, {"valor": 10}]
        result = sanitize_raw_payload(payload)
        assert result[0]["senha"] == "***"
        assert result[1]["token"] == "***"
        assert result[2]["valor"] == 10

    def test_tuple_of_dicts_sanitized(self):
        payload = ({"senha": "s1"},)
        result = sanitize_raw_payload(payload)
        assert result[0]["senha"] == "***"

    def test_none_payload_returns_none(self):
        # sanitize_raw_payload(None) should not raise; returns None (not a Mapping/list/tuple)
        result = sanitize_raw_payload(None)
        assert result is None

    def test_non_dict_scalar_returns_unchanged(self):
        assert sanitize_raw_payload(42) == 42
        assert sanitize_raw_payload("plain_string") == "plain_string"

    def test_empty_dict_returns_empty_dict(self):
        assert sanitize_raw_payload({}) == {}

    def test_case_insensitive_key_matching(self):
        # Key fragments are matched lowercase — "TOKEN" should still be redacted
        result = sanitize_raw_payload({"TOKEN": "abc"})
        assert result["TOKEN"] == "***"

    def test_partial_key_match_redacts(self):
        # "access_token" contains "token" fragment
        result = sanitize_raw_payload({"access_token": "abc"})
        assert result["access_token"] == "***"


# ---------------------------------------------------------------------------
# TestQuoteResponse
# ---------------------------------------------------------------------------

class TestQuoteResponse:
    def test_ok_classmethod_creates_ok_response(self):
        resp = QuoteResponse.ok(provider="JADLOG", valor_frete=120.5, prazo_dias=3)
        assert resp.status == "ok"
        assert resp.valor_frete == 120.5
        assert resp.prazo_dias == 3
        assert resp.provider == "JADLOG"

    def test_ok_coerces_valor_frete_to_float(self):
        resp = QuoteResponse.ok(provider="X", valor_frete="99", prazo_dias=2)
        assert isinstance(resp.valor_frete, float)
        assert resp.valor_frete == 99.0

    def test_ok_coerces_prazo_dias_to_int(self):
        resp = QuoteResponse.ok(provider="X", valor_frete=50.0, prazo_dias="5")
        assert isinstance(resp.prazo_dias, int)
        assert resp.prazo_dias == 5

    def test_ok_optional_fields_default_to_none(self):
        resp = QuoteResponse.ok(provider="X", valor_frete=10.0, prazo_dias=1)
        assert resp.detalhes is None
        assert resp.duration_ms is None
        assert resp.stage is None

    def test_error_classmethod_creates_erro_response(self):
        resp = QuoteResponse.error(provider="JADLOG", detalhes="Timeout")
        assert resp.status == "erro"
        assert resp.provider == "JADLOG"
        assert resp.detalhes == "Timeout"

    def test_error_valor_frete_defaults_to_none(self):
        resp = QuoteResponse.error(provider="X")
        assert resp.valor_frete is None
        assert resp.prazo_dias is None

    def test_error_with_error_code(self):
        resp = QuoteResponse.error(provider="X", error_code="TIMEOUT_LOGIN")
        assert resp.error_code == "TIMEOUT_LOGIN"

    def test_disabled_classmethod_creates_desabilitada_response(self):
        resp = QuoteResponse.disabled(provider="CARRIER")
        assert resp.status == "desabilitada"
        assert resp.provider == "CARRIER"

    def test_no_quote_defaults_to_sem_cotacao(self):
        resp = QuoteResponse.no_quote(provider="X")
        assert resp.status == "sem_cotacao"

    def test_no_quote_accepts_nao_atendido(self):
        resp = QuoteResponse.no_quote(provider="X", status="nao_atendido")
        assert resp.status == "nao_atendido"

    def test_no_quote_rejects_invalid_status(self):
        with pytest.raises(ValueError):
            QuoteResponse.no_quote(provider="X", status="ok")

    def test_no_quote_rejects_erro_status(self):
        with pytest.raises(ValueError):
            QuoteResponse.no_quote(provider="X", status="erro")

    def test_constructor_normalizes_status_alias(self):
        resp = QuoteResponse(provider="X", status="error")  # type: ignore[arg-type]
        assert resp.status == "erro"

    def test_constructor_raises_for_invalid_status(self):
        with pytest.raises(ValueError, match="status inválido"):
            QuoteResponse(provider="X", status="invalid_status")  # type: ignore[arg-type]

    def test_constructor_strips_provider_whitespace(self):
        resp = QuoteResponse(provider="  JADLOG  ", status="ok")
        assert resp.provider == "JADLOG"

    def test_constructor_raw_is_sanitized(self):
        resp = QuoteResponse(provider="X", status="ok", raw={"senha": "abc", "valor": 1.0})
        assert resp.raw["senha"] == "***"
        assert resp.raw["valor"] == 1.0

    def test_constructor_raw_none_stays_none(self):
        resp = QuoteResponse(provider="X", status="ok", raw=None)
        assert resp.raw is None


# ---------------------------------------------------------------------------
# TestQuoteRequest
# ---------------------------------------------------------------------------

class TestQuoteRequest:
    def _make_request(self, **overrides):
        defaults = dict(
            origem_cep="01310100",
            destino_cep="90040040",
            uf_destino="RS",
            cnpj_destinatario="12345678000190",
            peso_total_kg=10.0,
            valor_nf=500.0,
            volumes=2,
            cubagem_m3=0.05,
            cubagens=[],
        )
        defaults.update(overrides)
        return QuoteRequest(**defaults)

    def test_to_legacy_kwargs_maps_fields(self):
        req = self._make_request()
        kwargs = req.to_legacy_kwargs()
        assert kwargs["origem"] == "01310100"
        assert kwargs["destino"] == "90040040"
        assert kwargs["peso"] == 10.0
        assert kwargs["valor"] == 500.0
        assert kwargs["uf_destino"] == "RS"
        assert kwargs["volumes"] == 2
        assert kwargs["cubagem_m3"] == 0.05
        assert kwargs["cnpj_destinatario"] == "12345678000190"

    def test_to_legacy_kwargs_excludes_tipo_frete_when_empty(self):
        req = self._make_request(tipo_frete="")
        kwargs = req.to_legacy_kwargs()
        assert "tipo_frete" not in kwargs

    def test_to_legacy_kwargs_includes_tipo_frete_when_set(self):
        req = self._make_request(tipo_frete="CIF")
        kwargs = req.to_legacy_kwargs()
        assert kwargs["tipo_frete"] == "CIF"

    def test_to_legacy_kwargs_merges_metadata_legacy_kwargs(self):
        req = self._make_request(metadata={"legacy_kwargs": {"extra_field": "extra_value"}})
        kwargs = req.to_legacy_kwargs()
        assert kwargs["extra_field"] == "extra_value"

    def test_to_legacy_kwargs_does_not_overwrite_core_fields_from_metadata(self):
        req = self._make_request(
            origem_cep="01310100",
            metadata={"legacy_kwargs": {"origem": "99999999"}},
        )
        kwargs = req.to_legacy_kwargs()
        assert kwargs["origem"] == "01310100"

    def test_to_legacy_kwargs_cubagens_preserved(self):
        cubagens = [{"comprimento": 10, "largura": 5, "altura": 2, "quantidade": 1}]
        req = self._make_request(cubagens=cubagens)
        assert req.to_legacy_kwargs()["cubagens"] == cubagens


# ---------------------------------------------------------------------------
# TestQuoteRequestFromLegacyKwargs
# ---------------------------------------------------------------------------

class TestQuoteRequestFromLegacyKwargs:
    def test_basic_conversion(self):
        kwargs = {
            "origem": "01310100",
            "destino": "90040040",
            "peso": 15.0,
            "valor": 700.0,
            "uf_destino": "RS",
            "volumes": 3,
            "cubagem_m3": 0.1,
            "cubagens": [],
            "cnpj_destinatario": "12345678000190",
        }
        req = quote_request_from_legacy_kwargs(kwargs)
        assert req.origem_cep == "01310100"
        assert req.destino_cep == "90040040"
        assert req.peso_total_kg == 15.0
        assert req.valor_nf == 700.0
        assert req.uf_destino == "RS"
        assert req.volumes == 3
        assert req.cubagem_m3 == 0.1
        assert req.cnpj_destinatario == "12345678000190"

    def test_missing_fields_default_to_zero_or_empty(self):
        req = quote_request_from_legacy_kwargs({})
        assert req.origem_cep == ""
        assert req.destino_cep == ""
        assert req.peso_total_kg == 0.0
        assert req.valor_nf == 0.0
        assert req.volumes == 0
        assert req.cubagem_m3 == 0.0
        assert req.cubagens == []

    def test_uf_destino_fallback_from_parameter(self):
        req = quote_request_from_legacy_kwargs({}, uf_destino="SP")
        assert req.uf_destino == "SP"

    def test_uf_destino_from_kwargs_takes_priority(self):
        req = quote_request_from_legacy_kwargs({"uf_destino": "RJ"}, uf_destino="SP")
        assert req.uf_destino == "RJ"

    def test_cnpj_fallback_from_parameter(self):
        req = quote_request_from_legacy_kwargs({}, cnpj_destinatario="98765432000100")
        assert req.cnpj_destinatario == "98765432000100"

    def test_extra_fields_stored_in_metadata(self):
        req = quote_request_from_legacy_kwargs({"origem": "01310100", "custom_field": "custom_value"})
        assert req.metadata is not None
        assert req.metadata["legacy_kwargs"]["custom_field"] == "custom_value"

    def test_no_extra_fields_metadata_is_none(self):
        req = quote_request_from_legacy_kwargs({"origem": "01310100", "destino": "90040040"})
        assert req.metadata is None

    def test_cubagens_list_of_dicts_preserved(self):
        cubagens = [{"comprimento": 10, "largura": 5, "altura": 2, "quantidade": 1}]
        req = quote_request_from_legacy_kwargs({"cubagens": cubagens})
        assert req.cubagens == cubagens

    def test_cubagens_non_list_defaults_to_empty(self):
        req = quote_request_from_legacy_kwargs({"cubagens": "not_a_list"})
        assert req.cubagens == []

    def test_cubagens_non_dict_items_filtered_out(self):
        req = quote_request_from_legacy_kwargs({"cubagens": [{"a": 1}, "bad_item", 42]})
        assert req.cubagens == [{"a": 1}]

    def test_tipo_frete_preserved(self):
        req = quote_request_from_legacy_kwargs({"tipo_frete": "CIF"})
        assert req.tipo_frete == "CIF"

    def test_numeric_string_peso_coerced(self):
        req = quote_request_from_legacy_kwargs({"peso": "12.5"})
        assert req.peso_total_kg == 12.5

    def test_numeric_string_volumes_coerced(self):
        req = quote_request_from_legacy_kwargs({"volumes": "4"})
        assert req.volumes == 4

    def test_roundtrip_to_legacy_kwargs(self):
        original = {
            "origem": "01310100",
            "destino": "90040040",
            "peso": 5.0,
            "valor": 200.0,
            "uf_destino": "SP",
            "volumes": 1,
            "cubagem_m3": 0.02,
            "cubagens": [],
            "cnpj_destinatario": "12345678000190",
        }
        req = quote_request_from_legacy_kwargs(original)
        back = req.to_legacy_kwargs()
        for key in ("origem", "destino", "peso", "valor", "uf_destino", "volumes", "cubagem_m3", "cnpj_destinatario"):
            assert back[key] == original[key], f"mismatch for {key}"


# ---------------------------------------------------------------------------
# TestCotacaoLegadaToQuoteResponse
# ---------------------------------------------------------------------------

class TestCotacaoLegadaToQuoteResponse:
    # Finding 4: refactored to use types.SimpleNamespace instead of inner class
    def _make_cotacao(self, **kwargs):
        defaults = {"transportadora": "TEST", "valor_frete": 99.90, "prazo_dias": 3, "restricoes": None}
        defaults.update(kwargs)
        return types.SimpleNamespace(**defaults)

    def test_none_cotacao_returns_sem_cotacao(self):
        resp = cotacao_legada_to_quote_response(None, provider="X")
        assert resp.status == "sem_cotacao"

    def test_none_cotacao_uses_provider_parameter(self):
        resp = cotacao_legada_to_quote_response(None, provider="JADLOG")
        assert resp.provider == "JADLOG"

    def test_cotacao_with_transportadora_uses_it(self):
        cotacao = self._make_cotacao(transportadora="RODONAVES", valor_frete=300.0, prazo_dias=5)
        resp = cotacao_legada_to_quote_response(cotacao)
        assert resp.provider == "RODONAVES"

    def test_cotacao_with_valor_frete_preserved(self):
        cotacao = self._make_cotacao(transportadora="X", valor_frete=150.75, prazo_dias=3)
        resp = cotacao_legada_to_quote_response(cotacao)
        assert resp.status == "ok"
        assert resp.valor_frete == 150.75

    def test_cotacao_with_prazo_dias_preserved(self):
        cotacao = self._make_cotacao(transportadora="X", valor_frete=100.0, prazo_dias=7)
        resp = cotacao_legada_to_quote_response(cotacao)
        assert resp.prazo_dias == 7

    def test_cotacao_with_restricoes_used_as_detalhes(self):
        cotacao = self._make_cotacao(transportadora="X", valor_frete=100.0, prazo_dias=2, restricoes="Restrição de rota")
        resp = cotacao_legada_to_quote_response(cotacao)
        assert resp.detalhes == "Restrição de rota"

    def test_cotacao_with_detalhes_used_when_no_restricoes(self):
        cotacao = self._make_cotacao(transportadora="X", valor_frete=100.0, prazo_dias=2, detalhes="Detalhe qualquer")
        resp = cotacao_legada_to_quote_response(cotacao)
        assert resp.detalhes == "Detalhe qualquer"

    def test_cotacao_fallback_to_provider_parameter_when_transportadora_empty(self):
        cotacao = self._make_cotacao(transportadora="", valor_frete=200.0, prazo_dias=1)
        resp = cotacao_legada_to_quote_response(cotacao, provider="FALLBACK")
        assert resp.provider == "FALLBACK"

    def test_duration_ms_passed_through(self):
        cotacao = self._make_cotacao(transportadora="X", valor_frete=50.0, prazo_dias=1)
        resp = cotacao_legada_to_quote_response(cotacao, duration_ms=350)
        assert resp.duration_ms == 350

    # Finding 3: test cotacao_legada_to_quote_response when valor_frete=None
    def test_cotacao_with_valor_frete_none_returns_ok_with_none_valor(self):
        cotacao = self._make_cotacao(transportadora="X", valor_frete=None, prazo_dias=None)
        resp = cotacao_legada_to_quote_response(cotacao)
        assert resp.status == "ok"
        assert resp.valor_frete is None


# ---------------------------------------------------------------------------
# TestResultadoCotacaoToQuoteResponse
# ---------------------------------------------------------------------------

class TestResultadoCotacaoToQuoteResponse:
    # Finding 4: refactored to use types.SimpleNamespace instead of inner class
    def _make_resultado(self, **kwargs):
        defaults = {"transportadora": "TEST", "valor_frete": None, "prazo_dias": None, "status": "ok"}
        defaults.update(kwargs)
        return types.SimpleNamespace(**defaults)

    def test_ok_status_preserved(self):
        resultado = self._make_resultado(
            transportadora="JADLOG", status="ok", valor_frete=200.0, prazo_dias=4
        )
        resp = resultado_cotacao_to_quote_response(resultado)
        assert resp.status == "ok"

    def test_erro_status_preserved(self):
        resultado = self._make_resultado(
            transportadora="X", status="erro", valor_frete=None, prazo_dias=None
        )
        resp = resultado_cotacao_to_quote_response(resultado)
        assert resp.status == "erro"

    def test_unknown_status_defaults_to_erro(self):
        resultado = self._make_resultado(
            transportadora="X", status="some_unknown_status"
        )
        resp = resultado_cotacao_to_quote_response(resultado)
        assert resp.status == "erro"
        # Finding 2: auto-derivation sets error_code to the original unknown status value
        assert resp.error_code == "some_unknown_status"

    def test_valor_frete_preserved_for_ok(self):
        resultado = self._make_resultado(
            transportadora="X", status="ok", valor_frete=333.33, prazo_dias=2
        )
        resp = resultado_cotacao_to_quote_response(resultado)
        assert resp.valor_frete == 333.33

    def test_prazo_dias_preserved_for_ok(self):
        resultado = self._make_resultado(
            transportadora="X", status="ok", valor_frete=100.0, prazo_dias=6
        )
        resp = resultado_cotacao_to_quote_response(resultado)
        assert resp.prazo_dias == 6

    def test_transportadora_mapped_to_provider(self):
        resultado = self._make_resultado(transportadora="RODONAVES", status="ok", valor_frete=1.0, prazo_dias=1)
        resp = resultado_cotacao_to_quote_response(resultado)
        assert resp.provider == "RODONAVES"

    def test_error_code_propagated(self):
        resultado = self._make_resultado(
            transportadora="X", status="erro", error_code="CAPTCHA_TIMEOUT"
        )
        resp = resultado_cotacao_to_quote_response(resultado)
        assert resp.error_code == "CAPTCHA_TIMEOUT"

    def test_alias_status_normalized(self):
        resultado = self._make_resultado(transportadora="X", status="error")
        resp = resultado_cotacao_to_quote_response(resultado)
        assert resp.status == "erro"
        # Finding 1: 'error' is an alias that normalizes to 'erro', but is not 'erro' itself,
        # so auto-derivation sets error_code to the original alias value 'error'
        assert resp.error_code == "error"

    def test_raw_override_takes_precedence(self):
        resultado = self._make_resultado(transportadora="X", status="ok", raw={"old": "data"})
        override = {"new": "data"}
        resp = resultado_cotacao_to_quote_response(resultado, raw=override)
        assert resp.raw == {"new": "data"}

    def test_raw_from_resultado_used_when_no_override(self):
        resultado = self._make_resultado(transportadora="X", status="ok", raw={"key": "value"})
        resp = resultado_cotacao_to_quote_response(resultado)
        assert resp.raw == {"key": "value"}


# ---------------------------------------------------------------------------
# TestQuoteResponseToResultadoCotacao
# ---------------------------------------------------------------------------

class TestQuoteResponseToResultadoCotacao:
    def test_returns_dict_when_no_class_provided(self):
        resp = QuoteResponse.ok(provider="JADLOG", valor_frete=100.0, prazo_dias=3)
        result = quote_response_to_resultado_cotacao(resp)
        assert isinstance(result, dict)

    def test_dict_has_status_field(self):
        resp = QuoteResponse.ok(provider="X", valor_frete=50.0, prazo_dias=2)
        result = quote_response_to_resultado_cotacao(resp)
        assert result["status"] == "ok"

    def test_dict_has_transportadora(self):
        resp = QuoteResponse.ok(provider="JADLOG", valor_frete=50.0, prazo_dias=2)
        result = quote_response_to_resultado_cotacao(resp)
        assert result["transportadora"] == "JADLOG"

    def test_dict_preserves_valor_frete_for_ok(self):
        resp = QuoteResponse.ok(provider="X", valor_frete=123.45, prazo_dias=5)
        result = quote_response_to_resultado_cotacao(resp)
        assert result["valor_frete"] == 123.45

    def test_dict_preserves_prazo_dias_for_ok(self):
        resp = QuoteResponse.ok(provider="X", valor_frete=10.0, prazo_dias=7)
        result = quote_response_to_resultado_cotacao(resp)
        assert result["prazo_dias"] == 7

    def test_erro_status_preserved_in_dict(self):
        resp = QuoteResponse.error(provider="X")
        result = quote_response_to_resultado_cotacao(resp)
        assert result["status"] == "erro"

    def test_erro_with_error_code_starting_with_erro_expands_status(self):
        resp = QuoteResponse(provider="X", status="erro", error_code="erro_captcha")
        result = quote_response_to_resultado_cotacao(resp)
        assert result["status"] == "erro_captcha"
        # Finding 5: error_code in payload must still hold the original value
        assert result["error_code"] == "erro_captcha"

    def test_error_code_not_starting_with_erro_keeps_status_as_erro(self):
        resp = QuoteResponse(provider="X", status="erro", error_code="TIMEOUT")
        result = quote_response_to_resultado_cotacao(resp)
        assert result["status"] == "erro"

    def test_resultado_cls_instantiated_with_payload(self):
        class SimpleResultado:
            def __init__(self, transportadora, status, valor_frete, prazo_dias, **kwargs):
                self.transportadora = transportadora
                self.status = status
                self.valor_frete = valor_frete
                self.prazo_dias = prazo_dias

        resp = QuoteResponse.ok(provider="JADLOG", valor_frete=200.0, prazo_dias=4)
        result = quote_response_to_resultado_cotacao(resp, resultado_cls=SimpleResultado)
        assert isinstance(result, SimpleResultado)
        assert result.transportadora == "JADLOG"
        assert result.status == "ok"
        assert result.valor_frete == 200.0
        assert result.prazo_dias == 4

    def test_resultado_cls_without_var_kwargs_filters_unknown_fields(self):
        class StrictResultado:
            def __init__(self, transportadora, status):
                self.transportadora = transportadora
                self.status = status

        resp = QuoteResponse.ok(provider="X", valor_frete=10.0, prazo_dias=1)
        # Should not raise even though the class does not accept valor_frete, prazo_dias, etc.
        result = quote_response_to_resultado_cotacao(resp, resultado_cls=StrictResultado)
        assert isinstance(result, StrictResultado)
        assert result.status == "ok"

    def test_detalhes_included_in_dict(self):
        resp = QuoteResponse.error(provider="X", detalhes="Falha de conexão")
        result = quote_response_to_resultado_cotacao(resp)
        assert result["detalhes"] == "Falha de conexão"

    def test_duration_ms_included_in_dict(self):
        resp = QuoteResponse(provider="X", status="ok", duration_ms=1200)
        result = quote_response_to_resultado_cotacao(resp)
        assert result["duration_ms"] == 1200

    def test_sem_cotacao_status_preserved(self):
        resp = QuoteResponse.no_quote(provider="X")
        result = quote_response_to_resultado_cotacao(resp)
        assert result["status"] == "sem_cotacao"
