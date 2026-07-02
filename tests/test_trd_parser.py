"""Testes unitários para o parser de valores da TRD (_parse_monetary_value e _extrair_valor_frete)."""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

from fretio.providers.trd import TRDProvider


# ── _parse_monetary_value ──────────────────────────────────────────────────────

def test_parse_monetary_br_com_separadores():
    """R$ 1.234,56 e variantes brasileiras são corretamente parseados."""
    assert TRDProvider._parse_monetary_value("1.234,56") == 1234.56
    assert TRDProvider._parse_monetary_value("R$ 1.234,56") == 1234.56
    assert TRDProvider._parse_monetary_value("1234,56") == 1234.56
    assert TRDProvider._parse_monetary_value("  1.234,56  ") == 1234.56


def test_parse_monetary_formato_ingles():
    """1234.56 (decimal com ponto) é convertido corretamente."""
    assert TRDProvider._parse_monetary_value("1234.56") == 1234.56
    assert TRDProvider._parse_monetary_value("99.99") == 99.99


def test_parse_monetary_valores_simples():
    """Valores sem separador de milhar funcionam."""
    assert TRDProvider._parse_monetary_value("250,00") == 250.0
    assert TRDProvider._parse_monetary_value("1000,00") == 1000.0


def test_parse_monetary_none_e_invalidos():
    """None e strings sem número retornam None."""
    assert TRDProvider._parse_monetary_value(None) is None
    assert TRDProvider._parse_monetary_value("") is None
    assert TRDProvider._parse_monetary_value("abc") is None
    assert TRDProvider._parse_monetary_value("R$ abc") is None


def test_parse_monetary_tipos_numericos():
    """int e float já são retornados como float diretamente."""
    assert TRDProvider._parse_monetary_value(150) == 150.0
    assert TRDProvider._parse_monetary_value(1234.56) == 1234.56


# ── _extrair_valor_frete ───────────────────────────────────────────────────────

def test_extrair_via_valor_da_prestacao():
    """O marcador 'VALOR DA PRESTAÇÃO' é a regra prioritária."""
    texto = "Cotação TRD\nVALOR DA PRESTAÇÃO: R$ 1.234,56\nPrazo: 3 dias"
    assert TRDProvider._extrair_valor_frete(texto, valor_mercadoria=500.0) == 1234.56


def test_extrair_via_frete_label():
    """'Frete: R$ ...' é reconhecido pela regra de padrão \bfrete\b."""
    texto = "Resumo da cotação\nFrete: R$ 350,00\nTotal da NF: R$ 2.000,00"
    resultado = TRDProvider._extrair_valor_frete(texto, valor_mercadoria=2000.0)
    assert resultado == 350.0


def test_extrair_via_valor_frete_label():
    """'Valor Frete: ...' é reconhecido pelo novo padrão valor_frete."""
    texto = "Simulação #12345\nValor Frete: R$ 780,50\nPrazo entrega: 5 dias"
    resultado = TRDProvider._extrair_valor_frete(texto, valor_mercadoria=1500.0)
    assert resultado == 780.50


def test_extrair_via_total_frete():
    """'Total Frete: ...' é reconhecido pelo novo padrão total_frete."""
    texto = "Total Frete: 430,00\nPeso: 10,000 kg\nVolumes: 2"
    resultado = TRDProvider._extrair_valor_frete(texto, valor_mercadoria=999.0)
    assert resultado == 430.0


def test_extrair_via_total_com_rs_explicito():
    """'Total: R$ ...' com R$ explícito é reconhecido."""
    texto = "Serviço de frete\nTotal: R$ 612,00\nValidade: 30 dias"
    resultado = TRDProvider._extrair_valor_frete(texto, valor_mercadoria=3000.0)
    assert resultado == 612.0


def test_extrair_via_valor_total_com_rs():
    """'Valor total: R$ ...' com R$ explícito é reconhecido."""
    texto = "Prestação de serviço\nValor total: R$ 895,75"
    resultado = TRDProvider._extrair_valor_frete(texto, valor_mercadoria=2500.0)
    assert resultado == 895.75


def test_nao_confunde_mercadoria_sem_label_frete():
    """Quando o texto só tem valores em contexto de mercadoria/nota (sem 'frete' ou 'prestação'),
    o valor da mercadoria não deve ser retornado como frete."""
    texto = "Valor da Mercadoria: R$ 1.000,00\nNota Fiscal: R$ 1.000,00"
    resultado = TRDProvider._extrair_valor_frete(texto, valor_mercadoria=1000.0)
    assert resultado is None


def test_nao_confunde_peso_com_frete():
    """Valores em contexto de peso não devem ser retornados como frete."""
    texto = "Peso bruto: 12,500 kg\nFrete: R$ 200,00"
    resultado = TRDProvider._extrair_valor_frete(texto, valor_mercadoria=500.0)
    assert resultado == 200.0


def test_nao_confunde_imposto_com_frete():
    """Valores em contexto de imposto/ICMS não devem ser priorizados."""
    texto = "ICMS: R$ 48,00\nValor do Frete: R$ 320,00\nIPI: R$ 10,00"
    resultado = TRDProvider._extrair_valor_frete(texto, valor_mercadoria=800.0)
    assert resultado == 320.0


def test_retorna_none_quando_sem_valor():
    """Texto sem valor monetário retorna None (falha controlada)."""
    texto = "Cotação TRD - aguardando processamento"
    resultado = TRDProvider._extrair_valor_frete(texto, valor_mercadoria=500.0)
    assert resultado is None


def test_retorna_none_quando_texto_vazio():
    """Texto vazio retorna None."""
    assert TRDProvider._extrair_valor_frete("", valor_mercadoria=500.0) is None
    assert TRDProvider._extrair_valor_frete(None, valor_mercadoria=500.0) is None


def test_extrair_valor_total_da_prestacao():
    """'Valor total da Prestação' também é reconhecido."""
    texto = "Resultado\nValor total da Prestação R$ 1.500,00\nPrazo: 2 dias"
    resultado = TRDProvider._extrair_valor_frete(texto, valor_mercadoria=5000.0)
    assert resultado == 1500.0


def test_formato_br_sem_separador_milhar():
    """Valores como '250,00' sem separador de milhar são extraídos corretamente."""
    texto = "Frete calculado\nValor do Frete 250,00\nCotação válida"
    resultado = TRDProvider._extrair_valor_frete(texto, valor_mercadoria=999.0)
    assert resultado == 250.0
