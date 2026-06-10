"""Testes unitários para app/cotacao/validation.py.

Todas as funções testadas são puras (sem browser, sem I/O), logo
não há mocks — apenas assertions diretas.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

import pytest

from cotacao.validation import (
    _cep,
    _cep_para_uf,
    _cubagens_validas,
    _digits,
    _resolver_cep_origem_cached,
    _uf_atendida,
)
from cotacao.common import CEP_ORIGEM_PADRAO


# ---------------------------------------------------------------------------
# _cep
# ---------------------------------------------------------------------------

class TestCep:
    def test_cep_com_hifen(self):
        assert _cep("01310-100") == "01310100"

    def test_cep_sem_hifen(self):
        assert _cep("01310100") == "01310100"

    def test_cep_vazio(self):
        assert _cep("") == ""

    def test_cep_none(self):
        assert _cep(None) == ""

    def test_cep_curto_retorna_o_que_tem(self):
        assert _cep("012") == "012"

    def test_cep_longo_trunca_em_8(self):
        # Mais de 8 dígitos: mantém apenas os 8 primeiros
        assert _cep("012345678") == "01234567"

    def test_cep_com_espacos(self):
        assert _cep("01310 100") == "01310100"


# ---------------------------------------------------------------------------
# _digits
# ---------------------------------------------------------------------------

class TestDigits:
    def test_cnpj_formatado(self):
        assert _digits("12.345.678/0001-90") == "12345678000190"

    def test_none_retorna_vazio(self):
        assert _digits(None) == ""

    def test_string_sem_digitos(self):
        assert _digits("abc") == ""

    def test_string_vazia(self):
        assert _digits("") == ""

    def test_cpf_formatado(self):
        assert _digits("123.456.789-09") == "12345678909"

    def test_so_digitos_permanece(self):
        assert _digits("12345") == "12345"

    def test_zero_inteiro_retorna_vazio(self):
        # 0 e falsy -> tratado como ausente, retorna ''
        assert _digits(0) == ""


# ---------------------------------------------------------------------------
# _cep_para_uf
# ---------------------------------------------------------------------------

class TestCepParaUf:
    def test_sp_sao_paulo(self):
        assert _cep_para_uf("01310100") == "SP"

    def test_rs_porto_alegre(self):
        assert _cep_para_uf("90010000") == "RS"

    def test_pr_curitiba(self):
        assert _cep_para_uf("80000000") == "PR"

    def test_rj_rio_de_janeiro(self):
        assert _cep_para_uf("20000000") == "RJ"

    def test_cep_invalido_retorna_none(self):
        assert _cep_para_uf("00000000") is None

    def test_cep_maximo_rs(self):
        assert _cep_para_uf("99999999") == "RS"

    def test_cep_7_digitos_retorna_none(self):
        # 7 dígitos → não tem 8 chars → None
        assert _cep_para_uf("0131010") is None

    def test_cep_com_hifen_normalizado(self):
        # Aceita CEP formatado como string
        assert _cep_para_uf("01310-100") == "SP"

    def test_mg_minas_gerais(self):
        assert _cep_para_uf("30000000") == "MG"

    def test_sc_santa_catarina(self):
        assert _cep_para_uf("88000000") == "SC"

    def test_none_retorna_none(self):
        assert _cep_para_uf(None) is None


# ---------------------------------------------------------------------------
# _uf_atendida
# ---------------------------------------------------------------------------

class TestUfAtendida:
    def test_uf_presente_na_lista(self):
        assert _uf_atendida(["RS", "SC", "PR"], "RS") is True

    def test_uf_ausente_na_lista(self):
        assert _uf_atendida(["RS", "SC", "PR"], "SP") is False

    def test_sem_filtro_none_atende_tudo(self):
        assert _uf_atendida(None, "RS") is True

    def test_sem_filtro_lista_vazia_atende_tudo(self):
        assert _uf_atendida([], "RS") is True

    def test_sem_uf_destino_tenta_mesmo_assim(self):
        assert _uf_atendida(["RS"], None) is True

    def test_case_insensitive(self):
        assert _uf_atendida(["rs", "sc"], "RS") is True

    def test_case_insensitive_destino_minusculo(self):
        assert _uf_atendida(["RS", "SC"], "rs") is True

    def test_lista_com_um_elemento_match(self):
        assert _uf_atendida(["SP"], "SP") is True

    def test_lista_com_um_elemento_no_match(self):
        assert _uf_atendida(["SP"], "RJ") is False

    def test_csv_string_como_config(self):
        # Config como string CSV (ex: "RS,SC,PR") deve funcionar igual a lista
        assert _uf_atendida("RS,SC,PR", "RS") is True

    def test_csv_string_uf_ausente(self):
        assert _uf_atendida("RS,SC,PR", "SP") is False


# ---------------------------------------------------------------------------
# _cubagens_validas
# ---------------------------------------------------------------------------

class TestCubagens:
    def test_lista_vazia_retorna_vazia(self):
        assert _cubagens_validas([]) == []

    def test_none_retorna_vazia(self):
        assert _cubagens_validas(None) == []

    def test_linha_valida_retorna_um_item(self):
        entrada = [{"quantidade": 2, "comprimento_cm": 30, "largura_cm": 20, "altura_cm": 10}]
        resultado = _cubagens_validas(entrada)
        assert len(resultado) == 1
        assert resultado[0]["quantidade"] == 2
        assert resultado[0]["comprimento_cm"] == 30
        assert resultado[0]["largura_cm"] == 20
        assert resultado[0]["altura_cm"] == 10

    def test_quantidade_zero_filtrada(self):
        entrada = [{"quantidade": 0, "comprimento_cm": 30, "largura_cm": 20, "altura_cm": 10}]
        assert _cubagens_validas(entrada) == []

    def test_comprimento_zero_filtrado(self):
        entrada = [{"quantidade": 1, "comprimento_cm": 0, "largura_cm": 20, "altura_cm": 10}]
        assert _cubagens_validas(entrada) == []

    def test_largura_zero_filtrada(self):
        entrada = [{"quantidade": 1, "comprimento_cm": 30, "largura_cm": 0, "altura_cm": 10}]
        assert _cubagens_validas(entrada) == []

    def test_altura_zero_filtrada(self):
        entrada = [{"quantidade": 1, "comprimento_cm": 30, "largura_cm": 20, "altura_cm": 0}]
        assert _cubagens_validas(entrada) == []

    def test_linha_sem_campo_obrigatorio_filtrada(self):
        # Sem 'altura_cm'
        entrada = [{"quantidade": 1, "comprimento_cm": 30, "largura_cm": 20}]
        # altura_cm ausente → default 0 → filtrado
        assert _cubagens_validas(entrada) == []

    def test_mixed_validas_invalidas(self):
        entrada = [
            {"quantidade": 1, "comprimento_cm": 10, "largura_cm": 10, "altura_cm": 10},  # válida
            {"quantidade": 0, "comprimento_cm": 10, "largura_cm": 10, "altura_cm": 10},  # inválida
            {"quantidade": 2, "comprimento_cm": 20, "largura_cm": 15, "altura_cm": 5},   # válida
        ]
        resultado = _cubagens_validas(entrada)
        assert len(resultado) == 2

    def test_nao_lista_retorna_vazia(self):
        assert _cubagens_validas("string") == []
        assert _cubagens_validas(42) == []
        assert _cubagens_validas({"a": 1}) == []

    def test_linha_nao_dict_ignorada(self):
        assert _cubagens_validas(["string", None, 42]) == []

    def test_peso_por_volume_incluido_quando_valido(self):
        entrada = [
            {
                "quantidade": 1,
                "comprimento_cm": 10,
                "largura_cm": 10,
                "altura_cm": 10,
                "peso_por_volume_kg": 5.5,
            }
        ]
        resultado = _cubagens_validas(entrada)
        assert len(resultado) == 1
        assert resultado[0]["peso_por_volume_kg"] == 5.5

    def test_peso_por_volume_zero_vira_none(self):
        entrada = [
            {
                "quantidade": 1,
                "comprimento_cm": 10,
                "largura_cm": 10,
                "altura_cm": 10,
                "peso_por_volume_kg": 0,
            }
        ]
        resultado = _cubagens_validas(entrada)
        assert len(resultado) == 1
        assert resultado[0]["peso_por_volume_kg"] is None

    def test_peso_por_volume_ausente_vira_none(self):
        entrada = [{"quantidade": 1, "comprimento_cm": 10, "largura_cm": 10, "altura_cm": 10}]
        resultado = _cubagens_validas(entrada)
        assert resultado[0]["peso_por_volume_kg"] is None

    def test_quantidade_negativa_filtrada(self):
        entrada = [{"quantidade": -1, "comprimento_cm": 10, "largura_cm": 10, "altura_cm": 10}]
        assert _cubagens_validas(entrada) == []


# ---------------------------------------------------------------------------
# _resolver_cep_origem_cached
# ---------------------------------------------------------------------------

class TestResolverCepOrigem:
    def test_cep_informado_tem_prioridade(self):
        resultado = _resolver_cep_origem_cached(
            "12345678",       # cep_informado
            "99999999",       # cep_romaneio
            ("88888888",),    # transportadora_ceps
        )
        assert resultado == "12345678"

    def test_cep_romaneio_tem_prioridade_sobre_fallback(self):
        resultado = _resolver_cep_origem_cached(
            "",               # cep_informado vazio
            "99999999",       # cep_romaneio
            ("88888888",),    # transportadora_ceps
        )
        assert resultado == "99999999"

    def test_transportadora_cep_quando_sem_informado_e_romaneio(self):
        resultado = _resolver_cep_origem_cached(
            "",               # cep_informado vazio
            "",               # cep_romaneio vazio
            ("88888888",),    # transportadora_ceps
        )
        assert resultado == "88888888"

    def test_fallback_padrao_quando_tudo_vazio(self):
        resultado = _resolver_cep_origem_cached(
            "",
            "",
            (),
        )
        assert resultado == CEP_ORIGEM_PADRAO

    def test_transportadora_ceps_ignora_vazios(self):
        # Primeiro elemento vazio → vai para o segundo
        resultado = _resolver_cep_origem_cached(
            "",
            "",
            ("", "77000000"),
        )
        assert resultado == "77000000"

    def test_todos_transportadora_ceps_vazios_usa_padrao(self):
        resultado = _resolver_cep_origem_cached(
            "",
            "",
            ("", ""),
        )
        assert resultado == CEP_ORIGEM_PADRAO
