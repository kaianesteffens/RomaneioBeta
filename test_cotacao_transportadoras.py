import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

import cotacao_transportadoras as ct


def test_cep_helpers_normalize_map_uf_and_filter_supported_states():
    assert ct._cep("90.010-123") == "90010123"
    assert ct._cep_para_uf("90010-123") == "RS"
    assert ct._uf_atendida(["rs", "sc"], "RS") is True
    assert ct._uf_atendida("SP, PR", "RS") is False
    assert ct._uf_atendida([], None) is True


def test_resolver_cep_origem_uses_expected_precedence():
    config = {
        "romaneio": {"cep_origem": "11.222-333"},
        "transportadoras": {
            "braspress": {"cep_origem": "22.333-444"},
            "bauer": {"cep_origem": "33.444-555"},
        },
    }

    assert ct._resolver_cep_origem(config, "44.555-666") == "44555666"
    assert ct._resolver_cep_origem(config, "") == "11222333"
    assert ct._resolver_cep_origem({"transportadoras": {"trd": {"cep_origem": "55.666-777"}}}, "") == "55666777"
    assert ct._resolver_cep_origem({}, "") == ct.CEP_ORIGEM_PADRAO


def test_text_normalization_and_dimension_parsing_are_stable():
    texto = "<p>Linha 1</p><br>Linha&nbsp;&nbsp;2\r\n<div>Linha 3</div>"

    assert ct._normalizar_romaneio_colado(texto) == "Linha 1\nLinha 2\nLinha 3"
    assert ct._parse_dim_cm("0,31") == 31
    assert ct._parse_dim_cm("31") == 31
    assert ct._parse_dim_cm("invalido") == 0


def test_selecionar_cep_destino_prefers_matching_uf_after_reference():
    texto = (
        "CEP anterior 01001-000\n"
        "CNPJ/CPF: 12.345.678/0001-90\n"
        "Cidade: Porto Alegre / RS\n"
        "Outro CEP 80010-000\n"
        "CEP: 90010-123\n"
    )
    pos_ref = texto.index("CNPJ/CPF")

    assert ct._selecionar_cep_destino(texto, pos_referencia=pos_ref, uf_hint="RS") == "90010123"


def test_dados_envio_romaneio_colado_extracts_core_shipping_fields():
    romaneio = """
    DESTINATARIO
    CNPJ/CPF: 12.345.678/0001-90
    Endereco: Rua Exemplo, 123
    Cidade: Porto Alegre / RS
    CEP: 90010-123
    - VOL: 2
    - CUBAGEM: 0,044 m3
    - PESO: 3,300 kg
    - TOTAL: R$ 150,99
    2 x Caixas fechadas - 1,650 kg - 0,044 m3 - 31x31x45
    Produto Teste: 2 und
    """

    dados = ct._dados_envio_romaneio_colado(romaneio)

    assert dados["destino_cep"] == "90010123"
    assert dados["uf_destino"] == "RS"
    assert dados["cnpj_destinatario"] == "12345678000190"
    assert dados["peso"] == 3.3
    assert dados["valor"] == 150.99
    assert dados["volumes"] == 2
    assert dados["cubagem_m3"] == 0.044
    assert dados["comprimento_cm"] == 45
    assert dados["largura_cm"] == 31
    assert dados["altura_cm"] == 31
    assert dados["cubagens"] == [
        {
            "quantidade": 2,
            "comprimento_cm": 45,
            "largura_cm": 31,
            "altura_cm": 31,
            "peso_por_volume_kg": 1.65,
        }
    ]
    assert dados["descricoes_itens"] == ["Produto Teste"]


def test_cubagens_validas_discards_invalid_rows():
    cubagens = ct._cubagens_validas(
        [
            {"quantidade": 2, "comprimento_cm": 45, "largura_cm": 31, "altura_cm": 31, "peso_por_volume_kg": "1.65"},
            {"quantidade": 0, "comprimento_cm": 10, "largura_cm": 10, "altura_cm": 10},
            {"quantidade": 1, "comprimento_cm": 30, "largura_cm": 20, "altura_cm": 10, "peso_por_volume_kg": "-5"},
            "invalido",
        ]
    )

    assert cubagens == [
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


def test_rodonaves_setup_error_does_not_escape_except_scope(monkeypatch):
    class FakeFactory:
        def __init__(self, config):
            self.config = config

        def is_available(self, nome):
            return nome == "rodonaves"

        def get_provider_config(self, nome):
            if nome != "rodonaves":
                return {"habilitado": False}
            return {
                "habilitado": True,
                "dominio": "RTE",
                "usuario": "user",
                "senha": "secret",
                "cnpj_pagador": "12.345.678/0001-90",
                "headless": True,
                "ufs_atendidas": ["RS"],
            }

        def create(self, nome, **kwargs):
            raise RuntimeError("falha fake rodonaves")

    monkeypatch.setattr(ct, "ProviderFactory", FakeFactory)

    config = {
        "fretio": {},
        "romaneio": {"cep_origem": "99740000"},
        "transportadoras": {"rodonaves": {"habilitado": True}},
    }
    dados = {
        "destino_cep": "90010123",
        "uf_destino": "RS",
        "cnpj_destinatario": "12345678000190",
        "peso": 3.3,
        "valor": 150.99,
        "volumes": 1,
        "cubagem_m3": 0.044,
        "cubagens": [
            {
                "quantidade": 1,
                "comprimento_cm": 45,
                "largura_cm": 31,
                "altura_cm": 31,
                "peso_por_volume_kg": 3.3,
            }
        ],
        "descricoes_itens": [],
    }

    resultados = asyncio.run(
        ct._executar_cotacoes_com_dados(
            config=config,
            dados=dados,
            cep_origem="99740000",
        )
    )

    assert len(resultados) == 1
    assert resultados[0].transportadora == "RODONAVES"
    assert resultados[0].status == "erro"
    assert "falha fake rodonaves" in resultados[0].detalhes


def test_session_lazy_prelogin_runs_for_new_trd_provider(monkeypatch):
    monkeypatch.setattr(ct, "_carregar_config", lambda config_path=None: {})

    class FakeProvider:
        def __init__(self):
            self.pre_login_calls = 0
            self._logged_in = False

        async def pre_login(self):
            self.pre_login_calls += 1
            self._logged_in = True
            return True

    async def scenario():
        session = ct.TransportadoraSession()
        provider = FakeProvider()
        same_provider = await session.assegurar_provider("trd", lambda: provider)
        await session.assegurar_provider("trd", lambda: FakeProvider())
        return provider, same_provider

    provider, same_provider = asyncio.run(scenario())

    assert same_provider is provider
    assert provider.pre_login_calls == 1
