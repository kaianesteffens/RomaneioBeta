/* Página Configurações — sub-abas Empresa / Aparência / Transportadoras / Credenciais.
   Data-driven a partir de api.config_get(); grava via api.config_salvar_*. */
"use strict";

(function () {
  const F = () => window.Fmt;
  let cfg = null;
  let sub = "empresa";

  const SUBTABS = [
    ["empresa", "Empresa"], ["aparencia", "Aparência"],
    ["transportadoras", "Transportadoras"], ["credenciais", "Credenciais"],
  ];

  function maskCNPJ(v) {
    const d = v.replace(/\D/g, "").slice(0, 14); let o = "";
    for (let i = 0; i < d.length; i++) { if (i === 2 || i === 5) o += "."; if (i === 8) o += "/"; if (i === 12) o += "-"; o += d[i]; }
    return o;
  }
  const maskCEP = (v) => { const d = v.replace(/\D/g, "").slice(0, 8); return d.length > 5 ? d.slice(0, 5) + "-" + d.slice(5) : d; };

  function seg(name, options, current) {
    return `<div class="seg" data-seg="${name}">${options.map((o) => {
      const val = Array.isArray(o) ? o[0] : o, lbl = Array.isArray(o) ? o[1] : o;
      const active = String(val).toLowerCase() === String(current).toLowerCase() ? " active" : "";
      return `<button class="seg-btn${active}" type="button" data-val="${F().esc(val)}">${F().esc(lbl)}</button>`;
    }).join("")}</div>`;
  }
  function bindSeg(root) {
    root.querySelectorAll(".seg").forEach((s) => s.addEventListener("click", (e) => {
      const b = e.target.closest(".seg-btn"); if (!b) return;
      s.querySelectorAll(".seg-btn").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
    }));
  }
  const segVal = (root, name) => {
    const b = root.querySelector(`.seg[data-seg="${name}"] .seg-btn.active`);
    return b ? b.dataset.val : "";
  };

  // ── Painéis ──────────────────────────────────────────────────────────────
  function painelEmpresa(app) {
    const e = cfg.empresa;
    return {
      html: `<div class="cfg-card">
        <h2 class="card-title">Empresa</h2>
        <p class="card-hint">Dados usados como padrão nas cotações e identificação da empresa ativa.</p>
        <div class="cfg-grid">
          <label class="field"><span class="field-label">Nome da empresa</span><input class="field-input" id="cEmp" value="${F().esc(e.nome)}" disabled/></label>
          <label class="field"><span class="field-label">CEP de origem</span><input class="field-input" id="cCep" value="${F().esc(F().cep(e.cep_origem))}" placeholder="00000-000"/></label>
          <label class="field"><span class="field-label">Documento pagador padrão</span><input class="field-input" id="cPag" value="${F().esc(e.cnpj_pagador)}" placeholder="CNPJ/CPF padrão"/></label>
          <label class="field"><span class="field-label">Cotações paralelas (1–7)</span><input class="field-input" id="cPar" value="${e.paralelas}" inputmode="numeric"/></label>
        </div>
        <button class="btn btn-primary" id="cSaveEmp" type="button">Salvar</button>
      </div>`,
      bind(root) {
        const cep = root.querySelector("#cCep"), pag = root.querySelector("#cPag");
        cep.addEventListener("input", () => cep.value = maskCEP(cep.value));
        pag.addEventListener("input", () => pag.value = maskCNPJ(pag.value));
        root.querySelector("#cSaveEmp").addEventListener("click", async () => {
          const data = { cep_origem: cep.value, cnpj_pagador: pag.value, paralelas: root.querySelector("#cPar").value };
          const r = await (await app.api()).config_salvar_empresa(data);
          app.toast(r && r.ok ? "Empresa salva" : "Falha ao salvar");
        });
      },
    };
  }

  function painelAparencia(app) {
    const a = cfg.aparencia;
    return {
      html: `<div class="cfg-card">
        <h2 class="card-title">Aparência</h2>
        <p class="card-hint">Tema, arredondamento e estilo de botão da interface.</p>
        <div class="cfg-row"><span class="field-label">Tema</span>${seg("tema", [["claro", "Claro"], ["escuro", "Escuro"], ["sistema", "Sistema"]], a.tema)}</div>
        <div class="cfg-row"><span class="field-label">Cantos</span>${seg("raio", a.raios, a.raio)}</div>
        <div class="cfg-row"><span class="field-label">Botão primário</span>${seg("botao", a.botoes, a.botao)}</div>
        <div class="cfg-row"><span class="field-label">Cor de destaque</span><span class="accent-chip"><span class="accent-dot"></span>${F().esc(a.accent)}</span></div>
        <button class="btn btn-primary" id="cSaveAp" type="button">Salvar e aplicar</button>
      </div>`,
      bind(root) {
        bindSeg(root);
        root.querySelector("#cSaveAp").addEventListener("click", async () => {
          const data = { tema: segVal(root, "tema"), raio: segVal(root, "raio"), botao: segVal(root, "botao") };
          const r = await (await app.api()).config_salvar_aparencia(data);
          if (r && r.ok) {
            window.App.applyTema(r.tema_efetivo);
            window.App.applyAparencia(data.raio, data.botao);
            app.toast("Aparência aplicada");
          } else app.toast("Falha ao salvar");
        });
      },
    };
  }

  function ufChips(carrier) {
    const set = new Set((carrier.ufs_atendidas || []).map((u) => u.toUpperCase()));
    return `<div class="uf-chips" data-carrier="${F().esc(carrier.nome)}">${cfg.ufs.map((uf) =>
      `<button class="uf-chip${set.has(uf) ? " active" : ""}" type="button" data-uf="${uf}">${uf}</button>`).join("")}</div>`;
  }

  function painelTransportadoras(app) {
    return {
      html: `<div class="cfg-card">
        <h2 class="card-title">Transportadoras</h2>
        <p class="card-hint">Habilite as transportadoras e defina as UFs atendidas. Sem nenhuma UF marcada = atende todas.</p>
        ${cfg.transportadoras.map((c) => `
          <div class="carrier-cfg" data-carrier="${F().esc(c.nome)}">
            <div class="carrier-cfg-head">
              <span class="carrier-name">${F().esc(c.nome)}</span>
              <button class="switch-btn${c.habilitado ? " on" : ""}" type="button" role="switch" aria-checked="${c.habilitado}" data-enable>
                <span class="switch"><span class="knob"></span></span>
                <span class="switch-lbl">${c.habilitado ? "Habilitada" : "Desabilitada"}</span>
              </button>
            </div>
            ${ufChips(c)}
          </div>`).join("")}
        <button class="btn btn-primary" id="cSaveTransp" type="button">Salvar transportadoras</button>
      </div>`,
      bind(root) {
        root.querySelectorAll("[data-enable]").forEach((b) => b.addEventListener("click", () => {
          const on = b.classList.toggle("on");
          b.setAttribute("aria-checked", String(on));
          b.querySelector(".switch-lbl").textContent = on ? "Habilitada" : "Desabilitada";
        }));
        root.querySelectorAll(".uf-chip").forEach((c) => c.addEventListener("click", () => c.classList.toggle("active")));
        root.querySelector("#cSaveTransp").addEventListener("click", async () => {
          const a = await app.api();
          let ok = true;
          for (const card of root.querySelectorAll(".carrier-cfg")) {
            const nome = card.dataset.carrier;
            const habilitado = card.querySelector("[data-enable]").classList.contains("on");
            const ufs = Array.from(card.querySelectorAll(".uf-chip.active")).map((x) => x.dataset.uf);
            const r = await a.config_salvar_transportadora(nome, { habilitado, ufs_atendidas: ufs });
            ok = ok && r && r.ok;
          }
          app.toast(ok ? "Transportadoras salvas" : "Falha ao salvar");
        });
      },
    };
  }

  function painelCredenciais(app) {
    return {
      html: `<div class="cfg-card">
        <h2 class="card-title">Credenciais</h2>
        <p class="card-hint">Logins e senhas das transportadoras. Ficam apenas no seu computador.</p>
        ${cfg.transportadoras.map((c) => `
          <div class="cred-group" data-carrier="${F().esc(c.nome)}">
            <div class="cred-name">${F().esc(c.nome)}</div>
            <div class="cfg-grid">
              ${c.campos.map((f) => `<label class="field">
                <span class="field-label">${F().esc(f.label)}</span>
                <input class="field-input" data-key="${F().esc(f.key)}" type="${f.tipo === "password" ? "password" : "text"}"
                       value="${F().esc(f.valor)}" autocomplete="off"
                       placeholder="${f.tipo === "password" && f.tem_valor ? "•••••• salva — deixe em branco para manter" : ""}"/>
              </label>`).join("")}
            </div>
          </div>`).join("")}
        <button class="btn btn-primary" id="cSaveCred" type="button">Salvar credenciais</button>
      </div>`,
      bind(root) {
        root.querySelector("#cSaveCred").addEventListener("click", async () => {
          const a = await app.api();
          let ok = true;
          for (const grp of root.querySelectorAll(".cred-group")) {
            const nome = grp.dataset.carrier;
            const campos = {};
            grp.querySelectorAll("[data-key]").forEach((inp) => { campos[inp.dataset.key] = inp.value; });
            const r = await a.config_salvar_credenciais(nome, campos);
            ok = ok && r && r.ok;
          }
          app.toast(ok ? "Credenciais salvas" : "Falha ao salvar");
        });
      },
    };
  }

  function renderSub(app) {
    const panel = document.getElementById("cfgPanel");
    if (!panel) return;
    const builder = { empresa: painelEmpresa, aparencia: painelAparencia,
      transportadoras: painelTransportadoras, credenciais: painelCredenciais }[sub](app);
    panel.innerHTML = builder.html;
    builder.bind(panel);
    document.querySelectorAll(".cfg-tab").forEach((t) => t.classList.toggle("active", t.dataset.sub === sub));
  }

  window.Pages = window.Pages || {};
  window.Pages.config = {
    title: "Configurações",
    render(view, app) {
      view.innerHTML = `
        <div class="cfg-nav">${SUBTABS.map(([k, l]) =>
          `<button class="cfg-tab${k === sub ? " active" : ""}" type="button" data-sub="${k}">${l}</button>`).join("")}</div>
        <div id="cfgPanel"><div class="placeholder"><div class="ph-text">Carregando configurações…</div></div></div>`;
      view.querySelectorAll(".cfg-tab").forEach((t) => t.addEventListener("click", () => { sub = t.dataset.sub; renderSub(app); }));
      app.api().then((a) => a.config_get()).then((c) => { cfg = c; renderSub(app); })
        .catch(() => { document.getElementById("cfgPanel").innerHTML = `<div class="placeholder"><div class="ph-text">Erro ao carregar configurações.</div></div>`; });
    },
  };
})();
