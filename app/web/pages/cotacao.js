/* Página Cotação — cola/revisa romaneio, dispara cotação e mostra progresso
   ao vivo por transportadora (porta _rotulos_status_cotacao / a tabela de status). */
"use strict";

(function () {
  let running = false;

  async function iniciar(app) {
    const ta = document.getElementById("cotInput");
    const texto = (ta.value || "").trim();
    if (!texto) { app.toast("Cole um romaneio antes de cotar"); return; }
    running = true;
    window.CotStatus.reset("cotStatusBody");
    document.getElementById("cotStatusWrap").hidden = false;
    document.getElementById("cotResult").textContent = "Cotação em andamento. As respostas aparecem conforme cada transportadora finaliza.";
    const btn = document.getElementById("cotStart");
    btn.disabled = true; btn.classList.add("loading");
    document.getElementById("cotRunStatus").textContent = "Cotação em andamento. Aguarde as respostas das transportadoras.";
    const res = await (await app.api()).cotacao_iniciar(texto);
    if (res && res.erro) { app.toast(res.erro); finalizar(); }
  }

  function finalizar() {
    running = false;
    const btn = document.getElementById("cotStart");
    if (btn) { btn.disabled = false; btn.classList.remove("loading"); }
  }

  window.Pages = window.Pages || {};
  window.Pages.cotacao = {
    title: "Cotação",
    render(view, app) {
      running = false;
      view.innerHTML = `
        <div class="cot-cols">
          <section class="card cot-card">
            <div class="card-pad">
              <h2 class="card-title">1. Cole ou revise o romaneio</h2>
              <p class="card-hint">Use o texto processado do PDF ou cole o romaneio completo antes de iniciar.</p>
              <textarea id="cotInput" class="input-area mono" spellcheck="false" placeholder="Exemplo:&#10;CNPJ/CPF: ...&#10;- VOL: ...&#10;- CUBAGEM: ... m3&#10;- PESO: ... kg&#10;- TOTAL: R$ ..."></textarea>
              <div class="cot-hintbox" id="cotHint">Aguardando romaneio para liberar a cotação.</div>
              <button class="btn btn-primary" id="cotStart" type="button" disabled>
                <span class="spin" aria-hidden="true"></span>Iniciar cotação
              </button>
              <div class="cot-runstatus" id="cotRunStatus">Pronto para cotar assim que houver romaneio.</div>
            </div>
          </section>

          <section class="card cot-card">
            <div class="card-pad">
              <div class="card-title-row">
                <h2 class="card-title">Resultado da cotação</h2>
                <button class="btn btn-soft btn-sm" id="cotCopy" type="button">Copiar</button>
              </div>
              <div class="cot-status-wrap" id="cotStatusWrap" hidden>
                <table class="cot-table">
                  <thead><tr><th>Transp.</th><th>Situação</th><th>Etapa</th><th>Mensagem</th><th>Tempo</th></tr></thead>
                  <tbody id="cotStatusBody"></tbody>
                </table>
              </div>
              <pre class="result-text" id="cotResult">O resultado calculado pelas transportadoras aparecerá aqui.</pre>
            </div>
          </section>
        </div>`;

      const ta = $("#cotInput", view);
      const updateHint = () => {
        const txt = (ta.value || "").trim();
        const pronto = !!txt;
        $("#cotStart", view).disabled = pronto ? running : true;
        const linhas = txt ? txt.split("\n").filter((l) => l.trim()).length : 0;
        $("#cotHint", view).textContent = pronto
          ? `Romaneio preenchido com ${linhas} linha(s). Confira os dados e clique em Iniciar cotação.`
          : "Aguardando romaneio para liberar a cotação.";
        if (!running) {
          $("#cotRunStatus", view).textContent = pronto ? "Pronto para iniciar." : "Pronto para cotar assim que houver romaneio.";
        }
      };
      ta.addEventListener("input", updateHint);
      $("#cotStart", view).addEventListener("click", () => iniciar(app));
      $("#cotCopy", view).addEventListener("click", async () => {
        const txt = $("#cotResult", view).textContent || "";
        try { await navigator.clipboard.writeText(txt); app.toast("Resultado copiado"); }
        catch { app.toast("Não foi possível copiar"); }
      });

      // Pré-carrega romaneio vindo do PDF/processamento, se houver
      if (app.state.romaneioTexto) { ta.value = app.state.romaneioTexto; }
      updateHint();
    },

    onEvent(evt, app) {
      switch (evt.event) {
        case "cotacao_progress":
          window.CotStatus.upsert("cotStatusBody", evt.payload || {});
          break;
        case "cotacao_result": {
          const el = document.getElementById("cotResult");
          if (el) el.textContent = (evt.payload && evt.payload.resumo) || "";
          break;
        }
        case "cotacao_finished":
          finalizar();
          document.getElementById("cotRunStatus") &&
            (document.getElementById("cotRunStatus").textContent = "Cotação finalizada.");
          break;
        case "status_update":
          if (running) {
            const rs = document.getElementById("cotRunStatus");
            if (rs && evt.payload && evt.payload.texto) rs.textContent = evt.payload.texto;
          }
          break;
        case "chrome_missing":
          app.toast((evt.payload && evt.payload.texto) || "Google Chrome não encontrado");
          finalizar();
          break;
      }
    },
  };
})();
