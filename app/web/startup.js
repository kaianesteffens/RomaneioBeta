/* Fretio — fluxo de partida (licença → versão mínima/update → seletor de empresa).
   Dirige a tela startup.html chamando os métodos startup_* da ponte.
   Ao final, api.startup_entrar(empresa) + api.abrir_app() carregam o app (index.html). */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const card = () => $("#suCard");
let empresaSel = "";

function apiBridge() {
  if (window.pywebview && window.pywebview.api) return Promise.resolve(window.pywebview.api);
  return new Promise((resolve) => window.addEventListener("pywebviewready", () => resolve(window.pywebview.api), { once: true }));
}

let _tt = null;
function toast(msg) {
  const el = $("#toast"); el.textContent = msg; el.classList.add("show");
  clearTimeout(_tt); _tt = setTimeout(() => el.classList.remove("show"), 3000);
}

window.onBackendEvent = function (evt) {
  if (evt && evt.event === "startup_progress") {
    const p = $("#suProgress"); if (p) p.textContent = (evt.payload && evt.payload.texto) || "";
  }
};

/* ── Fases ─────────────────────────────────────────────────────────────── */
async function boot() {
  const params = new URLSearchParams(location.search);
  if (params.get("fase") === "empresa") { renderEmpresas(); return; }
  const a = await apiBridge();
  const lic = await a.startup_licenca_estado();
  if (lic.fase === "ok") { posLicenca(); return; }
  renderLicenca(lic.fase === "revogada" ? (lic.msg || "Sua licença foi revogada. Informe uma nova chave.") : "");
}

function renderLicenca(aviso) {
  card().innerHTML = `
    <h1 class="su-title">Ativação</h1>
    <p class="su-sub">Informe sua chave de licença para usar o Fretio.</p>
    ${aviso ? `<div class="su-warn">${window.Fmt.esc(aviso)}</div>` : ""}
    <label class="field"><span class="field-label">Chave de licença</span>
      <input class="field-input mono" id="suKey" placeholder="FBOT-XXXX-XXXX-XXXX-XXXX" autocomplete="off"/></label>
    <button class="btn btn-primary su-full" id="suAtivar" type="button">Ativar</button>`;
  const key = $("#suKey");
  key.addEventListener("keydown", (e) => { if (e.key === "Enter") $("#suAtivar").click(); });
  key.focus();
  $("#suAtivar").addEventListener("click", async () => {
    const btn = $("#suAtivar"); btn.disabled = true;
    try {
      const r = await (await apiBridge()).startup_ativar_licenca(key.value);
      if (r && r.ok) posLicenca();
      else renderLicenca(r ? r.msg : "Falha na ativação");
    } catch (e) {
      toast("Falha na ativação");
    } finally {
      btn.disabled = false;
    }
  });
}

async function posLicenca() {
  card().innerHTML = `<div class="su-loading">Verificando versão e atualizações…</div>`;
  const r = await (await apiBridge()).startup_pos_licenca();
  if (r.bloqueado) return renderBloqueio(r.msg);
  if (r.update) return renderUpdate(r.update);
  renderEmpresas();
}

function renderBloqueio(msg) {
  card().innerHTML = `
    <h1 class="su-title">Atualização obrigatória</h1>
    <p class="su-sub">${window.Fmt.esc(msg).replace(/\n/g, "<br>")}</p>
    <div class="su-progress" id="suProgress"></div>
    <div class="su-actions">
      <button class="btn btn-primary" id="suUpd" type="button">Atualizar agora</button>
      <button class="btn btn-soft" id="suSair" type="button">Fechar</button>
    </div>`;
  $("#suUpd").addEventListener("click", aplicarUpdate);
  $("#suSair").addEventListener("click", async () => (await apiBridge()).sair());
}

function renderUpdate(u) {
  card().innerHTML = `
    <h1 class="su-title">${u.mandatory ? "Atualização obrigatória" : "Atualização disponível"}</h1>
    <p class="su-sub">Nova versão: <strong>v${window.Fmt.esc(u.version)}</strong></p>
    ${u.notes ? `<pre class="su-notes">${window.Fmt.esc(u.notes)}</pre>` : ""}
    <div class="su-progress" id="suProgress"></div>
    <div class="su-actions">
      <button class="btn btn-primary" id="suUpd" type="button">Atualizar agora</button>
      <button class="btn btn-soft" id="suSkip" type="button">${u.mandatory ? "Fechar aplicativo" : "Continuar sem atualizar"}</button>
    </div>`;
  $("#suUpd").addEventListener("click", aplicarUpdate);
  $("#suSkip").addEventListener("click", async () => {
    if (u.mandatory) (await apiBridge()).sair();
    else renderEmpresas();
  });
}

