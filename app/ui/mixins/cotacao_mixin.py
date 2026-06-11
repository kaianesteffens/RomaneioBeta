from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QPushButton,
    QPlainTextEdit,
    QFileDialog,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QLineEdit,
    QGridLayout,
    QApplication,
)

from cotacao_transportadoras import (
    ResultadoCotacao,
)
from extrator_pedidos import ExtratorPedidos
from ui.events import PdfProcessedEvent
from ui.widgets import IndeterminateBar
from ui.formatting import (
    _apply_cep_mask,
    _apply_cnpj_mask,
    _apply_currency_mask,
    _apply_decimal_mask,
)
from remote_permissions import ensure_feature_allowed
from usage_reporter import report_romaneio_processed


class CotacaoMixin:
    def _selecionar_arquivo(self):
        if not ensure_feature_allowed("romaneio", self):
            return
        arquivo, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar PDF",
            "",
            "PDF files (*.pdf);;All files (*.*)"
        )
        if arquivo:
            self._processar_pdf(arquivo)

    def _limpar(self):
        self.result_text.clear()
        self.label_info.setText("Nenhum arquivo carregado")
        self.label_info.setStyleSheet("color: #6b7a96;")
        self.html_original = ''
        self.pedidos = []
        self._romaneio_colado = ""
        self.romaneio_calculado_text.clear()
        self.romaneio_colado_text.clear()
        self.progress_bar.stop_anim()
        self.progress_bar.setVisible(False)
        self._cotacao_total = 0
        self._cotacao_concluidas = 0
        self.btn_quote_colado.setEnabled(False)
        self.btn_select.setEnabled(True)

    def _copiar_resultado(self):
        if self.stack.currentIndex() == 1:
            texto_ui = (self.romaneio_calculado_text.toPlainText() or "").strip()
        else:
            texto_ui = (self.result_text.toPlainText() or "").strip()
        if not texto_ui:
            QMessageBox.warning(self, "Aviso", "Nenhum conteúdo para copiar")
            return
        texto_com_br = texto_ui.replace("\r\n", "\n").replace("\n", "<br>\n")
        if not texto_com_br.endswith("<br><br>"):
            texto_com_br = f"{texto_com_br.rstrip()}<br><br>"
        QApplication.clipboard().setText(texto_com_br)
        QMessageBox.information(self, "Sucesso", "Resultado copiado para a area de transferencia")

    def _processar_pdf(self, arquivo: str):
        if not ensure_feature_allowed("romaneio", self):
            return
        self.btn_select.setEnabled(False)
        self.label_info.setText(f"Processando PDF: {Path(arquivo).name}...")
        self.label_info.setStyleSheet(f"color: {getattr(self, '_c_info', '#d97757')};")

        def _worker():
            extrator = ExtratorPedidos()
            pedidos = extrator.extrair_arquivo(arquivo)
            if not pedidos:
                return PdfProcessedEvent(arquivo, [], "", "nenhum_pedido")
            try:
                if len(pedidos) == 1:
                    html_result = extrator.formatar_pedido_html(pedidos[0])
                else:
                    html_result = extrator.formatar_pedidos_agrupados_html(pedidos)
            except ValueError as exc:
                return PdfProcessedEvent(arquivo, pedidos, "", str(exc))
            return PdfProcessedEvent(arquivo, pedidos, html_result)

        self._run_sync_worker(
            _worker,
            context="importacao_pdf",
            log_label="Erro ao processar PDF",
            on_success=self._post_event_safe,
            ui_error_handler=lambda exc: self._post_event_safe(
                PdfProcessedEvent(arquivo, [], "", str(exc))
            ),
        )

    def _on_pdf_processed(self, event: PdfProcessedEvent) -> None:
        self.btn_select.setEnabled(True)
        if event.error == "nenhum_pedido":
            QMessageBox.warning(
                self,
                "Aviso",
                "Nenhum pedido encontrado no arquivo selecionado.\n\nVerifique se o PDF tem o formato esperado.",
            )
            report_romaneio_processed("error", metadata={"erro": "nenhum_pedido"})
            return
        if event.error:
            QMessageBox.warning(self, "Erro de dados", event.error)
            self.label_info.setText("Erro: verifique informações de volume")
            self.label_info.setStyleSheet("color: #b42318;")
            report_romaneio_processed("error", metadata={"erro": "processamento_pdf"})
            return

        self.pedidos = event.pedidos
        if not self._validar_local_entrega(self.pedidos):
            self.label_info.setText("Locais de entrega diferentes - processamento interrompido")
            self.label_info.setStyleSheet("color: #b42318;")
            report_romaneio_processed("error", metadata={"erro": "local_entrega_divergente"})
            return

        self.html_original = event.html_result
        self.romaneio_calculado_text.setPlainText(event.html_result.replace('<br>', '\n'))
        self._show_page(1)
        self.label_info.setText(f"OK: {len(self.pedidos)} pedido(s) extraido(s) de {Path(event.arquivo).name}")
        self.label_info.setStyleSheet("color: #067647;")
        self._registrar_romaneio(event.arquivo)
        report_romaneio_processed("ok", metadata={"quantidade_pedidos": len(self.pedidos)})
        self._atualizar_dashboard()

    def _atualizar_estado_romaneio_colado(self):
        texto = (self.romaneio_colado_text.toPlainText() or "").strip()
        pronto = bool(texto)
        self.btn_quote_colado.setEnabled(pronto)
        if hasattr(self, "cotacao_input_hint"):
            if pronto:
                linhas = len([linha for linha in texto.splitlines() if linha.strip()])
                self.cotacao_input_hint.setText(
                    f"Romaneio preenchido com {linhas} linha(s). Confira os dados e clique em Iniciar cotação."
                )
            else:
                self.cotacao_input_hint.setText("Aguardando romaneio para liberar a cotação.")
        if hasattr(self, "cotacao_run_status") and (not hasattr(self, "progress_bar") or not self.progress_bar.isVisible()):
            self.cotacao_run_status.setText(
                "Pronto para iniciar." if pronto else "Pronto para cotar assim que houver romaneio."
            )

    def _criar_tabela_status_cotacao(self) -> QTableWidget:
        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(["Transp.", "Situação", "Etapa", "Mensagem", "Tempo"])
        table.setObjectName("CotacaoStatusTable")
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setFocusPolicy(Qt.NoFocus)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setMinimumHeight(150)
        table.setMaximumHeight(230)
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setMinimumSectionSize(48)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setTextElideMode(Qt.ElideRight)
        return table

    def _resetar_tabela_status_cotacao(self, *, fornecedor: bool = False) -> None:
        table = self.forn_cotacao_status_table if fornecedor else self.cotacao_status_table
        rows = self._forn_cotacao_status_rows if fornecedor else self._cotacao_status_rows
        rows.clear()
        table.setRowCount(0)
        if not fornecedor and hasattr(self, "cotacao_summary_label"):
            self.cotacao_summary_label.setText("Cotação iniciada. As transportadoras aparecerão conforme o fluxo avançar.")

    def _resumir_progresso_cotacao(self, total: int, concluidas: int, resultado: Any = None) -> str:
        if total <= 0:
            return "Preparando transportadoras para cotação."
        pendentes = max(0, total - concluidas)
        if concluidas >= total:
            return f"Cotação finalizada: {concluidas} de {total} transportadora(s) concluída(s)."
        if isinstance(resultado, ResultadoCotacao):
            nome = (resultado.transportadora or "Transportadora").strip().upper()
            return f"{nome} respondeu. Faltam {pendentes} de {total} transportadora(s)."
        return f"Cotando: {concluidas} de {total} transportadora(s) concluída(s)."

    def _rotulos_status_cotacao(self, status: str, mensagem: str, resultado: Any = None) -> tuple[str, str, str]:
        raw_status = str(status or "").strip().lower()
        result_status = str(getattr(resultado, "status", "") or "").strip().lower() if isinstance(resultado, ResultadoCotacao) else ""
        mensagem_lower = str(mensagem or "").lower()
        if result_status == "ok":
            status_key = "ok"
        elif result_status in {"sem_cotacao", "sem cotacao", "sem cotação"} or "sem cot" in mensagem_lower:
            status_key = "sem_cotacao"
        elif result_status == "nao_atendido" or raw_status == "nao_atendido":
            status_key = "nao_atendido"
        elif result_status == "desabilitada" or raw_status == "desabilitada":
            status_key = "desabilitada"
        elif "configura" in result_status or "configura" in mensagem_lower:
            status_key = "configuracao"
        elif raw_status in {"login", "cotando", "aguardando"}:
            status_key = raw_status
        elif raw_status == "finalizada":
            status_key = "finalizada"
        else:
            status_key = "erro" if raw_status == "erro" or result_status else (raw_status or "aguardando")

        labels = {
            "aguardando": "Aguardando",
            "login": "Acessando portal",
            "cotando": "Cotando",
            "finalizada": "Concluída",
            "ok": "Sucesso",
            "sem_cotacao": "Sem cotação",
            "nao_atendido": "Não atende",
            "desabilitada": "Indisponível",
            "configuracao": "Configuração",
            "erro": "Erro",
        }
        colors = {
            "aguardando": "#6b7280",
            "login": "#1f6feb",
            "cotando": "#1f6feb",
            "finalizada": "#067647",
            "ok": "#067647",
            "sem_cotacao": "#b54708",
            "nao_atendido": "#b54708",
            "desabilitada": "#6b7280",
            "configuracao": "#b54708",
            "erro": "#b42318",
        }
        return status_key, labels.get(status_key, labels["aguardando"]), colors.get(status_key, "#344054")

    def _atualizar_tabela_status_cotacao(self, payload: dict[str, Any], *, fornecedor: bool = False) -> None:
        provider = str(payload.get("provider") or "").strip().upper()
        if not provider:
            resultado = payload.get("resultado")
            provider = str(getattr(resultado, "transportadora", "") or "").strip().upper()
        if not provider:
            return

        table = self.forn_cotacao_status_table if fornecedor else self.cotacao_status_table
        rows = self._forn_cotacao_status_rows if fornecedor else self._cotacao_status_rows
        row = rows.get(provider)
        if row is None:
            row = table.rowCount()
            table.insertRow(row)
            rows[provider] = row

        status = str(payload.get("status") or "").strip()
        stage = str(payload.get("stage") or "").strip()
        mensagem = str(payload.get("mensagem") or "").strip()
        duration_ms = payload.get("duration_ms")

        resultado = payload.get("resultado")
        if isinstance(resultado, ResultadoCotacao):
            if not status:
                status = resultado.status
            if not mensagem:
                mensagem = resultado.detalhes or ""
            if duration_ms is None:
                duration_ms = resultado.duration_ms

        status_key, status_label, color = self._rotulos_status_cotacao(status, mensagem, resultado)
        stage_label = {
            "aguardando": "Aguardando",
            "login": "Acesso",
            "cotacao": "Cotação",
            "resultado": "Resultado",
            "finalizado": "Finalizada",
            "validacao": "Validação",
            "configuracao": "Configuração",
            "licenca": "Licença",
        }.get(stage, stage or "Aguardando")
        if not mensagem:
            mensagem = status_label
        mensagem = re.sub(r"\s+", " ", mensagem).strip()
        mensagem_lower = mensagem.lower()
        if status_key == "configuracao":
            mensagem = "Configuração incompleta"
        elif status_key == "sem_cotacao":
            mensagem = "Sem cotação retornada"
        elif status_key == "nao_atendido":
            mensagem = "UF não atendida"
        elif status_key == "desabilitada":
            mensagem = "Transportadora indisponível pela licença/configuração"
        elif "timeout" in mensagem_lower:
            mensagem = "Tempo limite aguardando resultado"
        if len(mensagem) > 160:
            mensagem = mensagem[:157] + "..."

        tempo = ""
        try:
            if duration_ms is not None:
                tempo = f"{int(duration_ms) / 1000:.1f}s"
        except Exception:
            tempo = ""

        values = [provider, status_label, stage_label, mensagem, tempo]
        for col, value in enumerate(values):
            item = table.item(row, col)
            if item is None:
                item = QTableWidgetItem()
                table.setItem(row, col, item)
            item.setText(value)
            if col == 1:
                item.setForeground(QColor(color))

    def _iniciar_cotacao(self, modo: str):
        if self._is_shutting_down():
            return
        if not ensure_feature_allowed("cotacao", self):
            return
        if modo == "romaneio_colado" and not ensure_feature_allowed("romaneio", self):
            return
        self._modo_cotacao = modo
        self._cep_origem_override = ""
        self.btn_quote_colado.setEnabled(False)
        self.btn_select.setEnabled(False)
        self.btn_cotar_fornecedor.setEnabled(False)
        self._cotacao_total = 0
        self._cotacao_concluidas = 0
        self._resetar_tabela_status_cotacao(fornecedor=False)
        self.progress_bar.setVisible(True)
        self.progress_bar.start_anim()
        self.result_text.setPlainText("Cotação em andamento. As respostas serão listadas aqui conforme cada transportadora finalizar.")
        if hasattr(self, "cotacao_run_status"):
            self.cotacao_run_status.setText("Cotação em andamento. Aguarde as respostas das transportadoras.")
        if hasattr(self, "cotacao_summary_label"):
            self.cotacao_summary_label.setText("Preparando transportadoras para cotação.")
        self._show_page(2)
        self.label_info.setText("Executando cotações de transportadoras...")
        self.label_info.setStyleSheet(f"color: {getattr(self, '_c_info', '#d97757')};")
        self._run_async_cotacao()

    def _cotar_romaneio_colado(self):
        texto = (self.romaneio_colado_text.toPlainText() or "").strip()
        if not texto:
            QMessageBox.warning(self, "Aviso", "Cole um romaneio antes de cotar")
            return
        self._romaneio_colado = texto
        self._iniciar_cotacao("romaneio_colado")

    def _obter_cnpj_empresa(self) -> str:
        """Busca o CNPJ da empresa na configuração das transportadoras."""
        transp = self._sessao.config.get("transportadoras", {}) or {}
        cnpj = re.sub(r"\D", "", str((transp.get("braspress") or {}).get("cnpj", "") or ""))
        if len(cnpj) == 14:
            return cnpj

        agex_cfg = transp.get("agex") or {}
        for chave in ("cnpj_remetente", "cnpj"):
            cnpj = re.sub(r"\D", "", str(agex_cfg.get(chave, "") or ""))
            if len(cnpj) == 14:
                return cnpj

        for nome in ("rodonaves",):
            cnpj = re.sub(r"\D", "", str((transp.get(nome) or {}).get("cnpj_pagador", "") or ""))
            if len(cnpj) == 14:
                return cnpj
        return ""

    def _obter_cep_empresa(self) -> str:
        rom = self._sessao.config.get("romaneio", {}) or {}
        cep = re.sub(r"\D", "", str(rom.get("cep_origem", "") or ""))
        return cep if len(cep) == 8 else ""

    def _montar_romaneio_fornecedor(self) -> tuple[str, str]:
        """Monta romaneio a partir dos campos de fornecedor.
        Returns (romaneio_text, cep_origem_fornecedor)."""
        cnpj_empresa = self._obter_cnpj_empresa()
        cep_empresa = self._obter_cep_empresa()
        cep_forn = re.sub(r"\D", "", self._forn_cep.text())

        try:
            qtd = int(self._forn_qtd.text().strip() or "0")
        except ValueError:
            qtd = 0

        def _fbr(txt: str) -> float:
            txt = re.sub(r"[R$\s]", "", txt.strip())
            # Remove pontos de milhar, depois converte vírgula decimal em ponto
            txt = txt.replace(".", "").replace(",", ".")
            return float(txt) if txt else 0.0

        alt = _fbr(self._forn_alt.text())
        larg = _fbr(self._forn_larg.text())
        comp = _fbr(self._forn_comp.text())
        peso_cx_txt = self._forn_peso_cx.text().strip()
        peso_total_txt = self._forn_peso_total.text().strip()
        valor = _fbr(self._forn_valor.text())

        if peso_cx_txt:
            peso_caixa = _fbr(peso_cx_txt)
            peso_total = peso_caixa * qtd
        elif peso_total_txt:
            peso_total = _fbr(peso_total_txt)
            peso_caixa = peso_total / qtd if qtd > 0 else 0.0
        else:
            raise ValueError("Informe o peso por volume ou o peso total (pelo menos um é obrigatório)")

        cubagem_unit = (alt * larg * comp) / 1_000_000
        cubagem_total = cubagem_unit * qtd

        erros: list[str] = []
        if len(cnpj_empresa) != 14:
            erros.append("CNPJ da empresa não configurado (verifique Configurações > Credenciais)")
        if len(cep_empresa) != 8:
            erros.append("CEP da empresa não configurado (verifique Configurações > Empresa > CEP de Origem)")
        if len(cep_forn) != 8:
            erros.append("CEP do fornecedor inválido (deve ter 8 dígitos)")
        if qtd <= 0:
            erros.append("Quantidade de volumes deve ser maior que zero")
        if alt <= 0 or larg <= 0 or comp <= 0:
            erros.append("Dimensões devem ser maiores que zero")
        if peso_total <= 0:
            erros.append("Peso deve ser maior que zero")
        if erros:
            raise ValueError("\n".join(erros))

        c = cnpj_empresa
        cnpj_fmt = f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"
        cep_fmt = f"{cep_empresa[:5]}-{cep_empresa[5:]}"
        lines = [
            f"CNPJ/CPF: {cnpj_fmt}",
            f"CEP: {cep_fmt}",
            f"- VOL: {qtd}",
            f"- CUBAGEM: {cubagem_total:.6f} m3",
            f"- PESO: {peso_total:.2f} kg",
            f"- TOTAL: R$ {valor:.2f}",
            f"{qtd} x Volume fornecedor - {peso_caixa:.3f} kg - {cubagem_unit:.6f} m3 - {int(alt)}x{int(larg)}x{int(comp)}",
        ]
        return "\n".join(lines), cep_forn

    def _cotar_frete_fornecedor(self):
        if self._is_shutting_down():
            return
        if not ensure_feature_allowed("cotacao", self):
            return
        try:
            romaneio_texto, cep_fornecedor = self._montar_romaneio_fornecedor()
        except (ValueError, Exception) as e:
            QMessageBox.warning(self, "Dados inválidos", str(e))
            return
        self._romaneio_colado = romaneio_texto
        self._cep_origem_override = cep_fornecedor
        self._cnpj_fornecedor = re.sub(r"\D", "", self._forn_cnpj.text())
        self._modo_cotacao = "fornecedor"
        self.btn_cotar_fornecedor.setEnabled(False)
        self.btn_quote_colado.setEnabled(False)
        self.btn_select.setEnabled(False)
        self._resetar_tabela_status_cotacao(fornecedor=True)
        self.forn_progress_bar.setVisible(True)
        self.forn_progress_bar.start_anim()
        self.forn_result_text.setPlainText("Cotação em andamento. As respostas serão listadas conforme cada transportadora finalizar.")
        self.label_info.setText("Cotando frete fornecedor...")
        self.label_info.setStyleSheet(f"color: {getattr(self, '_c_info', '#d97757')};")
        self._run_async_cotacao()

    def _verificar_erro_divergencia_uf(self, texto_resultado: str) -> None:
        """Se o resultado contiver erro de divergência CEP/UF, mostra popup."""
        if "erro_divergencia_uf" not in texto_resultado and "pertence à UF" not in texto_resultado:
            return
        # Extrai a mensagem limpa
        for linha in texto_resultado.split("\n"):
            if "pertence à UF" in linha or "CEP de destino" in linha:
                msg = linha.strip().lstrip("- ").strip()
                QMessageBox.warning(self, "Divergência CEP / UF de Destino", msg)
                return
        QMessageBox.warning(
            self,
            "Divergência CEP / UF de Destino",
            "O CEP de destino não corresponde à UF informada no romaneio.\n"
            "Verifique os dados do destinatário.",
        )
