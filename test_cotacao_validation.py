import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

from cotacao import orchestrator as cotacao_orchestrator
from cotacao.validation import _cep, _cep_para_uf, _cubagens_validas, _digits, _uf_atendida
from fretio.providers.factory import ProviderFactory


def _dados_validos_para_cotacao(**overrides):
    dados = {
        "destino_cep": "90010123",
        "uf_destino": "RS",
        "cnpj_destinatario": "12345678000190",
        "peso": 3.3,
        "valor": 150.99,
        "volumes": 2,
        "cubagem_m3": 0.044,
        "cubagens": [
            {
                "quantidade": 2,
                "comprimento_cm": 45,
                "largura_cm": 31,
                "altura_cm": 31,
                "peso_por_volume_kg": 1.65,
            }
        ],
        "descricoes_itens": [],
    }
    dados.update(overrides)
    return dados


def _validar_pre_cotacao(monkeypatch, dados):
    class FakeFactory:
        def __init__(self, config):
            self.config = config

        def get_provider_config(self, nome):
            return {"habilitado": False}

        def is_available(self, nome):
            return False

    monkeypatch.setattr(cotacao_orchestrator, "ProviderFactory", FakeFactory)
    monkeypatch.setattr(cotacao_orchestrator, "apply_safe_runtime_overrides", lambda config: config)
    monkeypatch.setattr(cotacao_orchestrator, "_log_diag", lambda msg: None)
    monkeypatch.setattr(cotacao_orchestrator, "carrier_enabled_or_message", lambda carrier: (True, ""))
    return asyncio.run(
        cotacao_orchestrator._executar_cotacoes_com_dados(
            config={"romaneio": {"cep_origem": "99740000"}, "fretio": {}, "transportadoras": {}},
            dados=dados,
            cep_origem="99740000",
        )
    )


def test_digits_cep_and_uf_mapping_are_preserved():
    assert _digits("12.345.678/0001-90") == "12345678000190"
    assert _cep("90010-123") == "90010123"
    assert _cep_para_uf("90010-123") == "RS"
    assert _cep_para_uf("abc") is None


def test_uf_atendida_keeps_empty_filter_permissive():
    assert _uf_atendida([], "RS") is True
    assert _uf_atendida(["RS", "SC"], "RS") is True
    assert _uf_atendida("RS,SC", "PR") is False
    assert _uf_atendida(["RS"], "") is True


def test_cubagens_validas_discards_invalid_rows():
    assert _cubagens_validas(
        [
            {"quantidade": 2, "comprimento_cm": 45, "largura_cm": 31, "altura_cm": 31, "peso_por_volume_kg": "1.65"},
            {"quantidade": 0, "comprimento_cm": 10, "largura_cm": 10, "altura_cm": 10},
            {"quantidade": 1, "comprimento_cm": 30, "largura_cm": 20, "altura_cm": 10, "peso_por_volume_kg": "-5"},
            "invalido",
        ]
    ) == [
        {
            "quantidade": 2,
            "comprimento_cm": 45,
            "largura_cm": 31,
            "altura_cm": 31,
            "peso_por_volume_kg": 1.65,
        },
        {
            "quantidade": 1,
            "comprimento_cm": 30,
            "largura_cm": 20,
            "altura_cm": 10,
            "peso_por_volume_kg": None,
        },
    ]


def test_pre_cotacao_detects_cep_uf_divergence(monkeypatch):
    resultados = _validar_pre_cotacao(monkeypatch, _dados_validos_para_cotacao(uf_destino="SP"))

    assert len(resultados) == 1
    assert resultados[0].transportadora == "GERAL"
    assert resultados[0].status == "erro_divergencia_uf"
    assert "pertence à UF RS" in resultados[0].detalhes
    assert "romaneio informa UF SP" in resultados[0].detalhes


def test_pre_cotacao_blocks_volume_total_different_from_cubage_rows(monkeypatch):
    resultados = _validar_pre_cotacao(monkeypatch, _dados_validos_para_cotacao(volumes=3))

    assert len(resultados) == 1
    assert resultados[0].status == "erro"
    assert "volume total do romaneio diverge da soma das cubagens" in resultados[0].detalhes


def test_pre_cotacao_blocks_zero_weight(monkeypatch):
    resultados = _validar_pre_cotacao(monkeypatch, _dados_validos_para_cotacao(peso=0))

    assert len(resultados) == 1
    assert resultados[0].status == "erro"
    assert "peso total ausente ou inválido" in resultados[0].detalhes


def test_pre_cotacao_blocks_zero_cubage(monkeypatch):
    resultados = _validar_pre_cotacao(monkeypatch, _dados_validos_para_cotacao(cubagem_m3=0))

    assert len(resultados) == 1
    assert resultados[0].status == "erro"
    assert "cubagem total ausente ou inválida" in resultados[0].detalhes


def test_pre_cotacao_blocks_negative_total_value(monkeypatch):
    resultados = _validar_pre_cotacao(monkeypatch, _dados_validos_para_cotacao(valor=-1))

    assert len(resultados) == 1
    assert resultados[0].status == "erro"
    assert "valor total negativo" in resultados[0].detalhes


def test_enabled_incomplete_provider_returns_clear_status_without_cotacao(monkeypatch):
    monkeypatch.setattr(cotacao_orchestrator, "apply_safe_runtime_overrides", lambda config: config)
    monkeypatch.setattr(cotacao_orchestrator, "ProviderFactory", ProviderFactory)
    monkeypatch.setattr(cotacao_orchestrator, "_log_diag", lambda msg: None)
    monkeypatch.setattr(cotacao_orchestrator, "carrier_enabled_or_message", lambda carrier: (True, ""))

    resultados = asyncio.run(
        cotacao_orchestrator._executar_cotacoes_com_dados(
            config={
                "romaneio": {"cep_origem": "99740000"},
                "fretio": {},
                "transportadoras": {
                    "braspress": {"habilitado": False},
                    "trd": {"habilitado": True, "email": "cliente@example.com", "senha": ""},
                    "agex": {"habilitado": False},
                    "eucatur": {"habilitado": False},
                    "rodonaves": {"habilitado": False},
                    "alfa": {"habilitado": False},
                    "coopex": {"habilitado": False},
                },
            },
            dados=_dados_validos_para_cotacao(),
            cep_origem="99740000",
        )
    )

    assert len(resultados) == 1
    assert resultados[0].transportadora == "TRD"
    assert resultados[0].status == "Configuração incompleta"
    assert "senha" in resultados[0].detalhes
