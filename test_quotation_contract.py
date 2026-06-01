import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

import cotacao_transportadoras as ct
from fretio.models import Cotacao
from fretio.quotation_contract import (
    QuoteResponse,
    cotacao_legada_to_quote_response,
    quote_request_from_legacy_kwargs,
    quote_response_to_resultado_cotacao,
    resultado_cotacao_to_quote_response,
)


def test_quote_request_from_legacy_kwargs_preserva_campos_essenciais_e_extras():
    request = quote_request_from_legacy_kwargs(
        {
            "origem": "99740000",
            "destino": "90010123",
            "peso": 12.5,
            "valor": 345.67,
            "volumes": 2,
            "cubagem_m3": 0.144,
            "cubagens": [
                {"quantidade": 2, "comprimento_cm": 40, "largura_cm": 30, "altura_cm": 60}
            ],
            "tipo_frete": "2",
            "cnpj_destinatario": "12345678000190",
            "cnpj_remetente": "00000000000191",
        },
        uf_destino="RS",
    )

    assert request.origem_cep == "99740000"
    assert request.destino_cep == "90010123"
    assert request.uf_destino == "RS"
    assert request.metadata == {"legacy_kwargs": {"cnpj_remetente": "00000000000191"}}
    legacy = request.to_legacy_kwargs()
    assert legacy["uf_destino"] == "RS"
    assert legacy["volumes"] == 2
    assert legacy["peso"] == 12.5
    assert legacy["cubagem_m3"] == 0.144
    assert legacy["cubagens"] == [
        {"quantidade": 2, "comprimento_cm": 40, "largura_cm": 30, "altura_cm": 60}
    ]
    assert legacy["cnpj_remetente"] == "00000000000191"


def test_cotacao_legada_to_quote_response_converte_ok_e_none():
    cotacao = Cotacao(
        transportadora="TRD",
        prazo_dias=3,
        valor_frete=199.9,
        restricoes="Cotação #10",
    )

    ok = cotacao_legada_to_quote_response(cotacao, duration_ms=180)
    sem = cotacao_legada_to_quote_response(None, provider="TRD", duration_ms=25, stage="timeout")

    assert ok.status == "ok"
    assert ok.provider == "TRD"
    assert ok.valor_frete == 199.9
    assert ok.prazo_dias == 3
    assert ok.detalhes == "Cotação #10"
    assert ok.duration_ms == 180

    assert sem.status == "sem_cotacao"
    assert sem.provider == "TRD"
    assert sem.duration_ms == 25
    assert sem.stage == "timeout"


def test_resultado_quote_response_roundtrip_com_resultadocotacao():
    resultado = ct.ResultadoCotacao(
        transportadora="AGEX",
        status="erro_divergencia_uf",
        detalhes="CEP pertence a outra UF",
        duration_ms=41,
    )

    response = resultado_cotacao_to_quote_response(
        resultado,
        raw={"senha": "segredo", "payload": {"cnpj": "12345678000190", "ok": True}},
    )
    volta = quote_response_to_resultado_cotacao(response, resultado_cls=ct.ResultadoCotacao)

    assert response.status == "erro"
    assert response.provider == "AGEX"
    assert response.error_code == "erro_divergencia_uf"
    assert response.raw == {"senha": "***", "payload": {"cnpj": "***", "ok": True}}

    assert volta.transportadora == "AGEX"
    assert volta.status == "erro_divergencia_uf"
    assert volta.detalhes == "CEP pertence a outra UF"
    assert volta.duration_ms == 41


def test_quote_response_helpers_validam_status_permitidos():
    assert QuoteResponse.no_quote(provider="TRD").status == "sem_cotacao"
    assert QuoteResponse.no_quote(provider="TRD", status="nao_atendido").status == "nao_atendido"

    with pytest.raises(ValueError):
        QuoteResponse.no_quote(provider="TRD", status="erro")


def test_quote_response_to_resultado_filtra_campos_para_construtor_legado():
    class ResultadoLegado:
        def __init__(self, transportadora, status, valor_frete=None, prazo_dias=None, detalhes=None, duration_ms=None):
            self.transportadora = transportadora
            self.status = status
            self.valor_frete = valor_frete
            self.prazo_dias = prazo_dias
            self.detalhes = detalhes
            self.duration_ms = duration_ms

    response = QuoteResponse.error(
        provider="TRD",
        detalhes="UF divergente",
        duration_ms=77,
        stage="validacao_uf",
        error_code="erro_divergencia_uf",
        raw={"token": "secreto"},
    )

    resultado = quote_response_to_resultado_cotacao(response, resultado_cls=ResultadoLegado)
    assert resultado.transportadora == "TRD"
    assert resultado.status == "erro_divergencia_uf"
    assert resultado.duration_ms == 77


def test_detector_de_contrato_novo_so_aceita_cotar_com_request():
    class LegacyProvider:
        async def coteir(self, origem, destino, peso, valor):
            return None

    class OldCotarProvider:
        async def cotar(self, origem, destino, peso, valor):
            return None

    class NewContractProvider:
        async def cotar(self, request):
            return QuoteResponse.no_quote(provider="NOVO")

    assert ct._provider_supports_quote_request_cotar(LegacyProvider()) is False
    assert ct._provider_supports_quote_request_cotar(OldCotarProvider()) is False
    assert ct._provider_supports_quote_request_cotar(NewContractProvider()) is True
