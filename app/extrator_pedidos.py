"""
MÃ³dulo de extraÃ§Ã£o de dados de Pedidos em PDF
"""

import pdfplumber
import re
import math
import unicodedata
import logging
from typing import Dict, List, Any
from dataclasses import dataclass
from difflib import SequenceMatcher

from extrator_pedidos_common import (
    Item,
    Pedido,
    _normalizar_sem_acentos,
    _linhas_nao_vazias,
    PEDIDO_INTERNO_RE,
    CNPJ_CLIENTE_RE,
    VALOR_TOTAL_RE,
    AGENDAMENTO_NAO_RE,
    AGENDAR_ENTREGA_RE,
    LOCAL_ENTREGA_RE,
    LOCAL_ENTREGA_FALLBACK_RE,
    TOKENS_MAIUSCULOS_RE,
    ITENS_PEDIDO_RE,
    OBSERVACOES_RE,
    SPEC_RE,
    CODE_RE,
    CODIGO_INICIO_RE,
    CODIGO_TOKEN_RE,
    CODIGO_CONTINUACAO_RE,
    EMBALAGEM_RE,
    PESO_RE,
    VOLUME_RE,
    TRAILING_NUM_RE,
    APOS_KG_RE,
    DIMENSAO_RE,
    DIMENSAO_SIMPLES_RE,
    NORMALIZAR_ESPACOS_RE,
    UNIDADE_SPLIT_RE,
    COMPONENTE_KIT_RE,
    EMBALAGEM_OU_DIMENSAO_RE,
    INFO_TECNICA_RE,
    NUMERO_MEDIDA_RE,
    LINHA_HEADER_RE,
    CODIGO_PRINCIPAL_RE,
    ITEM_SAME_LINE_RE,
    ITEM_CX_PONTUADO_RE,
    ITEM_CX_RE,
    UNIDADE_QTD_VALOR_RE,
    ITEM_SUBTOTAL_RE,
    SKU_NUMERO_RE,
    SUFIXO_NUMERICO_RE,
    CX_FD_NUMERO_RE_TEMPLATE,
    CEP_RE,
    SO_DIGITOS_RE,
    CIDADE_UF_RE,
    PREFIXO_ENDERECO_RE,
    PREFIXO_ENDERECO_FLEX_RE,
    UF_FINAL_RE,
    FD_CONTAINER_RE,
    UNIDADES_EMBALAGEM_RE,
    PESO_ALTERNATIVO_RE,
    M3_NORMALIZADO_RE,
)
from extrator_pedidos_parse import ParseMixin
from extrator_pedidos_local import LocalEntregaMixin
from extrator_pedidos_boxes import BoxesMixin

logger = logging.getLogger(__name__)


class ExtratorPedidos(ParseMixin, LocalEntregaMixin, BoxesMixin):
    """Extrai pedidos de arquivos PDF"""

    # Caixa pequena para sobras (complementares)
    CAIXA_PEQUENA_DIMS = "13x31x51"
    CAIXA_PEQUENA_VOL = 0.022
    CAIXA_PEQUENA_PESO = 0.0

    def __init__(self):
        self.pedidos = []

    def _is_disco(self, item: Item) -> bool:
        codigo = (item.produto or "").upper()
        desc = (item.descricao or "").upper()
        return codigo.startswith("DIS") or "DISCO" in desc

    def extrair_arquivo(self, caminho_pdf: str) -> List[Pedido]:
        """
        Extrai todos os pedidos de um arquivo PDF

        Args:
            caminho_pdf: Caminho do arquivo PDF

        Returns:
            Lista de pedidos extraÃ­dos
        """
        self.pedidos = []

        try:
            with pdfplumber.open(caminho_pdf) as pdf:
                logger.debug("PDF aberto com %d pagina(s)", len(pdf.pages))
                for page_num, page in enumerate(pdf.pages, 1):
                    texto = page.extract_text()
                    if texto:
                        logger.debug("Pagina %d contem %d caracteres", page_num, len(texto))
                        pedido = self._extrair_pedido_pagina(texto, page_num)
                        if pedido:
                            self.pedidos.append(pedido)
                            logger.debug("Pedido extraido: PD %s", pedido.numero)
                        else:
                            logger.debug("Nenhum pedido valido na pagina %d", page_num)
                    else:
                        logger.debug("Pagina %d esta vazia", page_num)
        except Exception as e:
            logger.exception("Erro ao processar %s: %s", caminho_pdf, e)

        return self.pedidos

    def _converter_valor(self, valor_str: str) -> float:
        """Converte string de valor para float"""
        if not valor_str:
            return 0.0
        # Substituir separadores: vÃ­rgula para decimal, ponto para nada
        valor_str = valor_str.strip()
        valor_str = valor_str.replace('.', '')  # Remove pontos (separador de milhar)
        valor_str = valor_str.replace(',', '.')  # Converte vÃ­rgula em ponto decimal
        try:
            return float(valor_str)
        except ValueError:
            return 0.0

    def _converter_medida(self, valor_str: str) -> float:
        """Converte string de medida (kg/m3) para float"""
        if not valor_str:
            return 0.0
        valor_str = valor_str.strip()
        if ',' in valor_str:
            valor_str = valor_str.replace('.', '')
            valor_str = valor_str.replace(',', '.')
        try:
            return float(valor_str)
        except ValueError:
            return 0.0

    def _formatar_decimal(self, valor: float, casas: int = 3) -> str:
        """Formata nÃºmero com N casas e vÃ­rgula como separador decimal."""
        if valor is None:
            return ("0," + ("0" * casas)) if casas > 0 else "0"
        return format(valor, f'.{casas}f').replace('.', ',')

    def _arredondar_cima(self, valor: float, casas: int = 3) -> float:
        """Arredonda para cima mantendo o nÃºmero de casas decimais."""
        if valor is None:
            return 0.0
        fator = 10 ** casas
        return math.ceil(valor * fator) / fator

    def _formatar_info_caixa(self, info: str) -> str:
        """Normaliza pesos e cubagens dentro da info_caixa para 3 casas com vÃ­rgula."""
        if not info:
            return info or ""
        partes = [p.strip() for p in info.split('|')]
        # Peso alternativo apÃ³s CX/FD (quando peso kg Ã© muito baixo)
        m_alt = PESO_ALTERNATIVO_RE.search(info)
        peso_alt = self._converter_medida(m_alt.group(1)) if m_alt else None
        partes_formatadas = []
        for p in partes:
            p_fmt = p
            m_peso = PESO_RE.search(p_fmt)
            if m_peso:
                val = self._converter_medida(m_peso.group(1))
                if val is not None:
                    if val < 1 and peso_alt is not None and peso_alt >= 1:
                        val = peso_alt
                    p_fmt = re.sub(r'[\d.,]+\s*kg', f"{self._formatar_decimal(val)} kg", p_fmt, flags=re.IGNORECASE)
            m_vol = M3_NORMALIZADO_RE.search(p_fmt)
            if m_vol:
                val = self._converter_medida(m_vol.group(1))
                if val is not None:
                    p_fmt = re.sub(r'[\d.,]+\s*m3', f"{self._formatar_decimal(val)} m3", p_fmt, flags=re.IGNORECASE)
            partes_formatadas.append(p_fmt)
        return ' | '.join(partes_formatadas)

    def obter_pedidos(self) -> List[Pedido]:
        """Retorna a lista de pedidos extraÃ­dos"""
        return self.pedidos


del ParseMixin
del LocalEntregaMixin
del BoxesMixin
