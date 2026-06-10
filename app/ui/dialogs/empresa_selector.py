from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from company_config import (
    _criar_config_empresa_vazia,
    _empresa_config_path,
    _ler_ultima_empresa,
    _listar_empresas,
    _renomear_pasta_empresa,
    _salvar_ultima_empresa,
)
from startup import _resource_path


class EmpresaSelectorDialog(QDialog):
    """Tela inicial para escolher com qual empresa operar."""

    def __init__(self, parent=None, dark: bool = True):
        super().__init__(parent)
        self._dark = dark
        self.setWindowTitle("Fretio — Selecionar Empresa")
        self.setFixedSize(420, 340)
        self.empresa_selecionada: str | None = None
        icon_path = _resource_path("assets/romaneio.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self._build_ui()
        self._apply_style()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        title = QLabel("Selecione a empresa")
        title.setObjectName("TitleLabel")
        layout.addWidget(title)

        subtitle = QLabel("Escolha com qual empresa deseja operar")
        subtitle.setObjectName("SubtitleLabel")
        layout.addWidget(subtitle)

        self.lista = QListWidget()
        self.lista.setObjectName("EmpresaList")
        self.lista.doubleClicked.connect(self._entrar)
        self._carregar_lista()
        layout.addWidget(self.lista, 1)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        btn_nova = QPushButton("Nova Empresa")
        btn_nova.setObjectName("SecondaryButton")
        btn_nova.clicked.connect(self._nova_empresa)
        btn_renomear = QPushButton("Renomear")
        btn_renomear.setObjectName("SecondaryButton")
        btn_renomear.clicked.connect(self._renomear_empresa)
        btn_entrar = QPushButton("Entrar")
        btn_entrar.clicked.connect(self._entrar)
        btn_layout.addWidget(btn_nova)
        btn_layout.addWidget(btn_renomear)
        btn_layout.addStretch(1)
        btn_layout.addWidget(btn_entrar)
        layout.addLayout(btn_layout)

    def _carregar_lista(self):
        self.lista.clear()
        for emp in _listar_empresas():
            self.lista.addItem(emp)
        ultima = _ler_ultima_empresa()
        if ultima:
            items = self.lista.findItems(ultima, Qt.MatchExactly)
            if items:
                self.lista.setCurrentItem(items[0])
        elif self.lista.count() > 0:
            self.lista.setCurrentRow(0)

    def _nova_empresa(self):
        nome, ok = QInputDialog.getText(self, "Nova Empresa", "Nome da empresa:")
        if not ok or not nome.strip():
            return
        nome = re.sub(r'[<>:"/\\|?*]', '_', nome.strip())
        if not _empresa_config_path(nome).exists():
            _criar_config_empresa_vazia(nome)
        self._carregar_lista()
        items = self.lista.findItems(nome, Qt.MatchExactly)
        if items:
            self.lista.setCurrentItem(items[0])

    def _renomear_empresa(self):
        item = self.lista.currentItem()
        if not item:
            QMessageBox.warning(self, "Aviso", "Selecione uma empresa para renomear")
            return
        nome_atual = item.text()
        novo_nome, ok = QInputDialog.getText(self, "Renomear Empresa", "Novo nome:", text=nome_atual)
        if not ok or not novo_nome.strip():
            return
        novo_nome = re.sub(r'[<>:"/\\|?*]', '_', novo_nome.strip())
        if novo_nome == nome_atual:
            return
        if _renomear_pasta_empresa(nome_atual, novo_nome):
            self._carregar_lista()
            items = self.lista.findItems(novo_nome, Qt.MatchExactly)
            if items:
                self.lista.setCurrentItem(items[0])
        else:
            QMessageBox.warning(self, "Erro",
                                f"Não foi possível renomear para '{novo_nome}'.\n"
                                "Verifique se o nome já existe.")

    def _entrar(self):
        item = self.lista.currentItem()
        if not item:
            QMessageBox.warning(self, "Aviso", "Selecione uma empresa")
            return
        self.empresa_selecionada = item.text()
        _salvar_ultima_empresa(self.empresa_selecionada)
        self.accept()

    def _apply_style(self):
        if self._dark:
            c_bg = "#0d1117"; c_panel2 = "#1c232c"; c_panel3 = "#21282f"
            c_border = "#262f3a"; c_ink = "#e6edf3"; c_muted = "#768390"
            c_ink2 = "#adbac7"; c_accent = "#00b4d8"
        else:
            c_bg = "#f0f4f8"; c_panel2 = "#f8fafc"; c_panel3 = "#f1f5f9"
            c_border = "#e2e8f0"; c_ink = "#0f172a"; c_muted = "#64748b"
            c_ink2 = "#334155"; c_accent = "#0077b6"
        self.setStyleSheet(f"""
            QDialog {{ background: {c_bg}; }}
            #TitleLabel {{ font-size: 20px; font-weight: 700; color: {c_ink}; }}
            #SubtitleLabel {{ font-size: 12px; color: {c_muted}; }}
            QLabel {{ color: {c_ink}; }}
            QListWidget {{ background: {c_panel2}; color: {c_ink}; border: 1px solid {c_border};
                          border-radius: 8px; padding: 6px; font-size: 13px; outline: none; }}
            QListWidget:focus {{ border: 1px solid {c_border}; outline: none; }}
            QListWidget::item {{ padding: 8px; border-radius: 6px; }}
            QListWidget::item:selected {{ background: {c_accent}; color: #fff; }}
            QPushButton {{ background: {c_accent}; color: #fff; border: none; border-radius: 8px;
                          padding: 10px 18px; font-weight: 600; }}
            QPushButton#SecondaryButton {{ background: {c_panel2}; color: {c_ink2};
                                          border: 1px solid {c_border}; }}
            QPushButton#SecondaryButton:hover {{ background: {c_panel3}; color: {c_ink}; }}
        """)
