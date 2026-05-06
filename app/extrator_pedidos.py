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

logger = logging.getLogger(__name__)

PEDIDO_INTERNO_RE = re.compile(r'PEDIDO INTERNO:\s*PD\s*(\d+)')
CNPJ_CLIENTE_RE = re.compile(r'CNPJ/CPF:\s*([0-9./-]{11,18})')
VALOR_TOTAL_RE = re.compile(r'Total:\s*R\$\s*([\d.,]+)')
AGENDAMENTO_NAO_RE = re.compile(r'\bN\W*(?:A\W*)?O\b')
AGENDAR_ENTREGA_RE = re.compile(r'AGENDAR\s+ENTREGA\?\s*(SIM|S|NAO|N)\b')
LOCAL_ENTREGA_RE = re.compile(
    r'LOCAL\s+(?:DE|DDE|PARA)?\s*ENTREGA\s*:\s*([^\n]+(?:\n[^\n]+)*?)(?:\n(?:HOR[ÃA]RIO|CONTATO|PE\s+|NAF\s+|EMP\s+|CRM\s+|[A-Z].*?-\s*\w+@|$))',
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
CODIGO_TOKEN_RE = re.compile(r'^[A-Z0-9ÃÃ‰ÃÃ“ÃšÃƒÃ•Ã‚ÃŠÃŽÃ”Ã›Ã‡./-]+-?[A-Z0-9ÃÃ‰ÃÃ“ÃšÃƒÃ•Ã‚ÃŠÃŽÃ”Ã›Ã‡./-]*-?$', re.IGNORECASE)
CODIGO_CONTINUACAO_RE = re.compile(r'^[A-Z0-9ÃÃ‰ÃÃ“ÃšÃƒÃ•Ã‚ÃŠÃŽÃ”Ã›Ã‡./-]+-?$', re.IGNORECASE)
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


class ExtratorPedidos:
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

    def _extrair_pedido_pagina(self, texto: str, pagina: int) -> Pedido:
        """Extrai um pedido de uma pÃ¡gina"""
        
        # Extrair nÃºmero do pedido
        match = PEDIDO_INTERNO_RE.search(texto)
        numero = match.group(1) if match else "N/A"
        logger.debug("_extrair_pedido_pagina: numero=%s", numero)

        # Extrair CNPJ do cliente
        match = CNPJ_CLIENTE_RE.search(texto)
        cnpj = match.group(1) if match else "N/A"
        logger.debug("_extrair_pedido_pagina: cnpj=%s", cnpj)

        # Extrair local de entrega (da seÃ§Ã£o de ObservaÃ§Ãµes)
        local_entrega = self._extrair_local_entrega(texto)
        logger.debug(
            "_extrair_pedido_pagina: local=%s",
            local_entrega[:30] if local_entrega != "N/A" else local_entrega,
        )

        # Extrair indicacao de agendamento
        agendar_entrega = self._extrair_agendar_entrega(texto)
        logger.debug("_extrair_pedido_pagina: agendar_entrega=%s", agendar_entrega)

        # Extrair valor total
        match = VALOR_TOTAL_RE.search(texto)
        valor_total_str = match.group(1) if match else "0"
        valor_total = self._converter_valor(valor_total_str)
        logger.debug("_extrair_pedido_pagina: valor=%s", valor_total)

        # Extrair itens
        itens = self._extrair_itens(texto)
        logger.debug("_extrair_pedido_pagina: itens=%d", len(itens))

        return Pedido(
            numero=numero,
            cnpj_cliente=cnpj,
            local_entrega=local_entrega,
            valor_total=valor_total,
            itens=itens,
            pagina=pagina,
            agendar_entrega=agendar_entrega
        )

    def _extrair_agendar_entrega(self, texto: str) -> str:
        """Extrai indicacao de agendamento de entrega (SIM/NAO)."""
        if not texto:
            return "NAO"

        linhas = _linhas_nao_vazias(texto)

        # Regra principal:
        # AGENDAMENTO: NAO => NAO
        # qualquer outro conteudo apos AGENDAMENTO: => SIM
        for linha in linhas:
            linha_norm = _normalizar_sem_acentos(linha)
            if 'AGENDAMENTO' not in linha_norm:
                continue

            conteudo = linha.split(':', 1)[1].strip() if ':' in linha else ''
            conteudo_norm = _normalizar_sem_acentos(conteudo)
            if AGENDAMENTO_NAO_RE.search(conteudo_norm):
                return "NAO"
            return "SIM"

        # Fallback para formatos antigos: "Agendar Entrega? SIM/NAO"
        m_flag = AGENDAR_ENTREGA_RE.search(_normalizar_sem_acentos(texto))
        if m_flag:
            return "SIM" if m_flag.group(1).startswith('S') else "NAO"

        return "NAO"

    def _extrair_local_entrega(self, texto: str) -> str:
        """Extrai o local de entrega da seÃ§Ã£o de ObservaÃ§Ãµes"""
        
        # Procurar pela seÃ§Ã£o "LOCAL DE ENTREGA:" ou variantes
        # Alguns PDFs vÃªm com typo: "LOCAL DDE ENTREGA"
        match = LOCAL_ENTREGA_RE.search(texto)
        
        if match:
            local_raw = match.group(1).strip()
            # Limpar linhas vazias e extras
            linhas = _linhas_nao_vazias(local_raw)
            result = '\n'.join(linhas)
            logger.debug("_extrair_local_entrega: encontrado = %s...", result[:50])
            return result
        else:
            # Se nÃ£o encontrar com o padrÃ£o original, tentar busca simples
            match2 = LOCAL_ENTREGA_FALLBACK_RE.search(texto)
            if match2:
                local_raw = match2.group(1).strip()
                linhas = _linhas_nao_vazias(local_raw)
                result = '\n'.join(linhas[:3])  # Pegar apenas primeiras 3 linhas
                logger.debug("_extrair_local_entrega: encontrado (fallback) = %s...", result[:50])
                return result

            # Fallback tolerante a erro de digitacao no rotulo do local de entrega
            def _similar(a: str, b: str) -> float:
                return SequenceMatcher(None, a, b).ratio()

            linhas_txt = [ln.strip() for ln in (texto or '').split('\n')]
            idx_inicio = -1
            resto_mesma_linha = ''

            for idx, linha in enumerate(linhas_txt):
                if ':' not in linha:
                    continue
                norm = _normalizar_sem_acentos(linha)
                tokens = TOKENS_MAIUSCULOS_RE.findall(norm)
                if not tokens:
                    continue
                has_local = any(_similar(tok, 'LOCAL') >= 0.7 for tok in tokens)
                has_entrega = any(_similar(tok, 'ENTREGA') >= 0.7 for tok in tokens)
                if has_local and has_entrega:
                    idx_inicio = idx
                    resto_mesma_linha = linha.split(':', 1)[1].strip()
                    break

            if idx_inicio >= 0:
                coletadas = []
                if resto_mesma_linha:
                    coletadas.append(resto_mesma_linha)

                stop_prefixes = (
                    'HORARIO', 'CONTATO', 'PE ', 'NAF ', 'EMP ', 'CRM',
                    'RESPONSAVEL', 'RESPONSÃVEL', 'TELEFONE', 'AGENDAMENTO',
                    'OBS:', 'EDUARDO', 'DATA'
                )

                for linha in linhas_txt[idx_inicio + 1:]:
                    l = linha.strip()
                    if not l:
                        if coletadas:
                            break
                        continue
                    l_norm = _normalizar_sem_acentos(l)
                    if any(l_norm.startswith(_normalizar_sem_acentos(p)) for p in stop_prefixes):
                        break
                    coletadas.append(l)
                    if len(coletadas) >= 8:
                        break

                if coletadas:
                    result = '\n'.join(coletadas)
                    logger.debug("_extrair_local_entrega: encontrado (fuzzy) = %s...", result[:50])
                    return result
            
            logger.debug("_extrair_local_entrega: NAO ENCONTRADO")
            return "N/A"

    def _extrair_itens(self, texto: str) -> List[Item]:
        """Extrai lista de itens do pedido"""
        itens = []
        
        # Encontrar seÃ§Ã£o de itens
        match_inicio = ITENS_PEDIDO_RE.search(texto)
        match_fim = OBSERVACOES_RE.search(texto)
        
        if not match_inicio:
            return itens

        if match_fim:
            secao_itens = texto[match_inicio.end():match_fim.start()]
        else:
            secao_itens = texto[match_inicio.end():]
        linhas = secao_itens.split('\n')
        pending_desc = None
        last_header_line = None
        last_header_desc = None
        last_header_code = None
        last_code_line = None
        last_spec_line = None

        def _extrair_codigo_de_texto(texto: str) -> str:
            if not texto:
                return ""
            texto_limpo = texto.strip()
            # SKU no início da linha (ex.: "FLM-OURO-20x30. Flanela ...")
            m_inicio = CODIGO_INICIO_RE.match(texto_limpo)
            if m_inicio:
                return m_inicio.group(1).strip().rstrip('.')
            tokens = texto.strip().split()
            if not tokens:
                return ""
            code_tokens = []
            for idx, tok in enumerate(tokens[:3]):
                if CODIGO_TOKEN_RE.match(tok) and '-' in tok:
                    code_tokens.append(tok)
                    # incluir segundo token se parecer continuaÃ§Ã£o do cÃ³digo (ex: SL-)
                    if idx == 0 and len(tokens) > 1:
                        tok2 = tokens[1]
                        if CODIGO_CONTINUACAO_RE.match(tok2) and (tok2.endswith('-') or '-' in tok2):
                            code_tokens.append(tok2)
                    break
            return ' '.join(code_tokens).strip().rstrip('.')

        def _extrair_desc_de_header(header_line: str, codigo: str) -> str:
            if not header_line:
                return ""
            first = header_line.split('|')[0].strip()
            if codigo and first.startswith(codigo):
                return first[len(codigo):].lstrip(" .-").strip()
            return first

        def _extrair_info_caixa(header_line: str, spec_line: str) -> str:
            info_parts = []
            texto = ' '.join([x for x in [header_line, spec_line] if x])
            m_cx = EMBALAGEM_RE.search(texto)
            if m_cx:
                info_parts.append(m_cx.group(1).replace(' ', ''))
            m_kg = PESO_RE.search(texto)
            if m_kg:
                info_parts.append(f"{m_kg.group(1)} kg")
            # Volume (m3/m³). Alguns PDFs quebram o "m3" em outra linha.
            m_m3 = VOLUME_RE.search(texto)
            vol_m3 = self._converter_medida(m_m3.group(1)) if m_m3 else None

            # Candidato quando a linha termina com "0,044" (sem m3)
            vol_trailing = None
            if header_line:
                m_trailing = TRAILING_NUM_RE.search(header_line)
                if m_trailing:
                    vol_trailing = self._converter_medida(m_trailing.group(1))
                if vol_trailing is None:
                    m_after_kg = APOS_KG_RE.search(header_line)
                    if m_after_kg:
                        vol_trailing = self._converter_medida(m_after_kg.group(1))

            chosen_vol = None
            if vol_m3 is not None:
                if vol_trailing is not None and vol_trailing < 1 and (vol_m3 >= 1 or vol_m3 == 0):
                    chosen_vol = vol_trailing
                else:
                    chosen_vol = vol_m3
            elif vol_trailing is not None:
                chosen_vol = vol_trailing

            if chosen_vol is not None:
                info_parts.append(f"{self._formatar_decimal(chosen_vol)} m3")
            m_dim = DIMENSAO_SIMPLES_RE.search(texto)
            if m_dim:
                info_parts.append(m_dim.group(1))
            return ' | '.join(info_parts).strip()

        def _contem_dimensao(texto_linha: str | None) -> bool:
            return bool(texto_linha and DIMENSAO_SIMPLES_RE.search(texto_linha))

        def _enriquecer_info_caixa(base_info: str, linha_specs: str | None) -> str:
            info_caixa = base_info
            if not linha_specs:
                return info_caixa
            match_specs = SPEC_RE.search(linha_specs)
            if match_specs:
                return f"{base_info} | {match_specs.group(1)}"
            if not _contem_dimensao(info_caixa):
                m_dim_sep = DIMENSAO_SIMPLES_RE.search(linha_specs)
                if m_dim_sep:
                    return info_caixa.rstrip(' |') + ' | ' + m_dim_sep.group(1)
            return info_caixa

        def _criar_item(codigo: str, descricao: str, quantidade: int, valor_unitario: float, info_caixa: str) -> Item:
            return Item(
                produto=codigo,
                descricao=descricao,
                quantidade=quantidade,
                valor_unitario=valor_unitario,
                subtotal=quantidade * valor_unitario,
                info_caixa=info_caixa,
            )

        def _reset_contexto_item() -> None:
            nonlocal pending_desc, last_header_line, last_header_desc, last_header_code, last_code_line, last_spec_line
            pending_desc = None
            last_header_line = None
            last_header_desc = None
            last_header_code = None
            last_code_line = None
            last_spec_line = None

        def _eh_item_kit(codigo: str, descricao: str) -> bool:
            texto = f"{codigo or ''} {descricao or ''}".upper()
            return ('KIT-' in texto) or ('KIT ' in texto)

        def _adicionar_item_cabo_kit(
            *,
            codigo: str,
            descricao: str,
            quantidade: int,
            idx_base: int,
            item_principal: Item | None = None,
        ) -> int:
            """
            Para itens KIT, separa componentes em linhas próximas no formato:
            NOME | CX/.. ou FD/.. | ...

            - Ajusta o item principal para o primeiro componente identificado.
            - Adiciona os demais componentes com mesma quantidade (valor 0).
            - Retorna o maior deslocamento positivo consumido após idx_base.
            """
            if not _eh_item_kit(codigo, descricao):
                return 0

            def _norm(s: str) -> str:
                return NORMALIZAR_ESPACOS_RE.sub(' ', (s or '').upper()).strip()

            def _rotulo_componente_kit(codigo_kit: str, nome_comp: str) -> str:
                """
                Mantém nomes normais para kits genéricos, mas para kit de apoio
                mouse/teclado em PU retorna os códigos esperados:
                - MOUSE   -> KIT-MP-PU
                - TECLADO -> KIT-AT-PU
                """
                codigo_u = (codigo_kit or '').upper().strip()
                nome_u = _norm(nome_comp)
                if not codigo_u.startswith('KIT-'):
                    return nome_comp

                partes = [p for p in codigo_u.split('-') if p]
                if len(partes) < 2:
                    return nome_comp

                sufixo = partes[-1]
                tem_mp = 'MP' in partes
                tem_at = 'AT' in partes

                if 'MOUSE' in nome_u and tem_mp and sufixo:
                    return f'KIT-MP-{sufixo}'
                if 'TECLADO' in nome_u and tem_at and sufixo:
                    return f'KIT-AT-{sufixo}'

                # MOP/CABO: derivar variante do código do kit
                # KIT-MOP-FIB-P-CABO → MOP-FIB-P
                # KIT-MOP-ESP-G      → MOP-ESP-G
                if 'MOP' in nome_u and 'MOP' in partes:
                    idx_mop = partes.index('MOP')
                    variant_parts = [p for p in partes[idx_mop:] if p.upper() != 'CABO']
                    if len(variant_parts) > 1:
                        return '-'.join(variant_parts)

                return nome_comp

            comps: list[tuple[int, str, str]] = []
            vistos: set[tuple[str, str]] = set()
            max_offset = 0

            # Janela curta para não "engolir" o próximo item do pedido.
            # Ex.: KIT atual + linha de componente imediatamente anterior e até +3 linhas.
            for offset in range(-1, 4):
                idx = idx_base + offset
                if idx < 0 or idx >= len(linhas):
                    continue

                candidata_raw = (linhas[idx] or '').strip()
                if not candidata_raw:
                    continue

                # Alguns PDFs colam "UNIDADE ... R$ ..." na mesma linha do componente.
                # Nesse caso, usa apenas o trecho antes de "UNIDADE".
                candidata = UNIDADE_SPLIT_RE.split(candidata_raw, maxsplit=1)[0].strip()
                if not candidata:
                    continue

                if not EMBALAGEM_OU_DIMENSAO_RE.search(candidata):
                    continue

                # Evita capturar linha de outro KIT (próximo item do pedido).
                cabecalho_cand = candidata.split('|', 1)[0].strip() if '|' in candidata else candidata
                codigo_cand = _extrair_codigo_de_texto(cabecalho_cand)
                if (
                    codigo
                    and codigo_cand
                    and codigo.upper().startswith('KIT-')
                    and codigo_cand.upper().startswith('KIT-')
                    and codigo_cand.upper() != codigo.upper()
                ):
                    continue

                if '|' in candidata:
                    esquerda = candidata.split('|', 1)[0].strip().strip('.')
                else:
                    esquerda = re.split(
                        r'\b(?:CX|FD|C)\s*/\s*\d+',
                        candidata,
                        maxsplit=1,
                        flags=re.IGNORECASE,
                    )[0].strip().strip('.')
                esquerda_raw = esquerda
                if not esquerda or esquerda in ('-', '.'):
                    continue

                # Remove o SKU KIT quando ele vem junto do nome do componente.
                if codigo:
                    esquerda = re.sub(re.escape(codigo), '', esquerda, flags=re.IGNORECASE).strip()
                esquerda = re.sub(r'^[\-–—.\s]+', '', esquerda)
                esquerda = re.sub(r'[\-–—.\s]+$', '', esquerda)

                # Fallback para casos como "KIT-...CABOMOP" (sem separador).
                if not esquerda:
                    m_comp = COMPONENTE_KIT_RE.search(esquerda_raw)
                    if not m_comp:
                        # Buscar nome de componente na linha completa
                        m_comp = COMPONENTE_KIT_RE.search(candidata)
                    if m_comp:
                        esquerda = m_comp.group(1).upper()

                if not esquerda:
                    continue
                if esquerda.upper() == (codigo or '').upper():
                    continue
                if esquerda.upper().startswith('KIT-'):
                    # Quando vier "KIT-... MOP", manter só o componente final.
                    if ' ' in esquerda:
                        esquerda = esquerda.split()[-1].strip()
                    else:
                        continue

                # Filtrar nomes invalidos: descricoes longas ou medidas
                _esq_words = esquerda.split()
                if len(_esq_words) > 3 or '+' in esquerda or '=' in esquerda or '$' in esquerda:
                    continue
                if NUMERO_MEDIDA_RE.match(esquerda):
                    continue

                info_comp = _extrair_info_caixa(candidata, None)
                chave = (_norm(esquerda), _norm(info_comp))
                if chave in vistos:
                    continue
                vistos.add(chave)
                comps.append((offset, esquerda, info_comp))
                if offset > max_offset:
                    max_offset = offset

            # ---------------------------------------------------------------
            # Detecção inline: componentes (MOUSE/TECLADO/MOP/CABO)
            # embutidos em linhas pipe-delimited.
            # Sempre roda; se encontrar resultados mais completos que o loop
            # principal, substitui `comps`.
            # Ex: "... | MOUSE | CX/50 | 3,950kg | ... TECLADO | CX/40 | ..."
            # ---------------------------------------------------------------
            collected_parts = []
            inline_max_offset = 0
            for offset in range(-5, 6):
                idx = idx_base + offset
                if idx < 0 or idx >= len(linhas):
                    continue
                lr = (linhas[idx] or '').strip()
                if not lr:
                    continue
                if UNIDADE_SPLIT_RE.search(lr) and 'R$' in lr:
                    continue
                if INFO_TECNICA_RE.search(lr):
                    collected_parts.append(lr)
                    if offset > inline_max_offset:
                        inline_max_offset = offset

            if collected_parts:
                combined = ' '.join(collected_parts)
                segments = COMPONENTE_KIT_RE.split(combined)
                # segments: [before, kw1, text1, kw2, text2, ...]
                if len(segments) >= 3:
                    inline_comps: list[tuple[int, str, str]] = []
                    for k in range(1, len(segments), 2):
                        comp_name = segments[k].strip().upper()
                        comp_text = segments[k + 1] if k + 1 < len(segments) else ''
                        comp_info = _extrair_info_caixa(comp_text, None)
                        if comp_info:
                            inline_comps.append((0, comp_name, comp_info))

                    # Usar resultados inline se encontrou mais componentes OU
                    # se encontrou info mais completa (com kg/m3)
                    if inline_comps:
                        inline_has_full = any(
                            'kg' in ci.lower() and 'm3' in ci.lower()
                            for _, _, ci in inline_comps
                        )
                        orig_has_full = comps and all(
                            'kg' in ci.lower() and 'm3' in ci.lower()
                            for _, _, ci in comps
                        )
                        if (
                            len(inline_comps) > len(comps)
                            or (inline_has_full and not orig_has_full)
                            or not comps
                        ):
                            comps = inline_comps
                            max_offset = max(max_offset, inline_max_offset)
                            vistos = {(_norm(n), _norm(i)) for _, n, i in comps}

            if not comps:
                return 0

            principal_nome = ''
            desc_norm = _norm(descricao)
            if desc_norm:
                for _, nome_comp, _ in comps:
                    nome_norm = _norm(nome_comp)
                    if nome_norm and (nome_norm == desc_norm or nome_norm in desc_norm or desc_norm in nome_norm):
                        principal_nome = nome_comp
                        break
            if not principal_nome:
                anteriores = [c for c in comps if c[0] <= 0]
                alvo = sorted(anteriores, key=lambda c: (abs(c[0]), c[0]))[0] if anteriores else comps[0]
                principal_nome = alvo[1]

            if item_principal and principal_nome and (item_principal.produto or '').upper().startswith('KIT'):
                principal_rotulo = _rotulo_componente_kit(codigo, principal_nome)
                item_principal.produto = principal_rotulo
                item_principal.descricao = principal_rotulo
                for _, nome_comp, info_comp in comps:
                    if _norm(nome_comp) == _norm(principal_nome) and info_comp:
                        item_principal.info_caixa = info_comp
                        break

            for _, nome_comp, info_comp in comps:
                if _norm(nome_comp) == _norm(principal_nome):
                    continue
                nome_rotulo = _rotulo_componente_kit(codigo, nome_comp)
                itens.append(Item(
                    produto=nome_rotulo,
                    descricao=nome_rotulo,
                    quantidade=quantidade,
                    valor_unitario=0.0,
                    subtotal=0.0,
                    info_caixa=info_comp or ''
                ))

            return max_offset
        
        i = 0
        while i < len(linhas):
            linha = linhas[i].strip()
            linha_upper = linha.upper()
            linha_lower = linha.lower()
            added = False

            # Ignorar cabeÃ§alhos comuns
            if not linha or linha_lower in ('unidade', 'principal') or LINHA_HEADER_RE.match(linha):
                i += 1
                continue

            # Atualizar contexto para linhas de cabeÃ§alho (com CX/FD)
            if ('CX/' in linha or 'FD/' in linha) and ('UNIDADE' not in linha_upper):
                last_header_line = linha
                codigo_tmp = _extrair_codigo_de_texto(linha.split('|')[0].strip())
                last_header_desc = _extrair_desc_de_header(linha, codigo_tmp)
                last_header_code = codigo_tmp
            # Atualizar contexto para linha de specs
            if (('m3' in linha_lower or 'm\u00b3' in linha_lower or _contem_dimensao(linha)) and ('UNIDADE' not in linha_upper)):
                last_spec_line = linha
            # Atualizar contexto para linha de cÃ³digo isolado
            # (alguns PDFs trazem "CODIGO descricao" na linha anterior ao "UNIDADE ...")
            if ('UNIDADE' not in linha_upper) and ('R$' not in linha):
                head = linha.split('|')[0].strip() if '|' in linha else linha.strip()
                codigo_head = _extrair_codigo_de_texto(head)
                if codigo_head:
                    last_code_line = head
                    if not pending_desc:
                        desc_head = head
                        if desc_head.upper().startswith(codigo_head.upper()):
                            desc_head = desc_head[len(codigo_head):].lstrip(" .-").strip()
                        if desc_head:
                            pending_desc = desc_head

            # Guardar linha de descriÃ§Ã£o (sem cÃ³digo e sem unidade)
            if (not CODE_RE.match(linha)) and ('UNIDADE' not in linha_upper) and ('|' not in linha) and ('R$' not in linha):
                pending_desc = linha.strip()
                i += 1
                continue
            
            # Procurar por linha que comeÃ§a com cÃ³digo (letra+hÃ­fen)
            # Ex: ELM-DF-70x100. Esponja de Limpeza... | CX/240 | 1,650
            if CODIGO_PRINCIPAL_RE.match(linha):
                # Caso: UNIDADE na mesma linha (cÃ³digo | CX/... | ... UNIDADE qtd R$ ...)
                match_same = ITEM_SAME_LINE_RE.search(linha)
                if match_same:
                    codigo = match_same.group(1)
                    descricao_parte1 = pending_desc or ""
                    info_caixa = match_same.group(2).strip()
                    quantidade = int(match_same.group(3))
                    valor_unitario = self._converter_valor(match_same.group(4))

                    # Se a linha de specs vier separada
                    if i + 1 < len(linhas):
                        linha_specs = linhas[i + 1].strip()
                        if SPEC_RE.search(linha_specs) and ('kg' not in info_caixa.lower() or 'm3' not in info_caixa.lower()):
                            info_caixa = _enriquecer_info_caixa(info_caixa, linha_specs)
                            i += 1

                    item = _criar_item(codigo, descricao_parte1, quantidade, valor_unitario, info_caixa)
                    itens.append(item)
                    pending_desc = None

                    offset_fardo = _adicionar_item_cabo_kit(
                        codigo=codigo,
                        descricao=descricao_parte1,
                        quantidade=quantidade,
                        idx_base=i,
                        item_principal=item,
                    )
                    i += 1 + max(0, offset_fardo)
                    added = True
                    continue

                # Extrair cÃ³digo e info de caixa da mesma linha
                match = ITEM_CX_PONTUADO_RE.search(linha)
                if match:
                    codigo = match.group(1)
                    descricao_parte1 = match.group(2).strip() if match.group(2) else ""
                    cx_info_parcial = f"CX/{match.group(3)}"
                    
                    # PrÃ³xima linha tem: UNIDADE quantidade R$ valor data
                    if i + 1 < len(linhas):
                        prox_linha = linhas[i + 1].strip()
                        match_qtde = UNIDADE_QTD_VALOR_RE.search(prox_linha)
                        
                        if match_qtde:
                            quantidade = int(match_qtde.group(1))
                            valor_unitario = self._converter_valor(match_qtde.group(2))
                            
                            # PrÃ³xima linha tem: peso kg | volume m3 | dimensÃµes
                            # Ex: 3.240 kg | 0,044m3 | 31x31x45
                            info_caixa = cx_info_parcial
                            if i + 2 < len(linhas):
                                linha_specs = linhas[i + 2].strip()
                                info_caixa = _enriquecer_info_caixa(cx_info_parcial, linha_specs)
                            
                            item = _criar_item(codigo, descricao_parte1, quantidade, valor_unitario, info_caixa)
                            itens.append(item)
                            pending_desc = None

                            offset_fardo = _adicionar_item_cabo_kit(
                                codigo=codigo,
                                descricao=descricao_parte1,
                                quantidade=quantidade,
                                idx_base=i,
                                item_principal=item,
                            )
                            i += 3 + max(0, offset_fardo - 2)
                            added = True
                            continue

                # Caso: cÃ³digo | CX/... (sem ponto) e UNIDADE na linha seguinte
                match_cx = ITEM_CX_RE.search(linha)
                if match_cx:
                    codigo = match_cx.group(1)
                    descricao_parte1 = pending_desc or ""
                    cx_info_parcial = f"CX/{match_cx.group(2).strip()}"

                    if i + 1 < len(linhas):
                        prox_linha = linhas[i + 1].strip()
                        match_qtde = UNIDADE_QTD_VALOR_RE.search(prox_linha)
                        if match_qtde:
                            quantidade = int(match_qtde.group(1))
                            valor_unitario = self._converter_valor(match_qtde.group(2))

                            info_caixa = cx_info_parcial
                            if i + 2 < len(linhas):
                                linha_specs = linhas[i + 2].strip()
                                info_caixa = _enriquecer_info_caixa(cx_info_parcial, linha_specs)

                            item = _criar_item(codigo, descricao_parte1, quantidade, valor_unitario, info_caixa)
                            itens.append(item)
                            pending_desc = None

                            offset_fardo = _adicionar_item_cabo_kit(
                                codigo=codigo,
                                descricao=descricao_parte1,
                                quantidade=quantidade,
                                idx_base=i,
                                item_principal=item,
                            )
                            i += 3 + max(0, offset_fardo - 2)
                            added = True
                            continue

            # Fallback: linha com UNIDADE (nao capturada acima)
            if (not added) and ('UNIDADE' in linha_upper) and ('R$' in linha):
                match_qtde = UNIDADE_QTD_VALOR_RE.search(linha)
                if match_qtde:
                    quantidade = int(match_qtde.group(1))
                    valor_unitario = self._converter_valor(match_qtde.group(2))

                    # Codigo e descricao
                    before = linha.split('UNIDADE')[0].strip()
                    codigo = _extrair_codigo_de_texto(before)
                    descricao = ""
                    if codigo:
                        descricao = before.replace(codigo, '').strip()
                    if not codigo and last_code_line:
                        codigo = _extrair_codigo_de_texto(last_code_line.split('|')[0].strip())
                    if not descricao:
                        descricao = last_header_desc or pending_desc or ""
                    if not codigo and last_header_code:
                        codigo = last_header_code
                        m_num = SUFIXO_NUMERICO_RE.search(descricao)
                        numero_final = m_num.group(1) if m_num else ""
                        veio_de_embalagem = bool(
                            numero_final and re.search(CX_FD_NUMERO_RE_TEMPLATE.format(numero=re.escape(numero_final)), descricao, re.IGNORECASE)
                        )
                        if numero_final and numero_final not in codigo and not veio_de_embalagem:
                            codigo = f"{codigo} {m_num.group(1)}"
                    if not codigo:
                        codigo = descricao or before or "ITEM"

                    # Info de caixa: usa header e specs proximos
                    spec_line = None
                    if i + 1 < len(linhas):
                        spec_line = linhas[i + 1].strip()
                        if not (('m3' in spec_line.lower()) or ('m\u00b3' in spec_line.lower()) or _contem_dimensao(spec_line)):
                            spec_line = None
                    info_caixa = _extrair_info_caixa(last_header_line, spec_line or last_spec_line)

                    item = _criar_item(codigo, descricao, quantidade, valor_unitario, info_caixa)
                    itens.append(item)
                    _reset_contexto_item()

                    offset_fardo = _adicionar_item_cabo_kit(
                        codigo=codigo,
                        descricao=descricao,
                        quantidade=quantidade,
                        idx_base=i,
                        item_principal=item,
                    )
                    i += 1 + max(0, offset_fardo)
                    continue

            # Fallback: linha com subtotal (R$) sem "UNIDADE"
            # Ex: PICOLO.01.30 PCT 250 GR 1000 R$ 2.590,00 31/01/2026
            if (not added) and ('R$' in linha) and ('UNIDADE' not in linha_upper):
                match_sub = ITEM_SUBTOTAL_RE.search(linha)
                if match_sub:
                    codigo = match_sub.group(1).strip().rstrip('.')
                    descricao = (match_sub.group(2) or "").strip()
                    quantidade = int(match_sub.group(3))
                    subtotal = self._converter_valor(match_sub.group(4))
                    valor_unitario = (subtotal / quantidade) if quantidade else 0.0

                    # Caso comum em alguns PDFs: "MR 00022 CAIXA 10 50 R$ ..."
                    # O SKU vem quebrado em 2 tokens (prefixo + numero).
                    if codigo and re.fullmatch(r'[A-Z]{1,4}', codigo):
                        m_sku_num = SKU_NUMERO_RE.match(descricao)
                        if m_sku_num:
                            codigo = f"{codigo} {m_sku_num.group(1)}"
                            if last_header_desc:
                                descricao = last_header_desc

                    # Se a linha nao traz um codigo completo, herdar do header
                    if last_header_code and (not codigo or len(codigo) <= 3 or not re.search(r'[-./]', codigo)):
                        codigo = last_header_code
                        if last_header_desc:
                            descricao = last_header_desc

                    if not descricao:
                        descricao = last_header_desc or pending_desc or ""

                    spec_line = last_spec_line
                    if i + 1 < len(linhas):
                        prox = linhas[i + 1].strip()
                        if _contem_dimensao(prox):
                            spec_line = prox if not spec_line else f"{spec_line} | {prox}"

                    info_caixa = _extrair_info_caixa(last_header_line, spec_line)
                    if not info_caixa:
                        info_caixa = _extrair_info_caixa(linha, spec_line)

                    item = Item(
                        produto=codigo,
                        descricao=descricao,
                        quantidade=quantidade,
                        valor_unitario=valor_unitario,
                        subtotal=subtotal,
                        info_caixa=info_caixa
                    )
                    itens.append(item)
                    _reset_contexto_item()

                    offset_fardo = _adicionar_item_cabo_kit(
                        codigo=codigo,
                        descricao=descricao,
                        quantidade=quantidade,
                        idx_base=i,
                        item_principal=item,
                    )
                    i += 1 + max(0, offset_fardo)
                    continue

            i += 1

        return itens

    def formatar_pedido_html(self, pedido: Pedido, nome_empresa: str = "Log\u00edstica de Transporte") -> str:
        """
        Args:
            pedido: Objeto Pedido a ser formatado
            nome_empresa: Nome da empresa (padrÃ£o: LogÃ­stica de Transporte)
            
        Returns:
            String formatada em HTML com <br> tags
        """
        # Para pedido Ãºnico, usar a mesma lÃ³gica de agrupamento/mistura
        return self.formatar_pedidos_agrupados_html([pedido], nome_empresa)

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

    def _info_caixa_ajustada(self, item: Item) -> str:
        """
        Ajusta peso/cubagem proporcional para fardo parcial.
        """
        info = item.info_caixa or ''
        units_per_box, peso_box, vol_box, dims, container = self._parse_info_caixa(info)
        if (
            container == 'FD'
            and units_per_box
            and peso_box is not None
            and vol_box is not None
            and item.quantidade
            and item.quantidade < units_per_box
        ):
            peso_adj = (peso_box * item.quantidade) / units_per_box
            vol_adj = vol_box
            parts = [f'FD/{units_per_box}', f'{self._formatar_decimal(peso_adj)} kg', f'{self._formatar_decimal(vol_adj)} m3']
            if dims:
                parts.append(dims)
            return ' | '.join(parts)

        return info

    def _extrair_componentes_local(self, local_entrega: str):
        """Extrai rua/numero, CEP e cidade/UF do local de entrega."""
        if not local_entrega:
            return None, None, None

        linhas = _linhas_nao_vazias(local_entrega)

        # Cidade/UF
        def _match_cidade_uf(linha: str):
            if not linha:
                return None
            return CIDADE_UF_RE.match(linha.strip())

        cidade_uf = None
        idx_cidade_uf = -1
        # Em blocos com múltiplas ocorrências, a última costuma ser a do destinatário.
        for idx, linha in enumerate(linhas):
            m_city = _match_cidade_uf(linha)
            if m_city:
                cidade_uf = f"{m_city.group(1).strip()}/{m_city.group(2).strip()}"
                idx_cidade_uf = idx

        # CEP
        cep = None
        cep_candidates = []
        for idx, linha in enumerate(linhas):
            for m_cep in CEP_RE.finditer(linha):
                digitos = SO_DIGITOS_RE.sub('', m_cep.group(1) or '')
                if len(digitos) != 8:
                    continue
                cep_fmt = digitos[:5] + '-' + digitos[5:]
                has_label = bool(re.search(r'\bCEP\b', linha, re.IGNORECASE))
                dist_city = abs(idx - idx_cidade_uf) if idx_cidade_uf >= 0 else 9999
                cep_candidates.append(
                    {
                        "cep": cep_fmt,
                        "idx": idx,
                        "has_label": has_label,
                        "dist_city": dist_city,
                    }
                )
        if cep_candidates:
            cep_candidates.sort(
                key=lambda c: (
                    int(c.get("dist_city", 9999)),
                    0 if c.get("has_label") else 1,
                    int(c.get("idx", 9999)),
                )
            )
            cep_val = str(cep_candidates[0].get("cep", "") or "").strip()
            cep = cep_val or None

        # Rua/numero
        rua_num = None
        endereco_fallback = None
        for linha in linhas:
            if re.search(r'\bCEP\b', linha, re.IGNORECASE):
                continue
            if _match_cidade_uf(linha):
                continue
            if PREFIXO_ENDERECO_RE.match(linha):
                endereco_fallback = PREFIXO_ENDERECO_RE.sub('', linha).strip()
            if re.search(r'\d', linha):
                rua_num = PREFIXO_ENDERECO_RE.sub('', linha).strip()
                break
        if not rua_num and endereco_fallback:
            rua_num = endereco_fallback

        # Fallback robusto para linhas como "ENDEREÇO: ... S/N" e afins.
        if not rua_num:
            for linha in linhas:
                linha_limpa = (linha or '').strip()
                if not linha_limpa:
                    continue
                if re.search(r'\bCEP\b', linha_limpa, re.IGNORECASE):
                    continue
                if CIDADE_UF_RE.match(linha_limpa):
                    continue
                sem_prefixo = PREFIXO_ENDERECO_FLEX_RE.sub('', linha_limpa).strip()
                tinha_prefixo = sem_prefixo != linha_limpa
                if tinha_prefixo and sem_prefixo:
                    endereco_fallback = sem_prefixo
                    if re.search(r'\d', sem_prefixo) or re.search(r'\bS\s*/?\s*N\b', sem_prefixo, re.IGNORECASE):
                        rua_num = sem_prefixo
                        break

        if not rua_num and endereco_fallback:
            rua_num = endereco_fallback

        # Exibir somente rua e número, sem o prefixo "ENDEREÇO:".
        if rua_num:
            rua_num = PREFIXO_ENDERECO_FLEX_RE.sub('', rua_num).strip()
            rua_num = re.sub(r'^\s*[-–—]\s*', '', rua_num)

        return rua_num, cep, cidade_uf

    def _formatar_local_entrega(self, local_entrega: str) -> str:
        """
        Retorna apenas CEP, rua/numero e cidade/UF do local de entrega.
        """
        rua_num, cep, cidade_uf = self._extrair_componentes_local(local_entrega)

        partes = []
        if rua_num:
            partes.append(rua_num)
        if cep:
            partes.append(f'CEP: {cep}')
        if cidade_uf:
            partes.append(cidade_uf)

        return '\n'.join(partes).strip()

    def normalizar_local_entrega(self, local_entrega: str) -> str:
        """Normaliza o local de entrega para comparacoes e exibicao."""
        return self._formatar_local_entrega(local_entrega)

    def obter_cep_local_entrega(self, local_entrega: str) -> str:
        """Retorna o CEP encontrado no local de entrega."""
        _, cep, _ = self._extrair_componentes_local(local_entrega)
        return cep

    def obter_uf_local_entrega(self, local_entrega: str) -> str:
        """Retorna a UF encontrada no local de entrega."""
        _, _, cidade_uf = self._extrair_componentes_local(local_entrega)
        if not cidade_uf:
            return ""
        m = UF_FINAL_RE.search(str(cidade_uf))
        return m.group(1).upper() if m else ""

    def chave_local_entrega(self, local_entrega: str) -> str:
        """Gera chave de comparacao para locais de entrega."""
        rua_num, cep, cidade_uf = self._extrair_componentes_local(local_entrega)

        if rua_num:
            chave = rua_num
        elif cep and cidade_uf:
            chave = f"{cep} {cidade_uf}"
        elif cep:
            chave = f"CEP {cep}"
        elif cidade_uf:
            chave = cidade_uf
        else:
            chave = self._formatar_local_entrega(local_entrega)

        chave = re.sub(r'\s+', ' ', (chave or '')).strip().upper()
        return chave

    def _parse_info_caixa(self, info: str):
        """Extrai unidades por embalagem, peso, cubagem, dimensÃµes e tipo (CX/FD) da info_caixa"""
        info = (info or '').strip()
        if not info:
            return None, None, None, '', 'CX'

        container = 'FD' if FD_CONTAINER_RE.search(info) else 'CX'

        m_u = UNIDADES_EMBALAGEM_RE.search(info)
        units_per_box = int(next(g for g in m_u.groups() if g)) if m_u else None

        m_peso = PESO_RE.search(info)
        peso_box = self._converter_medida(m_peso.group(1)) if m_peso else None

        # Alguns PDFs trazem o peso logo apos o CX/FD sem "kg"
        m_alt = PESO_ALTERNATIVO_RE.search(info)
        peso_alt = self._converter_medida(m_alt.group(1)) if m_alt else None

        m_vol = M3_NORMALIZADO_RE.search(info)
        vol_box = self._converter_medida(m_vol.group(1)) if m_vol else None

        m_dim = DIMENSAO_RE.search(info)
        dims = m_dim.group(1) if m_dim else ''

        if peso_alt is not None:
            if peso_box is None:
                peso_box = peso_alt
            elif peso_box < 1 and peso_alt >= 1:
                peso_box = peso_alt
            elif peso_alt > 0 and peso_alt < peso_box:
                # Preferir o peso menor quando o valor apos CX/FD parece o correto (casos de esponja/pano)
                peso_box = peso_alt

        return units_per_box, peso_box, vol_box, dims, container

    def _empacotar_complementos(self, leftovers, box_sizes):
        """
        Empacota sobras em caixas por cubagem (volume).
        Retorna lista de caixas com itens e dimensÃµes escolhidas.
        """
        if not leftovers:
            return []

        sizes = [b for b in box_sizes if b.get('volume') and b.get('volume') > 0]
        if not sizes:
            return []

        max_box = max(sizes, key=lambda b: b['volume'])
        max_volume = max_box['volume']
        sizes_sorted = sorted(sizes, key=lambda b: b['volume'])

        # Ordenar itens por volume unitÃ¡rio (maior primeiro)
        leftovers_sorted = sorted(leftovers, key=lambda x: x['vol_unit'], reverse=True)

        bins = []
        for item in leftovers_sorted:
            qty = int(item.get('qty') or 0)
            v_unit = float(item.get('vol_unit') or 0.0)
            w_unit = float(item.get('peso_unit') or 0.0)
            sku = item.get('sku')

            if qty <= 0 or v_unit <= 0:
                continue

            # Preencher caixas existentes
            for b in bins:
                if qty <= 0:
                    break
                free = b['capacity'] - b['used_volume']
                if free <= 0:
                    continue
                fit_units = int((free + 1e-9) / v_unit)
                if fit_units <= 0:
                    continue
                take = min(qty, fit_units)
                b['items'][sku] = b['items'].get(sku, 0) + take
                b['used_volume'] += take * v_unit
                b['weight'] += take * w_unit
                qty -= take

            # Criar novas caixas quando necessÃ¡rio
            if qty > 0:
                units_per_bin = int((max_volume + 1e-9) / v_unit)
                if units_per_bin <= 0:
                    units_per_bin = 1
                while qty > 0:
                    take = min(qty, units_per_bin)
                    b = {
                        'capacity': max_volume,
                        'used_volume': take * v_unit,
                        'weight': take * w_unit,
                        'items': {sku: take}
                    }
                    bins.append(b)
                    qty -= take

        # Escolher o menor tamanho de caixa que comporta o volume usado
        for b in bins:
            used = b['used_volume']
            chosen = None
            for s in sizes_sorted:
                if s['volume'] + 1e-9 >= used:
                    chosen = s
                    break
            if not chosen:
                chosen = max_box
            b['box_volume'] = chosen['volume']
            b['dims'] = chosen.get('dims', '')
            b['small'] = chosen.get('small', False)
            if b['small']:
                b['weight'] += self.CAIXA_PEQUENA_PESO

        return bins

    def _calcular_caixas_agrupadas(self, pedidos: List[Pedido]):
        """
        Calcula caixas, peso e cubagem agrupando por tipo de caixa.
        Retorna (grupos_caixa, caixas_complementares, total_boxes, total_volume, total_weight, total_valor).
        """
        grupos_caixa = {}
        total_valor = 0.0

        for pedido in pedidos:
            total_valor += pedido.valor_total
            for item in pedido.itens:
                key = item.produto.strip()
                info = (item.info_caixa or '').strip()
                units_per_box, peso_box, vol_box, dims, container = self._parse_info_caixa(info)

                is_disco = self._is_disco(item)
                caixa_key = info or '(sem caixa)'
                g = grupos_caixa.setdefault(caixa_key, {
                    'units_per_box': units_per_box,
                    'peso_box': peso_box,
                    'vol_box': vol_box,
                    'dims': dims,
                    'container': container,
                    'skus': {}
                })

                # preencher valores faltantes se apareÃ§am em outro item
                if g['units_per_box'] is None and units_per_box is not None:
                    g['units_per_box'] = units_per_box
                if g['peso_box'] is None and peso_box is not None:
                    g['peso_box'] = peso_box
                if g['vol_box'] is None and vol_box is not None:
                    g['vol_box'] = vol_box
                if not g['dims'] and dims:
                    g['dims'] = dims
                if not g.get('container') and container:
                    g['container'] = container

                sk = g['skus'].setdefault(key, {
                    'produto': item.produto,
                    'quantidade': 0,
                    'no_mix': is_disco,
                    'box_sizes': [{'volume': vol_box or 0.0, 'dims': dims or '', 'small': False}] if is_disco else None
                })
                sk['quantidade'] += int(item.quantidade)

        # Validar: todas as caixas devem conter units_per_box e vol_box
        missing_info = []
        for caixa_key, info in grupos_caixa.items():
            if info.get('units_per_box') is None or info.get('vol_box') is None:
                missing_info.extend([v['produto'] for v in info.get('skus', {}).values()])

        if missing_info:
            raise ValueError('Faltam informaÃ§Ãµes de caixa (CX ou cubagem) para SKUs: {}'.format(', '.join(sorted(set(missing_info)))))

        total_full_boxes = 0
        total_weight = 0.0
        total_volume = 0.0
        leftovers = []

        # Caixas completas e sobras por SKU
        for caixa_key, info in grupos_caixa.items():
            u = info['units_per_box'] or 0
            peso_box = info['peso_box'] or 0.0
            vol_box = info['vol_box'] or 0.0

            full_boxes = 0
            for sku, v in info['skus'].items():
                q = v['quantidade']
                if u <= 0:
                    continue
                full = q // u
                rem = q % u
                full_boxes += full
                if rem > 0:
                    vol_unit = (vol_box / u) if u else 0.0
                    peso_unit = (peso_box / u) if u else 0.0
                    leftovers.append({
                        'sku': sku,
                        'qty': rem,
                        'vol_unit': vol_unit,
                        'peso_unit': peso_unit,
                        'container': info.get('container') or 'CX',
                        'no_mix': v.get('no_mix', False),
                        'box_sizes': v.get('box_sizes')
                    })

            info['calculated'] = {
                'full_boxes': full_boxes
            }

            total_full_boxes += full_boxes
            total_volume += full_boxes * vol_box
            total_weight += full_boxes * peso_box

        # Tamanhos de caixas disponÃ­veis para complementares
        box_sizes_by_type = {}
        seen_by_type = {}
        for info in grupos_caixa.values():
            vol_box = info.get('vol_box') or 0.0
            dims = info.get('dims') or ''
            container = info.get('container') or 'CX'
            if vol_box > 0:
                seen = seen_by_type.setdefault(container, set())
                key = (vol_box, dims)
                if key not in seen:
                    box_sizes_by_type.setdefault(container, []).append({'volume': vol_box, 'dims': dims, 'small': False})
                    seen.add(key)

        # caixa pequena padrÃ£o sÃ³ para CX
        seen_cx = seen_by_type.setdefault('CX', set())
        small_key = (self.CAIXA_PEQUENA_VOL, self.CAIXA_PEQUENA_DIMS)
        if small_key not in seen_cx:
            box_sizes_by_type.setdefault('CX', []).append({'volume': self.CAIXA_PEQUENA_VOL, 'dims': self.CAIXA_PEQUENA_DIMS, 'small': True})
            seen_cx.add(small_key)

        caixas_complementares = []
        leftovers_by_type = {}
        for item in leftovers:
            if item.get('no_mix'):
                # processar individualmente
                sizes = item.get('box_sizes') or box_sizes_by_type.get(item.get('container') or 'CX', [])
                if not sizes:
                    continue
                bins = self._empacotar_complementos([item], sizes)
                for b in bins:
                    b['container'] = item.get('container') or 'CX'
                caixas_complementares.extend(bins)
            else:
                leftovers_by_type.setdefault(item.get('container') or 'CX', []).append(item)

        for container, itens_left in leftovers_by_type.items():
            sizes = box_sizes_by_type.get(container, [])
            if not sizes:
                continue
            bins = self._empacotar_complementos(itens_left, sizes)
            for b in bins:
                b['container'] = container
            caixas_complementares.extend(bins)

        total_boxes = total_full_boxes + len(caixas_complementares)
        total_volume += sum(c.get('box_volume', 0.0) for c in caixas_complementares)
        total_weight += sum(c.get('weight', 0.0) for c in caixas_complementares)

        return grupos_caixa, caixas_complementares, total_boxes, total_volume, total_weight, total_valor

    def obter_pedidos(self) -> List[Pedido]:
        """Retorna a lista de pedidos extraÃ­dos"""
        return self.pedidos

    def formatar_pedidos_agrupados_html(self, pedidos: List[Pedido], nome_empresa: str = "Log\u00edstica de Transporte") -> str:
        """
        Formata vÃ¡rios pedidos (do mesmo arquivo) agrupados por local_entrega.
        Agrupa produtos iguais, soma quantidades, calcula caixas, peso e cubagem.
        Implementa caixa pequena (complementar) para sobras que caibam.
        """
        if not pedidos:
            return ""

        # Usar CNPJ e local do primeiro pedido (assume que todos tÃªm o mesmo local)
        cnpj = pedidos[0].cnpj_cliente
        local_entrega = pedidos[0].local_entrega

        grupos_caixa, caixas_complementares, total_boxes, total_volume, total_weight, total_valor = self._calcular_caixas_agrupadas(pedidos)

        def _display_label(sku_key, sku_info):
            label = (sku_info or {}).get('produto') or sku_key
            if label:
                # Se o parser trouxe descrição junto do SKU, manter apenas o código.
                m_sku = re.match(r'^([A-Z0-9]+(?:-[A-Z0-9./]+)+)\.?', str(label).strip(), re.IGNORECASE)
                if m_sku:
                    label = m_sku.group(1).rstrip('.')
            if label:
                label_up = label.upper()
                if len(label) <= 3 or label_up in ('PCT', 'UN', 'UND', 'UNIDADE', 'CX', 'FD'):
                    desc = (sku_info or {}).get('descricao') or ''
                    if desc:
                        return desc
            return label

        # Mapa de labels para complementares
        sku_label_map = {}
        for info in grupos_caixa.values():
            for sku_key, sku_info in info.get('skus', {}).items():
                sku_label_map[sku_key] = _display_label(sku_key, sku_info)

        # Montar linhas do HTML agrupado
        linhas = []
        linhas.append('###' + nome_empresa + ':<br><br><br><br>')
        linhas.append('CNPJ/CPF: ' + cnpj + '<br>')
        local_formatado = self._formatar_local_entrega(local_entrega) or local_entrega
        linhas.append(local_formatado.replace('\n', '<br>') + '<br>')

        linhas.append('<br>')
        linhas.append('- VOL: {}<br>'.format(total_boxes))
        linhas.append('- CUBAGEM: {} m3<br>'.format(self._formatar_decimal(total_volume)))
        # Peso total arredondado para cima na 1a casa decimal (ex: 0,675 -> 0,700)
        peso_total_arred = self._arredondar_cima(total_weight, casas=1)
        linhas.append('- PESO: {} kg<br>'.format(self._formatar_decimal(peso_total_arred)))
        linhas.append('- TOTAL: R$ {}<br>'.format(self._formatar_decimal(total_valor, casas=2)))
        linhas.append('<br>')
        agendar_entrega = 'SIM' if any((getattr(p, 'agendar_entrega', 'NAO') or 'NAO').upper().startswith('S') for p in pedidos) else 'NAO'
        linhas.append('- Agendar Entrega? {}<br>'.format(agendar_entrega))
        linhas.append('<br>')
        linhas.append('###Sugest\u00e3o de Separa\u00e7\u00e3o<br>')
        linhas.append('<br>')

        # Separar caixas completas e complementares por tipo
        caixas_completas_lista = []
        
        for caixa_key, info in grupos_caixa.items():
            calc = info.get('calculated', {})
            full_boxes = calc.get('full_boxes', 0)
            if full_boxes == 0:
                continue
            
            u_per_box = info.get('units_per_box') or 0
            peso_box = info.get('peso_box') or 0.0
            vol_box = info.get('vol_box') or 0.0
            dims = info.get('dims') or ''
            container = info.get('container') or 'CX'

            # Caixas completas
            rotulo = 'Fardos' if container == 'FD' else 'Caixas fechadas'
            desc_completas = '{} - {} kg - {} m3'.format(
                rotulo,
                self._formatar_decimal(peso_box), 
                self._formatar_decimal(vol_box),
            )
            if dims:
                desc_completas += ' - {}'.format(dims)
            
            skus_full = {}
            if u_per_box > 0:
                for sku, v in info['skus'].items():
                    q = v.get('quantidade', 0)
                    full_sku = q // u_per_box
                    if full_sku > 0:
                        skus_full[sku] = {'produto': v['produto'], 'quantidade': full_sku * u_per_box}
            if not skus_full:
                skus_full = info['skus']

            caixas_completas_lista.append({
                'quantidade': full_boxes,
                'descricao': desc_completas,
                'skus': skus_full
            })
        
        # Mostrar TODAS as caixas completas primeiro
        if caixas_completas_lista:
            for idx, caixa_info in enumerate(caixas_completas_lista, 1):
                linhas.append('{} x {} <br>'.format(caixa_info['quantidade'], caixa_info['descricao']))
                for sku, v in caixa_info['skus'].items():
                    label = _display_label(sku, v)
                    linhas.append('{}: {} und<br>'.format(label, v['quantidade']))
                linhas.append('<br>')
        
        # Mostrar TODAS as caixas complementares depois
        if caixas_complementares:
            for caixa_info in caixas_complementares:
                items = caixa_info.get('items', {})
                total_units_comp = sum(items.values())
                peso_comp = caixa_info.get('weight', 0.0)
                vol_comp = caixa_info.get('box_volume', 0.0)
                dims_comp = caixa_info.get('dims', '')

                container = caixa_info.get('container') or 'CX'
                rotulo = 'Fardo complementar' if container == 'FD' else 'Caixa complementar'
                peso_fmt = self._formatar_decimal(peso_comp)
                desc_comp = '{} - {} kg - {} m3'.format(
                    rotulo,
                    peso_fmt,
                    self._formatar_decimal(vol_comp),
                )
                if dims_comp:
                    desc_comp += ' - {}'.format(dims_comp)

                linhas.append('1 x {} <br>'.format(desc_comp))
                for sku, qtd_comp in items.items():
                    label = sku_label_map.get(sku, sku)
                    linhas.append('{}: {} und<br>'.format(label, qtd_comp))
                linhas.append('<br>')

        return ''.join(linhas)
