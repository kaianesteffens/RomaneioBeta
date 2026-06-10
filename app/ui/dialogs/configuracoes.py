from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from company_config import TODAS_UFS, _escrever_config_toml
from fretio.providers.factory import validate_provider_minimum_config
from startup import _resource_path

CAMPOS_CREDENCIAIS: dict[str, list[tuple[str, str, bool]]] = {
    "braspress": [("cnpj", "CNPJ", False), ("senha", "Senha", True)],
    "bauer": [
        ("cotacao_url", "URL de cotação", False),
        ("cnpj_pagador", "CNPJ Pagador", False),
        ("cnpj_remetente", "CNPJ Remetente", False),
        ("cnpj_destinatario", "CNPJ Destinatário", False),
    ],
    "trd": [("email", "Email", False), ("senha", "Senha", True)],
    "agex": [
        ("email", "Email", False),
        ("senha", "Senha", True),
        ("cnpj_remetente", "CNPJ Remetente", False),
    ],
    "eucatur": [("dominio", "Domínio", False), ("usuario", "Usuário", False), ("senha", "Senha", True)],
    "rodonaves": [
        ("dominio", "Domínio", False),
        ("usuario", "Usuário", False),
        ("senha", "Senha", True),
        ("cnpj_pagador", "CNPJ Pagador", False),
    ],
    "alfa": [
        ("login", "Login", False),
        ("senha", "Senha", True),
        ("cnpj_remetente", "CNPJ Remetente", False),
    ],
    "coopex": [("dominio", "Domínio", False), ("usuario", "Usuário", False), ("senha", "Senha", True)],
    "translovato": [("cnpj", "CNPJ", False), ("usuario", "Usuário", False), ("senha", "Senha", True)],
}

TRANSPORTADORAS_CONFIGURAVEIS = [
    "braspress",
    "bauer",
    "trd",
    "agex",
    "eucatur",
    "rodonaves",
    "alfa",
    "coopex",
    "translovato",
]


