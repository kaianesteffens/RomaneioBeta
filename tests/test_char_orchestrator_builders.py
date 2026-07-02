"""Characterization tests for app/cotacao/orchestrator.py.

These pin the CURRENT behavior of the orchestrator's pure transforms so a later
refactor (Phase 6 decomposition) that changes behavior is caught. They cover:

  * Per-carrier ``_build_*_kwargs`` builders (golden kwargs dicts), including the
    "modo fornecedor" (``cnpj_remetente`` set) branches that the audit flagged.
  * The result-dispatch conversion used by ``_processar_resultado`` /
    ``_run_cotacao`` (``QuoteResponse`` -> ``ResultadoCotacao`` mapping and the
    legacy-object mapping).
  * The module-level error classifiers ``_is_business_error`` /
    ``_is_expected_transient_failure`` / ``_is_expected_transient_failure_str``.
  * The security boundary: serialized ``raw`` payload must redact passwords and
    CNPJ (the contract is supposed to strip them).

NOTE: assertions encode what the code ACTUALLY does today, including anything
that looks like a latent bug (those are called out in comments, NOT fixed).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

from cotacao import orchestrator
from cotacao.common import (
    ResultadoCotacao,
    QuoteResponse,
    quote_response_to_resultado_cotacao,
)


# Shared minimal inputs -------------------------------------------------------

CUBS = [
    {
        "quantidade": 2,
        "comprimento_cm": 30,
        "largura_cm": 20,
        "altura_cm": 10,
        "peso_por_volume_kg": None,
    }
]


# ---------------------------------------------------------------------------
# BRASPRESS builder
# ---------------------------------------------------------------------------

def test_char_braspress_kwargs_normal_mode():
    """Pins BRASPRESS kwargs in normal (non-fornecedor) mode: first cubagem's
    dims are surfaced as comprimento/largura/altura_cm and no cnpj_remetente."""
    out = orchestrator._build_braspress_kwargs(
        cfg={"cnpj": "12345678000199", "senha": "s3nh4"},
        origem="99740000",
        destino="01001000",
        peso=12.5,
        valor=1000.0,
        cnpj_destinatario="11222333000181",
        volumes=2,
        cubagens_validas=CUBS,
        cnpj_remetente="",
        tipo_frete="",
        effective_config={},
    )
    assert out == {
        "origem": "99740000",
        "destino": "01001000",
        "peso": 12.5,
        "valor": 1000.0,
        "cnpj_destinatario": "11222333000181",
        "volumes": 2,
        "comprimento_cm": 30,
        "largura_cm": 20,
        "altura_cm": 10,
        "cubagens": CUBS,
    }


def test_char_braspress_kwargs_fornecedor_mode_adds_remetente_and_default_tipo_frete():
    """With cnpj_remetente set, adds cnpj_remetente and defaults tipo_frete to '2'
    when tipo_frete is empty."""
    out = orchestrator._build_braspress_kwargs(
        cfg={"cnpj": "12345678000199", "senha": "s3nh4"},
        origem="99740000",
        destino="01001000",
        peso=12.5,
        valor=1000.0,
        cnpj_destinatario="11222333000181",
        volumes=2,
        cubagens_validas=CUBS,
        cnpj_remetente="99888777000166",
        tipo_frete="",
        effective_config={},
    )
    assert out["cnpj_remetente"] == "99888777000166"
    assert out["tipo_frete"] == "2"


def test_char_braspress_kwargs_returns_none_without_credentials():
    """Missing cnpj/senha -> builder returns None (carrier skipped)."""
    assert (
        orchestrator._build_braspress_kwargs(
            cfg={"cnpj": "", "senha": ""},
            origem="99740000",
            destino="01001000",
            peso=12.5,
            valor=1000.0,
            cnpj_destinatario="11222333000181",
            volumes=2,
            cubagens_validas=CUBS,
            cnpj_remetente="",
            tipo_frete="",
            effective_config={},
        )
        is None
    )


# ---------------------------------------------------------------------------
# TRD builder
# ---------------------------------------------------------------------------

def test_char_trd_kwargs_normal_and_fornecedor_modes():
    base = dict(
        cfg={"email": "a@b.com", "senha": "s"},
        origem="99740000",
        destino="01001000",
        peso=12.5,
        valor=1000.0,
        volumes=2,
        cubagens_validas=CUBS,
        cnpj_destinatario="11222333000181",
        headless_trd=True,
    )
    normal = orchestrator._build_trd_kwargs(cnpj_remetente="", **base)
    assert normal == {
        "origem": "99740000",
        "destino": "01001000",
        "peso": 12.5,
        "valor": 1000.0,
        "volumes": 2,
        "cubagens": CUBS,
        "cnpj_destinatario": "11222333000181",
    }
    # Fornecedor mode adds cnpj_remetente AND sets cep_remetente = origem.
    forn = orchestrator._build_trd_kwargs(cnpj_remetente="99888777000166", **base)
    assert forn["cnpj_remetente"] == "99888777000166"
    assert forn["cep_remetente"] == "99740000"


def test_char_trd_kwargs_returns_none_without_credentials():
    assert (
        orchestrator._build_trd_kwargs(
            cfg={"email": "", "senha": ""},
            origem="99740000",
            destino="01001000",
            peso=1.0,
            valor=1.0,
            volumes=1,
            cubagens_validas=CUBS,
            cnpj_destinatario="11222333000181",
            cnpj_remetente="",
            headless_trd=True,
        )
        is None
    )


# ---------------------------------------------------------------------------
# EUCATUR builder (cnpj_pagador resolution is done by the caller; builder
# receives the already-resolved cnpj_pagador_euc value).
# ---------------------------------------------------------------------------

def test_char_eucatur_kwargs_normal_mode_uses_pagador_as_remetente():
    """In normal mode, cnpj_remetente AND cnpj_pagador both equal the resolved
    cnpj_pagador_euc; destino stays the real destination CEP."""
    out = orchestrator._build_eucatur_kwargs(
        cfg={"dominio": "EUC", "usuario": "u", "senha": "s"},
        origem="99740000",
        destino="01001000",
        peso=12.5,
        valor=1000.0,
        volumes=2,
        cubagem_m3=0.5,
        cubagens_validas=CUBS,
        cnpj_destinatario="11222333000181",
        cnpj_pagador_euc="55666777000188",
        cnpj_remetente="",
        effective_config={"romaneio": {"cep_origem": "88000000"}},
        headless_eucatur=True,
    )
    assert out["cnpj_remetente"] == "55666777000188"
    assert out["cnpj_pagador"] == "55666777000188"
    assert out["cnpj_destinatario"] == "11222333000181"
    assert out["destino"] == "01001000"
    assert "tipo_frete" not in out


def test_char_eucatur_kwargs_fornecedor_mode_overrides_destino_and_destinatario():
    """Fornecedor mode: cnpj_remetente becomes the supplier CNPJ, cnpj_destinatario
    is OVERWRITTEN with cnpj_pagador_euc, destino is resolved from config origem,
    and tipo_frete='2' is added. (cnpj_pagador stays = cnpj_pagador_euc.)"""
    out = orchestrator._build_eucatur_kwargs(
        cfg={"dominio": "EUC", "usuario": "u", "senha": "s"},
        origem="99740000",
        destino="01001000",
        peso=12.5,
        valor=1000.0,
        volumes=2,
        cubagem_m3=0.5,
        cubagens_validas=CUBS,
        cnpj_destinatario="11222333000181",
        cnpj_pagador_euc="55666777000188",
        cnpj_remetente="99888777000166",
        effective_config={"romaneio": {"cep_origem": "88000000"}},
        headless_eucatur=True,
    )
    assert out["cnpj_remetente"] == "99888777000166"
    assert out["cnpj_destinatario"] == "55666777000188"
    assert out["cnpj_pagador"] == "55666777000188"
    assert out["destino"] == "88000000"  # resolved from romaneio.cep_origem
    assert out["tipo_frete"] == "2"


def test_char_eucatur_kwargs_returns_none_without_credentials():
    assert (
        orchestrator._build_eucatur_kwargs(
            cfg={"dominio": "", "usuario": "", "senha": ""},
            origem="99740000",
            destino="01001000",
            peso=1.0,
            valor=1.0,
            volumes=1,
            cubagem_m3=0.1,
            cubagens_validas=CUBS,
            cnpj_destinatario="11222333000181",
            cnpj_pagador_euc="55666777000188",
            cnpj_remetente="",
            effective_config={},
            headless_eucatur=True,
        )
        is None
    )


# ---------------------------------------------------------------------------
# COOPEX builder (same SSW shape as Eucatur).
# ---------------------------------------------------------------------------

def test_char_coopex_kwargs_fornecedor_mode():
    out = orchestrator._build_coopex_kwargs(
        cfg={"dominio": "CO", "usuario": "u", "senha": "s"},
        origem="99740000",
        destino="01001000",
        peso=12.5,
        valor=1000.0,
        volumes=2,
        cubagem_m3=0.5,
        cubagens_validas=CUBS,
        cnpj_destinatario="11222333000181",
        cnpj_pagador_co="55666777000188",
        cnpj_remetente="99888777000166",
        effective_config={"romaneio": {"cep_origem": "88000000"}},
        headless_coopex=True,
    )
    assert out["cnpj_remetente"] == "99888777000166"
    assert out["cnpj_destinatario"] == "55666777000188"
    assert out["cnpj_pagador"] == "55666777000188"
    assert out["destino"] == "88000000"
    assert out["tipo_frete"] == "2"


# ---------------------------------------------------------------------------
# RODONAVES builder
# ---------------------------------------------------------------------------

def test_char_rodonaves_kwargs_digits_cnpj_and_preencher_cep_flag():
    """cnpj_pagador from cfg is reduced to digits and used as cnpj_remetente;
    preencher_cep_origem is True iff cep_origem normalizes to a non-empty CEP."""
    out = orchestrator._build_rodonaves_kwargs(
        cfg={
            "dominio": "RTE",
            "usuario": "u",
            "senha": "s",
            "cnpj_pagador": "55.666.777/0001-88",
        },
        origem="99740000",
        destino="01001000",
        peso=12.5,
        valor=1000.0,
        volumes=2,
        cubagem_m3=0.5,
        cubagens_validas=CUBS,
        cnpj_destinatario="11222333000181",
        cep_origem="99740-000",
        headless_rodonaves=False,
    )
    assert out["cnpj_remetente"] == "55666777000188"
    assert out["preencher_cep_origem"] is True


def test_char_rodonaves_kwargs_no_cep_sets_flag_false():
    out = orchestrator._build_rodonaves_kwargs(
        cfg={
            "dominio": "RTE",
            "usuario": "u",
            "senha": "s",
            "cnpj_pagador": "55666777000188",
        },
        origem="99740000",
        destino="01001000",
        peso=12.5,
        valor=1000.0,
        volumes=2,
        cubagem_m3=0.5,
        cubagens_validas=CUBS,
        cnpj_destinatario="11222333000181",
        cep_origem="",
        headless_rodonaves=False,
    )
    assert out["preencher_cep_origem"] is False


def test_char_rodonaves_kwargs_returns_none_for_short_cnpj_pagador():
    """cnpj_pagador with != 14 digits -> None (carrier skipped)."""
    assert (
        orchestrator._build_rodonaves_kwargs(
            cfg={"dominio": "RTE", "usuario": "u", "senha": "s", "cnpj_pagador": "123"},
            origem="99740000",
            destino="01001000",
            peso=12.5,
            valor=1000.0,
            volumes=2,
            cubagem_m3=0.5,
            cubagens_validas=CUBS,
            cnpj_destinatario="11222333000181",
            cep_origem="99740000",
            headless_rodonaves=False,
        )
        is None
    )


# ---------------------------------------------------------------------------
# ALFA builder
# ---------------------------------------------------------------------------

def test_char_alfa_kwargs_normal_mode_uses_cfg_remetente():
    out = orchestrator._build_alfa_kwargs(
        cfg={"login": "l", "senha": "s", "cnpj_remetente": "12345678000199"},
        origem="99740000",
        destino="01001000",
        peso=12.5,
        valor=1000.0,
        volumes=2,
        cubagem_m3=0.5,
        cubagens_validas=CUBS,
        cnpj_destinatario="11222333000181",
        cnpj_remetente="",
        effective_config={"romaneio": {"cep_origem": "88000000"}},
        headless_alfa=False,
    )
    assert out["cnpj_remetente"] == "12345678000199"
    assert out["cnpj_destinatario"] == "11222333000181"
    assert out["destino"] == "01001000"
    assert "tipo_pagador" not in out


def test_char_alfa_kwargs_fornecedor_mode_overrides_and_adds_tipo_pagador():
    """Fornecedor mode: cnpj_remetente=supplier, cnpj_destinatario=cfg cnpj_remetente,
    destino resolved from config, tipo_pagador='2' added."""
    out = orchestrator._build_alfa_kwargs(
        cfg={"login": "l", "senha": "s", "cnpj_remetente": "12345678000199"},
        origem="99740000",
        destino="01001000",
        peso=12.5,
        valor=1000.0,
        volumes=2,
        cubagem_m3=0.5,
        cubagens_validas=CUBS,
        cnpj_destinatario="11222333000181",
        cnpj_remetente="99888777000166",
        effective_config={"romaneio": {"cep_origem": "88000000"}},
        headless_alfa=False,
    )
    assert out["cnpj_remetente"] == "99888777000166"
    assert out["cnpj_destinatario"] == "12345678000199"
    assert out["destino"] == "88000000"
    assert out["tipo_pagador"] == "2"


def test_char_alfa_kwargs_returns_none_without_cnpj_remetente_in_cfg():
    assert (
        orchestrator._build_alfa_kwargs(
            cfg={"login": "l", "senha": "s", "cnpj_remetente": ""},
            origem="99740000",
            destino="01001000",
            peso=1.0,
            valor=1.0,
            volumes=1,
            cubagem_m3=0.1,
            cubagens_validas=CUBS,
            cnpj_destinatario="11222333000181",
            cnpj_remetente="",
            effective_config={},
            headless_alfa=False,
        )
        is None
    )


# ---------------------------------------------------------------------------
# TRANSLOVATO builder
# ---------------------------------------------------------------------------

def test_char_translovato_kwargs_normal_mode_falls_back_to_cfg_cnpj():
    """cnpj is digit-normalized; without a supplier cnpj_remetente, cnpj_remetente
    falls back to cfg cnpj. cep_origem/cep_destino mirror origem/destino."""
    out = orchestrator._build_translovato_kwargs(
        cfg={"cnpj": "12.345.678/0001-99", "usuario": "u", "senha": "s"},
        origem="99740000",
        destino="01001000",
        peso=12.5,
        valor=1000.0,
        volumes=2,
        cubagem_m3=0.5,
        cubagens_validas=CUBS,
        cnpj_destinatario="11222333000181",
        cnpj_remetente="",
        uf_destino="SP",
        cidade_destino="Sao Paulo",
        headless_translovato=True,
    )
    assert out["cep_origem"] == "99740000"
    assert out["cep_destino"] == "01001000"
    assert out["uf_destino"] == "SP"
    assert out["cidade_destino"] == "Sao Paulo"
    assert out["cnpj_remetente"] == "12345678000199"  # fallback to cfg cnpj


def test_char_translovato_kwargs_fornecedor_uses_supplier_cnpj():
    out = orchestrator._build_translovato_kwargs(
        cfg={"cnpj": "12345678000199", "usuario": "u", "senha": "s"},
        origem="99740000",
        destino="01001000",
        peso=12.5,
        valor=1000.0,
        volumes=2,
        cubagem_m3=0.5,
        cubagens_validas=CUBS,
        cnpj_destinatario="11222333000181",
        cnpj_remetente="99888777000166",
        uf_destino="SP",
        cidade_destino="Sao Paulo",
        headless_translovato=True,
    )
    assert out["cnpj_remetente"] == "99888777000166"


def test_char_translovato_kwargs_returns_none_for_short_cnpj():
    assert (
        orchestrator._build_translovato_kwargs(
            cfg={"cnpj": "123", "usuario": "u", "senha": "s"},
            origem="99740000",
            destino="01001000",
            peso=1.0,
            valor=1.0,
            volumes=1,
            cubagem_m3=0.1,
            cubagens_validas=CUBS,
            cnpj_destinatario="11222333000181",
            cnpj_remetente="",
            uf_destino="SP",
            cidade_destino="Sao Paulo",
            headless_translovato=True,
        )
        is None
    )


# ---------------------------------------------------------------------------
# AGEX email legacy-fallback.
#
# The audit flagged this. It currently lives INLINE inside
# _executar_cotacoes_com_dados (orchestrator.py lines ~943-948), not in a
# dedicated builder, so it cannot be imported. This test pins the exact branch
# behavior by reproducing the production expression verbatim. If Phase 6
# extracts it into a real helper, RE-POINT this test at that helper rather than
# at this local copy.
# ---------------------------------------------------------------------------

def _agex_email_resolution(acfg):
    # Verbatim copy of orchestrator.py lines ~943-948.
    email_agex = str(acfg.get("email", "")).strip()
    if not email_agex:
        legacy_login = str(acfg.get("cnpj", "")).strip()
        if "@" in legacy_login:
            email_agex = legacy_login
    return email_agex


def test_char_agex_email_uses_explicit_email_when_present():
    assert (
        _agex_email_resolution({"email": "real@x.com", "cnpj": "12345678000199"})
        == "real@x.com"
    )


def test_char_agex_email_falls_back_to_cnpj_field_when_it_contains_at():
    """Legacy installs stored the login (an e-mail) in the 'cnpj' field; when
    'email' is empty and 'cnpj' contains '@', it is used as the e-mail."""
    assert _agex_email_resolution({"email": "", "cnpj": "login@x.com"}) == "login@x.com"


def test_char_agex_email_empty_when_cnpj_has_no_at():
    assert _agex_email_resolution({"email": "", "cnpj": "12345678000199"}) == ""


# ---------------------------------------------------------------------------
# Result dispatch: QuoteResponse -> ResultadoCotacao.
# This is the conversion _processar_resultado / _run_cotacao apply to a
# QuoteResponse returned by a provider.
# ---------------------------------------------------------------------------

def test_char_dispatch_quote_response_ok_maps_value_prazo_stage():
    qr = QuoteResponse.ok(
        provider="BRASPRESS", valor_frete=123.45, prazo_dias=5, stage="ler_resultado"
    )
    r = quote_response_to_resultado_cotacao(qr, resultado_cls=ResultadoCotacao)
    assert isinstance(r, ResultadoCotacao)
    assert r.status == "ok"
    assert r.valor_frete == 123.45
    assert r.prazo_dias == 5
    assert r.stage == "ler_resultado"
    assert r.error_code is None


def test_char_dispatch_error_with_erro_prefixed_code_promotes_status():
    """When status is 'erro' and error_code starts with 'erro', the final
    ResultadoCotacao.status is REPLACED by the (lowercased) error_code."""
    qr = QuoteResponse.error(
        provider="TRD", detalhes="boom", error_code="erro_login", stage="login"
    )
    r = quote_response_to_resultado_cotacao(qr, resultado_cls=ResultadoCotacao)
    assert r.status == "erro_login"
    assert r.error_code == "erro_login"
    assert r.detalhes == "boom"


def test_char_dispatch_error_with_nonprefixed_code_keeps_status_erro():
    """error_code that does NOT start with 'erro' (e.g. 'timeout') leaves status
    as 'erro' but is still preserved in error_code."""
    qr = QuoteResponse.error(provider="TRD", detalhes="boom", error_code="timeout")
    r = quote_response_to_resultado_cotacao(qr, resultado_cls=ResultadoCotacao)
    assert r.status == "erro"
    assert r.error_code == "timeout"


def test_char_dispatch_nao_atendido_and_sem_cotacao_and_disabled_passthrough():
    nao = quote_response_to_resultado_cotacao(
        QuoteResponse.no_quote(provider="AGEX", detalhes="x", status="nao_atendido"),
        resultado_cls=ResultadoCotacao,
    )
    assert nao.status == "nao_atendido"

    sem = quote_response_to_resultado_cotacao(
        QuoteResponse.no_quote(provider="AGEX", detalhes="x", status="sem_cotacao"),
        resultado_cls=ResultadoCotacao,
    )
    assert sem.status == "sem_cotacao"

    off = quote_response_to_resultado_cotacao(
        QuoteResponse.disabled(provider="AGEX", detalhes="x"),
        resultado_cls=ResultadoCotacao,
    )
    assert off.status == "desabilitada"


def test_char_dispatch_preserves_duration_ms():
    qr = QuoteResponse.ok(provider="X", valor_frete=1.0, prazo_dias=1, duration_ms=4200)
    r = quote_response_to_resultado_cotacao(qr, resultado_cls=ResultadoCotacao)
    assert r.duration_ms == 4200


def test_char_dispatch_legacy_result_object_mapping():
    """Pins the legacy-object path in _processar_resultado: a non-QuoteResponse
    object is read via getattr(transportadora/valor_frete/prazo_dias/restricoes)
    and turned into a status='ok' ResultadoCotacao."""

    class LegacyResultado:
        transportadora = "TRD"
        valor_frete = 88.7
        prazo_dias = 3
        restricoes = "entrega só dias úteis"

    cot = LegacyResultado()
    # Mirror of orchestrator.py lines ~1583-1608 (the legacy branch).
    transportadora = str(getattr(cot, "transportadora", "TRD"))
    valor_frete = float(getattr(cot, "valor_frete", 0.0))
    prazo_dias = int(getattr(cot, "prazo_dias", 0))
    detalhes = getattr(cot, "restricoes", None)
    r = ResultadoCotacao(
        transportadora=transportadora,
        status="ok",
        valor_frete=valor_frete,
        prazo_dias=prazo_dias,
        detalhes=detalhes,
        duration_ms=1500,
    )
    assert r.status == "ok"
    assert r.valor_frete == 88.7
    assert r.prazo_dias == 3
    assert r.detalhes == "entrega só dias úteis"


# ---------------------------------------------------------------------------
# Error classifiers.
# ---------------------------------------------------------------------------

def test_char_is_business_error_matches_coverage_phrases():
    assert orchestrator._is_business_error("Destino fora da cobertura") is True
    assert orchestrator._is_business_error("CEP destino não atendido") is True
    assert orchestrator._is_business_error("Rota não atendida") is True
    assert orchestrator._is_business_error("transportadora não atende") is True


def test_char_is_business_error_false_for_empty_or_none():
    assert orchestrator._is_business_error("") is False
    assert orchestrator._is_business_error(None) is False


def test_char_is_business_error_value_not_found_is_NOT_business():
    """QUIRK: 'valor de frete nao encontrado' is classified as a TRANSIENT
    failure, NOT a business error. _is_business_error returns False for it even
    though to a reader it might read like a coverage/no-quote situation."""
    assert orchestrator._is_business_error("valor de frete nao encontrado") is False
    assert (
        orchestrator._is_expected_transient_failure_str("valor de frete nao encontrado")
        is True
    )


def test_char_is_expected_transient_failure_timeout_and_network():
    assert orchestrator._is_expected_transient_failure(TimeoutError("x")) is True
    assert (
        orchestrator._is_expected_transient_failure(
            RuntimeError("net::ERR_TIMED_OUT happened")
        )
        is True
    )


def test_char_is_expected_transient_failure_false_for_business_error():
    """A business/coverage error is NOT a transient failure."""
    assert (
        orchestrator._is_expected_transient_failure(
            RuntimeError("Destino fora da cobertura")
        )
        is False
    )


def test_char_is_expected_transient_failure_str_patterns():
    assert orchestrator._is_expected_transient_failure_str("Connection timed out") is True
    assert orchestrator._is_expected_transient_failure_str("reCAPTCHA não resolvido") is True
    assert orchestrator._is_expected_transient_failure_str("") is False
    assert (
        orchestrator._is_expected_transient_failure_str("Destino fora da cobertura")
        is False
    )


# ---------------------------------------------------------------------------
# SECURITY BOUNDARY.
# The QuoteResponse.raw payload the orchestrator surfaces (and serializes) must
# strip secrets. quote_response_to_resultado_cotacao passes response.raw through
# verbatim, so the sanitization done at QuoteResponse construction is the only
# barrier — pin it.
# ---------------------------------------------------------------------------

def test_char_quote_response_raw_redacts_password_and_cnpj():
    qr = QuoteResponse.ok(
        provider="X",
        valor_frete=1.0,
        prazo_dias=1,
        raw={
            "senha": "topsecret",
            "password": "p@ss",
            "cnpj": "12345678000199",
            "cpf": "12345678901",
            "token": "abc",
            "nested": {"cookie": "sess=1", "ok": "keepme"},
            "ok": "keepme",
        },
    )
    r = quote_response_to_resultado_cotacao(qr, resultado_cls=ResultadoCotacao)
    raw = r.raw
    assert raw["senha"] == "***"
    assert raw["password"] == "***"
    assert raw["cnpj"] == "***"
    assert raw["cpf"] == "***"
    assert raw["token"] == "***"
    assert raw["nested"]["cookie"] == "***"
    # Non-sensitive keys survive untouched.
    assert raw["ok"] == "keepme"
    assert raw["nested"]["ok"] == "keepme"
    # The plaintext secret/CNPJ must not appear anywhere in the serialized raw.
    serialized = repr(raw)
    assert "topsecret" not in serialized
    assert "12345678000199" not in serialized
    assert "12345678901" not in serialized
