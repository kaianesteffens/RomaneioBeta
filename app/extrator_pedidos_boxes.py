"""
Mixin de parsing de info_caixa, empacotamento e montagem do HTML agrupado.
"""

import re
from typing import List

from extrator_pedidos_common import (
    Item,
    Pedido,
    FD_CONTAINER_RE,
    UNIDADES_EMBALAGEM_RE,
    PESO_RE,
    PESO_ALTERNATIVO_RE,
    M3_NORMALIZADO_RE,
    DIMENSAO_RE,
)


class BoxesMixin:
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

    def formatar_pedido_html(self, pedido: Pedido, nome_empresa: str = "Logística de Transporte") -> str:
        """
        Args:
            pedido: Objeto Pedido a ser formatado
            nome_empresa: Nome da empresa (padrÃ£o: LogÃ­stica de Transporte)

        Returns:
            String formatada em HTML com <br> tags
        """
        # Para pedido Ãºnico, usar a mesma lÃ³gica de agrupamento/mistura
        return self.formatar_pedidos_agrupados_html([pedido], nome_empresa)

    def formatar_pedidos_agrupados_html(self, pedidos: List[Pedido], nome_empresa: str = "Logística de Transporte") -> str:
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
        linhas.append('###Sugestão de Separação<br>')
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
