/* Página Rastreio — importa NF-e (XML), mostra cards (licitação/entrega) e
   rastreia entregas ao vivo (porta _criar_card_nfe / _on_rastreio_result). */
"use strict";

(function () {
  const cards = {}; // chave -> elemento do card

  function cardHTML(c) {
    const F = window.Fmt;
    return `
      <div class="nfe-card" data-chave="${F.esc(c.chave)}">
        <div class="nfe-head">${F.esc(c.header)}</div>
        <div class="nfe-blocks">
          <div class="nfe-block lic">
            <div class="nfe-block-title">DADOS DA LICITAÇÃO</div>
            <pre>${F.esc(c.bloco_licitacao)}</pre>
          </div>
          <div class="nfe-block ent">
            <div class="nfe-block-title">DADOS DA ENTREGA</div>
            <pre>${F.esc(c.bloco_entrega)}</pre>
          </div>
        </div>
        <div class="nfe-track" hidden>
          <div class="nfe-block-title">RASTREAMENTO</div>
          <div class="nfe-track-status pend">Aguardando rastreamento…</div>
          <div class="nfe-track-detail"></div>
        </div>
      </div>`;
  }

  function placeholder() {
    return `<div class="rast-empty">Selecione um ou mais arquivos XML de NF-e para visualizar as
      informações do pedido e rastrear entregas automaticamente.</div>`;
  }

  function atualizarBotoes() {
    const temNotas = Object.keys(cards).length > 0;
    const bt = document.getElementById("rastTrack");
    if (bt) bt.disabled = !temNotas;
  }

  function setProgresso(txt) {
    const el = document.getElementById("rastProgress");
    if (el) el.textContent = txt || "";
  }

  async function selecionar(app) {
    const a = await app.api();
    const res = await a.nfe_selecionar();
    if (!res || res.cancelado) return;
    if (res.erros && res.erros.length) app.toast(res.erros.join(" · "));
    const novas = (res.cards || []);
    if (!novas.length) { if (!res.erros || !res.erros.length) app.toast("Nenhuma NF-e nova encontrada"); return; }

    const box = document.getElementById("rastCards");
    const ph = box.querySelector(".rast-empty");
    if (ph) ph.remove();
    const chaves = [];
    for (const c of novas) {
      box.insertAdjacentHTML("afterbegin", cardHTML(c));
      cards[c.chave] = box.querySelector(`.nfe-card[data-chave="${CSS.escape(c.chave)}"]`);
      chaves.push(c.chave);
    }
    atualizarBotoes();
    // Auto-rastreia as notas recém-adicionadas (espelha _on_nfe_imported)
    rastrear(app, chaves);
  }

  async function rastrear(app, chaves) {
    setProgresso("Iniciando rastreamento…");
    const bt = document.getElementById("rastTrack");
    if (bt) bt.disabled = true;
    const res = await (await app.api()).rastreio_iniciar(chaves || null);
    if (res && res.erro) { app.toast(res.erro); setProgresso(""); atualizarBotoes(); }
  }

  function limpar(app) {
    for (const k in cards) delete cards[k];
    const box = document.getElementById("rastCards");
    box.innerHTML = placeholder();
    setProgresso("");
    document.getElementById("rastScreens").hidden = true;
    atualizarBotoes();
    app.api().then((a) => a.rastreio_limpar());
  }

  function aplicarResultado(chave, r) {
    const card = cards[chave];
    if (!card || !r) return;
    const track = card.querySelector(".nfe-track");
    const status = card.querySelector(".nfe-track-status");
    const detail = card.querySelector(".nfe-track-detail");
    track.hidden = false;
    detail.innerHTML = "";
    const F = window.Fmt;
    const linha = (lbl, val) => `<div class="nfe-row"><span class="nfe-row-l">${lbl}</span><span class="nfe-row-v">${F.esc(val)}</span></div>`;
    const linkBtn = (lbl, target) => `<button class="link-btn" data-open="${F.esc(target)}" type="button">${lbl}</button>`;

    if (r.erro) {
      status.className = "nfe-track-status err";
      status.textContent = "✕ Erro: " + r.erro;
    } else if (r.entregue) {
      status.className = "nfe-track-status ok";
      status.textContent = "✓ ENTREGUE";
      if (r.status_texto && r.status_texto !== "ENTREGUE") detail.innerHTML += linha("Status:", r.status_texto);
      if (r.previsao_entrega) detail.innerHTML += linha("Data entrega:", r.previsao_entrega);
      if (r.screenshot_path) detail.innerHTML += linkBtn("Abrir screenshot", r.screenshot_path);
      if (r.link_rastreio) detail.innerHTML += linkBtn("Abrir rastreio", r.link_rastreio);
    } else {
      status.className = "nfe-track-status transito";
      status.textContent = "▣ " + (r.status_texto || "Em trânsito");
      if (r.previsao_entrega) detail.innerHTML += linha("Previsão:", r.previsao_entrega);
      if (r.link_rastreio) detail.innerHTML += linkBtn("Abrir rastreio", r.link_rastreio);
    }
  }

  window.Pages = window.Pages || {};
  window.Pages.rastreio = {
    title: "Rastreio",
    render(view, app) {
      for (const k in cards) delete cards[k];
      view.innerHTML = `
        <div class="rast-toolbar">
          <button class="btn btn-primary" id="rastSelect" type="button">Selecionar XML(s)</button>
          <button class="btn btn-soft" id="rastTrack" type="button" disabled>Rastrear entregas</button>
          <button class="btn btn-soft" id="rastClear" type="button">Limpar</button>
          <button class="btn btn-soft" id="rastScreens" type="button" hidden>Abrir pasta de screenshots</button>
          <span class="rast-progress" id="rastProgress"></span>
        </div>
        <div class="rast-cards" id="rastCards">${placeholder()}</div>`;

      view.addEventListener("click", (e) => {
        const el = e.target.closest("[data-open]");
        if (el) { app.api().then((a) => a.abrir_externo(el.getAttribute("data-open"))); }
      });
      $("#rastSelect", view).addEventListener("click", () => selecionar(app));
      $("#rastTrack", view).addEventListener("click", () => rastrear(app, null));
      $("#rastClear", view).addEventListener("click", () => limpar(app));
      $("#rastScreens", view).addEventListener("click", () => app.api().then((a) => a.abrir_screenshots()));
    },

    onEvent(evt, app) {
      switch (evt.event) {
        case "rastreio_progress": {
          const p = evt.payload || {};
          setProgresso(`Rastreando… ${p.indice}/${p.total}`);
          aplicarResultado(p.chave, p.resultado);
          break;
        }
        case "rastreio_finished": {
          const p = evt.payload || {};
          setProgresso(`Rastreamento concluído: ${p.entregues || 0}/${p.total || 0} entregue(s)` +
            (p.screenshots ? ` — ${p.screenshots} screenshot(s)` : ""));
          atualizarBotoes();
          if (p.screenshots) document.getElementById("rastScreens").hidden = false;
          if (p.erro) app.toast("Erro no rastreamento: " + p.erro);
          break;
        }
        case "chrome_missing":
          app.toast((evt.payload && evt.payload.texto) || "Google Chrome não encontrado");
          setProgresso("");
          atualizarBotoes();
          break;
      }
    },
  };
})();
