"""Fretio — RomaneioMixin (romaneio por PDF e cotação de fornecedor FOB).

Extraído de web_app.py; opera sobre o estado do Api via self. Importa helpers
compartilhados de web_app_shared (nunca de web_app) para evitar ciclo.
"""
from __future__ import annotations

from pathlib import Path

import webview

from web_app_shared import (
    _load_config,
    montar_romaneio_fornecedor,
    validar_local_entrega,
)


class RomaneioMixin:
    """Romaneio por PDF e cotação de fornecedor (frete FOB). Delegate de domínio
    do Api (opera sobre self; superfície pública pywebview inalterada)."""

    # ── Romaneio (PDF) ──────────────────────────────────────────────────────
    def romaneio_processar(self) -> dict:
        if self._window is None:
            return {"erro": "Janela indisponível"}
        bloqueio = self._gate("romaneio")
        if bloqueio:
            return bloqueio
        paths = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=("PDF de romaneio (*.pdf)", "Todos os arquivos (*.*)"),
        )
        if not paths:
            return {"cancelado": True}
        arq = paths[0] if isinstance(paths, (list, tuple)) else paths

        from extrator_pedidos import ExtratorPedidos
        extrator = ExtratorPedidos()
        try:
            pedidos = extrator.extrair_arquivo(arq)
        except Exception as exc:
            return {"erro": f"Erro ao processar PDF: {exc}"}
        if not pedidos:
            return {"erro": "Nenhum pedido encontrado no arquivo. Verifique se o PDF tem o formato esperado."}

        ok, msg = validar_local_entrega(extrator, pedidos)
        if not ok:
            return {"erro": msg}

        try:
            if len(pedidos) == 1:
                html = extrator.formatar_pedido_html(pedidos[0])
            else:
                html = extrator.formatar_pedidos_agrupados_html(pedidos)
        except ValueError as exc:
            return {"erro": str(exc)}

        texto = html.replace("<br>", "\n")
        destino = "—"
        try:
            d0 = getattr(pedidos[0], "local_entrega", "") or ""
            norm = extrator.normalizar_local_entrega(d0) if hasattr(extrator, "normalizar_local_entrega") else d0
            destino = (norm or d0 or "—").split("\n")[0] or "—"
        except Exception:
            pass

        from datetime import date
        self._romaneios.append({
            "data": date.today().strftime("%d/%m"),
            "nome": Path(arq).name,
            "destino": destino,
            "volumes": len(pedidos),
        })
        self._romaneio_texto = texto
        return {
            "ok": True, "texto": texto, "arquivo": Path(arq).name,
            "pedidos": len(pedidos), "destino": destino,
        }

    def get_romaneio_texto(self) -> dict:
        return {"texto": self._romaneio_texto}

    # ── Fornecedores (frete FOB) ────────────────────────────────────────────
    def _montar_romaneio_fornecedor(self, form: dict) -> tuple[str, str]:
        """Carrega a config da empresa e delega ao formatter de domínio
        (web_presenters.montar_romaneio_fornecedor)."""
        return montar_romaneio_fornecedor(_load_config(self._config_path), form)

    def fornecedor_cotar(self, form: dict) -> dict:
        import re
        bloqueio = self._gate("cotacao")
        if bloqueio:
            return bloqueio
        try:
            texto, cep_forn = self._montar_romaneio_fornecedor(form or {})
        except ValueError as exc:
            return {"erro": str(exc)}
        cnpj_forn = re.sub(r"\D", "", str((form or {}).get("cnpj", "")))
        return self.cotacao_iniciar(texto, cnpj_remetente=cnpj_forn, cep_origem=cep_forn)
