"""
Mixin de extracao/normalizacao do local de entrega.
"""

import re

from extrator_pedidos_common import (
    _linhas_nao_vazias,
    CEP_RE,
    SO_DIGITOS_RE,
    CIDADE_UF_RE,
    PREFIXO_ENDERECO_RE,
    PREFIXO_ENDERECO_FLEX_RE,
    UF_FINAL_RE,
)


class LocalEntregaMixin:
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
