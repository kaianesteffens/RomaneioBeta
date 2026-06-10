from __future__ import annotations

import re
from datetime import date as _date
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QGridLayout,
)

from cotacao_transportadoras import ResultadoCotacao


class DashboardMixin:
    def _registrar_romaneio(self, arquivo: str) -> None:
        destino = (self.pedidos[0].local_entrega or "—") if self.pedidos else "—"
        self._romaneios_processados.append({
            "data": _date.today().strftime("%d/%m"),
            "nome": Path(arquivo).name,
            "destino": destino,
            "volumes": len(self.pedidos),
        })

    def _criar_estado_vazio_dashboard(self, titulo: str, texto: str) -> QFrame:
        box = QFrame()
        box.setObjectName("DashboardEmpty")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(12, 10, 12, 10)
        box_layout.setSpacing(3)

        title_label = QLabel(titulo)
        title_label.setObjectName("DashboardEmptyTitle")
        title_label.setWordWrap(True)
        text_label = QLabel(texto)
        text_label.setObjectName("DashboardEmptyText")
        text_label.setWordWrap(True)

        box_layout.addWidget(title_label)
        box_layout.addWidget(text_label)
        return box

    @staticmethod
    def _formatar_moeda_dashboard(valor: float) -> str:
        moeda = f"R$ {valor:,.2f}"
        return moeda.replace(",", "_").replace(".", ",").replace("_", ".")

    def _atualizar_dashboard(self) -> None:
        if not hasattr(self, '_kpi_value_labels'):
            return

        total_rom = len(self._romaneios_processados)
        self._kpi_value_labels[0].setText(str(total_rom))
        self._kpi_sub_labels[0].setText(
            "processado nesta sessão" if total_rom == 1 else "processados nesta sessão"
        )

        total_vol = sum(int(r.get("volumes", 0) or 0) for r in self._romaneios_processados)
        self._kpi_value_labels[1].setText(str(total_vol))
        self._kpi_sub_labels[1].setText(
            "volume processado" if total_vol == 1 else "volumes processados"
        )

        ok_results = [
            r for r in self._last_cotacao_results
            if getattr(r, "status", "") == "ok" and getattr(r, "valor_frete", None) is not None
        ]
        if ok_results:
            melhor = min(float(r.valor_frete) for r in ok_results)
            self._kpi_value_labels[2].setText(self._formatar_moeda_dashboard(melhor))
            self._kpi_sub_labels[2].setText("menor valor da última cotação")
        else:
            self._kpi_value_labels[2].setText("—")
            self._kpi_sub_labels[2].setText("inicie uma cotação")

        total_cot = len(self._last_cotacao_results)
        ok_cot = len(ok_results)
        if total_cot > 0:
            pct = round(ok_cot / total_cot * 100)
            self._kpi_value_labels[3].setText(f"{pct}%")
            self._kpi_sub_labels[3].setText(f"{ok_cot} de {total_cot} transportadoras")
        else:
            self._kpi_value_labels[3].setText("—")
            self._kpi_sub_labels[3].setText("aguardando retorno")

        layout = self._recentes_body_layout
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not self._romaneios_processados:
            layout.addWidget(
                self._criar_estado_vazio_dashboard(
                    "Nenhum romaneio processado nesta sessão",
                    "Selecione um PDF de romaneio para preencher esta lista e atualizar os indicadores acima.",
                )
            )
            return

        rows = list(reversed(self._romaneios_processados))[:6]
        for i, r in enumerate(rows):
            row_w = QWidget()
            rw_layout = QHBoxLayout(row_w)
            rw_layout.setContentsMargins(14, 9, 14, 9)
            rw_layout.setSpacing(10)
            ld = QLabel(str(r.get("data") or "—"))
            ld.setObjectName("TableMono")
            ld.setFixedWidth(42)
            ln = QLabel(str(r.get("nome") or "—"))
            ln.setObjectName("TableMono2")
            ln.setToolTip(str(r.get("nome") or ""))
            lde = QLabel(str(r.get("destino") or "—"))
            lde.setObjectName("TableText")
            lde.setToolTip(str(r.get("destino") or ""))
            volumes = int(r.get("volumes", 0) or 0)
            lv = QLabel(f"{volumes} vol.")
            lv.setObjectName("TableMono")
            lv.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            rw_layout.addWidget(ld)
            rw_layout.addWidget(ln, 2)
            rw_layout.addWidget(lde, 2)
            rw_layout.addWidget(lv, 1)
            layout.addWidget(row_w)
            if i < len(rows) - 1:
                sep_r = QFrame()
                sep_r.setFrameShape(QFrame.HLine)
                sep_r.setObjectName("SoftSep")
                layout.addWidget(sep_r)

        ocultos = len(self._romaneios_processados) - len(rows)
        if ocultos > 0:
            resumo = QLabel(f"+ {ocultos} romaneio(s) anterior(es) nesta sessão")
            resumo.setObjectName("SectionHint")
            resumo.setContentsMargins(14, 8, 14, 10)
            layout.addWidget(resumo)

    def _formatar_linha_progresso(self, resultado: ResultadoCotacao) -> str:
        nome = (resultado.transportadora or "GERAL").strip().upper()
        if resultado.status == "ok" and resultado.valor_frete is not None:
            prazo = int(resultado.prazo_dias or 0)
            return f"- {nome} pronta: R$ {resultado.valor_frete:.2f} | {prazo} dia(s)"

        detalhe = (resultado.detalhes or resultado.status or "Sem detalhe")
        detalhe = re.sub(r"\s+", " ", str(detalhe)).strip()
        detalhe_lower = detalhe.lower()
        if resultado.status == "desabilitada":
            detalhe = "Transportadora desabilitada pela licença"
        elif resultado.status == "nao_atendido":
            detalhe = "UF não atendida"
        elif "configura" in detalhe_lower:
            detalhe = "Configuração incompleta"
        elif "timeout" in detalhe_lower:
            detalhe = "Tempo limite aguardando resultado"
        elif "sem resultado" in detalhe_lower or "sem cot" in detalhe_lower:
            detalhe = "Sem cotação retornada"
        if len(detalhe) > 140:
            detalhe = detalhe[:137] + "..."
        if resultado.status == "desabilitada":
            return f"- {nome} ignorada: {detalhe}"
        if resultado.status == "nao_atendido":
            return f"- {nome} não atendida: {detalhe}"
        return f"- {nome} falhou: {detalhe}"
