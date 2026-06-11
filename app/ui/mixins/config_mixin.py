from __future__ import annotations

import re
from typing import Any
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QPushButton,
    QDialog,
    QScrollArea,
    QLineEdit,
    QGridLayout,
    QCheckBox,
    QListWidget,
    QMessageBox,
    QStackedWidget,
)

from company_config import (
    TODAS_UFS,
    _escrever_config_toml,
    _renomear_pasta_empresa,
)
from fretio.providers.factory import validate_provider_minimum_config


class ConfigMixin:
    def _build_config_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PageContent")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._cfg_ufs_cbs: dict = {}
        self._cfg_hab_checks: dict = {}
        self._cfg_cred_fields: dict = {}
        self._cfg_cred_warnings: dict = {}

        header = QFrame()
        header.setObjectName("PageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 8, 12, 8)
        header_layout.setSpacing(10)

        btn_voltar = QPushButton("← Voltar")
        btn_voltar.setObjectName("BackButton")
        btn_voltar.setCursor(Qt.PointingHandCursor)
        btn_voltar.clicked.connect(lambda: self._show_page(0))
        header_layout.addWidget(btn_voltar)
        header_layout.addStretch(1)
        layout.addWidget(header)

        # Sub-navegacao por secoes: cada secao abre numa tela propria.
        self._cfg_section_stack = QStackedWidget()
        self._cfg_section_buttons: dict[int, QPushButton] = {}
        secoes = (
            ("Empresa", self._build_card_empresa_inline),
            ("Aparência", self._build_card_aparencia_inline),
            ("Transportadoras", self._build_card_transportadoras_inline),
            ("Credenciais", self._build_card_credenciais_inline),
        )

        nav = QFrame()
        nav.setObjectName("SettingsNav")
        nav_layout = QHBoxLayout(nav)
        nav_layout.setContentsMargins(20, 12, 20, 8)
        nav_layout.setSpacing(8)
        nav_layout.addStretch(1)
        for idx, (titulo, construtor) in enumerate(secoes):
            btn = QPushButton(titulo)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setObjectName("ThemeOptionActive" if idx == 0 else "ThemeOption")
            btn.setChecked(idx == 0)
            btn.clicked.connect(lambda _c=False, i=idx: self._mostrar_secao_config(i))
            self._cfg_section_buttons[idx] = btn
            nav_layout.addWidget(btn)
            self._cfg_section_stack.addWidget(self._settings_section_wrapper(construtor()))
        nav_layout.addStretch(1)
        layout.addWidget(nav)
        layout.addWidget(self._cfg_section_stack, 1)
        return page

    def _settings_section_wrapper(self, card: QWidget) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("SettingsScroll")
        content = QWidget()
        content.setObjectName("SettingsSurface")
        outer = QHBoxLayout(content)
        outer.setContentsMargins(20, 8, 20, 20)
        outer.setSpacing(0)
        column_host = QWidget()
        column_host.setMaximumWidth(760)
        column = QVBoxLayout(column_host)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(14)
        column.addWidget(card)
        column.addStretch(1)
        outer.addStretch(1)
        outer.addWidget(column_host)
        outer.addStretch(1)
        scroll.setWidget(content)
        return scroll

    def _mostrar_secao_config(self, indice: int) -> None:
        self._cfg_section_stack.setCurrentIndex(indice)
        for i, btn in self._cfg_section_buttons.items():
            ativo = i == indice
            btn.setChecked(ativo)
            btn.setObjectName("ThemeOptionActive" if ativo else "ThemeOption")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _settings_card(self, title: str, subtitle: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("SettingsCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 15, 16, 16)
        layout.setSpacing(12)
        header = QVBoxLayout()
        header.setSpacing(3)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("SettingsCardTitle")
        sub_lbl = QLabel(subtitle)
        sub_lbl.setObjectName("SettingsCardSubtitle")
        sub_lbl.setWordWrap(True)
        header.addWidget(title_lbl)
        header.addWidget(sub_lbl)
        layout.addLayout(header)
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setObjectName("SoftSep")
        layout.addWidget(sep)
        return card, layout

    def _setting_field_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("SettingsFieldLabel")
        return label

    def _transportadora_status_text(self, nome: str, tcfg: dict[str, Any]) -> tuple[str, str]:
        if not tcfg.get("habilitado", False):
            return "Desabilitada", "TagAmber"
        validation = validate_provider_minimum_config(nome, tcfg)
        if validation.valid:
            return "Pronta", "TagGreen"
        return "Pendente", "TagRed"

    def _build_card_empresa_inline(self) -> QFrame:
        card, layout = self._settings_card(
            "EMPRESA",
            "Dados usados como padrão nas cotações e identificação da empresa ativa.",
        )
        cfg = self._sessao.config if isinstance(self._sessao.config, dict) else {}
        rom_cfg = cfg.get("romaneio", {}) or {}
        fb_cfg = cfg.get("fretio", {}) or {}
        self._cfg_cep_origem = QLineEdit(str(rom_cfg.get("cep_origem", "") or ""))
        self._cfg_cep_origem.setObjectName("InputField")
        self._cfg_cnpj_pagador_padrao = QLineEdit(str(rom_cfg.get("cnpj_pagador_padrao", "") or ""))
        self._cfg_cnpj_pagador_padrao.setObjectName("InputField")
        self._cfg_cnpj_pagador_padrao.setPlaceholderText("CNPJ/CPF padrão para transportadoras sem documento próprio")
        self._cfg_paralelo = QLineEdit(str(int(fb_cfg.get("max_paralelo", 3) or 3)))
        self._cfg_paralelo.setObjectName("InputField")
        self._cfg_paralelo.setMaximumWidth(110)
        self._cfg_nome_empresa = QLineEdit(self.empresa_nome)
        self._cfg_nome_empresa.setObjectName("InputField")

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(9)
        fields = [
            ("Nome da empresa", self._cfg_nome_empresa),
            ("CEP de origem", self._cfg_cep_origem),
            ("Documento pagador padrão", self._cfg_cnpj_pagador_padrao),
            ("Cotações paralelas", self._cfg_paralelo),
        ]
        for row, (label, widget) in enumerate(fields):
            form.addWidget(self._setting_field_label(label), row, 0)
            form.addWidget(widget, row, 1)
        form.setColumnStretch(1, 1)
        layout.addLayout(form)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        btn_trocar = QPushButton("Trocar empresa")
        btn_trocar.setObjectName("SecondaryButton")
        btn_trocar.clicked.connect(self._trocar_empresa_embutido)
        btn_salvar = QPushButton("Salvar")
        btn_salvar.clicked.connect(self._salvar_config_embutido)
        actions.addWidget(btn_trocar)
        actions.addStretch(1)
        actions.addWidget(btn_salvar)
        layout.addLayout(actions)
        return card

    def _build_card_aparencia_inline(self) -> QFrame:
        card, layout = self._settings_card(
            "APARÊNCIA",
            "Escolha o tema do programa. A alternância também fica disponível na sidebar.",
        )
        theme_row = QHBoxLayout()
        theme_row.setSpacing(8)
        self._cfg_theme_buttons: dict[str, QPushButton] = {}
        for mode, text in (("claro", "Claro"), ("escuro", "Escuro"), ("sistema", "Sistema")):
            btn = QPushButton(text)
            btn.setObjectName("ThemeOptionActive" if self._theme_mode == mode else "ThemeOption")
            btn.setCheckable(True)
            btn.setChecked(self._theme_mode == mode)
            btn.clicked.connect(lambda _checked=False, m=mode: self._set_theme_mode_from_settings(m))
            self._cfg_theme_buttons[mode] = btn
            theme_row.addWidget(btn)
        layout.addWidget(self._setting_field_label("Tema"))
        layout.addLayout(theme_row)
        layout.addStretch(1)
        return card

    def _build_card_transportadoras_inline(self) -> QFrame:
        from romaneio_app import TRANSPORTADORAS_CONFIGURAVEIS
        card, layout = self._settings_card(
            "TRANSPORTADORAS",
            "Habilite transportadoras e mantenha as UFs atendidas para cotação.",
        )
        cfg = self._sessao.config if isinstance(self._sessao.config, dict) else {}
        transp_cfg = cfg.get("transportadoras", {}) or {}
        rows = QVBoxLayout()
        rows.setSpacing(8)
        for nome in sorted(TRANSPORTADORAS_CONFIGURAVEIS):
            tcfg = transp_cfg.get(nome, {}) or {}
            row_card = QFrame()
            row_card.setObjectName("SettingsRowCard")
            row = QVBoxLayout(row_card)
            row.setContentsMargins(10, 8, 10, 9)
            row.setSpacing(8)
            top = QHBoxLayout()
            top.setSpacing(8)
            name = QLabel(nome.upper())
            name.setObjectName("SettingsCarrierName")
            cb_hab = QCheckBox("Ativa")
            cb_hab.setChecked(bool(tcfg.get("habilitado", False)))
            self._cfg_hab_checks[nome] = cb_hab
            status_text, status_obj = self._transportadora_status_text(nome, tcfg)
            status = QLabel(status_text)
            status.setObjectName(status_obj)
            top.addWidget(name, 1)
            top.addWidget(status)
            top.addWidget(cb_hab)
            row.addLayout(top)

            ufs_atuais = tcfg.get("ufs_atendidas", [])
            if isinstance(ufs_atuais, str):
                ufs_atuais = [u.strip().upper() for u in ufs_atuais.split(",") if u.strip()]
            else:
                ufs_atuais = [u.upper() for u in (ufs_atuais or [])]
            uf_grid = QGridLayout()
            uf_grid.setHorizontalSpacing(3)
            uf_grid.setVerticalSpacing(2)
            cbs: dict = {}
            for i, uf in enumerate(TODAS_UFS):
                cb = QCheckBox(uf)
                cb.setObjectName("UfChip")
                cb.setChecked(uf in ufs_atuais)
                uf_grid.addWidget(cb, i // 9, i % 9)
                cbs[uf] = cb
            self._cfg_ufs_cbs[nome] = cbs
            row.addLayout(uf_grid)
            quick = QHBoxLayout()
            quick.addStretch(1)
            btn_all = QPushButton("Todas")
            btn_all.setObjectName("MiniButton")
            btn_none = QPushButton("Nenhuma")
            btn_none.setObjectName("MiniButton")
            btn_all.clicked.connect(lambda _, c=cbs: [v.setChecked(True) for v in c.values()])
            btn_none.clicked.connect(lambda _, c=cbs: [v.setChecked(False) for v in c.values()])
            quick.addWidget(btn_all)
            quick.addWidget(btn_none)
            row.addLayout(quick)
            rows.addWidget(row_card)
        layout.addLayout(rows)
        btn_salvar_ufs = QPushButton("Salvar transportadoras")
        btn_salvar_ufs.clicked.connect(self._salvar_ufs_embutido)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(btn_salvar_ufs)
        layout.addLayout(footer)
        return card

    def _build_card_credenciais_inline(self) -> QFrame:
        from romaneio_app import CAMPOS_CREDENCIAIS
        card, layout = self._settings_card(
            "CREDENCIAIS",
            "Acessos por transportadora. As senhas ficam ocultas na interface.",
        )
        cfg = self._sessao.config if isinstance(self._sessao.config, dict) else {}
        transp_cfg = cfg.get("transportadoras", {}) or {}
        rows = QVBoxLayout()
        rows.setSpacing(10)
        for nome in sorted(CAMPOS_CREDENCIAIS):
            campos = CAMPOS_CREDENCIAIS[nome]
            tcfg = transp_cfg.get(nome, {}) or {}
            row_card = QFrame()
            row_card.setObjectName("SettingsRowCard")
            row = QVBoxLayout(row_card)
            row.setContentsMargins(12, 10, 12, 11)
            row.setSpacing(9)

            top = QHBoxLayout()
            top.setSpacing(8)
            name = QLabel(nome.upper())
            name.setObjectName("SettingsCarrierName")
            status_text, status_obj = self._transportadora_status_text(nome, tcfg)
            status = QLabel(status_text)
            status.setObjectName(status_obj)
            top.addWidget(name, 1)
            top.addWidget(status)
            row.addLayout(top)

            fields: dict = {}
            form = QGridLayout()
            form.setHorizontalSpacing(10)
            form.setVerticalSpacing(8)
            for i, (chave, label, eh_senha) in enumerate(campos):
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
                fields[chave] = le
                cell = QVBoxLayout()
                cell.setSpacing(3)
                mini = QLabel(label)
                mini.setObjectName("SettingsFieldLabel")
                cell.addWidget(mini)
                cell.addWidget(le)
                form.addLayout(cell, i // 2, i % 2)
            form.setColumnStretch(0, 1)
            form.setColumnStretch(1, 1)
            row.addLayout(form)

            ultima = QLabel(f"Última verificação: {tcfg.get('ultima_verificacao', 'Nunca') or 'Nunca'}")
            ultima.setObjectName("SettingsMiniLabel")
            row.addWidget(ultima)

            warning = QLabel("")
            warning.setObjectName("ConfigWarning")
            warning.setWordWrap(True)
            self._cfg_cred_warnings[nome] = warning
            row.addWidget(warning)

            self._cfg_cred_fields[nome] = fields
            cb_hab = self._cfg_hab_checks.get(nome)
            if cb_hab is not None:
                cb_hab.toggled.connect(lambda _checked, n=nome: self._atualizar_aviso_credencial_embutido(n))
            for le in fields.values():
                le.textChanged.connect(lambda _text, n=nome: self._atualizar_aviso_credencial_embutido(n))
            self._atualizar_aviso_credencial_embutido(nome)
            rows.addWidget(row_card)
        layout.addLayout(rows)
        btn_salvar_cred = QPushButton("Salvar credenciais")
        btn_salvar_cred.clicked.connect(self._salvar_credenciais_embutido)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(btn_salvar_cred)
        layout.addLayout(footer)
        return card

    def _build_tab_empresa_inline(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._build_card_empresa_inline())
        return wrapper

    def _build_tab_ufs_inline(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._build_card_transportadoras_inline())
        return wrapper

    def _build_tab_credenciais_inline(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._build_card_credenciais_inline())
        return wrapper

    def _set_theme_mode_from_settings(self, mode: str) -> None:
        if mode not in ("claro", "escuro", "sistema"):
            return
        self._theme_mode = mode
        for key, btn in getattr(self, "_cfg_theme_buttons", {}).items():
            btn.setChecked(key == mode)
            btn.setObjectName("ThemeOptionActive" if key == mode else "ThemeOption")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        cfg = self._sessao.config if isinstance(self._sessao.config, dict) else {}
        fb = cfg.setdefault("fretio", {})
        fb["ui_tema"] = mode
        _escrever_config_toml(cfg, self._config_path)
        self._apply_style()

    def _salvar_ufs_embutido(self):
        cfg = self._sessao.config if isinstance(self._sessao.config, dict) else {}
        transp_cfg = cfg.setdefault("transportadoras", {})
        for nome, cbs in self._cfg_ufs_cbs.items():
            tcfg = transp_cfg.setdefault(nome, {})
            tcfg["ufs_atendidas"] = [uf for uf, cb in cbs.items() if cb.isChecked()]
            cb_hab = self._cfg_hab_checks.get(nome)
            if cb_hab is not None:
                tcfg["habilitado"] = cb_hab.isChecked()
        _escrever_config_toml(cfg, self._config_path)
        self.label_info.setText("Transportadoras salvas.")

    def _config_credencial_embutida_atual(self, nome: str) -> dict[str, Any]:
        cfg = self._sessao.config if isinstance(self._sessao.config, dict) else {}
        transp_cfg = cfg.get("transportadoras", {}) or {}
        tcfg = dict(transp_cfg.get(nome, {}) or {})
        cb = self._cfg_hab_checks.get(nome)
        if cb is not None:
            tcfg["habilitado"] = cb.isChecked()
        for chave, le in self._cfg_cred_fields.get(nome, {}).items():
            tcfg[chave] = le.text().strip()
        return tcfg

    def _atualizar_aviso_credencial_embutido(self, nome: str) -> None:
        label = self._cfg_cred_warnings.get(nome)
        if label is None:
            return
        validation = validate_provider_minimum_config(nome, self._config_credencial_embutida_atual(nome))
        label.setVisible(not validation.valid)
        label.setText(validation.user_message if not validation.valid else "")

    def _validar_credenciais_embutidas_antes_de_salvar(self) -> list[str]:
        erros: list[str] = []
        for nome in sorted(self._cfg_hab_checks):
            validation = validate_provider_minimum_config(nome, self._config_credencial_embutida_atual(nome))
            if not validation.valid:
                erros.append(f"- {nome.upper()}: {validation.user_message}")
        return erros

    def _salvar_credenciais_embutido(self):
        erros = self._validar_credenciais_embutidas_antes_de_salvar()
        if erros:
            QMessageBox.warning(
                self,
                "Configuração incompleta",
                "Preencha os campos obrigatórios das transportadoras habilitadas antes de salvar:\n\n"
                + "\n".join(erros),
            )
            return
        cfg = self._sessao.config if isinstance(self._sessao.config, dict) else {}
        transp_cfg = cfg.setdefault("transportadoras", {})
        cred_changed = False
        for nome, cb in self._cfg_hab_checks.items():
            tcfg = transp_cfg.setdefault(nome, {})
            tcfg["habilitado"] = cb.isChecked()
        for nome, fields in self._cfg_cred_fields.items():
            tcfg = transp_cfg.setdefault(nome, {})
            for chave, le in fields.items():
                novo = le.text().strip()
                if str(tcfg.get(chave, "") or "") != novo:
                    cred_changed = True
                tcfg[chave] = novo
        _escrever_config_toml(cfg, self._config_path)
        if cred_changed:
            self._reiniciar_sessao()
        self.label_info.setText("Credenciais salvas.")

    def _on_toggle_tema(self, dark: bool) -> None:
        self._theme_mode = "escuro" if dark else "claro"
        for key, btn in getattr(self, "_cfg_theme_buttons", {}).items():
            btn.setChecked(key == self._theme_mode)
            btn.setObjectName("ThemeOptionActive" if key == self._theme_mode else "ThemeOption")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        cfg = self._sessao.config if isinstance(self._sessao.config, dict) else {}
        fb = cfg.setdefault("fretio", {})
        fb["ui_tema"] = self._theme_mode
        _escrever_config_toml(cfg, self._config_path)
        self._apply_style()

    def _on_trocar_tema(self):
        dark = self._usar_tema_escuro()
        self._on_toggle_tema(dark)

    def _salvar_config_embutido(self):
        cfg = self._sessao.config if isinstance(self._sessao.config, dict) else {}
        rom = cfg.setdefault("romaneio", {})
        fb = cfg.setdefault("fretio", {})
        rom["cep_origem"] = self._cfg_cep_origem.text().strip()
        rom["cnpj_pagador_padrao"] = self._cfg_cnpj_pagador_padrao.text().strip()
        try:
            fb["max_paralelo"] = max(1, min(7, int(self._cfg_paralelo.text().strip() or "3")))
        except ValueError:
            fb["max_paralelo"] = 3
        _escrever_config_toml(cfg, self._config_path)
        novo_nome = re.sub(r'[<>:"/\\|?*]', '_', self._cfg_nome_empresa.text().strip())
        if novo_nome and novo_nome != self.empresa_nome:
            if not _renomear_pasta_empresa(self.empresa_nome, novo_nome):
                QMessageBox.warning(
                    self,
                    "Erro",
                    f"Não foi possível renomear a empresa para '{novo_nome}'.\n"
                    "Verifique se já existe outra empresa com esse nome.",
                )
                return
            self._proxima_empresa = novo_nome
            self.close()
            return
        self.label_info.setText("Configurações salvas com sucesso.")

    def _trocar_empresa_embutido(self):
        from romaneio_app import EmpresaSelectorDialog
        dlg = EmpresaSelectorDialog(self, dark=self._usar_tema_escuro())
        if dlg.exec() == QDialog.Accepted and dlg.empresa_selecionada:
            self._proxima_empresa = dlg.empresa_selecionada
            self.close()

    def _abrir_configuracoes_completas(self):
        from romaneio_app import ConfiguracoesDialog
        dlg = ConfiguracoesDialog(
            config=self._sessao.config,
            config_path=self._config_path,
            empresa_nome=self.empresa_nome,
            parent=self,
        )
        if dlg.exec() == QDialog.Accepted:
            if dlg.empresa_trocada:
                self._proxima_empresa = dlg.empresa_trocada
                self.close()
                return
            if dlg._credenciais_mudaram:
                self._reiniciar_sessao()
            self.label_info.setText("Configurações salvas com sucesso.")

    def _abrir_cmdk(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Buscar comando")
        dlg.setMinimumSize(460, 360)
        dlg.setStyleSheet(self.styleSheet())
        layout = QVBoxLayout(dlg)
        search = QLineEdit()
        search.setPlaceholderText("Digite para buscar...")
        search.setObjectName("InputField")
        layout.addWidget(search)
        lista = QListWidget()
        layout.addWidget(lista, 1)
        items = [
            ("Dashboard", lambda: self._show_page(0)),
            ("Romaneio", lambda: self._show_page(1)),
            ("Cotação", lambda: self._show_page(2)),
            ("Fornecedores", lambda: self._show_page(3)),
            ("Rastreio", lambda: self._show_page(4)),
            ("Configurações", lambda: self._show_page(5)),
        ]

        def preencher(q: str = ""):
            lista.clear()
            q = (q or "").strip().lower()
            for label, _ in items:
                if not q or q in label.lower():
                    lista.addItem(label)

        def executar():
            cur = lista.currentItem()
            if not cur:
                return
            texto = cur.text()
            for label, fn in items:
                if label == texto:
                    fn()
                    dlg.accept()
                    break

        preencher()
        search.textChanged.connect(preencher)
        lista.itemDoubleClicked.connect(lambda *_: executar())
        search.returnPressed.connect(executar)
        dlg.exec()
