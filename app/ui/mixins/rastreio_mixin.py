from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QPushButton,
    QPlainTextEdit,
    QMessageBox,
)

from extrator_nfe import (
    extrair_arquivo as extrair_nfe_arquivo,
    NotaFiscal,
    identificar_transportadora,
    parsear_info_complementar,
)
from rastreamento import rastrear_multiplas
from ui.events import NfeImportedEvent, RastreioResultEvent, RastreioFinishedEvent
from remote_permissions import ensure_feature_allowed
from usage_reporter import report_nfe_imported, report_tracking_finished, report_tracking_started
from fretio.providers.base import find_chrome

class RastreioMixin:
    def _selecionar_nfe(self):
        """Abre dialogo para selecionar arquivos XML de NF-e (um ou varios)."""
        if not ensure_feature_allowed("nfe", self):
            return
        from PySide6.QtWidgets import QFileDialog
        arquivos, _ = QFileDialog.getOpenFileNames(
            self,
            "Selecionar NF-e (XML)",
            "",
            "XML NF-e (*.xml);;Todos os arquivos (*.*)"
        )
        if not arquivos:
            return

        card_offset = len(self._rastreio_card_widgets)
        existing_keys = {
            n.chave_acesso
            for n in self._notas_rastreio
            if getattr(n, "chave_acesso", "")
        }
        self.btn_select_nfe.setEnabled(False)
        self.label_info.setText("Importando XML/DANFE...")
        self.label_info.setStyleSheet("color: #1f6feb;")

        def _worker():
            erros: list[str] = []
            novas_notas: list[NotaFiscal] = []
            seen_keys = set(existing_keys)
            for arq in arquivos:
                try:
                    notas = extrair_nfe_arquivo(arq)
                    if not notas:
                        erros.append(f"{Path(arq).name}: nenhuma NF-e encontrada")
                        continue
                    for nf in notas:
                        if nf.chave_acesso and nf.chave_acesso in seen_keys:
                            continue
                        if nf.chave_acesso:
                            seen_keys.add(nf.chave_acesso)
                        novas_notas.append(nf)
                except Exception as e:
                    erros.append(f"{Path(arq).name}: {e}")
            return NfeImportedEvent(list(arquivos), novas_notas, erros, card_offset)

        self._run_sync_worker(
            _worker,
            context="importacao_nfe",
            log_label="Erro ao importar XML/DANFE",
            on_success=self._post_event_safe,
            ui_error_handler=lambda exc: self._post_event_safe(
                NfeImportedEvent(list(arquivos), [], [str(exc)], card_offset)
            ),
        )

    def _on_nfe_imported(self, event: NfeImportedEvent) -> None:
        self.btn_select_nfe.setEnabled(True)
        erros = event.erros
        novas_notas = event.novas_notas
        if erros:
            QMessageBox.warning(
                self, "Aviso",
                "Alguns arquivos não puderam ser processados:\n\n" + "\n".join(erros)
            )

        if novas_notas or erros:
            report_nfe_imported(
                "ok" if novas_notas else "error",
                metadata={
                    "quantidade": len(novas_notas),
                    "arquivos_processados": len(event.arquivos),
                    "erros": len(erros),
                },
            )

        if novas_notas:
            self._notas_rastreio.extend(novas_notas)
            self._inserir_cards_novas_notas(novas_notas)
            self._rastreio_notas_subset = list(novas_notas)
            self._rastreio_card_offset = event.card_offset
            self.label_info.setText(f"{len(novas_notas)} XML(s) carregado(s) — iniciando rastreamento...")
            self.label_info.setStyleSheet("color: #1f6feb;")
            self._iniciar_rastreamento()

    def _limpar_rastreio(self):
        """Limpa as notas e resultados de rastreio."""
        self._notas_rastreio.clear()
        self._resultados_rastreio.clear()
        self._rastreio_card_widgets.clear()
        while self._rastreio_cards_layout.count() > 1:
            item = self._rastreio_cards_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._rastreio_placeholder = QLabel(
            "Selecione um ou mais arquivos XML de NF-e para visualizar as informações do pedido e rastrear entregas automaticamente."
        )
        self._rastreio_placeholder.setObjectName("SubtitleLabel")
        self._rastreio_placeholder.setAlignment(Qt.AlignCenter)
        self._rastreio_placeholder.setWordWrap(True)
        self._rastreio_cards_layout.insertWidget(0, self._rastreio_placeholder)
        self.btn_rastrear.setEnabled(False)
        self.btn_abrir_screenshots.setVisible(False)
        self.rastreio_progress_bar.stop_anim()
        self.rastreio_progress_bar.setVisible(False)
        self.label_info.setText("Rastreio limpo")
        self.label_info.setStyleSheet("color: #6b7a96;")

    def _criar_card_nfe(self, indice, nf):
        """Cria um card visual para uma NF-e com 2 blocos de informacao e rastreamento."""
        card = QFrame()
        card.setObjectName("RastreioCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(8)

        transp = identificar_transportadora(nf)
        transp_display = (nf.transportadora_nome or transp.upper() or "NAO IDENTIFICADA")
        data_emissao_display = ""
        if nf.data_emissao:
            # formata "2026-04-13T10:02:17-03:00" -> "13/04/2026"
            import re as _re
            m_data = _re.match(r'(\d{4})-(\d{2})-(\d{2})', nf.data_emissao)
            if m_data:
                data_emissao_display = f"  |  Emissao: {m_data.group(3)}/{m_data.group(2)}/{m_data.group(1)}"
        header = QLabel(f"[{indice}] NF-e {nf.numero} — {transp_display}{data_emissao_display}")
        header.setObjectName("RastreioCardHeader")
        card_layout.addWidget(header)

        info = parsear_info_complementar(nf.info_complementar)

        def _texto(valor):
            return str(valor or "").strip()

        def _formatar_data_nf(valor):
            import re as _re2

            match = _re2.match(r"(\d{4})-(\d{2})-(\d{2})", _texto(valor))
            if not match:
                return _texto(valor)
            return f"{match.group(3)}/{match.group(2)}/{match.group(1)}"

        def _formatar_cep(valor):
            digitos = "".join(ch for ch in str(valor or "") if ch.isdigit())
            if len(digitos) == 8:
                return f"{digitos[:5]}-{digitos[5:]}"
            return _texto(valor)

        def _linha_campos(campos):
            return "  |  ".join(f"{rotulo}: {_texto(valor)}" for rotulo, valor in campos)

        def _transportadora_bloco():
            if transp:
                return transp.upper()
            nome = _texto(nf.transportadora_nome)
            return (nome.split()[0] if nome else "") .upper()

        pd_display = _texto(info.get("pd"))
        if not pd_display and info.get("pedido_venda"):
            import re as _re3

            match_pd = _re3.search(r"\bPD\b\s*([A-Z0-9./-]+)", _texto(info.get("pedido_venda")), _re3.IGNORECASE)
            pd_display = match_pd.group(1) if match_pd else _texto(info.get("pedido_venda"))

        local_nome = _texto(info.get("local_entrega_nome"))

        endereco_entrega = _texto(info.get("endereco_entrega"))
        cep_entrega = _formatar_cep(info.get("cep_entrega") or nf.destinatario_cep)
        cidade_uf_entrega = _texto(info.get("cidade_uf_entrega"))
        if not cidade_uf_entrega and nf.destinatario_cidade and nf.destinatario_uf:
            cidade_uf_entrega = f"{nf.destinatario_cidade}/{nf.destinatario_uf}"

        destinatario_bloco = _texto(nf.destinatario_nome)
        if destinatario_bloco and nf.destinatario_uf and not destinatario_bloco.endswith(f"/{nf.destinatario_uf}"):
            destinatario_bloco = f"{destinatario_bloco}/{nf.destinatario_uf}"

        bloco_licitacao_linhas = [
            _linha_campos(
                [
                    ("Processo", info.get("processo")),
                    ("PE", info.get("pe")),
                    ("Ata", info.get("ata")),
                    ("Contrato", info.get("contrato")),
                    ("Empenho", info.get("empenho")),
                    ("OF", info.get("of")),
                ]
            ),
            _linha_campos(
                [
                    ("Entrega", info.get("entrega")),
                    ("Pagamento", info.get("pagamento")),
                ]
            ),
            destinatario_bloco,
            _linha_campos(
                [
                    ("CRM", info.get("crm")),
                    ("PD", pd_display),
                ]
            ),
            "",
            _linha_campos(
                [
                    ("NOTA FISCAL", nf.numero),
                    ("DATA NF", _formatar_data_nf(nf.data_emissao)),
                ]
            ),
            f"PRODUTOS: {_texto(nf.produtos_resumo)}",
            _linha_campos(
                [
                    ("TRANSPORTADORA", _transportadora_bloco()),
                    ("RASTREIO", "(NAO PREENCHA)"),
                ]
            ),
        ]
        if info.get("outras_info_licitacao"):
            bloco_licitacao_linhas.extend(
                [
                    "",
                    "Outras informações da licitação:",
                    _texto(info.get("outras_info_licitacao")),
                ]
            )

        bloco_licitacao_txt = "\n".join(bloco_licitacao_linhas).rstrip()

        bloco_entrega_linhas = [
            f"LOCAL DE ENTREGA: {local_nome}",
            f"ENDEREÇO: {endereco_entrega}",
            f"CEP: {cep_entrega}",
            cidade_uf_entrega,
            "",
            f"AGENDAMENTO: {_texto(info.get('agendamento'))}",
            _linha_campos(
                [
                    ("HORARIO", info.get("horario")),
                    ("CONTATO", info.get("contato") or info.get("recebedor")),
                    ("TELEFONE", info.get("telefone")),
                ]
            ),
        ]
        if info.get("outras_info_entrega"):
            bloco_entrega_linhas.extend(
                [
                    "",
                    "Outras informações da entrega:",
                    _texto(info.get("outras_info_entrega")),
                ]
            )
        bloco_entrega_txt = "\n".join(bloco_entrega_linhas).rstrip()

        blocos_row = QHBoxLayout()
        blocos_row.setSpacing(10)

        bloco_licitacao = QFrame()
        bloco_licitacao.setObjectName("RastreioBlueBlock")
        licitacao_layout = QVBoxLayout(bloco_licitacao)
        licitacao_layout.setContentsMargins(10, 8, 10, 8)
        licitacao_layout.setSpacing(3)

        lbl_licitacao_title = QLabel("\U0001f4cb DADOS DA LICITAÇÃO")
        lbl_licitacao_title.setObjectName("RastreioBlockTitle")
        licitacao_layout.addWidget(lbl_licitacao_title)

        te_licitacao = QPlainTextEdit(bloco_licitacao_txt)
        te_licitacao.setReadOnly(True)
        te_licitacao.setFrameShape(QFrame.NoFrame)
        te_licitacao.setObjectName("RastreioBlockText")
        te_licitacao.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        licitacao_layout.addWidget(te_licitacao, 1)
        blocos_row.addWidget(bloco_licitacao, 1)

        bloco_entrega = QFrame()
        bloco_entrega.setObjectName("RastreioGreenBlock")
        entrega_layout = QVBoxLayout(bloco_entrega)
        entrega_layout.setContentsMargins(10, 8, 10, 8)
        entrega_layout.setSpacing(3)

        lbl_entrega_title = QLabel("\U0001f4cd DADOS DA ENTREGA")
        lbl_entrega_title.setObjectName("RastreioBlockTitle")
        entrega_layout.addWidget(lbl_entrega_title)

        te_entrega = QPlainTextEdit(bloco_entrega_txt)
        te_entrega.setReadOnly(True)
        te_entrega.setFrameShape(QFrame.NoFrame)
        te_entrega.setObjectName("RastreioBlockText")
        te_entrega.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        entrega_layout.addWidget(te_entrega, 1)
        blocos_row.addWidget(bloco_entrega, 1)

        card_layout.addLayout(blocos_row)

        bloco_rastreio = QFrame()
        bloco_rastreio.setObjectName("RastreioSlateBlock")
        rastreio_layout = QVBoxLayout(bloco_rastreio)
        rastreio_layout.setContentsMargins(10, 8, 10, 8)
        rastreio_layout.setSpacing(4)

        lbl_rastreio_title = QLabel("\U0001f69a RASTREAMENTO")
        lbl_rastreio_title.setObjectName("RastreioBlockTitle")
        rastreio_layout.addWidget(lbl_rastreio_title)

        lbl_status = QLabel("⏳ Aguardando rastreamento...")
        lbl_status.setObjectName("RastreioStatusPendente")
        rastreio_layout.addWidget(lbl_status)

        rastreio_detail_container = QVBoxLayout()
        rastreio_detail_container.setSpacing(4)
        rastreio_layout.addLayout(rastreio_detail_container)

        rastreio_layout.addStretch(1)
        bloco_rastreio.setVisible(False)
        card_layout.addWidget(bloco_rastreio)

        card._rastreio_status_label = lbl_status
        card._rastreio_detail_container = rastreio_detail_container
        card._bloco_rastreio = bloco_rastreio

        return card

    def _make_info_row(self, label, value):
        """Cria uma linha label: valor para dentro dos blocos."""
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 1, 0, 1)
        row_layout.setSpacing(6)
        lbl = QLabel(label)
        lbl.setObjectName("RastreioBlockLabel")
        lbl.setFixedWidth(110)
        lbl.setAlignment(Qt.AlignRight | Qt.AlignTop)
        val = QLabel(value)
        val.setObjectName("RastreioBlockValue")
        val.setWordWrap(True)
        val.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        val.setCursor(Qt.IBeamCursor)
        row_layout.addWidget(lbl)
        row_layout.addWidget(val, 1)
        return row

    def _atualizar_lista_notas_rastreio(self):
        """Recria os cards de NF-e na área de scroll."""
        self._rastreio_card_widgets.clear()
        while self._rastreio_cards_layout.count() > 1:
            item = self._rastreio_cards_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        if not self._notas_rastreio:
            self._rastreio_placeholder = QLabel(
                "Selecione arquivos XML (NF-e) ou PDF (DANFE) para visualizar as informações do pedido e rastrear entregas."
            )
            self._rastreio_placeholder.setObjectName("SubtitleLabel")
            self._rastreio_placeholder.setAlignment(Qt.AlignCenter)
            self._rastreio_placeholder.setWordWrap(True)
            self._rastreio_cards_layout.insertWidget(0, self._rastreio_placeholder)
            self.btn_rastrear.setEnabled(False)
            return
        if hasattr(self, "_rastreio_placeholder") and self._rastreio_placeholder:
            self._rastreio_placeholder.deleteLater()
            self._rastreio_placeholder = None
        for i, nf in enumerate(self._notas_rastreio, 1):
            card = self._criar_card_nfe(i, nf)
            self._rastreio_cards_layout.insertWidget(0, card)
            self._rastreio_card_widgets.append(card)
        self.btn_rastrear.setEnabled(True)

    def _inserir_cards_novas_notas(self, novas_notas):
        """Insere cards apenas para notas novas sem recriar os existentes."""
        if not novas_notas:
            return
        if hasattr(self, "_rastreio_placeholder") and self._rastreio_placeholder:
            self._rastreio_placeholder.deleteLater()
            self._rastreio_placeholder = None
        existing_count = len(self._notas_rastreio) - len(novas_notas)
        for j, nf in enumerate(novas_notas):
            indice = existing_count + j + 1
            card = self._criar_card_nfe(indice, nf)
            self._rastreio_cards_layout.insertWidget(0, card)
            self._rastreio_card_widgets.append(card)
        self.btn_rastrear.setEnabled(True)

    def _iniciar_rastreamento(self):
        """Inicia o rastreamento das NF-es carregadas."""
        if self._is_shutting_down():
            return
        if not ensure_feature_allowed("rastreio", self):
            return
        try:
            find_chrome()
        except FileNotFoundError as exc:
            try:
                self._sessao._marcar_chrome_ausente(exc, source="rastreamento_usuario")
            except Exception:
                pass
            self._mostrar_chrome_ausente()
            return
        notas_a_rastrear = self._rastreio_notas_subset if self._rastreio_notas_subset else self._notas_rastreio
        if not notas_a_rastrear:
            QMessageBox.warning(self, "Aviso", "Nenhuma NF-e carregada para rastrear")
            return
        self._tracking_started_at = time.monotonic()
        report_tracking_started(metadata={"quantidade_notas": len(notas_a_rastrear)})
        self._rastreio_notas_para_thread = list(notas_a_rastrear)
        self.btn_rastrear.setEnabled(False)
        self.btn_select_nfe.setEnabled(False)
        self.rastreio_progress_bar.setVisible(True)
        self.rastreio_progress_bar.start_anim()
        self.btn_abrir_screenshots.setVisible(False)
        self.label_info.setText("Rastreando entregas...")
        self.label_info.setStyleSheet("color: #1f6feb;")
        self._run_rastreamento_async()

    def _run_rastreamento_async(self):
        """Executa o rastreamento em thread separada."""
        if self._is_shutting_down():
            return
        self._run_async_worker(
            self._rastrear_notas_async,
            context="rastreamento",
            log_label="Erro no rastreamento",
            on_success=lambda resultados: self._post_event_safe(RastreioFinishedEvent(resultados)),
            ui_error_handler=lambda _exc: self._post_event_safe(RastreioFinishedEvent([])),
        )

    async def _rastrear_notas_async(self):
        """Rastreia as NF-es do subset atual (ou todas se sem subset)."""
        notas_para_rastrear = []
        for nf in (self._rastreio_notas_para_thread or self._notas_rastreio):
            transp = identificar_transportadora(nf)
            notas_para_rastrear.append({
                "transportadora": transp,
                "numero_nfe": nf.numero,
                "cnpj_emitente": nf.emitente_cnpj,
                "chave_acesso": nf.chave_acesso,
            })
        resultados = await rastrear_multiplas(notas_para_rastrear, callback=self._post_rastreio_progress)
        return resultados

    def _on_rastreio_result(self, indice, total, resultado):
        """Atualiza o card da NF-e com o resultado do rastreamento."""
        self.label_info.setText(f"Rastreando... {indice}/{total}")
        self.label_info.setStyleSheet("color: #1f6feb;")
        idx = self._rastreio_card_offset + indice - 1
        if idx < 0 or idx >= len(self._rastreio_card_widgets):
            return
        card = self._rastreio_card_widgets[idx]
        card._bloco_rastreio.setVisible(True)
        status_label = card._rastreio_status_label
        detail_container = card._rastreio_detail_container
        if resultado.erro:
            status_label.setText(f"❌ Erro: {resultado.erro}")
            status_label.setObjectName("RastreioStatusErro")
        elif resultado.entregue:
            status_label.setText("✅ ENTREGUE")
            status_label.setObjectName("RastreioStatusEntregue")
            if resultado.status_texto and resultado.status_texto not in ("ENTREGUE",):
                detail_container.addWidget(self._make_info_row("Status:", resultado.status_texto))
            if resultado.previsao_entrega:
                detail_container.addWidget(self._make_info_row("Data entrega:", resultado.previsao_entrega))
            if resultado.screenshot_path:
                p_screenshot = Path(resultado.screenshot_path)
                lbl_ss = QLabel(
                    f'<a href="file:///{resultado.screenshot_path.replace(chr(92), "/")}">'
                    f'{p_screenshot.name}</a>'
                )
                lbl_ss.setOpenExternalLinks(True)
                lbl_ss.setStyleSheet("font-size: 12px;")
                detail_container.addWidget(self._make_info_row("Screenshot:", ""))
                detail_container.addWidget(lbl_ss)
            if resultado.link_rastreio:
                lbl_link = QLabel(f'<a href="{resultado.link_rastreio}">Abrir rastreio</a>')
                lbl_link.setOpenExternalLinks(True)
                lbl_link.setStyleSheet("font-size: 12px;")
                detail_container.addWidget(lbl_link)
        else:
            status_label.setText(f"\U0001f4e6 {resultado.status_texto or 'Em transito'}")
            status_label.setObjectName("RastreioStatusTransito")
            if resultado.previsao_entrega:
                detail_container.addWidget(self._make_info_row("Previsao:", resultado.previsao_entrega))
            if resultado.link_rastreio:
                lbl_link = QLabel(f'<a href="{resultado.link_rastreio}">Abrir rastreio</a>')
                lbl_link.setOpenExternalLinks(True)
                lbl_link.setStyleSheet("font-size: 12px;")
                detail_container.addWidget(lbl_link)
        status_label.style().unpolish(status_label)
        status_label.style().polish(status_label)

    def _on_rastreio_finished(self, resultados):
        """Chamado quando todo o rastreamento terminou."""
        self._resultados_rastreio = resultados
        self.rastreio_progress_bar.stop_anim()
        self.rastreio_progress_bar.setVisible(False)
        self.btn_rastrear.setEnabled(True)
        self.btn_select_nfe.setEnabled(True)
        entregues = sum(1 for r in resultados if r.entregue)
        com_screenshot = sum(1 for r in resultados if r.screenshot_path)
        total = len(resultados)
        started_at = self._tracking_started_at
        duration_ms = int((time.monotonic() - started_at) * 1000) if started_at else None
        status = "ok" if resultados and any(not getattr(r, "erro", "") for r in resultados) else "error"
        report_tracking_finished(
            status,
            duration_ms=duration_ms,
            metadata={"quantidade_notas": total},
        )
        self.label_info.setText(
            f"Rastreamento concluído: {entregues}/{total} entregue(s)"
            + (f" — {com_screenshot} screenshot(s)" if com_screenshot else "")
        )
        self.label_info.setStyleSheet("color: #067647;")
        if com_screenshot:
            self.btn_abrir_screenshots.setVisible(True)
        self._rastreio_notas_subset = None
        self._rastreio_card_offset = 0
        self._rastreio_notas_para_thread = None
        self._tracking_started_at = None
