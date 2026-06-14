"""Apresentação (formatters) da ponte web — extraído de web_app.py.

Funções PURAS de formatação/validação para a UI web, sem estado: recebem os
dados / a config como argumentos. Tirar isto da ponte (`web_app.Api`) deixa a
ponte fina e a apresentação testável e reutilizável.

O formato FOB do romaneio de fornecedor é fixado por
test_char_web_app_serializers.py — não mude a string sem atualizar o golden.
"""
from __future__ import annotations

import re
from typing import Any


def nota_card(indice: int, nf: Any) -> dict:
    """Monta os dados de um card de NF-e (porta de _criar_card_nfe, só texto)."""
    from extrator_nfe import identificar_transportadora, parsear_info_complementar

    transp = identificar_transportadora(nf)
    transp_display = (nf.transportadora_nome or transp.upper() or "NÃO IDENTIFICADA")
    data_emissao_display = ""
    if nf.data_emissao:
        md = re.match(r"(\d{4})-(\d{2})-(\d{2})", nf.data_emissao)
        if md:
            data_emissao_display = f"  |  Emissão: {md.group(3)}/{md.group(2)}/{md.group(1)}"
    header = f"[{indice}] NF-e {nf.numero} — {transp_display}{data_emissao_display}"

    info = parsear_info_complementar(nf.info_complementar)

    def t(v: Any) -> str:
        return str(v or "").strip()

    def fdata(v: Any) -> str:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", t(v))
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else t(v)

    def fcep(v: Any) -> str:
        d = "".join(c for c in str(v or "") if c.isdigit())
        return f"{d[:5]}-{d[5:]}" if len(d) == 8 else t(v)

    def linha(campos):
        return "  |  ".join(f"{r}: {t(v)}" for r, v in campos)

    def transp_bloco() -> str:
        if transp:
            return transp.upper()
        nome = t(nf.transportadora_nome)
        return (nome.split()[0] if nome else "").upper()

    pd_display = t(info.get("pd"))
    if not pd_display and info.get("pedido_venda"):
        mpd = re.search(r"\bPD\b\s*([A-Z0-9./-]+)", t(info.get("pedido_venda")), re.IGNORECASE)
        pd_display = mpd.group(1) if mpd else t(info.get("pedido_venda"))

    cidade_uf = t(info.get("cidade_uf_entrega"))
    if not cidade_uf and nf.destinatario_cidade and nf.destinatario_uf:
        cidade_uf = f"{nf.destinatario_cidade}/{nf.destinatario_uf}"
    dest = t(nf.destinatario_nome)
    if dest and nf.destinatario_uf and not dest.endswith(f"/{nf.destinatario_uf}"):
        dest = f"{dest}/{nf.destinatario_uf}"

    lic = [
        linha([("Processo", info.get("processo")), ("PE", info.get("pe")), ("Ata", info.get("ata")),
               ("Contrato", info.get("contrato")), ("Empenho", info.get("empenho")), ("OF", info.get("of"))]),
        linha([("Entrega", info.get("entrega")), ("Pagamento", info.get("pagamento"))]),
        dest,
        linha([("CRM", info.get("crm")), ("PD", pd_display)]),
        "",
        linha([("NOTA FISCAL", nf.numero), ("DATA NF", fdata(nf.data_emissao))]),
        f"PRODUTOS: {t(nf.produtos_resumo)}",
        linha([("TRANSPORTADORA", transp_bloco()), ("RASTREIO", "(NÃO PREENCHA)")]),
    ]
    if info.get("outras_info_licitacao"):
        lic += ["", "Outras informações da licitação:", t(info.get("outras_info_licitacao"))]

    entrega = [
        f"LOCAL DE ENTREGA: {t(info.get('local_entrega_nome'))}",
        f"ENDEREÇO: {t(info.get('endereco_entrega'))}",
        f"CEP: {fcep(info.get('cep_entrega') or nf.destinatario_cep)}",
        cidade_uf,
        "",
        f"AGENDAMENTO: {t(info.get('agendamento'))}",
        linha([("HORÁRIO", info.get("horario")),
               ("CONTATO", info.get("contato") or info.get("recebedor")),
               ("TELEFONE", info.get("telefone"))]),
    ]
    if info.get("outras_info_entrega"):
        entrega += ["", "Outras informações da entrega:", t(info.get("outras_info_entrega"))]

    return {
        "indice": indice,
        "header": header,
        "bloco_licitacao": "\n".join(lic).rstrip(),
        "bloco_entrega": "\n".join(entrega).rstrip(),
        "chave": getattr(nf, "chave_acesso", "") or f"nf-{indice}-{nf.numero}",
        "numero": nf.numero,
    }


