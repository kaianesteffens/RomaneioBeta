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


def _find_text_any(element, xpaths: list, ns: dict = _NS) -> str:
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
    # Informacoes complementares (infAdic/infCpl + infAdFisco)
    infadic_prefix = f"{p}infAdic"
    infcpl = _find_text(infNFe, f"{infadic_prefix}/{p}infCpl", ns)
    infadfisco = _find_text(infNFe, f"{infadic_prefix}/{p}infAdFisco", ns)
    partes_info = []
    if infcpl:
        partes_info.append(infcpl)
    if infadfisco:
        partes_info.append(infadfisco)
    nf.info_complementar = " | ".join(partes_info)
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


def _normalizar_info_linhas(info: str) -> list[str]:
    texto = (info or "").replace("\r\n", "\n").replace("\r", "\n").replace("||", "\n")
    linhas: list[str] = []
    for trecho in texto.splitlines():
        for parte in trecho.split("|"):
            linha = re.sub(r"\s+", " ", parte or "").strip()
            if linha:
                linhas.append(linha)
    return linhas


def _limpar_valor_info(valor: str) -> str:
    valor_limpo = re.sub(r"\s+", " ", str(valor or "")).strip()
    valor_limpo = re.sub(r"^[\s:;\-]+", "", valor_limpo)
    return valor_limpo.rstrip(" .;,")


def _match_linha_rotulada(linha: str, padrao: str):
    return re.match(
        rf"^(?:{padrao})(?:(?:\s*[:\-]\s*|\s+)(.*)|\s*)$",
        linha,
        re.IGNORECASE,
    )


def _formatar_cep_info(valor: str) -> str:
    digitos = re.sub(r"\D", "", str(valor or ""))
    if len(digitos) == 8:
        return f"{digitos[:5]}-{digitos[5:]}"
    return _limpar_valor_info(valor)


