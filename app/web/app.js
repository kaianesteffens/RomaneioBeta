/* Fretio web UI — núcleo: ponte pywebview, roteador, tema e dispatch de eventos.
   Páginas registram window.Pages[nome] = { title, render(view, app), onShow?, onEvent? }.
   Em teste headless (Playwright), window.__STUB_API__ substitui a ponte. */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);

function apiBridge() {
  if (window.__STUB_API__) return Promise.resolve(window.__STUB_API__);
  if (window.pywebview && window.pywebview.api) return Promise.resolve(window.pywebview.api);
  return new Promise((resolve) => {
    window.addEventListener("pywebviewready", () => resolve(window.pywebview.api), { once: true });
  });
}

const TITULOS = {
  dashboard: "Dashboard", romaneio: "Romaneio", cotacao: "Cotação",
  fornecedores: "Fornecedores", rastreio: "Rastreio", config: "Configurações",
};

window.App = {
  state: { empresa: "", versao: "", tema: "escuro", transportadoras: [], dashboard: {} },
  page: "dashboard",
  view: () => $("#view"),
  api: apiBridge,
  toast,
  fmt: () => window.Fmt,

  navigate(name) {
    if (!TITULOS[name]) return;
    this.page = name;
    document.querySelectorAll(".nav-item[data-page]").forEach((b) =>
      b.classList.toggle("is-active", b.dataset.page === name));
    $("#pageTitle").textContent = TITULOS[name];
    $("#pageSub").textContent = window.App.state.subTitulo || "Nenhum arquivo carregado";
    $("#backBtn").hidden = name === "dashboard";

    const view = this.view();
    view.innerHTML = "";
    const mod = window.Pages[name];
    if (!mod) {
      view.innerHTML = `<div class="placeholder"><div class="ph-title">Tela "${TITULOS[name]}" em migração</div>
        <div class="ph-text">Esta página ainda está sendo portada para a nova interface.</div></div>`;
      return;
    }
    try {
      mod.render(view, window.App);
      if (mod.onShow) mod.onShow(window.App);
    } catch (e) {
      view.innerHTML = `<div class="placeholder"><div class="ph-title">Erro ao abrir a tela</div>
        <div class="ph-text">${window.Fmt.esc(e.message || e)}</div></div>`;
    }
  },
};

/* ── Tema / aparência ──────────────────────────────────────────────────── */
function applyTema(temaEfetivo) {
  const dark = temaEfetivo !== "claro";
  document.documentElement.setAttribute("data-theme", dark ? "escuro" : "claro");
  const toggle = $("#themeToggle");
  toggle.setAttribute("aria-checked", String(dark));
  $("#themeLabel").textContent = dark ? "Modo escuro" : "Modo claro";
}
function applyAparencia(raio, botao) {
  if (raio) document.documentElement.setAttribute("data-raio", String(raio).toLowerCase());
  if (botao) document.documentElement.setAttribute("data-botao", String(botao).toLowerCase());
}
window.App.applyTema = applyTema;
window.App.applyAparencia = applyAparencia;
async function toggleTema() {
  const dark = $("#themeToggle").getAttribute("aria-checked") === "true";
  const novo = dark ? "claro" : "escuro";
  applyTema(novo);
  const res = await (await apiBridge()).set_tema(novo);
  if (res && res.tema_efetivo) applyTema(res.tema_efetivo);
}

/* ── Toast ─────────────────────────────────────────────────────────────── */
let _toastTimer = null;
function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove("show"), 2800);
}

/* ── Canal Python -> JS ────────────────────────────────────────────────── */
window.onBackendEvent = function (evt) {
  if (!evt || !evt.event) return;
  // Handlers globais
  if (evt.event === "status_update") {
    const txt = (evt.payload && evt.payload.texto) || "";
    window.App.state.subTitulo = txt;
    $("#pageSub").textContent = txt;
  } else if (evt.event === "toast") {
    toast((evt.payload && evt.payload.texto) || "");
  }
  // Encaminha para a página ativa
  const mod = window.Pages[window.App.page];
  if (mod && mod.onEvent) {
    try { mod.onEvent(evt, window.App); } catch (e) { /* isola erro de página */ }
  }
};

/* ── Boot ──────────────────────────────────────────────────────────────── */
function bindShell() {
  document.querySelectorAll(".nav-item[data-page]").forEach((btn) =>
    btn.addEventListener("click", () => window.App.navigate(btn.dataset.page)));
  $("#backBtn").addEventListener("click", () => window.App.navigate("dashboard"));
  $("#empresaChip").addEventListener("click", async () => {
    const a = await apiBridge();
    if (a.trocar_empresa) a.trocar_empresa();
  });
  $("#themeToggle").addEventListener("click", toggleTema);
  $("#cmdkBtn").addEventListener("click", () => toast("Command palette (Ctrl+K) — em breve"));
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault(); toast("Command palette (Ctrl+K) — em breve");
    }
  });
}

async function boot() {
  bindShell();
  const a = await apiBridge();
  const b = await a.get_bootstrap();
  Object.assign(window.App.state, b);
  window.App.state.subTitulo = "Nenhum arquivo carregado";

  $("#empresaName").textContent = b.empresa || "—";
  $("#empresaAvatar").textContent = (b.empresa || "?").trim().charAt(0).toUpperCase();
  $("#footerVersion").textContent = "Fretio " + (b.versao || "");
  applyTema(b.tema_efetivo || "escuro");
  applyAparencia(b.raio, b.botao);

  window.App.navigate("dashboard");
}

document.addEventListener("DOMContentLoaded", boot);
