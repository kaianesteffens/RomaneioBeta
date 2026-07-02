"""Parsing de status para rastreamento."""

from __future__ import annotations

import re


def _chave_acesso_valida(chave_acesso: str) -> bool:
    """Valida se a chave de acesso tem 44 dígitos."""
    return len(re.sub(r'\D', '', chave_acesso or "")) == 44


def _linhas_visiveis(texto: str) -> list[str]:
    return [linha.strip() for linha in texto.splitlines() if linha and linha.strip()]


def _montar_status(status: str, detalhe: str = "", previsao: str = "") -> str:
    base = (status or "").strip()
    detalhe = (detalhe or "").strip()
    if detalhe and detalhe.upper() != base.upper():
        base = f"{base} — {detalhe}" if base else detalhe
    if previsao and previsao not in base:
        base = f"{base} — Previsão: {previsao}" if base else f"Previsão: {previsao}"
    return base


def _extrair_status_ssw_remetente(texto: str) -> tuple[str, str]:
    linhas = _linhas_visiveis(texto)
    try:
        idx_situacao = next(
            i for i, linha in enumerate(linhas)
            if linha.upper() in {"SITUAÇÃO", "SITUACAO"}
        )
    except StopIteration:
        return "", ""

    relevantes: list[str] = []
    for linha in linhas[idx_situacao + 1:]:
        linha_upper = linha.upper()
        if linha_upper in {"MAIS DETALHES", "VOLTAR"} or linha.startswith("Processado por"):
            break
        relevantes.append(linha)

    if len(relevantes) >= 5:
        status = relevantes[4]
        detalhe = relevantes[5] if len(relevantes) >= 6 else ""
        return status, detalhe
    return "", ""


async def _extrair_etapas_agex(page) -> dict:
    return await page.evaluate(
        """() => {
            const labels = ['Recebida para transporte', 'A caminho', 'Saiu para entrega', 'Entregue'];
            const rows = Array.from(document.querySelectorAll('div.flex.flex-row.gap-4'))
                .map((row) => {
                    const labelNode = Array.from(row.querySelectorAll('span,div,p'))
                        .find((node) => labels.includes((node.textContent || '').trim()));
                    if (!labelNode) return null;
                    return {
                        label: (labelNode.textContent || '').trim(),
                        completed: row.outerHTML.includes('bg-green-500'),
                    };
                })
                .filter(Boolean);
            const bodyText = document.body ? (document.body.innerText || '') : '';
            const previsaoMatch = bodyText.match(/Previsão de entrega:\\s*([^\\n]+)/i);
            return {
                rows,
                previsao: previsaoMatch ? previsaoMatch[1].trim() : '',
            };
        }"""
    )


def _extrair_status_rodonaves(body_text: str) -> str:
    linhas = _linhas_visiveis(body_text)
    for idx, linha in enumerate(linhas):
        if linha.upper() == "STATUS" and idx + 1 < len(linhas):
            return linhas[idx + 1].strip()
    for pattern in [
        r'Status\s*\n\s*([^\n]+)',
        r'Status\s*[:\-]?\s*([^\n]+)',
    ]:
        match = re.search(pattern, body_text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""