class ConfiguracoesDialog(QDialog):
    """Painel de configurações: UFs atendidas, credenciais e troca de empresa."""

    def __init__(self, config: dict[str, Any], config_path: Path,
                 empresa_nome: str, parent=None):
        super().__init__(parent)
        self.config = config
        self.config_path = config_path
        self.empresa_nome = empresa_nome
        self.empresa_trocada: str | None = None
        self._credenciais_mudaram = False
        self._ufs_cbs: dict[str, dict[str, QCheckBox]] = {}
        self._cred_fields: dict[str, dict[str, QLineEdit]] = {}
        self._hab_checks: dict[str, QCheckBox] = {}
        self._cred_warnings: dict[str, QLabel] = {}
        self.setWindowTitle(f"Configurações — {empresa_nome}")
        self.setMinimumSize(720, 560)
        self._build_ui()
        self._apply_style()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        tabs = QTabWidget()
        tabs.setObjectName("MainTabs")
        tabs.addTab(self._tab_ufs(), "UFs atendidas")
        tabs.addTab(self._tab_credenciais(), "Credenciais")
        layout.addWidget(tabs, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_cancelar = QPushButton("Cancelar")
        btn_cancelar.setObjectName("SecondaryButton")
        btn_cancelar.clicked.connect(self.reject)
        btn_salvar = QPushButton("Salvar")
        btn_salvar.clicked.connect(self._salvar)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_cancelar)
        btn_row.addWidget(btn_salvar)
        layout.addLayout(btn_row)

    @staticmethod
    def _criar_label_transportadora(nome: str) -> QLabel:
        """Cria QLabel com logo da transportadora ou texto fallback."""
        LOGO_H = 38
        MAX_W = 220
        logo_exts = (".png", ".webp", ".jpg")
        for ext in logo_exts:
            logo_path = _resource_path(f"assets/logos/{nome}{ext}")
            if logo_path.exists():
                pix = QPixmap(str(logo_path))
                if not pix.isNull():
                    scaled = pix.scaledToHeight(LOGO_H, Qt.SmoothTransformation)
                    if scaled.width() > MAX_W:
                        scaled = pix.scaledToWidth(MAX_W, Qt.SmoothTransformation)
                    lbl = QLabel()
                    lbl.setPixmap(scaled)
                    lbl.setAlignment(Qt.AlignCenter)
                    lbl.setFixedHeight(LOGO_H + 14)
                    return lbl
        lbl = QLabel(nome.upper())
        lbl.setObjectName("TranspTitle")
        lbl.setAlignment(Qt.AlignCenter)
        return lbl

    # --- aba UFs Atendidas ---
    def _tab_ufs(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setObjectName("ConfigScroll")
        scroll.viewport().setObjectName("ConfigViewport")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content.setObjectName("ConfigSurface")
        vbox = QVBoxLayout(content)
        vbox.setSpacing(10)
        transp_cfg = self.config.get("transportadoras", {}) or {}
        for nome in sorted(TRANSPORTADORAS_CONFIGURAVEIS):
            tcfg = transp_cfg.get(nome, {}) or {}
            ufs_atuais = tcfg.get("ufs_atendidas", [])
            if isinstance(ufs_atuais, str):
                ufs_atuais = [u.strip().upper() for u in ufs_atuais.split(",") if u.strip()]
            else:
                ufs_atuais = [u.upper() for u in (ufs_atuais or [])]
            group = QGroupBox()
            group.setObjectName("SettingsGroup")
            grid = QGridLayout(group)
            grid.setSpacing(4)
            lbl_nome = self._criar_label_transportadora(nome)
            grid.addWidget(lbl_nome, 0, 0, 1, 9)
            cbs: dict[str, QCheckBox] = {}
            for i, uf in enumerate(TODAS_UFS):
                cb = QCheckBox(uf)
                cb.setChecked(uf in ufs_atuais)
                grid.addWidget(cb, 1 + i // 9, i % 9)
                cbs[uf] = cb
            self._ufs_cbs[nome] = cbs
            btn_row_ufs = QHBoxLayout()
            btn_all = QPushButton("Todas")
            btn_all.setFixedHeight(24)
            btn_all.setObjectName("MiniButton")
            btn_none = QPushButton("Nenhuma")
            btn_none.setFixedHeight(24)
            btn_none.setObjectName("MiniButton")
            btn_row_ufs.addStretch(1)
            btn_row_ufs.addWidget(btn_all)
            btn_row_ufs.addWidget(btn_none)
            last_row = 1 + (len(TODAS_UFS) - 1) // 9 + 1
            spacer = QLabel("")
            spacer.setFixedHeight(6)
            grid.addWidget(spacer, last_row, 0, 1, 9)
            grid.addLayout(btn_row_ufs, last_row + 1, 0, 1, 9)
            btn_all.clicked.connect(lambda _, c=cbs: [v.setChecked(True) for v in c.values()])
            btn_none.clicked.connect(lambda _, c=cbs: [v.setChecked(False) for v in c.values()])
            vbox.addWidget(group)
        vbox.addStretch(1)
        scroll.setWidget(content)
        wrapper = QWidget()
        wrapper.setObjectName("ConfigSurface")
        wl = QVBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(scroll)
        return wrapper

    # --- aba Credenciais ---
    def _tab_credenciais(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setObjectName("ConfigScroll")
        scroll.viewport().setObjectName("ConfigViewport")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content.setObjectName("ConfigSurface")
        vbox = QVBoxLayout(content)
        vbox.setSpacing(10)
        transp_cfg = self.config.get("transportadoras", {}) or {}
        for nome in sorted(CAMPOS_CREDENCIAIS):
            campos = CAMPOS_CREDENCIAIS[nome]
            tcfg = transp_cfg.get(nome, {}) or {}
            group = QGroupBox()
            group.setObjectName("SettingsGroup")
            form = QFormLayout(group)
            form.setSpacing(6)
            lbl_nome_cred = self._criar_label_transportadora(nome)
            form.addRow(lbl_nome_cred)
            cb_hab = QCheckBox("Habilitado")
            cb_hab.setChecked(bool(tcfg.get("habilitado", False)))
            form.addRow("", cb_hab)
            self._hab_checks[nome] = cb_hab
            warning = QLabel("")
            warning.setObjectName("ConfigWarning")
            warning.setWordWrap(True)
            form.addRow("", warning)
            self._cred_warnings[nome] = warning
            fields: dict[str, QLineEdit] = {}
            for chave, label, eh_senha in campos:
                le = QLineEdit()
                valor = str(tcfg.get(chave, "") or "")
                if nome == "agex" and chave == "email" and not valor:
                    legado = str(tcfg.get("cnpj", "") or "").strip()
                    if "@" in legado:
                        valor = legado
                le.setText(valor)
                if eh_senha:
                    le.setEchoMode(QLineEdit.Password)
                le.setObjectName("CredField")
                form.addRow(f"{label}:", le)
                fields[chave] = le
            self._cred_fields[nome] = fields
            cb_hab.toggled.connect(lambda _checked, n=nome: self._atualizar_aviso_credencial(n))
            for le in fields.values():
                le.textChanged.connect(lambda _text, n=nome: self._atualizar_aviso_credencial(n))
            self._atualizar_aviso_credencial(nome)
            vbox.addWidget(group)
        vbox.addStretch(1)
        scroll.setWidget(content)
        wrapper = QWidget()
        wrapper.setObjectName("ConfigSurface")
        wl = QVBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(scroll)
        return wrapper

    def _config_credencial_atual(self, nome: str) -> dict[str, Any]:
        transp_cfg = self.config.get("transportadoras", {}) or {}
        tcfg = dict(transp_cfg.get(nome, {}) or {})
        cb = self._hab_checks.get(nome)
        if cb is not None:
            tcfg["habilitado"] = cb.isChecked()
        for chave, le in self._cred_fields.get(nome, {}).items():
            tcfg[chave] = le.text().strip()
        return tcfg

    def _atualizar_aviso_credencial(self, nome: str) -> None:
        label = self._cred_warnings.get(nome)
        if label is None:
            return
        validation = validate_provider_minimum_config(nome, self._config_credencial_atual(nome))
        label.setVisible(not validation.valid)
        label.setText(validation.user_message if not validation.valid else "")

    def _validar_credenciais_antes_de_salvar(self) -> list[str]:
        erros: list[str] = []
        for nome in sorted(self._hab_checks):
            validation = validate_provider_minimum_config(nome, self._config_credencial_atual(nome))
            if not validation.valid:
                erros.append(f"- {nome.upper()}: {validation.user_message}")
        return erros

    def _salvar(self):
        transp_cfg = self.config.setdefault("transportadoras", {})
        cred_changed = False
        # UFs
        for nome, cbs in self._ufs_cbs.items():
            tcfg = transp_cfg.setdefault(nome, {})
            tcfg["ufs_atendidas"] = [uf for uf, cb in cbs.items() if cb.isChecked()]
        # Habilitado (toggle não requer relogin de todas as transportadoras)
        for nome, cb in self._hab_checks.items():
            tcfg = transp_cfg.setdefault(nome, {})
            tcfg["habilitado"] = cb.isChecked()
        # Credenciais
        for nome, fields in self._cred_fields.items():
            tcfg = transp_cfg.setdefault(nome, {})
            for chave, le in fields.items():
                novo = le.text().strip()
                if str(tcfg.get(chave, "") or "") != novo:
                    cred_changed = True
                tcfg[chave] = novo
        _escrever_config_toml(self.config, self.config_path)
        self._credenciais_mudaram = cred_changed
        # Aviso pós-save: captura erros antes de fechar o diálogo
        erros = self._validar_credenciais_antes_de_salvar()
        QMessageBox.information(self, "Sucesso", "Configurações salvas!")
        self.accept()
        if erros:
            QMessageBox.warning(
                self.parent(),
                "Configuração incompleta",
                "As configurações foram salvas, mas as transportadoras abaixo estão habilitadas "
                "com campos obrigatórios vazios e não serão cotadas:\n\n"
                + "\n".join(erros)
                + "\n\nPreencha os campos indicados para que a cotação funcione corretamente.",
            )

    def _apply_style(self):
        fb_cfg = self.config.get("fretio", {}) or {}
        dark = str(fb_cfg.get("ui_tema", "escuro")).lower() == "escuro"
        if dark:
            c_bg = "#0d1117"; c_panel = "#161b22"; c_panel2 = "#1c232c"; c_panel3 = "#21282f"
            c_border = "#262f3a"; c_ink = "#e6edf3"; c_muted = "#768390"
            c_ink2 = "#adbac7"; c_faint = "#444c56"; c_accent = "#00b4d8"; c_accent_hover = "#0098b8"
        else:
            c_bg = "#f0f4f8"; c_panel = "#ffffff"; c_panel2 = "#f8fafc"; c_panel3 = "#f1f5f9"
            c_border = "#e2e8f0"; c_ink = "#0f172a"; c_muted = "#64748b"
            c_ink2 = "#334155"; c_faint = "#94a3b8"; c_accent = "#0077b6"; c_accent_hover = "#0369a1"
        self.setStyleSheet(f"""
            QDialog {{ background: {c_bg}; color: {c_ink}; }}
            QWidget#ConfigSurface, QWidget#ConfigViewport {{ background: {c_bg}; color: {c_ink}; }}
            QLabel {{ color: {c_ink}; }}
            #TitleLabel {{ font-size: 18px; font-weight: 700; color: {c_ink}; }}
            #SettingsGroup {{ border: 1px solid {c_border}; border-radius: 8px;
                             padding: 12px 10px 10px 10px; margin-top: 6px; background: {c_panel}; }}
            QGroupBox#SettingsGroup {{ border: 1px solid {c_border}; background: {c_panel}; border-radius: 8px; margin-top: 0px; }}
            QGroupBox#SettingsGroup::title {{ subcontrol-origin: margin; height: 0px; width: 0px; padding: 0px; color: transparent; }}
            #TranspTitle {{ font-size: 17px; font-weight: 700; color: {c_ink};
                           padding: 6px 0 8px 0; }}
            #ConfigWarning {{ color: #f59e0b; background: rgba(245, 158, 11, 0.12);
                              border: 1px solid rgba(245, 158, 11, 0.45);
                              border-radius: 6px; padding: 6px 8px; }}
            #CredField {{ border: 1px solid {c_border}; border-radius: 6px; padding: 5px 8px;
                         background: {c_panel2}; color: {c_ink}; }}
            QTabWidget#MainTabs::pane {{ border: 1px solid {c_border}; border-radius: 10px;
                                        background: {c_panel}; }}
            QTabBar::tab {{ background: {c_panel2}; color: {c_muted}; border: 1px solid {c_border};
                           padding: 7px 12px; margin-right: 4px; border-top-left-radius: 8px;
                           border-top-right-radius: 8px; }}
            QTabBar::tab:selected {{ background: {c_panel}; color: {c_ink};
                                     border-bottom-color: {c_panel}; }}
            QTabBar::tab:hover {{ background: {c_panel3}; color: {c_ink}; }}
            QPushButton {{ background: {c_accent}; color: #fff; border: none; border-radius: 8px;
                          padding: 9px 16px; font-weight: 600; }}
            QPushButton:hover {{ background: {c_accent_hover}; color: #fff; }}
            QPushButton#SecondaryButton {{ background: {c_panel2}; color: {c_ink2};
                                          border: 1px solid {c_border}; }}
            QPushButton#SecondaryButton:hover {{ background: {c_panel3}; color: {c_ink}; }}
            QPushButton#MiniButton {{ background: {c_panel2}; color: {c_ink2};
                                     border: 1px solid {c_border}; border-radius: 4px;
                                     padding: 2px 8px; font-size: 11px; }}
            QPushButton#MiniButton:hover {{ background: {c_panel3}; }}
            QCheckBox {{ color: {c_ink}; spacing: 4px; }}
            QLineEdit {{ color: {c_ink}; background: {c_panel2}; border: 1px solid {c_border};
                        border-radius: 6px; padding: 5px 8px; selection-background-color: {c_accent}; }}
            QLineEdit:focus {{ border: 1px solid {c_accent}; background: {c_panel}; }}
            QScrollArea {{ background: transparent; border: none; }}
            QScrollArea#ConfigScroll > QWidget > QWidget {{ background: {c_bg}; }}
            QScrollBar:vertical {{ background: {c_panel2}; width: 10px; margin: 0; border-radius: 5px; }}
            QScrollBar::handle:vertical {{ background: {c_faint}; min-height: 28px; border-radius: 5px; }}
            QScrollBar::handle:vertical:hover {{ background: {c_muted}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            QScrollBar:horizontal {{ background: {c_panel2}; height: 10px; margin: 0; border-radius: 5px; }}
            QScrollBar::handle:horizontal {{ background: {c_faint}; min-width: 28px; border-radius: 5px; }}
            QScrollBar::handle:horizontal:hover {{ background: {c_muted}; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
            QGroupBox {{ border: none; }}
        """)
