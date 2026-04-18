"""
Extrator de dados de NF-e (Nota Fiscal Eletronica) a partir de XML e DANFE PDF.

Extrai: chave de acesso, numero NF, transportadora, destinatario, volumes, peso, valor.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

try:
    import pdfplumber
except ImportError:
    pdfplumber = None


# Namespace padrao da NF-e (SEFAZ)
_NS = {"nfe": "http://www.portalfiscal.inf.br/nfe"}


@dataclass
class NotaFiscal:
    """Dados extraidos de uma NF-e."""
    chave_acesso: str = ""
    numero: str = ""
    serie: str = ""
    data_emissao: str = ""
    emitente_nome: str = ""
    emitente_cnpj: str = ""
    destinatario_nome: str = ""
    destinatario_cnpj: str = ""
    destinatario_uf: str = ""
    destinatario_cidade: str = ""
    destinatario_cep: str = ""
    transportadora_nome: str = ""
    transportadora_cnpj: str = ""
    volumes: int = 0
    peso_bruto: float = 0.0
    peso_liquido: float = 0.0
    valor_total: float = 0.0
    valor_frete: float = 0.0
    info_complementar: str = ""
    arquivo_origem: str = ""


def _find_text(element, xpath: str, ns: dict = _NS) -> str:
    el = element.find(xpath, ns)
    return (el.text or "").strip() if el is not None else ""


def _find_text_any(element, xpaths: list[str], ns: dict = _NS) -> str:
    for xpath in xpaths:
        result = _find_text(element, xpath, ns)
        if result:
            return result
    return ""


def extrair_xml(caminho: str) -> List[NotaFiscal]:
    path = Path(caminho)
    conteudo = path.read_bytes()
    if conteudo.startswith(b"\xef\xbb\xbf"):
        conteudo = conteudo[3:]
    root = ET.fromstring(conteudo)
    notas = []
    nfe_elements = []
    for xpath in [".//nfe:NFe", ".//nfe:nfeProc/nfe:NFe", "./nfe:NFe"]:
        found = root.findall(xpath, _NS)
        if found:
            nfe_elements.extend(found)
            break
    if not nfe_elements:
        conteudo_str = conteudo.decode("utf-8", errors="replace")
        conteudo_str = re.sub(r'\sxmlns[^"]*"[^"]*"', "", conteudo_str)
        conteudo_str = re.sub(r"\sxmlns=[\"'][^\"']*[\"']", "", conteudo_str)
        root_clean = ET.fromstring(conteudo_str.encode("utf-8"))
        nfe_elements = root_clean.findall(".//NFe") or root_clean.findall("NFe")
        if nfe_elements:
            for nfe_el in nfe_elements:
                nf = _parse_nfe_element(nfe_el, ns={}, caminho=caminho)
                notas.append(nf)
            for i, nf in enumerate(notas):
                if not nf.chave_acesso:
                    chave = _extrair_chave_protNFe(root_clean, ns={})
                    if chave:
                        nf.chave_acesso = chave
            return notas
    for nfe_el in nfe_elements:
        nf = _parse_nfe_element(nfe_el, ns=_NS, caminho=caminho)
        notas.append(nf)
    for nf in notas:
        if not nf.chave_acesso:
            chave = _extrair_chave_protNFe(root, ns=_NS)
            if chave:
                nf.chave_acesso = chave
    return notas


def _extrair_chave_protNFe(root, ns: dict) -> str:
    xpaths = [
        ".//nfe:protNFe/nfe:infProt/nfe:chNFe",
        ".//protNFe/infProt/chNFe",
        ".//nfe:chNFe",
        ".//chNFe",
    ]
    for xpath in xpaths:
        try:
            el = root.find(xpath, ns) if ns else root.find(xpath)
            if el is not None and el.text and len(re.sub(r"\D", "", el.text)) == 44:
                return re.sub(r"\D", "", el.text)
        except Exception:
            pass
    return ""


def _parse_nfe_element(nfe_el, ns: dict, caminho: str) -> NotaFiscal:
    nf = NotaFiscal(arquivo_origem=caminho)
    infNFe = nfe_el.find("nfe:infNFe", ns) if ns else nfe_el.find("infNFe")
    if infNFe is None:
        infNFe = nfe_el
    nfe_id = infNFe.get("Id", "")
    digits = re.sub(r"\D", "", nfe_id)
    if len(digits) == 44:
        nf.chave_acesso = digits
    p = "nfe:" if ns else ""
    ide_prefix = f"{p}ide"
    nf.numero = _find_text(infNFe, f"{ide_prefix}/{p}nNF", ns)
    nf.serie = _find_text(infNFe, f"{ide_prefix}/{p}serie", ns)
    nf.data_emissao = _find_text(infNFe, f"{ide_prefix}/{p}dhEmi", ns)
    if not nf.data_emissao:
        nf.data_emissao = _find_text(infNFe, f"{ide_prefix}/{p}dEmi", ns)
    emit_prefix = f"{p}emit"
    nf.emitente_cnpj = _find_text(infNFe, f"{emit_prefix}/{p}CNPJ", ns)
    nf.emitente_nome = _find_text(infNFe, f"{emit_prefix}/{p}xNome", ns)
    if not nf.emitente_nome:
        nf.emitente_nome = _find_text(infNFe, f"{emit_prefix}/{p}xFant", ns)
    dest_prefix = f"{p}dest"
    nf.destinatario_cnpj = _find_text(infNFe, f"{dest_prefix}/{p}CNPJ", ns)
    if not nf.destinatario_cnpj:
        nf.destinatario_cnpj = _find_text(infNFe, f"{dest_prefix}/{p}CPF", ns)
    nf.destinatario_nome = _find_text(infNFe, f"{dest_prefix}/{p}xNome", ns)
    ender_dest_prefix = f"{dest_prefix}/{p}enderDest"
    nf.destinatario_uf = _find_text(infNFe, f"{ender_dest_prefix}/{p}UF", ns)
    nf.destinatario_cidade = _find_text(infNFe, f"{ender_dest_prefix}/{p}xMun", ns)
    nf.destinatario_cep = _find_text(infNFe, f"{ender_dest_prefix}/{p}CEP", ns)
    transp_prefix = f"{p}transp"
    transporta_prefix = f"{transp_prefix}/{p}transporta"
    nf.transportadora_cnpj = _find_text(infNFe, f"{transporta_prefix}/{p}CNPJ", ns)
    nf.transportadora_nome = _find_text(infNFe, f"{transporta_prefix}/{p}xNome", ns)
    vol_prefix = f"{transp_prefix}/{p}vol"
    vol_els = infNFe.findall(vol_prefix, ns) if ns else infNFe.findall(vol_prefix)
    total_vol = 0
    total_peso_b = 0.0
    total_peso_l = 0.0
    for vol_el in vol_els:
        qvol = _find_text_any(vol_el, [f"{p}qVol"], ns)
        try:
            total_vol += int(qvol)
        except (ValueError, TypeError):
            pass
        peso_b = _find_text_any(vol_el, [f"{p}pesoB"], ns)
        try:
            total_peso_b += float(peso_b.replace(",", "."))
        except (ValueError, TypeError):
            pass
        peso_l = _find_text_any(vol_el, [f"{p}pesoL"], ns)
        try:
            total_peso_l += float(peso_l.replace(",", "."))
        except (ValueError, TypeError):
            pass
    nf.volumes = total_vol
    nf.peso_bruto = total_peso_b
    nf.peso_liquido = total_peso_l
    total_prefix = f"{p}total"
    icms_prefix = f"{total_prefix}/{p}ICMSTot"
    vnf = _find_text(infNFe, f"{icms_prefix}/{p}vNF", ns)
    try:
        nf.valor_total = float(vnf.replace(",", "."))
    except (ValueError, TypeError):
        pass
    vfrete = _find_text(infNFe, f"{icms_prefix}/{p}vFrete", ns)
    try:
        nf.valor_frete = float(vfrete.replace(",", "."))
    except (ValueError, TypeError):
        pass
    return nf


def extrair_danfe_pdf(caminho: str) -> List[NotaFiscal]:
    if pdfplumber is None:
        raise ImportError("pdfplumber nao esta instalado")
    path = Path(caminho)
    nf = NotaFiscal(arquivo_origem=caminho)
    with pdfplumber.open(str(path)) as pdf:
        texto_completo = ""
        for page in pdf.pages:
            texto_completo += (page.extract_text() or "") + "\n"
    if not texto_completo.strip():
        return []
    chave_match = re.search(
        r'(?:CHAVE DE ACESSO|CHAVE.*?ACESSO)[:\s]*([\d\s.]{44,60})',
        texto_completo, re.IGNORECASE
    )
    if chave_match:
        nf.chave_acesso = re.sub(r"\D", "", chave_match.group(1))[:44]
    if len(nf.chave_acesso) != 44:
        blocos = re.search(r'(\d{4}\s+\d{4}\s+\d{4}\s+\d{4}\s+\d{4}\s+\d{4}\s+\d{4}\s+\d{4}\s+\d{4}\s+\d{4}\s+\d{4})', texto_completo)
        if blocos:
            digs = re.sub(r"\D", "", blocos.group(1))
            if len(digs) == 44:
                nf.chave_acesso = digs
    nf_match = re.search(r'(?:N[^\w]?\s*|NF[- ]?e?\s*(?:N[^\w]?)?\s*)(\d{3,9})', texto_completo, re.IGNORECASE)
    if nf_match:
        nf.numero = nf_match.group(1)
    serie_match = re.search(r'S[EE]RIE[:\s]*(\d{1,3})', texto_completo, re.IGNORECASE)
    if serie_match:
        nf.serie = serie_match.group(1)
    data_match = re.search(r'(?:DATA\s*(?:DE\s*)?EMISS.O|EMISS.O)[:\s]*(\d{2}[/.-]\d{2}[/.-]\d{4})', texto_completo, re.IGNORECASE)
    if data_match:
        nf.data_emissao = data_match.group(1)
    emit_cnpj = re.search(r'CNPJ[:\s]*([\d./\-]+)', texto_completo)
    if emit_cnpj:
        nf.emitente_cnpj = re.sub(r"\D", "", emit_cnpj.group(1))
    dest_match = re.search(r'DESTINAT.RIO.*?(?:NOME|RAZ.O)[:\s]*([^\n]{3,60})', texto_completo, re.IGNORECASE | re.DOTALL)
    if dest_match:
        nf.destinatario_nome = dest_match.group(1).strip()
    dest_cnpj = re.search(r'DESTINAT.RIO.*?CNPJ[:\s]*([\d./\-]+)', texto_completo, re.IGNORECASE | re.DOTALL)
    if dest_cnpj:
        nf.destinatario_cnpj = re.sub(r"\D", "", dest_cnpj.group(1))
    dest_uf = re.search(r'DESTINAT.RIO.*?UF[:\s]*([A-Z]{2})', texto_completo, re.IGNORECASE | re.DOTALL)
    if dest_uf:
        nf.destinatario_uf = dest_uf.group(1).upper()
    dest_cep = re.search(r'DESTINAT.RIO.*?CEP[:\s]*([\d.\-]+)', texto_completo, re.IGNORECASE | re.DOTALL)
    if dest_cep:
        nf.destinatario_cep = re.sub(r"\D", "", dest_cep.group(1))
    transp_match = re.search(r'TRANSPORTADOR.*?(?:NOME|RAZ.O)[:\s]*([^\n]{3,60})', texto_completo, re.IGNORECASE | re.DOTALL)
    if transp_match:
        nf.transportadora_nome = transp_match.group(1).strip()
    transp_cnpj = re.search(r'TRANSPORTADOR.*?CNPJ[:\s]*([\d./\-]+)', texto_completo, re.IGNORECASE | re.DOTALL)
    if transp_cnpj:
        nf.transportadora_cnpj = re.sub(r"\D", "", transp_cnpj.group(1))
    vol_match = re.search(r'QUANTIDADE[:\s]*(\d+)', texto_completo, re.IGNORECASE)
    if vol_match:
        try:
            nf.volumes = int(vol_match.group(1))
        except ValueError:
            pass
    peso_b = re.search(r'PESO\s*BRUTO[:\s]*([\d.,]+)', texto_completo, re.IGNORECASE)
    if peso_b:
        try:
            nf.peso_bruto = float(peso_b.group(1).replace(".", "").replace(",", "."))
        except ValueError:
            pass
    peso_l = re.search(r'PESO\s*L.QUIDO[:\s]*([\d.,]+)', texto_completo, re.IGNORECASE)
    if peso_l:
        try:
            nf.peso_liquido = float(peso_l.group(1).replace(".", "").replace(",", "."))
        except ValueError:
            pass
    valor_match = re.search(r'VALOR\s*TOTAL\s*(?:DA\s*)?(?:NOTA|NF)[:\s]*R?\$?\s*([\d.,]+)', texto_completo, re.IGNORECASE)
    if not valor_match:
        valor_match = re.search(r'VALOR\s*TOTAL[:\s]*R?\$?\s*([\d.,]+)', texto_completo, re.IGNORECASE)
    if valor_match:
        try:
            nf.valor_total = float(valor_match.group(1).replace(".", "").replace(",", "."))
        except ValueError:
            pass
    return [nf] if (nf.numero or nf.chave_acesso) else []


def extrair_arquivo(caminho: str) -> List[NotaFiscal]:
    path = Path(caminho)
    ext = path.suffix.lower()
    if ext == ".xml":
        return extrair_xml(caminho)
    elif ext == ".pdf":
        return extrair_danfe_pdf(caminho)
    else:
        raise ValueError(f"Formato nao suportado: {ext}. Use .xml ou .pdf")


def identificar_transportadora(nf: NotaFiscal) -> str:
    nome = (nf.transportadora_nome or "").upper()
    cnpj = re.sub(r"\D", "", nf.transportadora_cnpj or "")
    mapeamento_nome = {
        "braspress": ["BRASPRESS"],
        "eucatur": ["EUCATUR"],
        "bornelli": ["BORNELLI", "AZUL ERECHIM", "AZUL CARGO"],
        "viopex": ["VIOPEX"],
        "mengue": ["MENGUE"],
        "coopex": ["COOPEX"],
        "rodonaves": ["RODONAVES", "RTE RODONAVES"],
        "trd": ["TRD", "TRANSPORTE RODOVIARIO", "BENTO GONCALVES"],
        "agex": ["AGEX"],
        "bauer": ["BAUER"],
        "alfa": ["ALFA TRANSPORT", "ALFA"],
        "correios": ["CORREIO", "PAC", "SEDEX"],
    }
    for chave, termos in mapeamento_nome.items():
        for termo in termos:
            if termo in nome:
                return chave
    return ""


def formatar_nota_resumo(nf: NotaFiscal) -> str:
    linhas = []
    linhas.append(f"NF-e: {nf.numero}" + (f" (Serie {nf.serie})" if nf.serie else ""))
    if nf.chave_acesso:
        linhas.append(f"Chave: {nf.chave_acesso}")
    if nf.emitente_nome:
        linhas.append(f"Emitente: {nf.emitente_nome}")
    if nf.destinatario_nome:
        dest_info = nf.destinatario_nome
        if nf.destinatario_cidade and nf.destinatario_uf:
            dest_info += f" ({nf.destinatario_cidade}/{nf.destinatario_uf})"
        linhas.append(f"Destinatario: {dest_info}")
    if nf.destinatario_cep:
        linhas.append(f"CEP destino: {nf.destinatario_cep}")
    if nf.transportadora_nome:
        linhas.append(f"Transportadora: {nf.transportadora_nome}")
    if nf.volumes:
        linhas.append(f"Volumes: {nf.volumes}")
    if nf.peso_bruto:
        linhas.append(f"Peso bruto: {nf.peso_bruto:.2f} kg")
    if nf.valor_total:
        linhas.append(f"Valor NF: R$ {nf.valor_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    if nf.valor_frete:
        linhas.append(f"Valor frete: R$ {nf.valor_frete:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    return "\n".join(linhas)


def parsear_info_complementar(info: str) -> dict:
    """Parseia informações complementares da NF-e em campos estruturados."""
    result: dict = {}
    if not info:
        return result

    texto = info.replace("||", "\n").replace("|", "\n")

    m = re.search(r'Pedido\s+de\s+compra\s+do\s+cliente[:\s]*(.+)', texto, re.IGNORECASE)
    if m:
        result["pedido_compra"] = m.group(1).strip().rstrip(".")

    m = re.search(r'Pedido\s+de\s+Venda[:\s]*(.+)', texto, re.IGNORECASE)
    if m:
        result["pedido_venda"] = m.group(1).strip().rstrip(".")

    local_parts = []
    m_local = re.search(r'LOCAL\s+DE\s+ENTREGA[:\s]*(.*?)(?=\n\s*(?:AGENDAMENTO|HORARIO|$))', texto, re.IGNORECASE | re.DOTALL)
    if m_local:
        local_raw = m_local.group(1).strip()
        if local_raw:
            local_parts.append(local_raw)
    m = re.search(r'ENDERECO[:\s]*(.+)', texto, re.IGNORECASE)
    if m and m.group(1).strip() not in " ".join(local_parts):
        local_parts.append(m.group(1).strip())
    m = re.search(r'BAIRRO[:\s]*(.+)', texto, re.IGNORECASE)
    if m:
        bairro = m.group(1).strip()
        if bairro and bairro not in " ".join(local_parts):
            local_parts.append(bairro)
    m_cep = re.search(r'CEP[:\s]*([\d.\-]+)\s*\n?\s*([A-Za-z\u00c0-\u00fa\s]+/[A-Z]{2})?', texto, re.IGNORECASE)
    if m_cep:
        cep_str = m_cep.group(1).strip()
        cidade_uf = (m_cep.group(2) or "").strip()
        if cidade_uf:
            local_parts.append(f"CEP {cep_str} \u2014 {cidade_uf}")
        elif cep_str not in " ".join(local_parts):
            local_parts.append(f"CEP {cep_str}")
    if local_parts:
        result["local_entrega"] = "\n".join(local_parts)

    m = re.search(r'AGENDAMENTO[:\s]*(\S+)', texto, re.IGNORECASE)
    if m:
        result["agendamento"] = m.group(1).strip()

    m = re.search(r'HORARIO[:\s]*(.+?)(?:\n|$)', texto, re.IGNORECASE)
    if m:
        result["horario"] = m.group(1).strip()

    return result