def validar_local_entrega(extrator: Any, pedidos: list) -> tuple[bool, str]:
    """Porta de RomaneioWindow._validar_local_entrega: detecta CEP ausente ou
    locais de entrega divergentes entre os pedidos do romaneio."""
    if not pedidos:
        return True, ""

    sem_cep = []
    for p in pedidos:
        local = getattr(p, "local_entrega", "") or ""
        cep = extrator.obter_cep_local_entrega(local) if hasattr(extrator, "obter_cep_local_entrega") else None
        if not cep:
            sem_cep.append(str(getattr(p, "numero", "?")))
    if sem_cep:
        return False, "CEP não encontrado nos pedidos: " + ", ".join(sem_cep)

    locais: dict[str, dict] = {}
    for p in pedidos:
        local = getattr(p, "local_entrega", "") or ""
        norm = (extrator.normalizar_local_entrega(local) or "").strip() if hasattr(extrator, "normalizar_local_entrega") else local.strip()
        if not norm or norm.upper() == "N/A":
            continue
        chave = extrator.chave_local_entrega(local) if hasattr(extrator, "chave_local_entrega") else re.sub(r"\s+", " ", norm).strip().upper()
        if not chave:
            continue
        locais.setdefault(chave, {"local": norm, "pedidos": []})["pedidos"].append(str(getattr(p, "numero", "?")))

    if len(locais) <= 1:
        return True, ""
    msg = "Locais de entrega diferentes encontrados:\n"
    for info in locais.values():
        msg += f"• {info['local'].split(chr(10))[0]} — pedidos: {', '.join(info['pedidos'])}\n"
    return False, msg.rstrip()


def obter_cnpj_empresa(cfg: dict) -> str:
    transp = cfg.get("transportadoras", {}) or {}
    cnpj = re.sub(r"\D", "", str((transp.get("braspress") or {}).get("cnpj", "") or ""))
    if len(cnpj) == 14:
        return cnpj
    agex = transp.get("agex") or {}
    for chave in ("cnpj_remetente", "cnpj"):
        cnpj = re.sub(r"\D", "", str(agex.get(chave, "") or ""))
        if len(cnpj) == 14:
            return cnpj
    cnpj = re.sub(r"\D", "", str((transp.get("rodonaves") or {}).get("cnpj_pagador", "") or ""))
    return cnpj if len(cnpj) == 14 else ""


def obter_cep_empresa(cfg: dict) -> str:
    rom = cfg.get("romaneio", {}) or {}
    cep = re.sub(r"\D", "", str(rom.get("cep_origem", "") or ""))
    return cep if len(cep) == 8 else ""


def montar_romaneio_fornecedor(cfg: dict, form: dict) -> tuple[str, str]:
    """Monta o texto do romaneio de fornecedor (FOB) a partir da config da empresa
    e do form web. Levanta ValueError com a lista de erros de validação."""
    cnpj_empresa = obter_cnpj_empresa(cfg)
    cep_empresa = obter_cep_empresa(cfg)
    cep_forn = re.sub(r"\D", "", str(form.get("cep", "")))

    def fbr(txt: Any) -> float:
        t = re.sub(r"[R$\s]", "", str(txt or "").strip())
        t = t.replace(".", "").replace(",", ".")
        return float(t) if t else 0.0

    try:
        qtd = int(str(form.get("qtd", "")).strip() or "0")
    except ValueError:
        qtd = 0
    alt, larg, comp = fbr(form.get("alt")), fbr(form.get("larg")), fbr(form.get("comp"))
    peso_cx_txt = str(form.get("peso_cx", "")).strip()
    peso_total_txt = str(form.get("peso_total", "")).strip()
    valor = fbr(form.get("valor"))

    if peso_cx_txt:
        peso_caixa = fbr(peso_cx_txt)
        peso_total = peso_caixa * qtd
    elif peso_total_txt:
        peso_total = fbr(peso_total_txt)
        peso_caixa = peso_total / qtd if qtd > 0 else 0.0
    else:
        raise ValueError("Informe o peso por volume ou o peso total (pelo menos um é obrigatório)")

    cubagem_unit = (alt * larg * comp) / 1_000_000
    cubagem_total = cubagem_unit * qtd

    erros: list[str] = []
    if len(cnpj_empresa) != 14:
        erros.append("CNPJ da empresa não configurado (Configurações > Credenciais)")
    if len(cep_empresa) != 8:
        erros.append("CEP da empresa não configurado (Configurações > Empresa > CEP de origem)")
    if len(cep_forn) != 8:
        erros.append("CEP do fornecedor inválido (deve ter 8 dígitos)")
    if qtd <= 0:
        erros.append("Quantidade de volumes deve ser maior que zero")
    if alt <= 0 or larg <= 0 or comp <= 0:
        erros.append("Dimensões devem ser maiores que zero")
    if peso_total <= 0:
        erros.append("Peso deve ser maior que zero")
    if erros:
        raise ValueError("\n".join(erros))

    c = cnpj_empresa
    cnpj_fmt = f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"
    cep_fmt = f"{cep_empresa[:5]}-{cep_empresa[5:]}"
    lines = [
        f"CNPJ/CPF: {cnpj_fmt}",
        f"CEP: {cep_fmt}",
        f"- VOL: {qtd}",
        f"- CUBAGEM: {cubagem_total:.6f} m3",
        f"- PESO: {peso_total:.2f} kg",
        f"- TOTAL: R$ {valor:.2f}",
        f"{qtd} x Volume fornecedor - {peso_caixa:.3f} kg - {cubagem_unit:.6f} m3 - {int(alt)}x{int(larg)}x{int(comp)}",
    ]
    return "\n".join(lines), cep_forn
