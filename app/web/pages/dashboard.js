/* Página Dashboard — KPIs, romaneios recentes e status das transportadoras.
   Os KPIs e recentes vêm de api.get_dashboard() (estado vivo da sessão). */
"use strict";

(function () {
  function kpisHTML(d) {
    const melhor = d.melhor_frete, sucesso = d.sucesso_pct;
    const cards = [
      { label: "Romaneios", value: String(d.total_romaneios ?? 0), sub: d.sub_romaneios || "processados nesta sessão" },
      { label: "Volumes", value: String(d.total_volumes ?? 0), sub: d.sub_volumes || "volumes processados" },
      melhor == null
        ? { label: "Melhor frete", bar: "green", sub: "inicie uma cotação" }
        : { label: "Melhor frete", value: window.Fmt.moeda(melhor), valueClass: "green", sub: "menor valor da última cotação" },
      sucesso == null
        ? { label: "Sucesso cotação", bar: "amber", sub: "aguardando retorno" }
        : { label: "Sucesso cotação", value: sucesso + "%", valueClass: "amber", sub: d.sub_sucesso || "" },
    ];
    return cards.map((c) => `
      <div class="kpi">
        <div class="kpi-label">${c.label}</div>
        ${c.bar ? `<div class="kpi-bar ${c.bar}"></div>`
                : `<div class="kpi-value ${c.valueClass || ""}">${c.value}</div>`}
        <div class="kpi-sub">${c.sub}</div>
      </div>`).join("");
  }

  function recentesHTML(rows) {
    if (!rows || rows.length === 0) {
      return `<div class="empty">
        <span class="empty-ic" aria-hidden="true"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2Z"/></svg></span>
        <div><div class="empty-title">Nenhum romaneio processado nesta sessão</div>
        <div class="empty-text">Selecione um PDF de romaneio para preencher esta lista e atualizar os indicadores acima.</div></div>
      </div>`;
    }
    const F = window.Fmt;
    return rows.slice(0, 6).map((r) => `
      <div class="rec-row">
        <span class="rec-date">${F.esc(r.data || "—")}</span>
        <span class="rec-name" title="${F.esc(r.nome || "")}">${F.esc(r.nome || "—")}</span>
        <span class="rec-dest" title="${F.esc(r.destino || "")}">${F.esc(r.destino || "—")}</span>
        <span class="rec-vol">${r.volumes || 0} vol.</span>
      </div>`).join("");
  }

  function carriersHTML(list) {
    const F = window.Fmt;
    const habilitadas = (list || []).filter((c) => c.habilitado);
    if (habilitadas.length === 0) {
      return `<div class="carrier"><span class="dot pending"></span>
        <div><div class="empty-title">Nenhuma transportadora habilitada</div>
        <div class="empty-text">Abra Configurações e habilite as transportadoras usadas nas cotações.</div></div></div>`;
    }
    return habilitadas.map((c) => `
      <div class="carrier">
        <span class="dot ${c.status || "pending"}" id="dot-${F.esc(c.nome)}"></span>
        <span class="carrier-name">${F.esc(c.nome)}</span>
        <span class="carrier-tag green">habilitada</span>
      </div>`).join("");
  }

  async function refresh(app) {
    const a = await app.api();
    if (!a.get_dashboard) return;
    const d = await a.get_dashboard();
    if (!d) return;
    app.state.dashboard = d;
    const kg = document.getElementById("kpiGrid");
    const rb = document.getElementById("recentesBody");
    if (kg) kg.innerHTML = kpisHTML(d);
    if (rb) rb.innerHTML = recentesHTML(d.romaneios_recentes || []);
  }

  window.Pages = window.Pages || {};
  window.Pages.dashboard = {
    title: "Dashboard",
    render(view, app) {
      const s = app.state;
      const d = s.dashboard || {};
      view.innerHTML = `
        <div class="kpi-grid" id="kpiGrid">${kpisHTML(d)}</div>
        <div class="dash-cols">
          <div class="card">
            <div class="card-head"><span class="card-eyebrow">Romaneios recentes</span><span class="card-meta">sessão atual</span></div>
            <div class="card-body" id="recentesBody">${recentesHTML(d.romaneios_recentes || [])}</div>
          </div>
          <div class="card">
            <div class="card-pad">
              <span class="card-eyebrow">Status das transportadoras</span>
              <p class="card-hint">Mostra apenas transportadoras habilitadas para cotação.</p>
              <div>${carriersHTML(s.transportadoras || [])}</div>
            </div>
          </div>
        </div>`;
      refresh(app);
    },
    onEvent(evt, app) {
      if (evt.event === "login_status") {
        const p = evt.payload || {};
        const dot = document.getElementById("dot-" + p.nome);
        if (dot) dot.className = "dot " + (p.status || "pending");
      } else if (evt.event === "dashboard_dirty") {
        refresh(app);
      }
    },
  };
})();