async function aplicarUpdate() {
  $("#suUpd").disabled = true; $("#suSkip") && ($("#suSkip").disabled = true);
  $("#suProgress").textContent = "Iniciando atualização…";
  const r = await (await apiBridge()).startup_aplicar_update();
  if (r && r.ok) { $("#suProgress").textContent = "Atualização aplicada. Reiniciando…"; return; }
  $("#suProgress").textContent = "";
  toast((r && r.erro) || "Falha ao atualizar");
  $("#suUpd").disabled = false; $("#suSkip") && ($("#suSkip").disabled = false);
}

async function renderEmpresas() {
  const a = await apiBridge();
  const empresas = await a.startup_empresas();
  if (!empresaSel && empresas.length) empresaSel = empresas[0];
  card().innerHTML = `
    <h1 class="su-title">Selecione a empresa</h1>
    <p class="su-sub">Cada empresa tem sua própria configuração e transportadoras.</p>
    <div class="su-emp-list" id="suEmpList">
      ${empresas.map((e) => `
        <div class="su-emp${e === empresaSel ? " sel" : ""}" data-emp="${window.Fmt.esc(e)}">
          <span class="su-emp-avatar">${window.Fmt.esc(e.charAt(0).toUpperCase())}</span>
          <span class="su-emp-name">${window.Fmt.esc(e)}</span>
          <button class="su-emp-ren" data-ren="${window.Fmt.esc(e)}" type="button" title="Renomear">Renomear</button>
        </div>`).join("") || `<div class="su-empty">Nenhuma empresa ainda. Crie a primeira abaixo.</div>`}
    </div>
    <div class="su-nova">
      <input class="field-input" id="suNovaNome" placeholder="Nome da nova empresa" autocomplete="off"/>
      <button class="btn btn-soft" id="suNova" type="button">Criar</button>
    </div>
    <button class="btn btn-primary su-full" id="suEntrar" type="button" ${empresas.length ? "" : "disabled"}>Entrar</button>`;

  $("#suEmpList").querySelectorAll(".su-emp").forEach((row) => row.addEventListener("click", (e) => {
    if (e.target.closest("[data-ren]")) return;
    empresaSel = row.dataset.emp; renderEmpresas();
  }));
  $("#suEmpList").querySelectorAll("[data-ren]").forEach((b) => b.addEventListener("click", (e) => {
    e.stopPropagation(); renomear(b.getAttribute("data-ren"));
  }));
  $("#suNova").addEventListener("click", async () => {
    const nome = $("#suNovaNome").value.trim();
    const r = await (await apiBridge()).startup_criar_empresa(nome);
    if (r && r.ok) { empresaSel = nome; renderEmpresas(); } else toast((r && r.erro) || "Falha ao criar");
  });
  $("#suEntrar").addEventListener("click", async () => {
    if (!empresaSel) return;
    const btn = $("#suEntrar"); btn.disabled = true;
    try {
      const r = await (await apiBridge()).startup_entrar(empresaSel);
      if (r && r.ok) {
        // Navega só DEPOIS do await — o backend resolveu o callback de retorno do
        // pywebview, então trocar a página agora não deixa callback órfão. Navegar
        // no Python (load_url) correria com essa resolução e dispararia
        // JavascriptException ('_returnValuesCallbacks.abrir_app.<id> is not a function').
        const nav = await (await apiBridge()).abrir_app();
        window.location.assign((nav && nav.navegar) || "index.html");
      } else { toast((r && r.erro) || "Falha ao entrar"); btn.disabled = false; }
    } catch (e) {
      toast("Falha ao entrar");
      btn.disabled = false;
    }
  });
}

async function renomear(atual) {
  const novo = prompt(`Renomear empresa "${atual}" para:`, atual);
  if (!novo || novo.trim() === atual) return;
  const r = await (await apiBridge()).startup_renomear_empresa(atual, novo.trim());
  if (r && r.ok) { empresaSel = novo.trim(); renderEmpresas(); } else toast((r && r.erro) || "Falha ao renomear");
}

document.addEventListener("DOMContentLoaded", boot);
