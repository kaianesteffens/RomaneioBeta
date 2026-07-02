"""
Constantes, helpers e dataclasses compartilhados do extrator de pedidos.
"""

import re
import math
import unicodedata
from typing import Dict, List, Any
from dataclasses import dataclass
from difflib import SequenceMatcher

PEDIDO_INTERNO_RE = re.compile(r'PEDIDO INTERNO:\s*PD\s*(\d+)')
CNPJ_CLIENTE_RE = re.compile(r'CNPJ/CPF:\s*([0-9./-]{11,18})')
VALOR_TOTAL_RE = re.compile(r'Total:\s*R\$\s*([\d.,]+)')
AGENDAMENTO_NAO_RE = re.compile(r'\bN\W*(?:A\W*)?O\b')
AGENDAR_ENTREGA_RE = re.compile(r'AGENDAR\s+ENTREGA\?\s*(SIM|S|NAO|N)\b')
LOCAL_ENTREGA_RE = re.compile(
    r'LOCAL\s+(?:DE|DDE|PARA)?\s*ENTREGA\s*:\s*([^\n]+(?:\n[^\n]+)*?)(?:\n(?:HOR[ÃA]RIO|CONTATO|PE\s+|NAF\s+|EMP\s+|CRM\s+|[A-Z].*?-\s*\w+@|$))',
    re.MULTILINE | re.IGNORECASE,
)
LOCAL_ENTREGA_FALLBACK_RE = re.compile(
    r'LOCAL\s+(?:DE|DDE|PARA)?\s*ENTREGA\s*:\s*(.+?)(?:\n[A-Z]+|$)',
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
TOKENS_MAIUSCULOS_RE = re.compile(r'[A-Z]+')
ITENS_PEDIDO_RE = re.compile(r'Itens do pedido', re.IGNORECASE)
OBSERVACOES_RE = re.compile(r'Observa\w*\s*:', re.IGNORECASE)
SPEC_RE = re.compile(r'([\d.,]+\s*kg\s*\|\s*[\d.,]+\s*m3(?:\s*\|\s*[\d\-xX]+)?)', re.IGNORECASE)
CODE_RE = re.compile(r'^[A-Z0-9]+(?:[-/][A-Z0-9./]+)+', re.IGNORECASE)
CODIGO_INICIO_RE = re.compile(r'^([A-Z0-9]+(?:-[A-Z0-9./]+)+)\.?', re.IGNORECASE)
CODIGO_TOKEN_RE = re.compile(r'^[A-Z0-9ÃÃ‰ÃÃ“ÃšÃƒÃ•Ã‚ÃŠÃŽÃ”Ã›Ã‡./-]+-?[A-Z0-9ÃÃ‰ÃÃ“ÃšÃƒÃ•Ã‚ÃŠÃŽÃ”Ã›Ã‡./-]*-?$', re.IGNORECASE)
CODIGO_CONTINUACAO_RE = re.compile(r'^[A-Z0-9ÃÃ‰ÃÃ“ÃšÃƒÃ•Ã‚ÃŠÃŽÃ”Ã›Ã‡./-]+-?$', re.IGNORECASE)
EMBALAGEM_RE = re.compile(r'\b(CX\s*/\s*\d+|FD\s*/\s*\d+|C\s*/\s*\d+)\b', re.IGNORECASE)
PESO_RE = re.compile(r'([\d.,]+)\s*kg', re.IGNORECASE)
VOLUME_RE = re.compile(r'([\d.,]+)\s*m[³3]', re.IGNORECASE)
TRAILING_NUM_RE = re.compile(r'\|\s*([\d.,]+)\s*$')
APOS_KG_RE = re.compile(r'kg\s*\|\s*([\d.,]+)', re.IGNORECASE)
DIMENSAO_RE = re.compile(r'(\d+[xX]\d+[xX]\d+(?:\s*cm)?)')
DIMENSAO_SIMPLES_RE = re.compile(r'(\d+[xX]\d+[xX]\d+)')
NORMALIZAR_ESPACOS_RE = re.compile(r'[^A-Z0-9]+')
UNIDADE_SPLIT_RE = re.compile(r'\bUNIDADE\b', re.IGNORECASE)
COMPONENTE_KIT_RE = re.compile(r'\b(MOP|CABO|MOUSE|TECLADO)\b', re.IGNORECASE)
EMBALAGEM_OU_DIMENSAO_RE = re.compile(r'\b(?:CX|FD|C)\s*/\s*\d+|\d+[xX]\d+[xX]\d+', re.IGNORECASE)
INFO_TECNICA_RE = re.compile(r'\b(?:CX|FD|C)\s*/\s*\d+|\d+[xX]\d+[xX]\d+|[\d.,]+\s*(?:kg|m[³3])', re.IGNORECASE)
NUMERO_MEDIDA_RE = re.compile(r'^[\d.,]+\s*(kg|m[³3]|cm)?$', re.IGNORECASE)
LINHA_HEADER_RE = re.compile(r'produto descri', re.IGNORECASE)
CODIGO_PRINCIPAL_RE = re.compile(r'^[A-Z]+-[A-Z]+-')
ITEM_SAME_LINE_RE = re.compile(
    r'^([A-Z0-9]+(?:-[A-Z0-9]+)+)\s*\|\s*(.+?)\s*UNIDADE\s+(\d+)\s+R\$\s+([\d.,]+)',
    re.IGNORECASE,
)
ITEM_CX_PONTUADO_RE = re.compile(r'^([A-Z]+-[A-Z]+-[\d\w.]+)\.\s*(.+?)\s*\|\s*CX/(.+?)$')
ITEM_CX_RE = re.compile(r'^([A-Z0-9]+(?:-[A-Z0-9]+)+)\s*\|\s*CX\s*/?\s*(.+)$', re.IGNORECASE)
UNIDADE_QTD_VALOR_RE = re.compile(r'UNIDADE\s+(\d+)\s+R\$\s+([\d.,]+)', re.IGNORECASE)
ITEM_SUBTOTAL_RE = re.compile(r'^([A-Z0-9./-]+)\s+(.*?)\s+(\d+)\s+R\$\s*([\d.,]+)', re.IGNORECASE)
SKU_NUMERO_RE = re.compile(r'^(\d{3,}[A-Z0-9./-]*)\b')
SUFIXO_NUMERICO_RE = re.compile(r'(\d+)$')
CX_FD_NUMERO_RE_TEMPLATE = r'\b(?:CX|FD)\s*/\s*{numero}\b'
CEP_RE = re.compile(r'\b(\d{2}\.?\d{3}-?\d{3}|\d{5}-?\d{3})\b')
SO_DIGITOS_RE = re.compile(r'\D')
CIDADE_UF_RE = re.compile(r'^\s*([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .\'-]{1,})\s*/\s*([A-Za-z]{2})\s*$', re.IGNORECASE)
PREFIXO_ENDERECO_RE = re.compile(r'^(ENDERECO|ENDEREÃ‡O|ENDERECO DE ENTREGA|ENDEREÃ‡O DE ENTREGA)\s*:\s*', re.IGNORECASE)
PREFIXO_ENDERECO_FLEX_RE = re.compile(r'^\s*ENDERE[^:]*:\s*', re.IGNORECASE)
UF_FINAL_RE = re.compile(r'/\s*([A-Za-z]{2})\s*$')
FD_CONTAINER_RE = re.compile(r'\bFD\b', re.IGNORECASE)
UNIDADES_EMBALAGEM_RE = re.compile(r'(?:CX|FD|C)\s*[/]?\s*(\d+)|c/?\s*(\d+)\s*und|\b(\d+)\s*und\b', re.IGNORECASE)
PESO_ALTERNATIVO_RE = re.compile(r'(?:CX|FD|C)\s*/\s*\d+\s*\|\s*([\d.,]+)', re.IGNORECASE)
M3_NORMALIZADO_RE = re.compile(r'([\d.,]+)\s*m3', re.IGNORECASE)


def _normalizar_sem_acentos(texto: str) -> str:
    texto = unicodedata.normalize('NFKD', texto or '')
    texto = ''.join(ch for ch in texto if not unicodedata.combining(ch))
    return texto.upper()


def _linhas_nao_vazias(texto: str) -> list[str]:
    return [linha.strip() for linha in (texto or '').split('\n') if linha and linha.strip()]


@dataclass
class Item:
    """Representa um item do pedido"""
    produto: str
    descricao: str
    quantidade: int
    valor_unitario: float
    subtotal: float
    info_caixa: str  # CX/240 | 1,650 kg | 0,044m3 | 31x31x45


@dataclass
class Pedido:
    """Representa um pedido completo"""
    numero: str
    cnpj_cliente: str
    local_entrega: str
    valor_total: float
    itens: List[Item]
    pagina: int
    agendar_entrega: str = "NAO"
