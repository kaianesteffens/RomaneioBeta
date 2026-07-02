/* Página Romaneio — seleciona PDF, extrai pedidos e mostra o romaneio calculado
   (porta _processar_pdf / _on_pdf_processed). Alimenta a Cotação. */
"use strict";

(function () {
  function setInfo(txt, tone) {
    const el = document.getElementById("romInfo");
    if (!el) return;
    el.textContent = txt || "";
    el.className = "rom-info" + (tone ? " " + tone : "");
  }

  async function selecionar(app) {
    setInfo("Processando PDF…", "info");
    const btn = document.getElementById("romSelect");
    btn.disabled = true; btn.classList.add("loading");
    try {
      const res = await (await app.api()).romaneio_processar();
      if (!res || res.cancelado) { setInfo("Nenhum arquivo carregado", ""); return; }
      if (res.erro) { setInfo(res.erro, "err"); app.toast(res.erro.split("\n")[0]); return; }

      document.getElementById("romResult").textContent = res.texto || "";
      document.getElementById("romUse").disabled = false;
      document.getElementById("romCopy").disabled = false;
      app.state.romaneioTexto = res.texto || "";
      setInfo(`OK: ${res.pedidos} pedido(s) — destino ${res.destino || "—"} (${Path_name(res.arquivo)})`, "ok");
      app.state.subTitulo = `${res.arquivo} — ${res.pedidos} pedido(s)`;
      document.getElementById("pageSub").textContent = app.state.subTitulo;
    } catch (e) {
      setInfo("Nenhum arquivo carregado", "");
      app.toast("Falha ao processar o PDF");
    } finally {
      btn.disabled = false; btn.classList.remove("loading");
    }
  }

  function Path_name(p) { return String(p || ""); }

  function limpar(app) {
    document.getElementById("romResult").textContent = "O romaneio calculado a partir do PDF aparecerá aqui.";
    document.getElementById("romUse").disabled = true;
    document.getElementById("romCopy").disabled = true;
    app.state.romaneioTexto = "";
    setInfo("Nenhum arquivo carregado", "");
  }

  window.Pages = window.Pages || {};
  window.Pages.romaneio = {
    title: "Romaneio",
    render(view, app) {
      const temTexto = !!app.state.romaneioTexto;
      view.innerHTML = `
        <div class="cot-cols">
          <section class="card cot-card">
            <div class="card-pad">
              <h2 class="card-title">1. Selecione o romaneio (PDF)</h2>
              <p class="card-hint">Importe o PDF do romaneio. Os pedidos são extraídos, validados e o romaneio calculado é gerado automaticamente.</p>
              <button class="btn btn-primary" id="romSelect" type="button">
                <span class="spin" aria-hidden="true"></span>Selecionar PDF
              </button>
              <div class="rom-info" id="romInfo">Nenhum arquivo carregado</div>
            </div>
          </section>

          <section class="card cot-card">
            <div class="card-pad">
              <div class="card-title-row">
                <h2 class="card-title">Romaneio calculado</h2>
                <div style="display:flex; gap:8px;">
                  <button class="btn btn-soft btn-sm" id="romCopy" type="button" ${temTexto ? "" : "disabled"}>Copiar</button>
                  <button class="btn btn-soft btn-sm" id="romClear" type="button">Limpar</button>
                </div>
              </div>
              <pre class="result-text" id="romResult">${temTexto ? window.Fmt.esc(app.state.romaneioTexto) : "O romaneio calculado a partir do PDF aparecerá aqui."}</pre>
              <button class="btn btn-primary" id="romUse" type="button" ${temTexto ? "" : "disabled"} style="margin-top:10px;">Usar na cotação →</button>
            </div>
          </section>
        </div>`;

      $("#romSelect", view).addEventListener("click", () => selecionar(app));
      $("#romClear", view).addEventListener("click", () => limpar(app));
      $("#romCopy", view).addEventListener("click", async () => {
        // O destino espera o <br> literal ao final de cada linha, mas as
        // quebras de linha devem ser mantidas (não amontoar tudo numa linha só).
        // Por isso anexamos <br> antes de cada \n, preservando a quebra real.
        const txt = ($("#romResult", view).textContent || "").replace(/\n/g, "<br>\n");
        try { await navigator.clipboard.writeText(txt); app.toast("Romaneio copiado"); }
        catch { app.toast("Não foi possível copiar"); }
      });
      $("#romUse", view).addEventListener("click", () => {
        if (!app.state.romaneioTexto) return;
        app.navigate("cotacao");
      });
    },
  };
})();
