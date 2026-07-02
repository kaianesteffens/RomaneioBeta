"""
Mixin de parsing de pagina/itens do extrator de pedidos.
"""

import re
import logging
from typing import List
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
)

logger = logging.getLogger("extrator_pedidos")


class ParseMixin:
    def _extrair_pedido_pagina(self, texto: str, pagina: int) -> Pedido:
        """Extrai um pedido de uma pÃ¡gina"""

        # Extrair nÃºmero do pedido
        match = PEDIDO_INTERNO_RE.search(texto)
        numero = match.group(1) if match else "N/A"
        logger.debug("_extrair_pedido_pagina: numero=%s", numero)

        match = CNPJ_CLIENTE_RE.search(texto)
        cnpj = match.group(1) if match else "N/A"
        logger.debug("_extrair_pedido_pagina: cnpj_encontrado=%s", "sim" if match else "nao")

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
                    'RESPONSAVEL', 'RESPONSÃVEL', 'TELEFONE', 'AGENDAMENTO',
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
            if (('m3' in linha_lower or 'm³' in linha_lower or _contem_dimensao(linha)) and ('UNIDADE' not in linha_upper)):
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
                        if not (('m3' in spec_line.lower()) or ('m³' in spec_line.lower()) or _contem_dimensao(spec_line)):
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
