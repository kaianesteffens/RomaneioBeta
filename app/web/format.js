/* Fretio — helpers de formatação compartilhados (espelham app/ui/formatting.py).
   Expostos em window.Fmt. Máscaras de input completas serão adicionadas quando
   as telas de Fornecedores/Configurações forem migradas. */
"use strict";

window.Fmt = {
  soDigitos: (s) => String(s == null ? "" : s).replace(/\D/g, ""),

  moeda: (v) =>
    v == null || v === "" || isNaN(v)
      ? "—"
      : new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(Number(v)),

  cnpj: (s) => {
    const d = window.Fmt.soDigitos(s).slice(0, 14);
    if (d.length !== 14) return s;
    return `${d.slice(0,2)}.${d.slice(2,5)}.${d.slice(5,8)}/${d.slice(8,12)}-${d.slice(12)}`;
  },

  cep: (s) => {
    const d = window.Fmt.soDigitos(s).slice(0, 8);
    if (d.length !== 8) return s;
    return `${d.slice(0,5)}-${d.slice(5)}`;
  },

  /* Escapa HTML para inserção segura via innerHTML. */
  esc: (s) =>
    String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;"),

  tempo: (ms) => {
    if (ms == null) return "";
    const n = Number(ms);
    return isNaN(n) ? "" : (n / 1000).toFixed(1) + "s";
  },
};