def _normalizar_cidade_uf_info(valor: str) -> str:
    texto = _limpar_valor_info(valor)
    if not texto:
        return ""
    match = re.search(r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s.'-]+)\s*/\s*([A-Za-z]{2})", texto)
    if not match:
        return texto
    return f"{match.group(1).strip()}/{match.group(2).upper()}"


def _juntar_linhas_unicas(valores: list[str]) -> str:
    unicos: list[str] = []
    vistos: set[str] = set()
    for valor in valores:
        texto = _limpar_valor_info(valor)
        if not texto:
            continue
        chave = texto.casefold()
        if chave in vistos:
            continue
        vistos.add(chave)
        unicos.append(texto)
    return "\n".join(unicos)


def parsear_info_complementar(info: str) -> dict:
    """Parseia informacoes complementares da NF-e em campos estruturados.

    Retorna dict com:
      Bloco 1 (licitacao):
        processo, pe, ata, contrato, empenho, of, entrega,
        pagamento, crm, outras_info_licitacao
      Bloco 2 (entrega):
        local_entrega_nome, endereco_entrega, cep_entrega,
        cidade_uf_entrega, agendamento, horario, contato,
        telefone, outras_info_entrega

      Mantem tambem chaves legadas usadas no app, como pedido_compra,
      pedido_venda, local_entrega, recebedor e bloco2_campos.
    """
    result = {}
    if not info:
        return result

    linhas = _normalizar_info_linhas(info)
    if not linhas:
        return result

    rotulos = {
        "pedido_compra": (r"PEDIDO\s+DE\s+COMPRA(?:\s+DO\s+CLIENTE)?",),
        "pedido_venda": (r"PEDIDO\s+DE\s+VENDA",),
        "processo": (r"PROCESSO(?:\s+ADMINISTRATIVO)?",),
        "pe": (r"PE",),
        "ata": (r"ATA",),
        "contrato": (r"CONTRATO",),
        "empenho": (r"EMPENHO",),
        "of": (r"OF",),
        "entrega": (r"ENTREGA(?!\s+LOCAL)", r"PRAZO\s+DE\s+ENTREGA"),
        "pagamento": (r"PAGAMENTO", r"CONDI(?:CAO|ÇÃO)\s+DE\s+PAGAMENTO"),
        "crm": (r"CRM",),
        "local_entrega": (r"LOCAL\s+DE\s+ENTREGA",),
        "endereco": (r"ENDERE(?:CO|ÇO)",),
        "cep": (r"CEP",),
        "cidade_uf": (r"CIDADE\s*/\s*UF", r"CIDADE\s+UF"),
        "agendamento": (r"AGENDAMENTO",),
        "horario": (r"HOR(?:ARIO|ÁRIO)",),
        "contato": (r"CONTATO", r"RECEBEDOR", r"RESPONS(?:AVEL|ÁVEL)"),
        "telefone": (r"TELEFONE", r"TEL(?:EFONE)?", r"FONE"),
        "obs": (
            r"OBS(?:ERVACOES?|ERV)?",
            r"OUTRAS?\s+INFORMA(?:COES|ÇÕES)(?:\s+DA)?\s+LICITA(?:CAO|ÇÃO)",
            r"OUTRAS?\s+INFORMA(?:COES|ÇÕES)(?:\s+DA)?\s+ENTREGA",
        ),
    }
    todos_rotulos = tuple(padrao for padroes in rotulos.values() for padrao in padroes)
    used_indices: set[int] = set()

    def _casa_rotulos(linha: str, padroes: tuple[str, ...]) -> bool:
        return any(_match_linha_rotulada(linha, padrao) for padrao in padroes)

    def _extrair_valor_linha(linha: str, padroes: tuple[str, ...]) -> str:
        for padrao in padroes:
            match = _match_linha_rotulada(linha, padrao)
            if match:
                return _limpar_valor_info(match.group(1) or "")
        return ""

    def _extrair_campo(chave: str, *, allow_next_line: bool = True) -> tuple[str, int]:
        padroes = rotulos[chave]
        for idx, linha in enumerate(linhas):
            if idx in used_indices:
                continue
            valor = _extrair_valor_linha(linha, padroes)
            if valor or _casa_rotulos(linha, padroes):
                used_indices.add(idx)
                if not valor and allow_next_line:
                    prox_idx = idx + 1
                    if (
                        prox_idx < len(linhas)
                        and prox_idx not in used_indices
                        and not _casa_rotulos(linhas[prox_idx], todos_rotulos)
                    ):
                        valor = _limpar_valor_info(linhas[prox_idx])
                        if valor:
                            used_indices.add(prox_idx)
                if valor:
                    result[chave] = valor
                return valor, idx
        return "", -1

    for chave in (
        "pedido_compra",
        "pedido_venda",
        "processo",
        "pe",
        "ata",
        "contrato",
        "empenho",
        "of",
        "entrega",
        "pagamento",
        "crm",
    ):
        _extrair_campo(chave)

    valor_local, idx_local = _extrair_campo("local_entrega", allow_next_line=False)
    idx_agendamento = idx_horario = idx_contato = idx_telefone = -1
    local_extra_lines: list[str] = []

    if idx_local >= 0:
        stop_padroes = (
            rotulos["agendamento"]
            + rotulos["horario"]
            + rotulos["contato"]
            + rotulos["telefone"]
            + rotulos["processo"]
            + rotulos["pe"]
            + rotulos["ata"]
            + rotulos["contrato"]
            + rotulos["empenho"]
            + rotulos["of"]
            + rotulos["entrega"]
            + rotulos["pagamento"]
            + rotulos["crm"]
        )
        linhas_local = [valor_local] if valor_local else []
        prox_idx = idx_local + 1
        while prox_idx < len(linhas):
            if prox_idx in used_indices:
                prox_idx += 1
                continue
            if _casa_rotulos(linhas[prox_idx], stop_padroes):
                break
            linhas_local.append(linhas[prox_idx])
            used_indices.add(prox_idx)
            prox_idx += 1

        local_nome = ""
        endereco = ""
        cep = ""
        cidade_uf = ""

        for linha in linhas_local:
            if not linha:
                continue
            valor_endereco = _extrair_valor_linha(linha, rotulos["endereco"])
            if valor_endereco:
                endereco = valor_endereco
                continue
            valor_cep = _extrair_valor_linha(linha, rotulos["cep"])
            if valor_cep:
                cep = _formatar_cep_info(valor_cep)
                continue
            valor_cidade = _extrair_valor_linha(linha, rotulos["cidade_uf"])
            if valor_cidade:
                cidade_uf = _normalizar_cidade_uf_info(valor_cidade)
                continue

            cidade_guess = _normalizar_cidade_uf_info(linha)
            if "/" in cidade_guess and cidade_guess.upper().endswith(("/AC", "/AL", "/AP", "/AM", "/BA", "/CE", "/DF", "/ES", "/GO", "/MA", "/MT", "/MS", "/MG", "/PA", "/PB", "/PR", "/PE", "/PI", "/RJ", "/RN", "/RS", "/RO", "/RR", "/SC", "/SP", "/SE", "/TO")):
                cidade_uf = cidade_guess
                continue

            if not endereco and re.search(r"\d", linha):
                endereco = _limpar_valor_info(linha)
                continue

            if not local_nome:
                local_nome = _limpar_valor_info(linha)
            else:
                local_extra_lines.append(linha)

        if not cep:
            for linha in linhas_local:
                cep_guess = _formatar_cep_info(linha)
                if cep_guess and re.fullmatch(r"\d{5}-\d{3}", cep_guess):
                    cep = cep_guess
                    break

        if local_nome:
            result["local_entrega_nome"] = local_nome
        if endereco:
            result["endereco_entrega"] = endereco
        if cep:
            result["cep_entrega"] = cep
        if cidade_uf:
            result["cidade_uf_entrega"] = cidade_uf

        local_composto = []
        if local_nome:
            local_composto.append(local_nome)
        if endereco:
            local_composto.append(endereco)
        if cep:
            local_composto.append(f"CEP {cep}")
        if cidade_uf:
            local_composto.append(cidade_uf)
        if local_composto:
            result["local_entrega"] = "\n".join(local_composto)

    agendamento, idx_agendamento = _extrair_campo("agendamento")
    if agendamento:
        if re.search(r"\bN(?:AO|ÃO)?\b", agendamento, re.IGNORECASE):
            result["agendamento"] = "NÃO"
        elif re.search(r"\bS(?:IM)?\b", agendamento, re.IGNORECASE):
            result["agendamento"] = "SIM"
        else:
            result["agendamento"] = agendamento

    horario, idx_horario = _extrair_campo("horario")
    if horario:
        result["horario"] = horario

    contato, idx_contato = _extrair_campo("contato")
    if contato:
        result["contato"] = contato
        result["recebedor"] = contato

    telefone, idx_telefone = _extrair_campo("telefone")
    if telefone:
        result["telefone"] = telefone

    delivery_indices = [idx for idx in (idx_local, idx_agendamento, idx_horario, idx_contato, idx_telefone) if idx >= 0]
    delivery_boundary_idx = min(delivery_indices) if delivery_indices else len(linhas)

    extras_licitacao: list[str] = []
    extras_entrega: list[str] = list(local_extra_lines)

    if result.get("pedido_compra"):
        extras_licitacao.append(f"Pedido de compra do cliente: {result['pedido_compra']}")
    if result.get("pedido_venda"):
        extras_licitacao.append(f"Pedido de Venda: {result['pedido_venda']}")

    for idx, linha in enumerate(linhas):
        if idx in used_indices:
            continue
        texto_linha = re.sub(
            r"^(?:OBS(?:ERVACOES?|ERV)?|OUTRAS?\s+INFORMA(?:COES|ÇÕES)(?:\s+DA)?\s+(?:LICITA(?:CAO|ÇÃO)|ENTREGA))\s*[:\-]?\s*",
            "",
            linha,
            flags=re.IGNORECASE,
        )
        texto_linha = _limpar_valor_info(texto_linha)
        if not texto_linha:
            continue
        if idx >= delivery_boundary_idx:
            extras_entrega.append(texto_linha)
        else:
            extras_licitacao.append(texto_linha)

    outras_licitacao = _juntar_linhas_unicas(extras_licitacao)
    outras_entrega = _juntar_linhas_unicas(extras_entrega)
    if outras_licitacao:
        result["outras_info_licitacao"] = outras_licitacao
    if outras_entrega:
        result["outras_info_entrega"] = outras_entrega

    if outras_licitacao and not result.get("observacoes"):
        result["observacoes"] = outras_licitacao

    bloco2_campos = []
    for label, chave in (
        ("PE", "pe"),
        ("ATA", "ata"),
        ("CONTRATO", "contrato"),
        ("EMPENHO", "empenho"),
        ("OF", "of"),
        ("CRM", "crm"),
    ):
        valor = result.get(chave)
        if valor:
            bloco2_campos.append((label, valor))
    if bloco2_campos:
        result["bloco2_campos"] = bloco2_campos

    return result
