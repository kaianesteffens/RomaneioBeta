"""Smoke headless: estado de operação sobrevive à navegação na UI web.

Reproduz os dois achados do review do Codex (PR #85) e prova as correções:

  1. Cotação iniciada, usuário navega para outra tela antes do término: o
     resultado/progresso/status é roteado à página dona (mesmo fora de vista) e
     re-hidratado ao voltar — não some mais.
  2. NF-e importadas no Rastreio: ao navegar e voltar, os cards são recarregados
     do backend (nfe_cards) e os resultados de rastreamento são re-aplicados.

Renderiza app/web/index.html no Chromium headless do Playwright (mesmo motor do
WebView2), injetando window.__STUB_API__ — sem backend Python, display ou WebView2.

Uso:
  python scripts/smoke_web_nav_persistence.py            # roda os checks (exit!=0 se falhar)
  python scripts/smoke_web_nav_persistence.py --shots DIR  # + screenshots antes/depois
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_WEB = Path(__file__).resolve().parent.parent / "app" / "web"
_INDEX = _WEB / "index.html"

# Backend falso com estado: registra as NF-e "importadas" para que nfe_cards()
# as devolva no reload (espelha self._notas do web_app real).
_STUB_JS = r"""
window.__STUB_API__ = (function () {
  const notas = [];
  function cardOf(n, i) {
    return {
      indice: i + 1,
      header: "[" + (i + 1) + "] NF-e " + n.numero + " — TRANSPORTADORA",
      bloco_licitacao: "DADOS LICITACAO " + n.numero,
      bloco_entrega: "DADOS ENTREGA " + n.numero,
      chave: n.chave,
      numero: n.numero,
    };
  }
  const api = {
    get_bootstrap: () => ({
      empresa: "DEMO", versao: "0.0", tema_efetivo: "escuro", raio: "Suave", botao: "Solido",
      transportadoras: [{ nome: "braspress", habilitado: true }],
      dashboard: { total_romaneios: 0, total_volumes: 0, melhor_frete: null },
    }),
    get_dashboard: () => ({ total_romaneios: 0, total_volumes: 0, melhor_frete: null, romaneios_recentes: [] }),
    set_tema: (t) => ({ tema_efetivo: t || "escuro" }),
    config_get: () => ({ empresa: {}, aparencia: { temas: [], raios: [], botoes: [] }, transportadoras: [], ufs: [] }),
    cotacao_iniciar: () => (window.__STUB_FAIL_COTACAO__ ? { erro: "Bloqueado pela licença" } : { ok: true }),
    fornecedor_cotar: () => ({ ok: true }),
    rastreio_iniciar: () => ({ ok: true }),
    rastreio_limpar: () => { notas.length = 0; return { ok: true }; },
    nfe_selecionar: () => {
      if (notas.length === 0) { notas.push({ chave: "CH1", numero: "111" }, { chave: "CH2", numero: "222" }); }
      return { cards: notas.map(cardOf), erros: [], total_notas: notas.length };
    },
    nfe_cards: () => ({ cards: notas.map(cardOf), total_notas: notas.length }),
    abrir_externo: () => ({ ok: true }),
    abrir_screenshots: () => ({ ok: true }),
  };
  return new Proxy(api, {
    get: (t, k) => {
      if (k in t) return t[k];
      // NÃO finja ser thenable: await/Promise.resolve checam `.then` no objeto;
      // um catch-all que devolve função para "then" faz `await api` travar.
      if (typeof k !== "string" || k === "then" || k === "catch" || k === "finally") return undefined;
      return () => Promise.resolve({});
    },
  });
})();
"""

_FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    mark = "OK  " if cond else "FAIL"
    print(f"  [{mark}] {msg}")
    if not cond:
        _FAILS.append(msg)


def fire(page, event: str, payload: dict | None = None) -> None:
    page.evaluate("([e, p]) => window.onBackendEvent({ event: e, payload: p || {} })", [event, payload or {}])


def nav(page, name: str) -> None:
    page.evaluate("(n) => window.App.navigate(n)", name)
    page.wait_for_timeout(150)


def cenario_cotacao(page, shots: Path | None) -> None:
    print("\nCenário 1 — resultado da cotação sobrevive à navegação")
    nav(page, "cotacao")
    page.fill("#cotInput", "CNPJ/CPF: 12.345.678/0001-95\n- VOL: 1")
    page.click("#cotStart")
    page.wait_for_timeout(120)
    check(page.evaluate("() => window.App.opOwners.cotacao") == "cotacao",
          "dono da operação = 'cotacao' após iniciar")

    # 1º progresso ainda na tela, depois o usuário NAVEGA PARA FORA
    fire(page, "cotacao_progress", {"provider": "BRASPRESS", "status": "cotando", "stage": "cotacao", "mensagem": "cotando"})
    nav(page, "dashboard")

    # resultado e término chegam com o usuário em outra tela
    fire(page, "cotacao_progress", {"provider": "BRASPRESS", "status": "ok", "stage": "resultado",
                                    "resultado": {"status": "ok", "transportadora": "BRASPRESS",
                                                  "valor_frete": 100, "prazo_dias": 3, "duration_ms": 1500}})
    fire(page, "cotacao_result", {"resumo": "BRASPRESS: R$ 100,00 (3 dias)"})
    fire(page, "cotacao_finished", {})
    check(page.evaluate("() => window.App.opOwners.cotacao") is None,
          "dono liberado após cotacao_finished")
    if shots:
        page.screenshot(path=str(shots / "cotacao_outra_tela.png"), full_page=True)

    # volta à Cotação: resultado/status devem ter sobrevivido
    nav(page, "cotacao")
    resumo = page.text_content("#cotResult") or ""
    check("BRASPRESS: R$ 100,00" in resumo, "resultado da cotação preservado ao voltar")
    check((page.text_content("#cotRunStatus") or "").strip() == "Cotação finalizada.",
          "linha de status preservada ('Cotação finalizada.')")
    tabela = page.text_content("#cotStatusBody") or ""
    check("BRASPRESS" in tabela, "linha da transportadora na tabela de status preservada")
    if shots:
        page.screenshot(path=str(shots / "cotacao_de_volta.png"), full_page=True)


def cenario_reinit_rejeitado(page, shots: Path | None) -> None:
    # Achado P3 do review: re-iniciar uma cotação que o backend REJEITA não pode
    # apagar o resultado anterior já concluído (o reset só vem após aceitar).
    print("\nCenário 1b — cotação rejeitada não apaga o resultado anterior")
    nav(page, "cotacao")  # app.state.cotacao ainda tem o resultado do cenário 1
    check("BRASPRESS: R$ 100,00" in (page.text_content("#cotResult") or ""),
          "resultado anterior visível antes da nova tentativa")
    page.evaluate("() => { window.__STUB_FAIL_COTACAO__ = true; }")
    page.fill("#cotInput", "OUTRO ROMANEIO\n- VOL: 2")
    page.click("#cotStart")
    page.wait_for_timeout(150)
    check("BRASPRESS: R$ 100,00" in (page.text_content("#cotResult") or ""),
          "resultado anterior PRESERVADO após rejeição (não virou 'em andamento')")
    check("BRASPRESS" in (page.text_content("#cotStatusBody") or ""),
          "tabela de status anterior preservada após rejeição")
    nav(page, "dashboard")
    nav(page, "cotacao")
    check("BRASPRESS: R$ 100,00" in (page.text_content("#cotResult") or ""),
          "resultado anterior ainda presente ao navegar e voltar")
    page.evaluate("() => { window.__STUB_FAIL_COTACAO__ = false; }")


def cenario_limpar_durante_rastreio(page, shots: Path | None) -> None:
    # Achado P2 do review: "Limpar" durante um rastreio em curso não pode deixar
    # eventos em voo (a coroutine não é cancelada) ressuscitarem o estado limpo.
    print("\nCenário 2b — Limpar durante rastreio não ressuscita estado")
    nav(page, "rastreio")
    page.wait_for_selector('.nfe-card[data-chave="CH1"]', timeout=4000)  # recarregados do backend
    page.click("#rastTrack")  # inicia novo rastreio (tracking=true, vira dono)
    page.wait_for_timeout(120)
    fire(page, "rastreio_progress", {"chave": "CH1", "indice": 1, "total": 2,
                                     "resultado": {"entregue": True, "status_texto": "ENTREGUE"}})
    page.click("#rastClear")
    page.wait_for_timeout(80)
    check(page.eval_on_selector_all(".nfe-card", "els => els.length") == 0, "Limpar removeu os cards")
    check(page.evaluate("() => window.App.opOwners.rastreio") is None, "Limpar liberou o dono do rastreio")
    # eventos em voo da operação abandonada chegam DEPOIS do Limpar
    fire(page, "rastreio_progress", {"chave": "CH2", "indice": 2, "total": 2,
                                     "resultado": {"entregue": False, "status_texto": "Em trânsito"}})
    fire(page, "rastreio_finished", {"total": 2, "entregues": 1, "screenshots": 0})
    check((page.text_content("#rastProgress") or "") == "", "progresso NÃO ressuscitou após Limpar")
    check(page.evaluate("() => Object.keys(window.App.state.rastreio.results).length") == 0,
          "results NÃO foi repovoado por eventos em voo")
    nav(page, "dashboard")
    nav(page, "rastreio")
    check((page.text_content("#rastProgress") or "") == "", "sem progresso fantasma ao voltar")
    check(page.eval_on_selector_all(".nfe-card", "els => els.length") == 0, "sem cards fantasma ao voltar")


def cenario_fornecedores(page, shots: Path | None) -> None:
    # Fornecedores é o 2º consumidor dos MESMOS eventos cotacao_* — o roteamento
    # por dono precisa entregar à fornStatusBody/fornResult, não à da Cotação.
    print("\nCenário 3 — cotação de fornecedor (FOB) sobrevive à navegação")
    nav(page, "fornecedores")
    page.click("#fornCotar")
    page.wait_for_timeout(120)
    check(page.evaluate("() => window.App.opOwners.cotacao") == "fornecedores",
          "dono da operação = 'fornecedores' (não 'cotacao')")

    fire(page, "cotacao_progress", {"provider": "TRD", "status": "cotando", "stage": "cotacao", "mensagem": "cotando"})
    nav(page, "dashboard")
    fire(page, "cotacao_result", {"resumo": "TRD: R$ 80,00 (5 dias)"})
    fire(page, "cotacao_finished", {})

    nav(page, "fornecedores")
    check("TRD: R$ 80,00" in (page.text_content("#fornResult") or ""),
          "resultado FOB preservado ao voltar")
    check("TRD" in (page.text_content("#fornStatusBody") or ""),
          "tabela de status FOB preservada ao voltar")
    if shots:
        page.screenshot(path=str(shots / "fornecedores_de_volta.png"), full_page=True)


def cenario_rastreio(page, shots: Path | None) -> None:
    print("\nCenário 2 — cards de NF-e e rastreamento sobrevivem à navegação")
    nav(page, "rastreio")
    page.click("#rastSelect")
    page.wait_for_selector('.nfe-card[data-chave="CH1"]', timeout=4000)
    check(page.eval_on_selector_all(".nfe-card", "els => els.length") == 2,
          "2 cards de NF-e importados")
    check(page.evaluate("() => window.App.opOwners.rastreio") == "rastreio",
          "dono da operação = 'rastreio' após auto-rastrear")

    fire(page, "rastreio_progress", {"chave": "CH1", "indice": 1, "total": 2,
                                     "resultado": {"entregue": True, "status_texto": "ENTREGUE"}})
    fire(page, "rastreio_progress", {"chave": "CH2", "indice": 2, "total": 2,
                                     "resultado": {"entregue": False, "status_texto": "Em trânsito"}})
    fire(page, "rastreio_finished", {"total": 2, "entregues": 1, "screenshots": 0})
    check("ENTREGUE" in (page.text_content('.nfe-card[data-chave="CH1"] .nfe-track-status') or ""),
          "resultado ENTREGUE aplicado ao card CH1")
    if shots:
        page.screenshot(path=str(shots / "rastreio_rastreado.png"), full_page=True)

    # navega para fora e volta: cards recarregam do backend, resultados re-aplicam
    nav(page, "dashboard")
    nav(page, "rastreio")
    page.wait_for_selector('.nfe-card[data-chave="CH1"]', timeout=4000)
    check(page.eval_on_selector_all(".nfe-card", "els => els.length") == 2,
          "2 cards recarregados do backend ao voltar (não some mais)")
    check("ENTREGUE" in (page.text_content('.nfe-card[data-chave="CH1"] .nfe-track-status') or ""),
          "status ENTREGUE preservado no CH1 ao voltar")
    check("trânsito" in (page.text_content('.nfe-card[data-chave="CH2"] .nfe-track-status') or "").lower(),
          "status Em trânsito preservado no CH2 ao voltar")
    check("concluído" in (page.text_content("#rastProgress") or "").lower(),
          "linha de progresso do rastreamento preservada")
    if shots:
        page.screenshot(path=str(shots / "rastreio_de_volta.png"), full_page=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shots", metavar="DIR", default=None, help="salva screenshots antes/depois no diretório")
    args = ap.parse_args()

    if not _INDEX.exists():
        print(f"index.html não encontrado em {_INDEX}", file=sys.stderr)
        return 2

    shots = None
    if args.shots:
        shots = Path(args.shots)
        shots.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1180, "height": 820}, device_scale_factor=2)
        ctx.add_init_script(_STUB_JS)
        page = ctx.new_page()
        page.goto(_INDEX.as_uri())
        page.wait_for_function(
            "typeof window.App !== 'undefined' && typeof window.App.navigate === 'function'",
            timeout=8000,
        )
        cenario_cotacao(page, shots)
        cenario_reinit_rejeitado(page, shots)
        cenario_fornecedores(page, shots)
        cenario_rastreio(page, shots)
        cenario_limpar_durante_rastreio(page, shots)
        browser.close()

    print()
    if _FAILS:
        print(f"RESULTADO: {len(_FAILS)} verificação(ões) falharam.")
        return 1
    print("RESULTADO: todas as verificações passaram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
