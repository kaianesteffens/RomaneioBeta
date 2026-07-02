"""Helpers de normalizacao de informacoes complementares de NF-e."""

import re
import unicodedata


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


def _normalizar_texto_busca_info(valor: str) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", texto).strip().lower()


def _deve_ignorar_linha_info(linha: str) -> bool:
    texto = _normalizar_texto_busca_info(linha)
    if not texto:
        return True

    if re.fullmatch(r"-+", texto):
        return True

    padroes_fixos = (
        "conta para deposito",
        "empresa optante pelo simples nacional",
        "nao sujeita a retencao de tributos federais",
        "nao gera direito a credito de icms",
        "total de tributos municipal, estadual e federal",
        "endereco de entrega",
    )
    if any(padrao in texto for padrao in padroes_fixos):
        return True

    if texto.startswith(("sicredi:", "bb:", "pix:")):
        return True

    if re.search(r"\b(?:ag|c/c)\b", texto) and re.search(r"\d", texto):
        if any(banco in texto for banco in ("sicredi", "bb", "banco do brasil", "pix")):
            return True

    return False


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


def _normalizar_endereco_info(valor: str) -> str:
    texto = _limpar_valor_info(valor)
    texto = re.sub(r"\s*,\s*", ", ", texto)
    return re.sub(r"\s+", " ", texto).strip(" ,")


def _parece_endereco_info(valor: str) -> bool:
    texto = _normalizar_texto_busca_info(valor)
    if not texto:
        return False
    if re.search(r"\d", texto):
        return True
    return bool(
        re.match(
            r"^(?:rua|r\.?|avenida|av\.?|travessa|tv\.?|alameda|rodovia|estrada|praca|praça|largo|quadra|qd\.?|lote|lt\.?|sitio|sítio|fazenda|chacara|chácara)\b",
            texto,
        )
    )


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
