/* Página Fornecedores — frete FOB: monta romaneio a partir do formulário e cota
   reusando o motor de cotação (porta _cotar_frete_fornecedor / _montar_romaneio_fornecedor). */
"use strict";

(function () {
  let running = false;

  function maskCNPJ(v) {
    const d = v.replace(/\D/g, "").slice(0, 14);
    let o = "";
    for (let i = 0; i < d.length; i++) {
      if (i === 2 || i === 5) o += ".";
      if (i === 8) o += "/";
      if (i === 12) o += "-";
      o += d[i];
    }
    return o;
  }
  function maskCEP(v) {
    const d = v.replace(/\D/g, "").slice(0, 8);
    return d.length > 5 ? d.slice(0, 5) + "-" + d.slice(5) : d;
  }

  function campo(id, label, ph, extra) {
    return `<label class="field">
      <span class="field-label">${label}</span>
      <input class="field-input" id="${id}" type="text" placeholder="${ph || ""}" autocomplete="off" ${extra || ""}/>
    </label>`;
  }

  function coletarForm() {
    const v = (id) => (document.getElementById(id).value || "").trim();
    return {
      cnpj: v("fornCnpj"), cep: v("fornCep"), qtd: v("fornQtd"),
      alt: v("fornAlt"), larg: v("fornLarg"), comp: v("fornComp"),
      peso_cx: v("fornPesoCx"), peso_total: v("fornPesoTotal"), valor: v("fornValor"),
    };
  }

  async function cotar(app) {
    const form = coletarForm();
    running = true;
    window.CotStatus.reset("fornStatusBody");
    document.getElementById("fornStatusWrap").hidden = false;
    document.getElementById("fornResult").textContent = "Cotação em andamento. As respostas aparecem conforme cada transportadora finaliza.";
    const btn = document.getElementById("fornCotar");
    btn.disabled = true; btn.classList.add("loading");
    const res = await (await app.api()).fornecedor_cotar(form);
    if (res && res.erro) {
      btn.disabled = false; btn.classList.remove("loading");
      running = false;
      document.getElementById("fornResult").textContent = res.erro;
      app.toast(res.erro.split("\n")[0]);
    }
  }

  function finalizar() {
    running = false;
    const btn = document.getElementById("fornCotar");
    if (btn) { btn.disabled = false; btn.classList.remove("loading"); }
  }

  window.Pages = window.Pages || {};
  window.Pages.fornecedores = {
    title: "Fornecedores",
    render(view, app) {
      running = false;
      view.innerHTML = `
        <div class="cot-cols">
          <section class="card cot-card">
            <div class="card-pad">
              <h2 class="card-title">Frete de fornecedor (FOB)</h2>
              <p class="card-hint">Informe os dados do fornecedor e da carga. O romaneio é montado e cotado automaticamente.</p>
              <div class="forn-grid">
                ${campo("fornCnpj", "CNPJ do fornecedor", "00.000.000/0000-00")}
                ${campo("fornCep", "CEP do fornecedor", "00000-000")}
                ${campo("fornQtd", "Qtd. de volumes", "0", 'inputmode="numeric"')}
                ${campo("fornValor", "Valor total (R$)", "0,00")}
                ${campo("fornAlt", "Altura (cm)", "0")}
                ${campo("fornLarg", "Largura (cm)", "0")}
                ${campo("fornComp", "Comprimento (cm)", "0")}
                ${campo("fornPesoCx", "Peso por volume (kg)", "opcional")}
                ${campo("fornPesoTotal", "Peso total (kg)", "opcional")}
              </div>
              <p class="forn-note">Informe o peso por volume <strong>ou</strong> o peso total.</p>
              <button class="btn btn-primary" id="fornCotar" type="button">
                <span class="spin" aria-hidden="true"></span>Cotar frete fornecedor
              </button>
            </div>
          </section>

          <section class="card cot-card">
            <div class="card-pad">
              <div class="card-title-row">
                <h2 class="card-title">Resultado da cotação</h2>
                <button class="btn btn-soft btn-sm" id="fornCopy" type="button">Copiar</button>
              </div>
              <div class="cot-status-wrap" id="fornStatusWrap" hidden>
                <table class="cot-table">
                  <thead><tr><th>Transp.</th><th>Situação</th><th>Etapa</th><th>Mensagem</th><th>Tempo</th></tr></thead>
                  <tbody id="fornStatusBody"></tbody>
                </table>
              </div>
              <pre class="result-text" id="fornResult">Preencha os dados do fornecedor e clique em Cotar.</pre>
            </div>
          </section>
        </div>`;

      const cnpj = $("#fornCnpj", view), cep = $("#fornCep", view);
      cnpj.addEventListener("input", () => { cnpj.value = maskCNPJ(cnpj.value); });
      cep.addEventListener("input", () => { cep.value = maskCEP(cep.value); });
      $("#fornCotar", view).addEventListener("click", () => cotar(app));
      $("#fornCopy", view).addEventListener("click", async () => {
        const txt = $("#fornResult", view).textContent || "";
        try { await navigator.clipboard.writeText(txt); app.toast("Resultado copiado"); }
        catch { app.toast("Não foi possível copiar"); }
      });
    },

    onEvent(evt, app) {
      switch (evt.event) {
        case "cotacao_progress":
          window.CotStatus.upsert("fornStatusBody", evt.payload || {});
          break;
        case "cotacao_result": {
          const el = document.getElementById("fornResult");
          if (el) el.textContent = (evt.payload && evt.payload.resumo) || "";
          break;
        }
        case "cotacao_finished":
          finalizar();
          break;
        case "chrome_missing":
          app.toast((evt.payload && evt.payload.texto) || "Google Chrome não encontrado");
          finalizar();
          break;
      }
    },
  };
})();
