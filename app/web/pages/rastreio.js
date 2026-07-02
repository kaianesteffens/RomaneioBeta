/* Página Rastreio — importa NF-e (XML), mostra cards (licitação/entrega) e
   rastreia entregas ao vivo (porta _criar_card_nfe / _on_rastreio_result). */
"use strict";

(function () {
  const cards = {}; // chave -> elemento do card

  // Estado de rastreamento por sessão (sobrevive à navegação). Os cards em si são
  // recarregados do backend (nfe_cards); aqui guardamos só os resultados e a UI.
  // tracking = há um rastreamento DESTA tela em curso cujos eventos nos interessam.
  function st(app) {
    return app.state.rastreio || (app.state.rastreio = { results: {}, progress: "", screens: false, tracking: false });
  }

  function cardHTML(c) {
    const F = window.Fmt;
    return `
      <div class="nfe-card" data-chave="${F.esc(c.chave)}">
        <div class="nfe-head">${F.esc(c.header)}</div>
        <div class="nfe-blocks">
          <div class="nfe-block lic">
            <div class="nfe-block-title">
              <span>DADOS DA LICITAÇÃO</span>
              <button class="link-btn nfe-copy" type="button" data-copy="lic">Copiar</button>
            </div>
            <pre>${F.esc(c.bloco_licitacao)}</pre>
          </div>
          <div class="nfe-block ent">
            <div class="nfe-block-title">
              <span>DADOS DA ENTREGA</span>
              <button class="link-btn nfe-copy" type="button" data-copy="ent">Copiar</button>
            </div>
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
    st(app).progress = "Iniciando rastreamento…";
    const bt = document.getElementById("rastTrack");
    if (bt) bt.disabled = true;
    const res = await (await app.api()).rastreio_iniciar(chaves || null);
    if (res && res.erro) { app.toast(res.erro); setProgresso(""); st(app).progress = ""; atualizarBotoes(); return; }
    // Dono só após o backend aceitar (evita reter o dono num rastreio rejeitado).
    app.beginOp("rastreio");
    st(app).tracking = true;
  }

  function limpar(app) {
    for (const k in cards) delete cards[k];
    const box = document.getElementById("rastCards");
    box.innerHTML = placeholder();
    setProgresso("");
    document.getElementById("rastScreens").hidden = true;
    atualizarBotoes();
    const s = st(app); s.results = {}; s.progress = ""; s.screens = false; s.tracking = false;
    // A coroutine de rastreio NÃO é cancelada pelo backend (rastreio_limpar só zera
    // _notas), então libera o dono p/ os eventos em voo não voltarem a esta tela e
    // ressuscitarem o estado já limpo (tracking=false já os ignora se ficarmos aqui).
    if (app.opOwners.rastreio === app.page) app.opOwners.rastreio = null;
    app.api().then((a) => a.rastreio_limpar());
  }

  // Recarrega as NF-e importadas (backend = fonte da verdade) e re-aplica os
  // resultados de rastreamento desta sessão. Chamado no render para os cards e o
  // status sobreviverem à navegação para outra tela e volta (Codex P2).
  async function reidratar(app) {
    const a = await app.api();
    if (!a.nfe_cards) return;
    let res;
    try { res = await a.nfe_cards(); } catch (e) { return; }
    const box = document.getElementById("rastCards");
    if (!box) return;  // saiu da tela enquanto carregava
    const lista = (res && res.cards) || [];
    if (lista.length) {
      // Reconstrói do zero (não insere sobre o que já existe) — evita cards
      // duplicados se uma seleção/render concorrente já populou o box.
      box.innerHTML = "";
      for (const k in cards) delete cards[k];
      for (const c of lista) {
        box.insertAdjacentHTML("afterbegin", cardHTML(c));
        cards[c.chave] = box.querySelector(`.nfe-card[data-chave="${CSS.escape(c.chave)}"]`);
      }
      atualizarBotoes();
    }
    const s = st(app);
    for (const chave in s.results) aplicarResultado(chave, s.results[chave]);
    if (s.progress) setProgresso(s.progress);
    if (s.screens) { const b = document.getElementById("rastScreens"); if (b) b.hidden = false; }
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
    const linkBtn = (lbl, target, kind) => `<button class="link-btn" data-open="${F.esc(target)}" data-kind="${kind}" type="button">${lbl}</button>`;

    if (r.erro) {
      status.className = "nfe-track-status err";
      status.textContent = "✕ Erro: " + r.erro;
    } else if (r.entregue) {
      status.className = "nfe-track-status ok";
      status.textContent = "✓ ENTREGUE";
      if (r.status_texto && r.status_texto !== "ENTREGUE") detail.innerHTML += linha("Status:", r.status_texto);
      if (r.previsao_entrega) detail.innerHTML += linha("Data entrega:", r.previsao_entrega);
      if (r.screenshot_path) detail.innerHTML += linkBtn("Abrir screenshot", r.screenshot_path, "file");
      if (r.link_rastreio) detail.innerHTML += linkBtn("Abrir rastreio", r.link_rastreio, "url");
    } else {
      status.className = "nfe-track-status transito";
      status.textContent = "▣ " + (r.status_texto || "Em trânsito");
      if (r.previsao_entrega) detail.innerHTML += linha("Previsão:", r.previsao_entrega);
      if (r.link_rastreio) detail.innerHTML += linkBtn("Abrir rastreio", r.link_rastreio, "url");
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

      // Delegação no container de cards (recriado a cada render) — evita acumular
      // listeners no #view a cada visita à tela.
      $("#rastCards", view).addEventListener("click", (e) => {
        const open = e.target.closest("[data-open]");
        if (open) {
          const alvo = open.getAttribute("data-open");
          // Defesa em profundidade: o link de rastreio vem do provider; só abre
          // http(s) — rejeita esquemas locais/perigosos (file:, javascript:, etc.).
          if (open.getAttribute("data-kind") === "url" && !/^https?:\/\//i.test(alvo || "")) {
            app.toast("Link de rastreio inválido");
            return;
          }
          app.api().then((a) => a.abrir_externo(alvo));
          return;
        }
        const copy = e.target.closest("[data-copy]");
        if (copy) {
          // Copia o texto do bloco (licitação/entrega) — selecionar no WebView2 é
          // pouco prático, então damos um botão como na tela Romaneio.
          const bloco = copy.closest(".nfe-block");
          const pre = bloco && bloco.querySelector("pre");
          const txt = (pre && pre.textContent) || "";
          navigator.clipboard.writeText(txt)
            .then(() => app.toast("Texto copiado"))
            .catch(() => app.toast("Não foi possível copiar"));
        }
      });
      $("#rastSelect", view).addEventListener("click", () => selecionar(app));
      $("#rastTrack", view).addEventListener("click", () => rastrear(app, null));
      $("#rastClear", view).addEventListener("click", () => limpar(app));
      $("#rastScreens", view).addEventListener("click", () => app.api().then((a) => a.abrir_screenshots()));

      reidratar(app);
    },

    onEvent(evt, app) {
      const s = st(app);
      switch (evt.event) {
        case "rastreio_progress": {
          if (!s.tracking) break;  // operação abandonada (Limpar): ignora eventos em voo
          const p = evt.payload || {};
          s.progress = `Rastreando… ${p.indice}/${p.total}`;
          if (p.chave) s.results[p.chave] = p.resultado;
          setProgresso(s.progress);
          aplicarResultado(p.chave, p.resultado);
          break;
        }
        case "rastreio_finished": {
          if (!s.tracking) break;
          const p = evt.payload || {};
          s.progress = `Rastreamento concluído: ${p.entregues || 0}/${p.total || 0} entregue(s)` +
            (p.screenshots ? ` — ${p.screenshots} screenshot(s)` : "");
          s.screens = !!p.screenshots;
          s.tracking = false;
          setProgresso(s.progress);
          atualizarBotoes();
          if (p.screenshots) { const b = document.getElementById("rastScreens"); if (b) b.hidden = false; }
          if (p.erro) app.toast("Erro no rastreamento: " + p.erro);
          break;
        }
        case "chrome_missing":
          app.toast((evt.payload && evt.payload.texto) || "Google Chrome não encontrado");
          setProgresso("");
          s.progress = "";
          s.tracking = false;
          atualizarBotoes();
          break;
      }
    },
  };
})();
