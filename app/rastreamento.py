"""
Modulo de rastreamento de entregas via Playwright.

Abre o site de rastreamento da transportadora, verifica status de entrega,
tira screenshot se entregue, e retorna previsao/link se em transito.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from fretebot.logging_conf import get_logger
from fretebot.providers.base import launch_browser_resilient

logger = get_logger(__name__)


@dataclass
class ResultadoRastreio:
    """Resultado do rastreamento de uma NF-e."""
    numero_nfe: str
    transportadora: str
    entregue: bool = False
    previsao_entrega: str = ""
    link_rastreio: str = ""
    screenshot_path: str = ""
    status_texto: str = ""
    erro: str = ""


_TRACKING_URLS: dict[str, str] = {
    "braspress": "https://www.braspress.com/rastreie-sua-encomenda/",
    "trd": "",       # site fora do ar (DNS nao resolve)
    "agex": "",      # site fora do ar (DNS nao resolve)
    "eucatur": "https://ssw.inf.br/2/rastreamento_danfe?sigla_emp=EUC",
    "bauer": "",     # pagina de rastreamento removida (404)
    "coopex": "https://ssw.inf.br/2/rastreamento_danfe?sigla_emp=COP",
    "bornelli": "https://ssw.inf.br/2/rastreamento_danfe?sigla_emp=AZU",
    "viopex": "https://ssw.inf.br/2/rastreamento_danfe?sigla_emp=VIO",
    "mengue": "https://ssw.inf.br/2/rastreamento_danfe?sigla_emp=MEN",
}

# Siglas SSW por transportadora (para rastreamento_danfe)
_SSW_SIGLAS: dict[str, str] = {
    "eucatur": "EUC",
    "viopex": "VIO",
    "mengue": "MEN",
    "coopex": "COP",
    "bornelli": "AZU",
}


def _download_dir() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        d = Path(appdata) / "FreteBot" / "rastreamento"
    else:
        d = Path.cwd() / "FreteBot_rastreamento"
    d.mkdir(parents=True, exist_ok=True)
    return d


def obter_link_rastreio(transportadora: str, numero_nfe: str = "", cnpj_emitente: str = "") -> str:
    return _TRACKING_URLS.get(transportadora, "")


async def rastrear_nfe(
    transportadora: str,
    numero_nfe: str,
    cnpj_emitente: str = "",
    chave_acesso: str = "",
) -> ResultadoRastreio:
    resultado = ResultadoRastreio(
        numero_nfe=numero_nfe,
        transportadora=transportadora.upper(),
        link_rastreio=obter_link_rastreio(transportadora),
    )
    _handlers = {
        "braspress": _rastrear_braspress,
        "trd": _rastrear_indisponivel,
        "agex": _rastrear_indisponivel,
        "eucatur": _rastrear_ssw,
        "bauer": _rastrear_indisponivel,
        "coopex": _rastrear_ssw,
        "bornelli": _rastrear_ssw,
        "viopex": _rastrear_ssw,
        "mengue": _rastrear_ssw,
    }
    handler = _handlers.get(transportadora, _rastrear_generico)
    try:
        await handler(resultado, numero_nfe, cnpj_emitente, chave_acesso)
    except Exception as e:
        logger.error(f"Erro ao rastrear NF {numero_nfe} na {transportadora}: {e}")
        resultado.erro = str(e)
        resultado.status_texto = f"Erro ao rastrear: {e}"
    return resultado


async def _rastrear_braspress(
    resultado: ResultadoRastreio,
    numero_nfe: str,
    cnpj_emitente: str,
    chave_acesso: str,
) -> None:
    """Rastreio Braspress via HTTP direto (blue.braspress.com bloqueia Chrome headless)."""
    cnpj_limpo = re.sub(r'\D', '', cnpj_emitente) if cnpj_emitente else ""
    track_url = f"https://blue.braspress.com/site/w/tracking/find?cpfCnpj={cnpj_limpo}&pedidoNf={numero_nfe}"
    resultado.link_rastreio = track_url

    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            follow_redirects=True,
            timeout=15,
        ) as client:
            resp = await client.get(track_url)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Extrair campos da primeira tabela (table-striped)
        status_campo = ""
        entregue_em = ""
        previsao = ""
        table = soup.find("table", class_="table-striped")
        if table:
            headers = [th.get_text(strip=True).upper() for th in table.find_all("th")]
            row = table.find("tbody")
            cells = row.find_all("td") if row else table.find_all("td")
            cell_texts = [td.get_text(strip=True) for td in cells]
            for i, h in enumerate(headers):
                if i < len(cell_texts):
                    if "STATUS" in h:
                        status_campo = cell_texts[i]
                    elif "ENTREGUE EM" in h:
                        entregue_em = cell_texts[i]
                    elif "PREVIS" in h and "ENTREGA" in h:
                        previsao = cell_texts[i]

        # Fallback: extrair previsao do texto se nao achou na tabela
        if not previsao:
            body_text = soup.get_text(separator="\n", strip=True)
            prev_match = re.search(r'Previs[ãa]o\s+de\s+Entrega\s*[:\n]?\s*(\d{2}/\d{2}/\d{4})', body_text)
            if prev_match:
                previsao = prev_match.group(1)

        # Detectar entregue pelo campo "Entregue em" (preenchido) ou Status contendo "entregue"
        entregue = bool(entregue_em and entregue_em != "-") or "ENTREGUE" in status_campo.upper()

        if entregue:
            resultado.entregue = True
            resultado.status_texto = f"ENTREGUE em {entregue_em}" if entregue_em and entregue_em != "-" else "ENTREGUE"
            logger.info(f"[RASTREIO-BRASPRESS] NF {numero_nfe}: {resultado.status_texto}")
            # Screenshot da pagina com detalhes expandidos via browser
            await _braspress_screenshot(resultado, numero_nfe, track_url)
        else:
            resultado.entregue = False
            if previsao:
                resultado.previsao_entrega = previsao
                resultado.status_texto = f"{status_campo} — Previsão: {previsao}" if status_campo else f"Previsão: {previsao}"
            else:
                resultado.status_texto = status_campo or "Em trânsito"
            logger.info(f"[RASTREIO-BRASPRESS] NF {numero_nfe}: {resultado.status_texto}")

    except Exception as e:
        logger.warning(f"[RASTREIO-BRASPRESS] NF {numero_nfe}: erro HTTP: {e}")
        resultado.status_texto = f"Erro ao rastrear: {e}"


async def _braspress_screenshot(resultado: ResultadoRastreio, numero_nfe: str, track_url: str) -> None:
    """Abre pagina de tracking no browser e tira screenshot com detalhes expandidos."""
    browser = None
    try:
        browser = await launch_browser_resilient(headless=True)
        contexts = browser.contexts
        if contexts:
            page = contexts[0].pages[0] if contexts[0].pages else await contexts[0].new_page()
        else:
            ctx = await browser.new_context(viewport={"width": 1366, "height": 900})
            page = await ctx.new_page()

        await page.goto(track_url, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(3)

        # Expandir "Ver linha do tempo"
        try:
            lupa = page.locator('a:has-text("Ver linha do tempo")').first
            await lupa.click(timeout=5000)
            await asyncio.sleep(2)
        except Exception:
            pass
        # Expandir "Mais Detalhes"
        try:
            mais = page.locator('a:has-text("Mais Detalhes")').last
            await mais.click(timeout=5000)
            await asyncio.sleep(2)
        except Exception:
            pass

        screenshot_path = _gerar_path_screenshot(numero_nfe)
        await page.screenshot(path=str(screenshot_path), full_page=True)
        resultado.screenshot_path = str(screenshot_path)
        logger.info(f"[RASTREIO-BRASPRESS] Screenshot salvo: {screenshot_path}")
    except Exception as e:
        logger.warning(f"[RASTREIO-BRASPRESS] Erro no screenshot: {e}")
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass



# ---------------------------------------------------------------------------
#  SSW - rastreamento via portal SSW (Eucatur, Viopex, Mengue, Coopex, Bornelli)
# ---------------------------------------------------------------------------

async def _rastrear_ssw(
    resultado: ResultadoRastreio,
    numero_nfe: str,
    cnpj_emitente: str,
    chave_acesso: str,
) -> None:
    """Rastreio via portal SSW usando chave de acesso (DANFE) de 44 digitos."""
    import re as _re
    transp_lower = resultado.transportadora.lower()
    sigla = _SSW_SIGLAS.get(transp_lower, "")

    if not chave_acesso or len(_re.sub(r'\D', '', chave_acesso)) != 44:
        resultado.status_texto = "Chave de acesso (DANFE) nao disponivel para rastreio"
        logger.warning(f"[RASTREIO-{resultado.transportadora}] NF {numero_nfe}: chave_acesso ausente ou invalida")
        return

    chave_limpa = _re.sub(r'\D', '', chave_acesso)
    base_url = "https://ssw.inf.br/2/rastreamento_danfe"
    if sigla:
        base_url += f"?sigla_emp={sigla}"
    resultado.link_rastreio = base_url

    browser = None
    try:
        browser = await launch_browser_resilient(headless=True)
        contexts = browser.contexts
        if contexts:
            page = contexts[0].pages[0] if contexts[0].pages else await contexts[0].new_page()
        else:
            ctx = await browser.new_context(viewport={"width": 1366, "height": 900})
            page = await ctx.new_page()

        logger.info(f"[RASTREIO-{resultado.transportadora}] Navegando para {base_url}")
        await page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

        # Preencher campo DANFE (chave de 44 digitos)
        danfe_field = await page.query_selector('#danfe')
        if not danfe_field:
            resultado.status_texto = "Campo DANFE nao encontrado na pagina SSW"
            return

        await danfe_field.fill(chave_limpa)
        await asyncio.sleep(0.5)

        # Clicar RASTREAR e aguardar redirecionamento para SSWDetalhado
        btn = await page.query_selector('#btn_rastrear')
        if btn:
            await btn.click()
        else:
            await danfe_field.press("Enter")

        # SSW fluxo: POST -> reload com DOCUMENTID -> JS redireciona para /2/SSWDetalhado?...
        try:
            await page.wait_for_url("**/SSWDetalhado**", timeout=15000)
        except Exception:
            # Sem redirecionamento = DANFE nao encontrada no sistema SSW
            resultado.status_texto = "NF nao encontrada no sistema de rastreamento"
            logger.info(f"[RASTREIO-{resultado.transportadora}] NF {numero_nfe}: nao encontrada no SSW")
            return

        await asyncio.sleep(2)
        resultado.link_rastreio = page.url

        # Extrair dados da pagina SSWDetalhado
        body_text = await page.inner_text("body")
        body_upper = body_text.upper()

        # Verificar se entregue
        termos_entregue = ["ENTREGUE", "ENTREGA REALIZADA", "ENTREGA EFETUADA"]
        entregue = any(t in body_upper for t in termos_entregue)

        if entregue:
            resultado.entregue = True
            resultado.status_texto = "ENTREGUE"
            screenshot_path = _gerar_path_screenshot(numero_nfe)
            await page.screenshot(path=str(screenshot_path), full_page=True)
            resultado.screenshot_path = str(screenshot_path)
            logger.info(f"[RASTREIO-{resultado.transportadora}] NF {numero_nfe}: ENTREGUE. Screenshot: {screenshot_path}")
        else:
            resultado.entregue = False
            previsao = _extrair_previsao(body_text)
            resultado.previsao_entrega = previsao

            # Extrair status descritivo
            status_desc = ""
            for pattern in [
                r'(?:Status|Situa[c\xe7][a\xe3]o)\s*[:\-]\s*([^\n]+)',
                r'(?:[\xdaU]ltimo\s*evento|Evento)\s*[:\-]\s*([^\n]+)',
            ]:
                m = _re.search(pattern, body_text, _re.IGNORECASE)
                if m:
                    status_desc = m.group(1).strip()
                    break

            if status_desc:
                resultado.status_texto = f"{status_desc}" + (f" \u2014 Previs\u00e3o: {previsao}" if previsao else "")
            else:
                resultado.status_texto = "Em tr\u00e2nsito" + (f" \u2014 Previs\u00e3o: {previsao}" if previsao else "")
            logger.info(f"[RASTREIO-{resultado.transportadora}] NF {numero_nfe}: {resultado.status_texto}")

    except Exception as e:
        logger.warning(f"[RASTREIO-{resultado.transportadora}] NF {numero_nfe}: erro SSW: {e}")
        resultado.status_texto = f"Erro ao rastrear: {e}"
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
#  Transportadoras sem rastreamento disponivel
# ---------------------------------------------------------------------------

async def _rastrear_indisponivel(
    resultado: ResultadoRastreio,
    numero_nfe: str,
    cnpj_emitente: str,
    chave_acesso: str,
) -> None:
    """Handler para transportadoras cujo site de rastreamento esta fora do ar."""
    resultado.status_texto = "Rastreamento n\u00e3o dispon\u00edvel para esta transportadora"
    resultado.link_rastreio = ""
    logger.info(f"[RASTREIO-{resultado.transportadora}] NF {numero_nfe}: rastreamento nao disponivel")



async def _rastrear_generico(
    resultado: ResultadoRastreio,
    numero_nfe: str,
    cnpj_emitente: str,
    chave_acesso: str,
) -> None:
    url = resultado.link_rastreio
    if not url:
        resultado.status_texto = "Link de rastreamento nao disponivel para esta transportadora"
        return
    browser = None
    try:
        browser = await launch_browser_resilient(headless=True)
        contexts = browser.contexts
        if contexts:
            page = contexts[0].pages[0] if contexts[0].pages else await contexts[0].new_page()
        else:
            ctx = await browser.new_context(viewport={"width": 1366, "height": 900})
            page = await ctx.new_page()
        logger.info(f"[RASTREIO-{resultado.transportadora}] Navegando para {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        campo_busca = await _encontrar_campo_busca(page)
        if campo_busca:
            valor_busca = numero_nfe
            await campo_busca.fill(valor_busca)
            await asyncio.sleep(0.5)
            # Tenta submeter: primeiro Enter no campo, depois click no botao como fallback
            submitted = False
            try:
                await campo_busca.press("Enter")
                submitted = True
            except Exception:
                pass
            if not submitted:
                btn = await page.query_selector(
                    'button[type="submit"], input[type="submit"], '
                    'button:has-text("Rastrear"), button:has-text("Buscar"), '
                    'button:has-text("Pesquisar"), button:has-text("Consultar")'
                )
                if btn:
                    try:
                        await btn.click(timeout=5000)
                    except Exception:
                        await btn.click(force=True)
            await asyncio.sleep(3)
        # Captura URL final (pagina de resultado) como link direto
        resultado.link_rastreio = page.url
        body_text = await page.inner_text("body")
        body_upper = body_text.upper()
        termos_entregue = ["ENTREGUE", "ENTREGA REALIZADA", "ENTREGA EFETUADA", "DELIVERED"]
        entregue = any(t in body_upper for t in termos_entregue)
        if entregue:
            resultado.entregue = True
            resultado.status_texto = "ENTREGUE"
            screenshot_path = _gerar_path_screenshot(numero_nfe)
            await page.screenshot(path=str(screenshot_path), full_page=True)
            resultado.screenshot_path = str(screenshot_path)
            logger.info(f"[RASTREIO-{resultado.transportadora}] NF {numero_nfe}: ENTREGUE. Screenshot: {screenshot_path}")
        else:
            resultado.entregue = False
            previsao = _extrair_previsao(body_text)
            resultado.previsao_entrega = previsao
            resultado.status_texto = f"Em transito" + (f" - Previsao: {previsao}" if previsao else "")
            logger.info(f"[RASTREIO-{resultado.transportadora}] NF {numero_nfe}: em transito")
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass


async def _encontrar_campo_busca(page) -> object | None:
    seletores = [
        'input[name*="rastr"]',
        'input[name*="track"]',
        'input[name*="nf"]',
        'input[name*="nota"]',
        'input[name*="chave"]',
        'input[placeholder*="rastr" i]',
        'input[placeholder*="nota" i]',
        'input[placeholder*="NF" i]',
        'input[placeholder*="chave" i]',
        'input[placeholder*="numero" i]',
        'input[placeholder*="busca" i]',
        'input[type="text"]:visible',
        'input[type="search"]:visible',
    ]
    for sel in seletores:
        try:
            el = await page.query_selector(sel)
            if el:
                return el
        except Exception:
            continue
    return None


def _extrair_previsao(texto: str) -> str:
    padroes = [
        # "Previsão de entrega: 22/04/2026" ou "Previsão: 22/04/2026"
        r'(?:previs[aã]o\s*(?:de\s*entrega)?)[:\s]*(\d{2}[/.-]\d{2}[/.-]\d{2,4})',
        # "Entrega prevista: 22/04/2026"
        r'(?:entrega\s*(?:prevista|estimada))[:\s]*(\d{2}[/.-]\d{2}[/.-]\d{2,4})',
        # "Data prevista: 22/04/2026" ou "Data estimada"
        r'(?:data\s*(?:prevista|estimada|de\s*entrega))[:\s]*(\d{2}[/.-]\d{2}[/.-]\d{2,4})',
        # "Estimativa: 22/04/2026" ou "Prazo: 22/04/2026"
        r'(?:estimativa|prazo)[:\s]*(\d{2}[/.-]\d{2}[/.-]\d{2,4})',
        # "Previsão de entrega\n22/04/2026" (quebra de linha entre label e data)
        r'(?:previs[aã]o\s*(?:de\s*entrega)?)\s*\n\s*(\d{2}[/.-]\d{2}[/.-]\d{2,4})',
        # "Prazo de entrega\n5 dias úteis" ou similar
        r'(?:prazo\s*de\s*entrega)[:\s]*(\d+\s*dias?\s*[uú]teis?)',
        # Formato "dd/mm/aaaa" próximo a "previsão" em até 50 chars
        r'previs[aã]o.{0,50}?(\d{2}/\d{2}/\d{2,4})',
    ]
    for padrao in padroes:
        match = re.search(padrao, texto, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _gerar_path_screenshot(numero_nfe: str, transportadora: str = "") -> Path:
    safe_nfe = re.sub(r"[^\d]", "", numero_nfe) or "sem_numero"
    filename = f"NF{safe_nfe}.png"
    return _download_dir() / filename


async def rastrear_multiplas(
    notas: list[dict],
    callback=None,
) -> list[ResultadoRastreio]:
    total = len(notas)
    resultados = []
    for i, nota in enumerate(notas):
        resultado = await rastrear_nfe(
            transportadora=nota.get("transportadora", ""),
            numero_nfe=nota.get("numero_nfe", ""),
            cnpj_emitente=nota.get("cnpj_emitente", ""),
            chave_acesso=nota.get("chave_acesso", ""),
        )
        resultados.append(resultado)
        if callback:
            try:
                callback(i + 1, total, resultado)
            except Exception:
                pass
    return resultados
