/* Renderização compartilhada da tabela de status de cotação (Cotação e Fornecedores).
   window.CotStatus.reset(tbodyId) / upsert(tbodyId, payload).
   Porta _rotulos_status_cotacao / _atualizar_tabela_status_cotacao. */
"use strict";

(function () {
  const STATUS_LABEL = {
    aguardando: "Aguardando", login: "Acessando portal", cotando: "Cotando",
    finalizada: "Concluída", ok: "Sucesso", sem_cotacao: "Sem cotação",
    nao_atendido: "Não atende", desabilitada: "Indisponível",
    configuracao: "Configuração", erro: "Erro",
  };
  const STATUS_CLASS = {
    ok: "ok", finalizada: "ok", login: "info", cotando: "info", aguardando: "muted",
    sem_cotacao: "warn", nao_atendido: "warn", configuracao: "warn",
    desabilitada: "muted", erro: "err",
  };
  const STAGE_LABEL = {
    aguardando: "Aguardando", login: "Acesso", cotacao: "Cotação", resultado: "Resultado",
    finalizado: "Finalizada", validacao: "Validação", configuracao: "Configuração", licenca: "Licença",
  };

  function statusKey(status, mensagem, resultado) {
    const raw = String(status || "").trim().toLowerCase();
    const rs = String((resultado && resultado.status) || "").trim().toLowerCase();
    const ml = String(mensagem || "").toLowerCase();
    if (rs === "ok") return "ok";
    if (rs === "sem_cotacao" || ml.includes("sem cot")) return "sem_cotacao";
    if (rs === "nao_atendido" || raw === "nao_atendido") return "nao_atendido";
    if (rs === "desabilitada" || raw === "desabilitada") return "desabilitada";
    if (rs.includes("configura") || ml.includes("configura")) return "configuracao";
    if (["login", "cotando", "aguardando"].includes(raw)) return raw;
    if (raw === "finalizada") return "finalizada";
    return (raw === "erro" || rs === "erro") ? "erro" : (raw || "aguardando");
  }

  function limparMensagem(key, mensagem, statusLabel) {
    let msg = String(mensagem || "").replace(/\s+/g, " ").trim();
    if (key === "configuracao") msg = "Configuração incompleta";
    else if (key === "sem_cotacao") msg = "Sem cotação retornada";
    else if (key === "nao_atendido") msg = "UF não atendida";
    else if (key === "desabilitada") msg = "Transportadora indisponível pela licença/configuração";
    else if (msg.toLowerCase().includes("timeout")) msg = "Tempo limite aguardando resultado";
    if (!msg) msg = statusLabel;
    if (msg.length > 160) msg = msg.slice(0, 157) + "...";
    return msg;
  }

  function providerDe(payload) {
    const p = payload || {};
    let provider = String(p.provider || "").trim().toUpperCase();
    if (!provider && p.resultado) provider = String(p.resultado.transportadora || "").trim().toUpperCase();
    return provider;
  }

  // tbodyId -> { provider -> <tr> }   (refs do DOM atual)
  const tables = {};
  // tbodyId -> { order: [providers], rows: { provider -> payload } }
  // Guarda os payloads crus para reexibir o status ao voltar à tela: o re-render
  // recria o <tbody> vazio e o dono (Cotação/Fornecedores) chama replay().
  const store = {};

  function renderRow(tbodyId, payload) {
    const F = window.Fmt;
    const tb = document.getElementById(tbodyId);
    if (!tb) return;
    const rows = tables[tbodyId] || (tables[tbodyId] = {});
    const p = payload || {};
    const resultado = p.resultado || null;
    const provider = providerDe(p);
    if (!provider) return;

    let status = String(p.status || "").trim();
    let mensagem = String(p.mensagem || "").trim();
    let duration = p.duration_ms;
    if (resultado) {
      if (!status) status = resultado.status || "";
      if (!mensagem) mensagem = resultado.detalhes || "";
      if (duration == null) duration = resultado.duration_ms;
    }
    const key = statusKey(status, mensagem, resultado);
    const label = STATUS_LABEL[key] || STATUS_LABEL.aguardando;
    const cls = STATUS_CLASS[key] || "muted";
    const stage = STAGE_LABEL[p.stage] || p.stage || "Aguardando";
    const msg = limparMensagem(key, mensagem, label);
    const tempo = F.tempo(duration);

    let tr = rows[provider];
    if (!tr) { tr = document.createElement("tr"); rows[provider] = tr; tb.appendChild(tr); }
    tr.innerHTML = `
      <td class="cot-prov">${F.esc(provider)}</td>
      <td><span class="cot-pill ${cls}">${F.esc(label)}</span></td>
      <td class="cot-stage">${F.esc(stage)}</td>
      <td class="cot-msg" title="${F.esc(msg)}">${F.esc(msg)}</td>
      <td class="cot-time">${F.esc(tempo)}</td>`;
  }

  window.CotStatus = {
    reset(tbodyId) {
      tables[tbodyId] = {};
      store[tbodyId] = { order: [], rows: {} };
      const tb = document.getElementById(tbodyId);
      if (tb) tb.innerHTML = "";
    },
    upsert(tbodyId, payload) {
      const provider = providerDe(payload);
      if (!provider) return;
      const st = store[tbodyId] || (store[tbodyId] = { order: [], rows: {} });
      if (!(provider in st.rows)) st.order.push(provider);
      st.rows[provider] = payload;
      renderRow(tbodyId, payload);
    },
    // Reconstrói a tabela a partir dos payloads guardados (após re-render da página).
    replay(tbodyId) {
      const st = store[tbodyId];
      tables[tbodyId] = {};               // refs antigas apontam p/ DOM destruído
      const tb = document.getElementById(tbodyId);
      if (tb) tb.innerHTML = "";
      if (!st) return false;
      for (const prov of st.order) renderRow(tbodyId, st.rows[prov]);
      return st.order.length > 0;
    },
    has(tbodyId) {
      const st = store[tbodyId];
      return !!(st && st.order.length);
    },
  };
})();
