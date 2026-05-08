#!/usr/bin/env python3
"""
Romaneio - Interface PySide6
"""

import os
import sys
import re
import concurrent.futures
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import (
    QFont,
    QIcon,
    QPixmap,
    QShortcut,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QPlainTextEdit,
    QFileDialog,
    QMessageBox,
    QFrame,
    QTabWidget,
    QDialog,
    QScrollArea,
    QGroupBox,
    QLineEdit,
    QGridLayout,
    QFormLayout,
    QListWidget,
    QInputDialog,
    QCheckBox,
    QStackedWidget,
)

import asyncio
import threading

from company_config import (
    TODAS_UFS,
    _DEFAULT_GITHUB_REPO,
    _DEFAULT_LICENSE_URL,
    _criar_config_empresa_vazia,
    _empresa_config_path,
    _empresas_dir,
    _escrever_config_toml,
    _fretio_appdata_dir,
    _garantir_defaults_fretio,
    _ler_ultima_empresa,
    _listar_empresas,
    _migrar_config_se_necessario,
    _renomear_pasta_empresa,
    _salvar_ultima_empresa,
    _toml_valor,
    _ultima_empresa_path,
)


def _add_fretio_src_to_path() -> None:
    src = Path(__file__).resolve().parent / "fretio" / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_add_fretio_src_to_path()

from extrator_pedidos import ExtratorPedidos
from cotacao_transportadoras import (
    cotar_transportadoras_romaneio_colado,
    formatar_resultados_cotacao,
    setup_global_exception_handler,
    TransportadoraSession,
    ResultadoCotacao,
)
from updater import check_for_update, apply_update, get_repo_from_config, needs_restart, restart_app
from license import get_saved_license, save_license, validate_license, get_machine_id, LicenseStatus
from error_reporter import install_global_hooks, report_error, report_error_message, configure as _er_configure
from extrator_nfe import extrair_arquivo as extrair_nfe_arquivo, NotaFiscal, identificar_transportadora, formatar_nota_resumo, parsear_info_complementar
from rastreamento import rastrear_multiplas, ResultadoRastreio, obter_link_rastreio
from ui_components import (
    CarrierDot,
    NAV_ICONS,
    NavItem,
    ToggleWidget,
    load_app_fonts,
    svg_icon,
)
from ui.events import (
    CotacaoProgressEvent,
    LoginStatusEvent,
    RastreioFinishedEvent,
    RastreioResultEvent,
    StatusUpdateEvent,
    UpdateFinishedEvent,
    UpdateResultEvent,
)
from ui.formatting import (
    _apply_cep_mask,
    _apply_cnpj_mask,
    _apply_currency_mask,
    _apply_decimal_mask,
)
from ui.widgets import IndeterminateBar


def _resource_path(relative_path: str) -> Path:
    base = getattr(sys, '_MEIPASS', None)
    if base:
        return Path(base) / relative_path
    return Path(__file__).resolve().parent / relative_path


def _carregar_versao_app() -> str:
    candidatos = [
        _resource_path("version.txt"),
        Path(__file__).resolve().parent / "version.txt",
    ]
    for caminho in candidatos:
        try:
            if caminho.exists():
                versao = caminho.read_text(encoding="utf-8").strip()
                if re.match(r"^\d+\.\d+$", versao):
                    return versao
        except Exception:
            pass
    return "1.0"


def _show_startup_text_input(title: str, label: str, text: str = "") -> tuple[str, bool]:
    dialog = QInputDialog()
    dialog.setInputMode(QInputDialog.TextInput)
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setTextValue(text)
    dialog.setWindowModality(Qt.ApplicationModal)
    dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
    dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    accepted = dialog.exec() == QDialog.Accepted
    return dialog.textValue(), accepted


def _show_startup_message(icon: QMessageBox.Icon, title: str, text: str) -> int:
    dialog = QMessageBox(icon, title, text, QMessageBox.Ok)
    dialog.setWindowModality(Qt.ApplicationModal)
    dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
    dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog.exec()


# ---------------------------------------------------------------------------
#  Constantes e utilitários — gestão de empresas e configurações
# ---------------------------------------------------------------------------

CAMPOS_CREDENCIAIS: dict[str, list[tuple[str, str, bool]]] = {
    "braspress": [("cnpj", "CNPJ", False), ("senha", "Senha", True)],
    "bauer": [
        ("cnpj_pagador", "CNPJ Pagador", False),
        ("cnpj_remetente", "CNPJ Remetente", False),
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
}


def _migrate_appdata_fretebot_to_fretio() -> None:
    """Migra %APPDATA%\\FreteBot → %APPDATA%\\Fretio e remove o diretório antigo.

    Suporta dois casos:
    1. Fretio ainda não existe → move o diretório inteiro.
    2. Fretio já existe (criado em startup anterior) → faz merge não destrutivo
       e preserva credenciais de reporte (error_gist_id/error_report_token)
       quando presentes no legado e ausentes no destino.
    """
    appdata = os.getenv("APPDATA")
    if not appdata:
        return
    old_dir = Path(appdata) / "FreteBot"
    new_dir = Path(appdata) / "Fretio"
    if not old_dir.exists():
        return

    import shutil

    if not new_dir.exists():
        # Caso 1: diretório novo não existe → mover tudo de uma vez
        try:
            shutil.move(str(old_dir), str(new_dir))
        except Exception:
            pass
        return

    def _load_toml(path: Path) -> dict[str, Any]:
        try:
            raw = path.read_text(encoding="utf-8-sig")
        except Exception:
            return {}
        data = None
        try:
            import tomllib  # type: ignore[import]
            data = tomllib.loads(raw)
        except Exception:
            pass
        if data is None:
            try:
                import toml  # type: ignore[import-untyped]
                data = toml.loads(raw)
            except Exception:
                pass
        if data is None:
            try:
                import tomli as _tomli  # type: ignore[import-not-found]
                data = _tomli.loads(raw)
            except Exception:
                pass
        return data if isinstance(data, dict) else {}

    def _backfill_report_credentials(src_cfg: Path, dst_cfg: Path) -> None:
        """Preenche defaults e credenciais ausentes no destino, sem sobrescrever valores já definidos."""
        try:
            src_data = _load_toml(src_cfg)
            dst_data = _load_toml(dst_cfg)

            _garantir_defaults_fretio(src_data)
            dst_fb = dst_data.get("fretio", {}) if isinstance(dst_data.get("fretio", {}), dict) else {}
            if _garantir_defaults_fretio(dst_data):
                dst_fb = dst_data.get("fretio", {}) if isinstance(dst_data.get("fretio", {}), dict) else {}

            changed = False
            src_fb = src_data.get("fretio", {}) if isinstance(src_data.get("fretio", {}), dict) else {}
            for key in ("github_repo", "license_url", "error_gist_id", "error_report_token"):
                src_val = str(src_fb.get(key, "") or "").strip()
                dst_val = str(dst_fb.get(key, "") or "").strip()
                if src_val and not dst_val:
                    if not isinstance(dst_data.get("fretio"), dict):
                        dst_data["fretio"] = {}
                    dst_data["fretio"][key] = src_val
                    changed = True

            if changed:
                _escrever_config_toml(dst_data, dst_cfg)
        except Exception:
            pass

    def _merge_missing(src: Path, dst: Path) -> None:
        if src.is_dir():
            if dst.exists() and not dst.is_dir():
                return
            dst.mkdir(parents=True, exist_ok=True)
            for child in sorted(src.iterdir(), key=lambda p: p.name.lower()):
                _merge_missing(child, dst / child.name)
            return

        if not src.is_file():
            return

        if not dst.exists():
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
            except Exception:
                pass
            return

        if src.name.lower() == "config.toml":
            _backfill_report_credentials(src, dst)

    # Caso 2: Fretio já existe → merge não destrutivo do conteúdo legado.
    try:
        _merge_missing(old_dir, new_dir)
    except Exception:
        pass

    # Remover diretório FreteBot após migração para evitar conflitos futuros
    try:
        shutil.rmtree(str(old_dir), ignore_errors=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
#  Diálogo: Seleção de Empresa
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
#  Diálogo: Configurações (UFs, Credenciais, Empresa)
# ---------------------------------------------------------------------------

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
        tabs.addTab(self._tab_ufs(), "UFs Atendidas")
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
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        vbox = QVBoxLayout(content)
        vbox.setSpacing(10)
        transp_cfg = self.config.get("transportadoras", {}) or {}
        for nome in sorted(["braspress", "bauer", "trd", "agex", "eucatur", "rodonaves", "alfa", "coopex"]):
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
        wl = QVBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(scroll)
        return wrapper

    # --- aba Credenciais ---
    def _tab_credenciais(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
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
            vbox.addWidget(group)
        vbox.addStretch(1)
        scroll.setWidget(content)
        wrapper = QWidget()
        wl = QVBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(scroll)
        return wrapper

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
        QMessageBox.information(self, "Sucesso", "Configurações salvas!")
        self.accept()

    def _apply_style(self):
        fb_cfg = self.config.get("fretio", {}) or {}
        dark = str(fb_cfg.get("ui_tema", "escuro")).lower() == "escuro"
        if dark:
            c_bg = "#0d1117"; c_panel = "#161b22"; c_panel2 = "#1c232c"; c_panel3 = "#21282f"
            c_border = "#262f3a"; c_ink = "#e6edf3"; c_muted = "#768390"
            c_ink2 = "#adbac7"; c_faint = "#444c56"; c_accent = "#00b4d8"
        else:
            c_bg = "#f0f4f8"; c_panel = "#ffffff"; c_panel2 = "#f8fafc"; c_panel3 = "#f1f5f9"
            c_border = "#e2e8f0"; c_ink = "#0f172a"; c_muted = "#64748b"
            c_ink2 = "#334155"; c_faint = "#94a3b8"; c_accent = "#0077b6"
        self.setStyleSheet(f"""
            QDialog {{ background: {c_bg}; color: {c_ink}; }}
            QLabel {{ color: {c_ink}; }}
            #TitleLabel {{ font-size: 18px; font-weight: 700; color: {c_ink}; }}
            #SettingsGroup {{ border: 1px solid {c_border}; border-radius: 8px;
                             padding: 12px 10px 10px 10px; margin-top: 6px; background: {c_panel}; }}
            QGroupBox#SettingsGroup {{ border: 1px solid {c_border}; background: {c_panel}; border-radius: 8px; margin-top: 0px; }}
            QGroupBox#SettingsGroup::title {{ subcontrol-origin: margin; height: 0px; width: 0px; padding: 0px; color: transparent; }}
            #TranspTitle {{ font-size: 17px; font-weight: 700; color: {c_ink};
                           padding: 6px 0 8px 0; }}
            #CredField {{ border: 1px solid {c_border}; border-radius: 6px; padding: 5px 8px;
                         background: {c_panel2}; color: {c_ink}; }}
            QTabWidget#MainTabs::pane {{ border: 1px solid {c_border}; border-radius: 10px;
                                        background: {c_panel}; }}
            QTabBar::tab {{ background: {c_panel2}; color: {c_muted}; border: 1px solid {c_border};
                           padding: 7px 12px; margin-right: 4px; border-top-left-radius: 8px;
                           border-top-right-radius: 8px; }}
            QTabBar::tab:selected {{ background: {c_panel}; color: {c_ink};
                                     border-bottom-color: {c_panel}; }}
            QPushButton {{ background: {c_accent}; color: #fff; border: none; border-radius: 8px;
                          padding: 9px 16px; font-weight: 600; }}
            QPushButton#SecondaryButton {{ background: {c_panel2}; color: {c_ink2};
                                          border: 1px solid {c_border}; }}
            QPushButton#SecondaryButton:hover {{ background: {c_panel3}; color: {c_ink}; }}
            QPushButton#MiniButton {{ background: {c_panel2}; color: {c_ink2};
                                     border: 1px solid {c_border}; border-radius: 4px;
                                     padding: 2px 8px; font-size: 11px; }}
            QPushButton#MiniButton:hover {{ background: {c_panel3}; }}
            QCheckBox {{ color: {c_ink}; spacing: 4px; }}
            QLineEdit {{ color: {c_ink}; background: {c_panel2}; border: 1px solid {c_border};
                        border-radius: 6px; padding: 5px 8px; }}
            QScrollArea {{ background: transparent; border: none; }}
            QGroupBox {{ border: none; }}
        """)


class _AsyncLoopThread:
    def __init__(self, *, name: str = "RomaneioAsyncLoop"):
        self._loop = asyncio.new_event_loop()
        self._state_lock = threading.Lock()
        self._started = threading.Event()
        self._closed = threading.Event()
        self._closing = False
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()
        self._started.wait(timeout=2)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._started.set()
        try:
            self._loop.run_forever()
        finally:
            pending = [task for task in asyncio.all_tasks(self._loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                try:
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception:
                pass
            try:
                self._loop.run_until_complete(self._loop.shutdown_default_executor())
            except Exception:
                pass
            try:
                self._loop.close()
            except Exception:
                pass
            self._closed.set()

    def submit(self, coro_factory: Callable[[], Any]) -> concurrent.futures.Future | None:
        with self._state_lock:
            if self._closing or self._closed.is_set() or self._loop.is_closed():
                return None
            loop = self._loop
        try:
            return asyncio.run_coroutine_threadsafe(coro_factory(), loop)
        except RuntimeError:
            return None

    async def _cleanup_and_cancel(
        self,
        cleanup_coro_factory: Callable[[], Any] | None,
    ) -> None:
        if cleanup_coro_factory is not None:
            try:
                await cleanup_coro_factory()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
        current = asyncio.current_task()
        pending = [
            task for task in asyncio.all_tasks()
            if task is not current and not task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def shutdown(
        self,
        *,
        cleanup_coro_factory: Callable[[], Any] | None = None,
        timeout: float = 3.0,
    ) -> None:
        with self._state_lock:
            if self._closing:
                thread = self._thread
                loop = self._loop
            else:
                self._closing = True
                thread = self._thread
                loop = self._loop
        if self._closed.is_set():
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._cleanup_and_cancel(cleanup_coro_factory),
                loop,
            )
            future.result(timeout=timeout)
        except Exception:
            pass
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass
        if thread.is_alive():
            thread.join(timeout)


class RomaneioWindow(QMainWindow):
    def __init__(self, empresa_nome: str = "default"):
        super().__init__()
        self.empresa_nome = empresa_nome
        self._config_path = _empresa_config_path(empresa_nome)
        _er_configure(self._config_path)
        self._proxima_empresa: str | None = None
        self.extrator = ExtratorPedidos()
        self.pedidos = []
        self.html_original = ''
        self._romaneio_colado = ""
        self._modo_cotacao = "pdf"
        self._sessao = TransportadoraSession(config_path=self._config_path)
        self._async_loop = _AsyncLoopThread()
        self._session_task_lock = threading.Lock()
        self._async_futures: set[concurrent.futures.Future] = set()
        self._async_futures_lock = threading.Lock()
        self._shutdown_started = threading.Event()
        self._cotacao_total = 0
        self._cotacao_concluidas = 0
        self._cep_origem_override = ""
        self._romaneios_processados: list[dict] = []
        self._last_cotacao_results: list = []
        self.app_version = _carregar_versao_app()
        self.app_name = f"Fretio {self.app_version} \u2014 {empresa_nome}"
        self._theme_mode = str((self._sessao.config.get("fretio", {}) or {}).get("ui_tema", "sistema")).lower()
        if self._theme_mode not in ("sistema", "claro", "escuro"):
            self._theme_mode = "sistema"

        self.setWindowTitle(self.app_name)
        icon_path = _resource_path("assets/romaneio.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.setMinimumSize(980, 620)
        self._build_ui()

    def _usar_tema_escuro(self) -> bool:
        if self._theme_mode == "escuro":
            return True
        if self._theme_mode == "claro":
            return False
        try:
            window_color = QApplication.palette().color(QApplication.palette().Window)
            return window_color.value() < 128
        except Exception:
            return True

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._page_titles: dict[int, str] = {
            0: "Dashboard",
            1: "Romaneio",
            2: "Cotação",
            3: "Fornecedores",
            4: "Rastreio",
            5: "Configurações",
        }
        self._nav_buttons: dict[int, NavItem] = {}

        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        # ── Logo row ──────────────────────────────────────────────────────
        logo_row = QWidget()
        logo_row.setObjectName("SidebarLogoRow")
        logo_layout = QHBoxLayout(logo_row)
        logo_layout.setContentsMargins(14, 14, 14, 14)
        logo_layout.setSpacing(8)
        icon_path = _resource_path("assets/romaneio.png")
        if icon_path.exists():
            logo_pix = QPixmap(str(icon_path)).scaled(
                24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            logo_img = QLabel()
            logo_img.setPixmap(logo_pix)
            logo_img.setFixedSize(24, 24)
            logo_layout.addWidget(logo_img)
        brand = QLabel("fretio")
        brand.setObjectName("BrandLabel")
        logo_layout.addWidget(brand)
        logo_layout.addStretch(1)
        sidebar_layout.addWidget(logo_row)

        sep_logo = QFrame()
        sep_logo.setFrameShape(QFrame.HLine)
        sep_logo.setObjectName("SidebarSep")
        sidebar_layout.addWidget(sep_logo)

        # ── Empresa chip ─────────────────────────────────────────────────
        chip_wrap = QWidget()
        chip_wrap.setObjectName("ChipWrap")
        chip_wrap_layout = QHBoxLayout(chip_wrap)
        chip_wrap_layout.setContentsMargins(10, 10, 10, 10)
        chip = QFrame()
        chip.setObjectName("EmpresaChip")
        chip_layout = QHBoxLayout(chip)
        chip_layout.setContentsMargins(8, 5, 8, 5)
        chip_layout.setSpacing(8)
        avatar = QLabel(self.empresa_nome[0].upper() if self.empresa_nome else "?")
        avatar.setObjectName("EmpresaAvatar")
        avatar.setFixedSize(22, 22)
        avatar.setAlignment(Qt.AlignCenter)
        chip_layout.addWidget(avatar)
        empresa_name_lbl = QLabel(self.empresa_nome)
        empresa_name_lbl.setObjectName("EmpresaName")
        empresa_name_lbl.setMaximumWidth(120)
        chip_layout.addWidget(empresa_name_lbl, 1)
        chip_wrap_layout.addWidget(chip)
        sidebar_layout.addWidget(chip_wrap)

        sep_chip = QFrame()
        sep_chip.setFrameShape(QFrame.HLine)
        sep_chip.setObjectName("SidebarSep")
        sidebar_layout.addWidget(sep_chip)

        # ── Nav items ─────────────────────────────────────────────────────
        nav_wrap = QWidget()
        nav_wrap_layout = QVBoxLayout(nav_wrap)
        nav_wrap_layout.setContentsMargins(6, 8, 6, 8)
        nav_wrap_layout.setSpacing(1)

        self._nav_items_list: list[NavItem] = []
        _nav_defs = [
            (0, 'radar', 'Dashboard',    '', lambda: self._show_page(0)),
            (1, 'doc',   'Romaneio',     '', lambda: self._show_page(1)),
            (2, 'money', 'Cotação',      '', lambda: self._show_page(2)),
            (3, 'box',   'Fornecedores', '', lambda: self._show_page(3)),
            (4, 'truck', 'Rastreio',     '', lambda: self._show_page(4)),
        ]
        for idx, icon_name, label, kbd, cb in _nav_defs:
            item = NavItem(icon_name, label, kbd)
            item.clicked.connect(cb)
            nav_wrap_layout.addWidget(item)
            self._nav_buttons[idx] = item
            self._nav_items_list.append(item)

        nav_wrap_layout.addStretch(1)
        sidebar_layout.addWidget(nav_wrap, 1)

        sep_nav = QFrame()
        sep_nav.setFrameShape(QFrame.HLine)
        sep_nav.setObjectName("SidebarSep")
        sidebar_layout.addWidget(sep_nav)

        # ── Bottom: theme toggle + settings ───────────────────────────────
        bottom_wrap = QWidget()
        bottom_layout = QVBoxLayout(bottom_wrap)
        bottom_layout.setContentsMargins(6, 6, 6, 6)
        bottom_layout.setSpacing(1)

        # Dark mode toggle row
        dark_now = self._usar_tema_escuro()
        toggle_row_w = QWidget()
        toggle_row_w.setObjectName("ToggleRow")
        toggle_row_w.setCursor(Qt.PointingHandCursor)
        toggle_row_layout = QHBoxLayout(toggle_row_w)
        toggle_row_layout.setContentsMargins(10, 9, 8, 9)
        toggle_row_layout.setSpacing(8)
        self._theme_icon_lbl = QLabel()
        self._theme_icon_lbl.setFixedSize(16, 16)
        toggle_row_layout.addWidget(self._theme_icon_lbl)
        self._theme_mode_lbl = QLabel("Modo escuro" if dark_now else "Modo claro")
        self._theme_mode_lbl.setObjectName("ToggleLabel")
        toggle_row_layout.addWidget(self._theme_mode_lbl, 1)
        self._theme_toggle = ToggleWidget(checked=dark_now)
        self._theme_toggle.toggled.connect(self._on_toggle_tema)
        toggle_row_layout.addWidget(self._theme_toggle)
        toggle_row_w.mousePressEvent = lambda e: (
            self._theme_toggle.mousePressEvent(e)
            if e.button() == Qt.LeftButton else None
        )
        bottom_layout.addWidget(toggle_row_w)

        # Settings NavItem
        settings_item = NavItem('cog', 'Configurações', '')
        settings_item.clicked.connect(lambda: self._show_page(5))
        bottom_layout.addWidget(settings_item)
        self._nav_buttons[5] = settings_item
        self._nav_items_list.append(settings_item)

        sidebar_layout.addWidget(bottom_wrap)
        root.addWidget(self.sidebar, 0)

        content_wrap = QVBoxLayout()
        content_wrap.setContentsMargins(0, 0, 0, 0)
        content_wrap.setSpacing(0)

        topbar = QFrame()
        topbar.setObjectName("TopBar")
        header_layout = QVBoxLayout(topbar)
        header_layout.setContentsMargins(16, 12, 16, 10)
        header_layout.setSpacing(8)

        header_top = QHBoxLayout()
        header_top.setSpacing(10)
        self.page_title_label = QLabel(self._page_titles[0])
        self.page_title_label.setObjectName("TopBarTitle")
        header_top.addWidget(self.page_title_label)
        header_top.addStretch(1)

        # CmdK button with icon + label + kbd badge
        self.btn_cmd = QFrame()
        self.btn_cmd.setObjectName("CmdKBtn")
        self.btn_cmd.setCursor(Qt.PointingHandCursor)
        self.btn_cmd.mousePressEvent = lambda e: self._abrir_cmdk() if e.button() == Qt.LeftButton else None
        cmd_layout = QHBoxLayout(self.btn_cmd)
        cmd_layout.setContentsMargins(10, 5, 10, 5)
        cmd_layout.setSpacing(8)
        self._cmd_icon_lbl = QLabel()
        self._cmd_icon_lbl.setFixedSize(13, 13)
        cmd_layout.addWidget(self._cmd_icon_lbl)
        cmd_text = QLabel("Buscar comando…")
        cmd_text.setObjectName("CmdKText")
        cmd_layout.addWidget(cmd_text)
        cmd_kbd = QLabel("Ctrl+K")
        cmd_kbd.setObjectName("CmdKKbd")
        cmd_layout.addWidget(cmd_kbd)
        header_top.addWidget(self.btn_cmd)

        header_layout.addLayout(header_top)
        self.label_info = QLabel("Nenhum arquivo carregado")
        self.label_info.setObjectName("StatusLabel")
        header_layout.addWidget(self.label_info)

        # --- Painel de status de login das transportadoras (visível só na página Cotação) ---
        self._carrier_status_frame = QFrame()
        self._carrier_status_frame.setObjectName("CarrierStatusFrame")
        carrier_status_layout = QHBoxLayout(self._carrier_status_frame)
        carrier_status_layout.setContentsMargins(0, 2, 0, 0)
        carrier_status_layout.setSpacing(16)
        self._login_status_dots: dict[str, CarrierDot] = {}
        config = self._sessao.config if hasattr(self._sessao, 'config') else {}
        transp_cfg = config.get("transportadoras", {}) if isinstance(config, dict) else {}
        for nome in ("braspress", "bauer", "trd", "agex", "eucatur", "rodonaves", "alfa", "coopex"):
            tcfg = transp_cfg.get(nome, {}) if isinstance(transp_cfg, dict) else {}
            if not tcfg.get("habilitado", False):
                continue
            dot = CarrierDot(nome)
            carrier_status_layout.addWidget(dot)
            self._login_status_dots[nome] = dot
        carrier_status_layout.addStretch(1)
        self._carrier_status_frame.setVisible(False)
        header_layout.addWidget(self._carrier_status_frame)

        self._pre_login_done = False
        self._notas_rastreio: list = []
        self._rastreio_card_widgets: list = []
        self._resultados_rastreio: list = []
        self._rastreio_notas_subset = None
        self._rastreio_card_offset = 0
        self._rastreio_notas_para_thread = None

        content_wrap.addWidget(topbar)

        # --- QStackedWidget (Home + paginas individuais) ---
        self.stack = QStackedWidget()
        self.stack.setObjectName("MainStack")

        # Pagina 0: Dashboard
        home_page = QWidget()
        home_layout = QVBoxLayout(home_page)
        home_layout.setContentsMargins(18, 18, 18, 18)
        home_layout.setSpacing(14)

        # KPI row
        kpi_grid = QGridLayout()
        kpi_grid.setHorizontalSpacing(10)
        kpi_grid.setVerticalSpacing(10)
        _kpi_data = [
            ("ROMANEIOS HOJE", "0",  "nesta sessão",       "KpiValueAccent"),
            ("VOLUME TOTAL",   "0",  "pacotes",             "KpiValue"),
            ("MELHOR FRETE",   "—",  "da última cotação",   "KpiValueGreen"),
            ("TAXA SUCESSO",   "—",  "da última cotação",   "KpiValueAmber"),
        ]
        self._kpi_value_labels: list[QLabel] = []
        self._kpi_sub_labels: list[QLabel] = []
        for idx, (titulo, valor, sub, val_obj) in enumerate(_kpi_data):
            card = QFrame()
            card.setObjectName("Card")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(14, 12, 14, 12)
            cl.setSpacing(4)
            lbl_t = QLabel(titulo)
            lbl_t.setObjectName("KpiLabel")
            lbl_v = QLabel(valor)
            lbl_v.setObjectName(val_obj)
            lbl_s = QLabel(sub)
            lbl_s.setObjectName("KpiSub")
            cl.addWidget(lbl_t)
            cl.addWidget(lbl_v)
            cl.addWidget(lbl_s)
            kpi_grid.addWidget(card, 0, idx)
            self._kpi_value_labels.append(lbl_v)
            self._kpi_sub_labels.append(lbl_s)
        home_layout.addLayout(kpi_grid)

        # Two-column area: recentes (left) + status carriers (right)
        two_col = QHBoxLayout()
        two_col.setSpacing(14)

        # Left: recent romaneios table
        recentes_card = QFrame()
        recentes_card.setObjectName("Card")
        recentes_vlayout = QVBoxLayout(recentes_card)
        recentes_vlayout.setContentsMargins(0, 0, 0, 0)
        recentes_vlayout.setSpacing(0)

        rh = QWidget()
        rh_layout = QHBoxLayout(rh)
        rh_layout.setContentsMargins(14, 10, 14, 10)
        rh_lbl = QLabel("ROMANEIOS RECENTES")
        rh_lbl.setObjectName("SectionLabel")
        rh_link = QLabel("ver todos →")
        rh_link.setObjectName("LinkLabel")
        rh_layout.addWidget(rh_lbl)
        rh_layout.addStretch(1)
        rh_layout.addWidget(rh_link)
        recentes_vlayout.addWidget(rh)

        sep_rh = QFrame(); sep_rh.setFrameShape(QFrame.HLine); sep_rh.setObjectName("SidebarSep")
        recentes_vlayout.addWidget(sep_rh)

        self._recentes_body_widget = QWidget()
        self._recentes_body_layout = QVBoxLayout(self._recentes_body_widget)
        self._recentes_body_layout.setContentsMargins(0, 0, 0, 0)
        self._recentes_body_layout.setSpacing(0)
        recentes_vlayout.addWidget(self._recentes_body_widget)
        recentes_vlayout.addStretch(1)
        two_col.addWidget(recentes_card, 1)

        # Right column (fixed 280px)
        right_col_w = QWidget()
        right_col_w.setFixedWidth(280)
        right_col_layout = QVBoxLayout(right_col_w)
        right_col_layout.setContentsMargins(0, 0, 0, 0)
        right_col_layout.setSpacing(10)

        # Carrier status card
        carr_card = QFrame()
        carr_card.setObjectName("Card")
        carr_vlayout = QVBoxLayout(carr_card)
        carr_vlayout.setContentsMargins(14, 12, 14, 12)
        carr_vlayout.setSpacing(4)
        carr_lbl = QLabel("STATUS CARRIERS")
        carr_lbl.setObjectName("SectionLabel")
        carr_vlayout.addWidget(carr_lbl)

        self._home_carrier_info: dict[str, tuple[QFrame, QLabel]] = {}
        _tag_styles = {"ok": "TagGreen", "fail": "TagRed", "pending": "TagAmber"}
        for nome in ("braspress", "bauer", "trd", "agex", "eucatur", "rodonaves", "alfa", "coopex"):
            _tcfg = transp_cfg.get(nome, {}) if isinstance(transp_cfg, dict) else {}
            if not _tcfg.get("habilitado", False):
                continue
            cr_row = QWidget()
            cr_layout = QHBoxLayout(cr_row)
            cr_layout.setContentsMargins(0, 5, 0, 5)
            cr_layout.setSpacing(8)
            cr_dot = QFrame()
            cr_dot.setFixedSize(6, 6)
            cr_dot.setStyleSheet("border-radius:3px;background:#e3b341;")
            cr_layout.addWidget(cr_dot, 0, Qt.AlignVCenter)
            cr_name = QLabel(nome.capitalize())
            cr_name.setObjectName("CarrierRowName")
            cr_layout.addWidget(cr_name, 1)
            cr_tag = QLabel("aguardando")
            cr_tag.setObjectName("TagAmber")
            cr_layout.addWidget(cr_tag)
            carr_vlayout.addWidget(cr_row)
            self._home_carrier_info[nome] = (cr_dot, cr_tag)

        right_col_layout.addWidget(carr_card)
        right_col_layout.addStretch(1)
        two_col.addWidget(right_col_w)

        home_layout.addLayout(two_col, 1)
        self.stack.addWidget(home_page)  # index 0 = home

        self.btn_select = QPushButton("Selecionar PDF")
        self.btn_select.setObjectName("PrimaryButton")
        self.btn_select.clicked.connect(self._selecionar_arquivo)

        tab_pdf = QWidget()
        tab_pdf_layout = QVBoxLayout(tab_pdf)
        tab_pdf_layout.setContentsMargins(12, 12, 12, 12)
        tab_pdf_layout.setSpacing(8)
        lbl_pdf = QLabel("Selecione um arquivo PDF para extrair e visualizar o romaneio.")
        lbl_pdf.setObjectName("SubtitleLabel")
        card_pdf = QFrame()
        card_pdf.setObjectName("Card")
        card_pdf_layout = QVBoxLayout(card_pdf)
        card_pdf_layout.setContentsMargins(12, 12, 12, 12)
        card_pdf_layout.addWidget(self.btn_select, 0, Qt.AlignLeft)
        self.romaneio_calculado_text = QPlainTextEdit()
        self.romaneio_calculado_text.setObjectName("InputText")
        self.romaneio_calculado_text.setReadOnly(True)
        self.romaneio_calculado_text.setPlaceholderText("O romaneio calculado a partir do PDF aparecerá aqui.")
        self.romaneio_calculado_text.setMinimumHeight(250)
        card_pdf_layout.addWidget(self.romaneio_calculado_text, 1)
        tab_pdf_layout.addWidget(lbl_pdf)
        tab_pdf_layout.addWidget(card_pdf, 1)
        btn_row_pdf = QHBoxLayout()
        btn_row_pdf.setSpacing(8)
        self.btn_copy = QPushButton("Copiar")
        self.btn_copy.clicked.connect(self._copiar_resultado)
        self.btn_clear = QPushButton("Limpar")
        self.btn_clear.setObjectName("SecondaryButton")
        self.btn_clear.clicked.connect(self._limpar)
        btn_row_pdf.addWidget(self.btn_copy)
        btn_row_pdf.addWidget(self.btn_clear)
        btn_row_pdf.addStretch(1)
        tab_pdf_layout.addLayout(btn_row_pdf)

        self.btn_quote_colado = QPushButton("Cotar frete")
        self.btn_quote_colado.setObjectName("SecondaryButton")
        self.btn_quote_colado.setEnabled(False)
        self.btn_quote_colado.clicked.connect(self._cotar_romaneio_colado)

        tab_colado = QWidget()
        tab_colado_layout = QHBoxLayout(tab_colado)
        tab_colado_layout.setContentsMargins(12, 12, 12, 12)
        tab_colado_layout.setSpacing(10)

        # Coluna esquerda — entrada do romaneio
        left_card = QFrame()
        left_card.setObjectName("Card")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)
        lbl_colado = QLabel("Cole aqui seu romaneio:")
        lbl_colado.setObjectName("SubtitleLabel")
        self.romaneio_colado_text = QPlainTextEdit()
        self.romaneio_colado_text.setObjectName("InputText")
        self.romaneio_colado_text.setPlaceholderText(
            "Exemplo:\n"
            "CNPJ/CPF: ...\n"
            "- VOL: ...\n"
            "- CUBAGEM: ... m3\n"
            "- PESO: ... kg\n"
            "- TOTAL: R$ ...\n"
        )
        self.romaneio_colado_text.textChanged.connect(self._atualizar_estado_romaneio_colado)
        left_layout.addWidget(lbl_colado)
        left_layout.addWidget(self.romaneio_colado_text, 1)
        left_layout.addWidget(self.btn_quote_colado, 0, Qt.AlignLeft)
        self.progress_bar = IndeterminateBar()
        self.progress_bar.setVisible(False)
        left_layout.addWidget(self.progress_bar)

        # Coluna direita — resultado da cotação
        right_card = QFrame()
        right_card.setObjectName("Card")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(8)
        result_header = QHBoxLayout()
        lbl_resultado = QLabel("Resultado da cotação:")
        lbl_resultado.setObjectName("SubtitleLabel")
        btn_copiar_result = QPushButton("Copiar")
        btn_copiar_result.setObjectName("MiniButton")
        btn_copiar_result.setFixedWidth(70)
        btn_copiar_result.clicked.connect(lambda: QApplication.clipboard().setText(self.result_text.toPlainText()))
        result_header.addWidget(lbl_resultado)
        result_header.addStretch()
        result_header.addWidget(btn_copiar_result)
        self.result_text = QPlainTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setObjectName("ResultText")
        self.result_text.setPlainText("")
        right_layout.addLayout(result_header)
        right_layout.addWidget(self.result_text, 1)

        tab_colado_layout.addWidget(left_card, 1)
        tab_colado_layout.addWidget(right_card, 1)

        # --- Tab Frete Fornecedores ---
        tab_fornecedor = QWidget()
        tab_forn_layout = QHBoxLayout(tab_fornecedor)
        tab_forn_layout.setContentsMargins(12, 12, 12, 12)
        tab_forn_layout.setSpacing(10)

        forn_left = QFrame()
        forn_left.setObjectName("Card")
        forn_left_layout = QVBoxLayout(forn_left)
        forn_left_layout.setContentsMargins(14, 14, 14, 14)
        forn_left_layout.setSpacing(8)
        lbl_forn = QLabel("Preencha os dados do fornecedor:")
        lbl_forn.setObjectName("SubtitleLabel")
        forn_left_layout.addWidget(lbl_forn)

        forn_form = QGridLayout()
        forn_form.setSpacing(8)
        forn_form.setColumnStretch(1, 1)

        row = 0
        lbl = QLabel("CNPJ:")
        lbl.setObjectName("FornLabel")
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        forn_form.addWidget(lbl, row, 0)
        self._forn_cnpj = QLineEdit()
        self._forn_cnpj.setPlaceholderText("00.000.000/0000-00")
        self._forn_cnpj.setObjectName("InputField")
        _apply_cnpj_mask(self._forn_cnpj)
        forn_form.addWidget(self._forn_cnpj, row, 1)

        row += 1
        lbl = QLabel("CEP:")
        lbl.setObjectName("FornLabel")
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        forn_form.addWidget(lbl, row, 0)
        self._forn_cep = QLineEdit()
        self._forn_cep.setPlaceholderText("00000-000")
        self._forn_cep.setObjectName("InputField")
        _apply_cep_mask(self._forn_cep)
        forn_form.addWidget(self._forn_cep, row, 1)

        row += 1
        lbl = QLabel("Quantidade de volumes:")
        lbl.setObjectName("FornLabel")
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        forn_form.addWidget(lbl, row, 0)
        self._forn_qtd = QLineEdit()
        self._forn_qtd.setPlaceholderText("10")
        self._forn_qtd.setObjectName("InputField")
        forn_form.addWidget(self._forn_qtd, row, 1)

        row += 1
        lbl = QLabel("Tamanho dos volumes:")
        lbl.setObjectName("FornLabel")
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        forn_form.addWidget(lbl, row, 0)
        dim_row = QHBoxLayout()
        dim_row.setSpacing(4)
        self._forn_comp = QLineEdit()
        self._forn_comp.setPlaceholderText("ex: 50")
        self._forn_comp.setObjectName("InputField")
        self._forn_larg = QLineEdit()
        self._forn_larg.setPlaceholderText("ex: 40")
        self._forn_larg.setObjectName("InputField")
        self._forn_alt = QLineEdit()
        self._forn_alt.setPlaceholderText("ex: 30")
        self._forn_alt.setObjectName("InputField")
        dim_row.addWidget(self._forn_alt)
        lbl_cm1 = QLabel("cm")
        lbl_cm1.setObjectName("FornUnit")
        lbl_cm1.setFixedWidth(20)
        dim_row.addWidget(lbl_cm1)
        lbl_x1 = QLabel("\u00d7")
        lbl_x1.setObjectName("FornUnit")
        lbl_x1.setFixedWidth(12)
        lbl_x1.setAlignment(Qt.AlignCenter)
        dim_row.addWidget(lbl_x1)
        dim_row.addWidget(self._forn_larg)
        lbl_cm2 = QLabel("cm")
        lbl_cm2.setObjectName("FornUnit")
        lbl_cm2.setFixedWidth(20)
        dim_row.addWidget(lbl_cm2)
        lbl_x2 = QLabel("\u00d7")
        lbl_x2.setObjectName("FornUnit")
        lbl_x2.setFixedWidth(12)
        lbl_x2.setAlignment(Qt.AlignCenter)
        dim_row.addWidget(lbl_x2)
        dim_row.addWidget(self._forn_comp)
        lbl_cm3 = QLabel("cm")
        lbl_cm3.setObjectName("FornUnit")
        lbl_cm3.setFixedWidth(20)
        dim_row.addWidget(lbl_cm3)
        forn_form.addLayout(dim_row, row, 1)

        row += 1
        lbl = QLabel("Peso por volume:")
        lbl.setObjectName("FornLabel")
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        forn_form.addWidget(lbl, row, 0)
        peso_cx_row = QHBoxLayout()
        peso_cx_row.setSpacing(4)
        self._forn_peso_cx = QLineEdit()
        self._forn_peso_cx.setPlaceholderText("0,000 (opcional se peso total preenchido)")
        self._forn_peso_cx.setObjectName("InputField")
        _apply_decimal_mask(self._forn_peso_cx, decimals=3)
        peso_cx_row.addWidget(self._forn_peso_cx)
        lbl_kg1 = QLabel("kg")
        lbl_kg1.setObjectName("FornUnit")
        lbl_kg1.setFixedWidth(20)
        peso_cx_row.addWidget(lbl_kg1)
        forn_form.addLayout(peso_cx_row, row, 1)

        row += 1
        lbl = QLabel("Peso total:")
        lbl.setObjectName("FornLabel")
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        forn_form.addWidget(lbl, row, 0)
        peso_total_row = QHBoxLayout()
        peso_total_row.setSpacing(4)
        self._forn_peso_total = QLineEdit()
        self._forn_peso_total.setPlaceholderText("0,000")
        self._forn_peso_total.setObjectName("InputField")
        _apply_decimal_mask(self._forn_peso_total, decimals=3)
        peso_total_row.addWidget(self._forn_peso_total)
        lbl_kg2 = QLabel("kg")
        lbl_kg2.setObjectName("FornUnit")
        lbl_kg2.setFixedWidth(20)
        peso_total_row.addWidget(lbl_kg2)
        forn_form.addLayout(peso_total_row, row, 1)

        row += 1
        lbl = QLabel("Valor da mercadoria:")
        lbl.setObjectName("FornLabel")
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        forn_form.addWidget(lbl, row, 0)
        self._forn_valor = QLineEdit()
        self._forn_valor.setPlaceholderText("R$ 0,00")
        self._forn_valor.setObjectName("InputField")
        _apply_currency_mask(self._forn_valor)
        forn_form.addWidget(self._forn_valor, row, 1)

        forn_left_layout.addLayout(forn_form)

        self.btn_cotar_fornecedor = QPushButton("Cotar Frete Fornecedor")
        self.btn_cotar_fornecedor.clicked.connect(self._cotar_frete_fornecedor)
        forn_left_layout.addWidget(self.btn_cotar_fornecedor, 0, Qt.AlignLeft)
        self.forn_progress_bar = IndeterminateBar()
        self.forn_progress_bar.setVisible(False)
        forn_left_layout.addWidget(self.forn_progress_bar)
        forn_left_layout.addStretch(1)

        forn_right = QFrame()
        forn_right.setObjectName("Card")
        forn_right_layout = QVBoxLayout(forn_right)
        forn_right_layout.setContentsMargins(12, 12, 12, 12)
        forn_right_layout.setSpacing(8)
        forn_result_header = QHBoxLayout()
        lbl_forn_result = QLabel("Resultado da cota\u00e7\u00e3o:")
        lbl_forn_result.setObjectName("SubtitleLabel")
        btn_copiar_forn_result = QPushButton("Copiar")
        btn_copiar_forn_result.setObjectName("MiniButton")
        btn_copiar_forn_result.setFixedWidth(70)
        btn_copiar_forn_result.clicked.connect(lambda: QApplication.clipboard().setText(self.forn_result_text.toPlainText()))
        forn_result_header.addWidget(lbl_forn_result)
        forn_result_header.addStretch()
        forn_result_header.addWidget(btn_copiar_forn_result)
        self.forn_result_text = QPlainTextEdit()
        self.forn_result_text.setReadOnly(True)
        self.forn_result_text.setObjectName("ResultText")
        forn_right_layout.addLayout(forn_result_header)
        forn_right_layout.addWidget(self.forn_result_text, 1)

        tab_forn_layout.addWidget(forn_left, 1)
        tab_forn_layout.addWidget(forn_right, 1)

        # --- Tab Rastreio ---
        tab_rastreio = QWidget()
        tab_rastreio_layout = QVBoxLayout(tab_rastreio)
        tab_rastreio_layout.setContentsMargins(12, 12, 12, 12)
        tab_rastreio_layout.setSpacing(10)

        # Barra superior - botoes
        rastreio_top_row = QHBoxLayout()
        rastreio_top_row.setSpacing(8)
        self.btn_select_nfe = QPushButton("Selecionar XML(s)")
        self.btn_select_nfe.setObjectName("PrimaryButton")
        self.btn_select_nfe.clicked.connect(self._selecionar_nfe)
        self.btn_rastrear = QPushButton("Rastrear Entregas")
        self.btn_rastrear.clicked.connect(self._iniciar_rastreamento)
        self.btn_rastrear.setEnabled(False)
        self.btn_limpar_nfe = QPushButton("Limpar")
        self.btn_limpar_nfe.setObjectName("SecondaryButton")
        self.btn_limpar_nfe.clicked.connect(self._limpar_rastreio)
        self.btn_abrir_screenshots = QPushButton("Abrir Pasta de Screenshots")
        self.btn_abrir_screenshots.setObjectName("SecondaryButton")
        self.btn_abrir_screenshots.clicked.connect(self._abrir_pasta_screenshots)
        self.btn_abrir_screenshots.setVisible(False)
        rastreio_top_row.addWidget(self.btn_select_nfe)
        rastreio_top_row.addWidget(self.btn_rastrear)
        rastreio_top_row.addWidget(self.btn_limpar_nfe)
        rastreio_top_row.addStretch(1)
        rastreio_top_row.addWidget(self.btn_abrir_screenshots)
        tab_rastreio_layout.addLayout(rastreio_top_row)

        self.rastreio_progress_bar = IndeterminateBar()
        self.rastreio_progress_bar.setVisible(False)
        tab_rastreio_layout.addWidget(self.rastreio_progress_bar)

        # Area de scroll com cards por NF-e
        self._rastreio_scroll = QScrollArea()
        self._rastreio_scroll.setWidgetResizable(True)
        self._rastreio_scroll.setObjectName("RastreioScroll")
        self._rastreio_scroll_content = QWidget()
        self._rastreio_cards_layout = QVBoxLayout(self._rastreio_scroll_content)
        self._rastreio_cards_layout.setContentsMargins(4, 4, 4, 4)
        self._rastreio_cards_layout.setSpacing(10)
        self._rastreio_cards_layout.addStretch(1)
        self._rastreio_scroll.setWidget(self._rastreio_scroll_content)

        # Placeholder quando nao ha notas
        self._rastreio_placeholder = QLabel(
            "Selecione um ou mais arquivos XML de NF-e para visualizar as informa\u00e7\u00f5es do pedido e rastrear entregas automaticamente."
        )
        self._rastreio_placeholder.setObjectName("SubtitleLabel")
        self._rastreio_placeholder.setAlignment(Qt.AlignCenter)
        self._rastreio_placeholder.setWordWrap(True)
        self._rastreio_cards_layout.insertWidget(0, self._rastreio_placeholder)

        tab_rastreio_layout.addWidget(self._rastreio_scroll, 1)

        page_romaneio = self._wrap_page_with_back("\U0001f4c4 ROMANEIO", tab_pdf)
        self.stack.addWidget(page_romaneio)  # index 1
        page_calcular = self._wrap_page_with_back("\U0001f4b0 CALCULAR FRETE", tab_colado)
        self.stack.addWidget(page_calcular)  # index 2
        page_fornecedor = self._wrap_page_with_back("\U0001f4e6 FRETE FORNECEDORES", tab_fornecedor)
        self.stack.addWidget(page_fornecedor)  # index 3
        page_rastreio = self._wrap_page_with_back("\U0001f69a RASTREIO", tab_rastreio)
        self.stack.addWidget(page_rastreio)  # index 4

        page_config = self._build_config_page()
        self.stack.addWidget(page_config)  # index 5

        footer = QHBoxLayout()
        footer.setSpacing(10)
        lbl_app_name = QLabel(f"Fretio {self.app_version}")
        lbl_app_name.setObjectName("FooterLabel")
        footer.addStretch(1)
        footer.addWidget(lbl_app_name)

        content_wrap.addWidget(self.stack, 1)
        content_wrap.addLayout(footer)
        root.addLayout(content_wrap, 1)

        self._atualizar_dashboard()
        self._apply_style()
        self._show_page(0)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self._abrir_cmdk)

    def _build_config_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PageContent")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self._cfg_ufs_cbs: dict = {}
        self._cfg_hab_checks: dict = {}
        self._cfg_cred_fields: dict = {}
        tabs = QTabWidget()
        tabs.setObjectName("MainTabs")
        tabs.addTab(self._build_tab_empresa_inline(), "Empresa")
        tabs.addTab(self._build_tab_ufs_inline(), "UFs Atendidas")
        tabs.addTab(self._build_tab_credenciais_inline(), "Credenciais")
        layout.addWidget(tabs, 1)
        return page

    def _build_tab_empresa_inline(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        vbox = QVBoxLayout(content)
        vbox.setSpacing(10)
        vbox.setContentsMargins(8, 8, 8, 8)

        card = QFrame()
        card.setObjectName("Card")
        form = QFormLayout(card)
        form.setContentsMargins(14, 14, 14, 14)
        form.setSpacing(8)
        cfg = self._sessao.config if isinstance(self._sessao.config, dict) else {}
        rom_cfg = cfg.get("romaneio", {}) or {}
        fb_cfg = cfg.get("fretio", {}) or {}
        self._cfg_cep_origem = QLineEdit(str(rom_cfg.get("cep_origem", "") or ""))
        self._cfg_cep_origem.setObjectName("InputField")
        self._cfg_paralelo = QLineEdit(str(int(fb_cfg.get("max_paralelo", 3) or 3)))
        self._cfg_paralelo.setObjectName("InputField")
        self._cfg_paralelo.setMaximumWidth(80)
        self._cfg_nome_empresa = QLineEdit(self.empresa_nome)
        self._cfg_nome_empresa.setObjectName("InputField")
        form.addRow("Empresa", self._cfg_nome_empresa)
        form.addRow("CEP origem", self._cfg_cep_origem)
        form.addRow("Cotações paralelas", self._cfg_paralelo)
        actions = QHBoxLayout()
        btn_salvar = QPushButton("Salvar")
        btn_salvar.clicked.connect(self._salvar_config_embutido)
        btn_trocar = QPushButton("Trocar empresa")
        btn_trocar.setObjectName("SecondaryButton")
        btn_trocar.clicked.connect(self._trocar_empresa_embutido)
        actions.addWidget(btn_trocar)
        actions.addStretch(1)
        actions.addWidget(btn_salvar)
        form.addRow(actions)

        vbox.addWidget(card)
        vbox.addStretch(1)
        scroll.setWidget(content)
        wrapper = QWidget()
        wl = QVBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(scroll)
        return wrapper

    def _build_tab_ufs_inline(self) -> QWidget:
        """Aba UFs Atendidas para a página de configurações inline."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        vbox = QVBoxLayout(content)
        vbox.setSpacing(10)
        cfg = self._sessao.config if isinstance(self._sessao.config, dict) else {}
        transp_cfg = cfg.get("transportadoras", {}) or {}
        for nome in sorted(["braspress", "bauer", "trd", "agex", "eucatur", "rodonaves", "alfa", "coopex"]):
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
            lbl_nome = ConfiguracoesDialog._criar_label_transportadora(nome)
            grid.addWidget(lbl_nome, 0, 0, 1, 9)
            cbs: dict = {}
            for i, uf in enumerate(TODAS_UFS):
                cb = QCheckBox(uf)
                cb.setChecked(uf in ufs_atuais)
                grid.addWidget(cb, 1 + i // 9, i % 9)
                cbs[uf] = cb
            self._cfg_ufs_cbs[nome] = cbs
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
        btn_salvar_ufs = QPushButton("Salvar UFs Atendidas")
        btn_salvar_ufs.clicked.connect(self._salvar_ufs_embutido)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(btn_salvar_ufs)
        vbox.addLayout(footer)
        vbox.addStretch(1)
        scroll.setWidget(content)
        wrapper = QWidget()
        wl = QVBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(scroll)
        return wrapper

    def _build_tab_credenciais_inline(self) -> QWidget:
        """Aba Credenciais para a página de configurações inline."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        vbox = QVBoxLayout(content)
        vbox.setSpacing(10)
        cfg = self._sessao.config if isinstance(self._sessao.config, dict) else {}
        transp_cfg = cfg.get("transportadoras", {}) or {}
        for nome in sorted(CAMPOS_CREDENCIAIS):
            campos = CAMPOS_CREDENCIAIS[nome]
            tcfg = transp_cfg.get(nome, {}) or {}
            group = QGroupBox()
            group.setObjectName("SettingsGroup")
            form = QFormLayout(group)
            form.setSpacing(6)
            lbl_nome_cred = ConfiguracoesDialog._criar_label_transportadora(nome)
            form.addRow(lbl_nome_cred)
            cb_hab = QCheckBox("Habilitado")
            cb_hab.setChecked(bool(tcfg.get("habilitado", False)))
            form.addRow("", cb_hab)
            self._cfg_hab_checks[nome] = cb_hab
            fields: dict = {}
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
            self._cfg_cred_fields[nome] = fields
            vbox.addWidget(group)
        btn_salvar_cred = QPushButton("Salvar Credenciais")
        btn_salvar_cred.clicked.connect(self._salvar_credenciais_embutido)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(btn_salvar_cred)
        vbox.addLayout(footer)
        vbox.addStretch(1)
        scroll.setWidget(content)
        wrapper = QWidget()
        wl = QVBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(scroll)
        return wrapper

    def _salvar_ufs_embutido(self):
        cfg = self._sessao.config if isinstance(self._sessao.config, dict) else {}
        transp_cfg = cfg.setdefault("transportadoras", {})
        for nome, cbs in self._cfg_ufs_cbs.items():
            tcfg = transp_cfg.setdefault(nome, {})
            tcfg["ufs_atendidas"] = [uf for uf, cb in cbs.items() if cb.isChecked()]
        _escrever_config_toml(cfg, self._config_path)
        self.label_info.setText("UFs atendidas salvas.")

    def _salvar_credenciais_embutido(self):
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
        dlg = EmpresaSelectorDialog(self, dark=self._usar_tema_escuro())
        if dlg.exec() == QDialog.Accepted and dlg.empresa_selecionada:
            self._proxima_empresa = dlg.empresa_selecionada
            self.close()

    def _abrir_configuracoes_completas(self):
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

    def _apply_style(self):
        dark = self._usar_tema_escuro()
        if dark:
            c_bg = "#0d1117"; c_panel = "#161b22"; c_panel2 = "#1c232c"; c_panel3 = "#21282f"
            c_border = "#262f3a"; c_border_soft = "#1d2530"
            c_ink = "#e6edf3"; c_muted = "#768390"; c_ink2 = "#adbac7"; c_faint = "#444c56"
            c_accent = "#00b4d8"; c_accent2 = "#0a2030"; c_accent_border = "#0d3d55"
            c_green = "#3fb950"; c_green_dim = "#0d2b16"
            c_red = "#f85149"; c_red_dim = "#2b0e0e"
            c_amber = "#e3b341"; c_amber_dim = "#2b2008"
        else:
            c_bg = "#f0f4f8"; c_panel = "#ffffff"; c_panel2 = "#f8fafc"; c_panel3 = "#f1f5f9"
            c_border = "#e2e8f0"; c_border_soft = "#edf2f7"
            c_ink = "#0f172a"; c_muted = "#64748b"; c_ink2 = "#334155"; c_faint = "#94a3b8"
            c_accent = "#0077b6"; c_accent2 = "#e0f2fe"; c_accent_border = "#bae6fd"
            c_green = "#16a34a"; c_green_dim = "#dcfce7"
            c_red = "#dc2626"; c_red_dim = "#fee2e2"
            c_amber = "#d97706"; c_amber_dim = "#fef3c7"

        self.setStyleSheet(f"""
            QMainWindow {{ background: {c_bg}; color: {c_ink}; }}
            #Sidebar {{ background: {c_panel}; border-right: 1px solid {c_border}; min-width: 200px; max-width: 200px; }}
            #BrandLabel {{ font-size: 18px; font-weight: 700; letter-spacing: -0.5px; color: {c_ink}; }}
            #SidebarSep {{ background: {c_border}; border: none; max-height: 1px; }}
            #ChipWrap {{ background: transparent; }}
            #EmpresaChip {{ background: {c_panel2}; border: 1px solid {c_border}; border-radius: 6px; }}
            #EmpresaAvatar {{ background: {c_accent}; color: #fff; font-size: 11px; font-weight: 700; border-radius: 5px; }}
            #EmpresaName {{ font-size: 12px; font-weight: 500; color: {c_ink2}; }}
            #ToggleRow {{ border-radius: 6px; }}
            #ToggleLabel {{ font-size: 13px; color: {c_muted}; }}
            #TopBar {{ background: {c_panel}; border-bottom: 1px solid {c_border}; }}
            #TopBarTitle {{ font-size: 14px; font-weight: 600; color: {c_ink}; }}
            #CmdKBtn {{ background: {c_panel2}; border: 1px solid {c_border}; border-radius: 6px; }}
            #CmdKText {{ font-size: 12px; color: {c_muted}; }}
            #CmdKKbd {{ font-family: 'JetBrains Mono'; font-size: 10px; padding: 1px 5px;
                        background: {c_panel3}; border: 1px solid {c_border}; border-radius: 3px; color: {c_faint}; }}
            #StatusLabel {{ color: {c_muted}; font-size: 12px; }}
            #FooterLabel {{ font-size: 11px; color: {c_muted}; }}
            #Card {{ background: {c_panel}; border: 1px solid {c_border}; border-radius: 8px; }}
            #SubtitleLabel {{ font-size: 12px; color: {c_muted}; }}
            #KpiLabel {{ font-size: 10px; font-weight: 700; letter-spacing: 0.1em; color: {c_muted}; }}
            #KpiValue {{ font-size: 28px; font-weight: 700; color: {c_ink}; letter-spacing: -0.03em; }}
            #KpiValueAccent {{ font-size: 28px; font-weight: 700; color: {c_accent}; letter-spacing: -0.03em; }}
            #KpiValueGreen {{ font-size: 28px; font-weight: 700; color: {c_green}; letter-spacing: -0.03em; }}
            #KpiValueAmber {{ font-size: 28px; font-weight: 700; color: {c_amber}; letter-spacing: -0.03em; }}
            #KpiSub {{ font-size: 11px; color: {c_muted}; }}
            #SectionLabel {{ font-size: 10px; font-weight: 700; letter-spacing: 0.1em; color: {c_muted}; }}
            #LinkLabel {{ font-size: 11px; font-weight: 500; color: {c_accent}; }}
            #SoftSep {{ background: {c_border_soft}; border: none; max-height: 1px; }}
            #TableMono {{ font-family: 'JetBrains Mono'; font-size: 11px; color: {c_faint}; }}
            #TableMono2 {{ font-family: 'JetBrains Mono'; font-size: 11px; color: {c_ink2}; }}
            #TableText {{ font-size: 12px; color: {c_muted}; }}
            #TableMonoBold {{ font-family: 'JetBrains Mono'; font-size: 13px; font-weight: 600; color: {c_ink}; }}
            #CarrierRowName {{ font-family: 'JetBrains Mono'; font-size: 12px; color: {c_ink2}; }}
            #TagGreen {{ background: {c_green_dim}; color: {c_green}; font-size: 11px; font-weight: 600; padding: 2px 7px; border-radius: 4px; }}
            #TagRed {{ background: {c_red_dim}; color: {c_red}; font-size: 11px; font-weight: 600; padding: 2px 7px; border-radius: 4px; }}
            #TagAmber {{ background: {c_amber_dim}; color: {c_amber}; font-size: 11px; font-weight: 600; padding: 2px 7px; border-radius: 4px; }}
            #InputText {{ background: {c_panel2}; color: {c_ink}; border: 1px solid {c_border}; border-radius: 8px; padding: 8px; font-family: "JetBrains Mono"; font-size: 10.5pt; }}
            #ResultText {{ background: {c_panel2}; color: {c_ink}; border: 1px solid {c_border}; border-radius: 10px; padding: 10px; font-family: "JetBrains Mono"; font-size: 11pt; }}
            #MainStack {{ background: transparent; }}
            #PageHeader {{ background: transparent; border: none; }}
            #BackButton {{ background: {c_panel2}; color: {c_ink2}; border: 1px solid {c_border}; border-radius: 8px; padding: 6px 14px; font-size: 13px; font-weight: 600; }}
            #BackButton:hover {{ background: {c_panel2}; color: {c_ink}; }}
            #PageTitleLabel {{ font-size: 18px; font-weight: 700; color: {c_ink}; }}
            #PageContent {{ background: {c_bg}; border: none; border-radius: 0px; }}
            #RastreioScroll {{ background: transparent; border: none; }}
            #RastreioScroll QWidget {{ background: transparent; }}
            #RastreioCard {{ background: {c_panel}; border: 1px solid {c_border}; border-radius: 10px; }}
            #RastreioBlockTitle {{ font-size: 12px; font-weight: 700; color: {c_accent}; }}
            #RastreioBlockLabel {{ font-size: 12px; font-weight: 600; color: {c_muted}; }}
            #RastreioBlockValue {{ font-size: 12px; color: {c_ink}; }}
            #RastreioCardHeader {{ font-size: 14px; font-weight: 700; color: {c_ink}; }}
            #RastreioStatusEntregue {{ font-size: 13px; font-weight: 700; color: {c_green}; }}
            #RastreioStatusTransito {{ font-size: 13px; font-weight: 700; color: {c_amber}; }}
            #RastreioStatusErro {{ font-size: 13px; font-weight: 700; color: {c_red}; }}
            #RastreioStatusPendente {{ font-size: 13px; font-weight: 600; color: {c_muted}; }}
            #RastreioBlockText {{ background: transparent; border: none; color: {c_ink};
                                  font-size: 12px; font-family: 'JetBrains Mono'; }}
            {"#RastreioBlueBlock  { background: #0d1f35; border: 1px solid #1a3353; border-radius: 8px; }" if dark else "#RastreioBlueBlock  { background: #f0f7ff; border: 1px solid #bfdbfe; border-radius: 8px; }"}
            {"#RastreioGreenBlock { background: #0d2010; border: 1px solid #1a3a20; border-radius: 8px; }" if dark else "#RastreioGreenBlock { background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; }"}
            {"#RastreioSlateBlock { background: #1c232c; border: 1px solid #262f3a; border-radius: 8px; }" if dark else "#RastreioSlateBlock { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; }"}
            QPushButton {{ background: {c_accent}; color: #ffffff; border: none; border-radius: 8px; padding: 10px 14px; font-weight: 600; }}
            QPushButton:hover {{ background: {c_accent}; }}
            QPushButton#SecondaryButton {{ background: {c_panel2}; color: {c_ink2}; border: 1px solid {c_border}; }}
            QPushButton#SecondaryButton:hover {{ background: {c_panel2}; color: {c_ink}; }}
            #InputField {{ background: {c_panel2}; color: {c_ink}; border: 1px solid {c_border}; border-radius: 6px; padding: 6px 8px; }}
            #FornLabel {{ font-size: 13px; font-weight: 600; color: {c_ink}; padding-right: 6px; }}
            #FornUnit {{ font-size: 12px; color: {c_muted}; }}
            QTabWidget#MainTabs::pane {{ border: 1px solid {c_border}; border-radius: 10px; background: {c_panel}; }}
            QTabBar::tab {{ background: {c_panel2}; color: {c_muted}; border: 1px solid {c_border};
                           padding: 7px 12px; margin-right: 4px; border-top-left-radius: 8px;
                           border-top-right-radius: 8px; }}
            QTabBar::tab:selected {{ background: {c_panel}; color: {c_ink}; border-bottom-color: {c_panel}; }}
            #SettingsGroup {{ border: 1px solid {c_border}; border-radius: 8px;
                             padding: 12px 10px 10px 10px; margin-top: 6px; background: {c_panel}; }}
            QGroupBox#SettingsGroup {{ border: 1px solid {c_border}; background: {c_panel}; border-radius: 8px; margin-top: 0px; }}
            QGroupBox#SettingsGroup::title {{ subcontrol-origin: margin; height: 0px; width: 0px; padding: 0px; color: transparent; }}
            #TranspTitle {{ font-size: 17px; font-weight: 700; color: {c_ink}; padding: 6px 0 8px 0; }}
            #CredField {{ border: 1px solid {c_border}; border-radius: 6px; padding: 5px 8px;
                         background: {c_panel2}; color: {c_ink}; }}
            QPushButton#MiniButton {{ background: {c_panel2}; color: {c_ink2};
                                     border: 1px solid {c_border}; border-radius: 4px;
                                     padding: 2px 8px; font-size: 11px; }}
            QPushButton#MiniButton:hover {{ background: {c_panel3}; }}
            QCheckBox {{ color: {c_ink}; spacing: 4px; }}
            QScrollArea {{ background: transparent; border: none; }}
        """)

        # Refresh NavItem widgets
        for item in getattr(self, '_nav_items_list', []):
            item.refresh_theme(
                accent=c_accent, muted=c_muted, panel2=c_panel2,
                panel3=c_panel3, border=c_border, faint=c_faint, accentDim=c_accent2,
            )

        # Refresh theme toggle icon + label
        if hasattr(self, '_theme_toggle'):
            self._theme_toggle.setChecked(dark)
            self._theme_toggle.refresh_theme(c_accent, c_faint)
            icon_body = NAV_ICONS['moon'] if dark else NAV_ICONS['sun']
            self._theme_icon_lbl.setPixmap(svg_icon(icon_body, 16, c_muted))
            self._theme_mode_lbl.setText("Modo escuro" if dark else "Modo claro")

        # Refresh CmdK search icon
        if hasattr(self, '_cmd_icon_lbl'):
            self._cmd_icon_lbl.setPixmap(svg_icon(NAV_ICONS['search'], 13, c_muted))



    def _wrap_page_with_back(self, title_text: str, content_widget: QWidget) -> QWidget:
        page = QWidget()
        page.setObjectName("PageContent")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # Header com botao voltar e titulo
        page_header = QFrame()
        page_header.setObjectName("PageHeader")
        header_layout = QHBoxLayout(page_header)
        header_layout.setContentsMargins(12, 8, 12, 8)
        header_layout.setSpacing(10)
        btn_back = QPushButton("\u2190 Voltar")
        btn_back.setObjectName("BackButton")
        btn_back.setCursor(Qt.PointingHandCursor)
        btn_back.clicked.connect(lambda: self._show_page(0))
        lbl_title = QLabel(title_text)
        lbl_title.setObjectName("PageTitleLabel")
        header_layout.addWidget(btn_back)
        header_layout.addWidget(lbl_title)
        header_layout.addStretch(1)
        layout.addWidget(page_header)
        layout.addWidget(content_widget, 1)
        return page

    def _show_page(self, index: int):
        self.stack.setCurrentIndex(index)
        if hasattr(self, "page_title_label"):
            self.page_title_label.setText(self._page_titles.get(index, "Fretio"))
        for btn_index, item in self._nav_buttons.items():
            if isinstance(item, NavItem):
                item.set_active(btn_index == index)
            else:
                item.setProperty("active", btn_index == index)
                item.style().unpolish(item)
                item.style().polish(item)
                item.update()
        # Carrier status bar visível apenas na página Cotação
        if hasattr(self, '_carrier_status_frame'):
            self._carrier_status_frame.setVisible(index == 2)
        # Pre-login so quando acessar Calcular Frete (2) ou Frete Fornecedores (3)
        if index in (2, 3) and not self._pre_login_done and not self._is_shutting_down():
            self._run_pre_login()
            self._pre_login_done = True

    def _selecionar_arquivo(self):
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
        self.pedidos = self.extrator.extrair_arquivo(arquivo)
        if not self.pedidos:
            QMessageBox.warning(
                self,
                "Aviso",
                "Nenhum pedido encontrado no arquivo selecionado.\n\nVerifique se o PDF tem o formato esperado."
            )
            return

        if not self._validar_local_entrega(self.pedidos):
            self.label_info.setText("Locais de entrega diferentes - processamento interrompido")
            self.label_info.setStyleSheet("color: #b42318;")
            return

        try:
            if len(self.pedidos) == 1:
                html_result = self.extrator.formatar_pedido_html(self.pedidos[0])
            else:
                html_result = self.extrator.formatar_pedidos_agrupados_html(self.pedidos)
        except ValueError as e:
            QMessageBox.warning(self, "Erro de dados", str(e))
            self.label_info.setText("Erro: verifique informações de volume")
            self.label_info.setStyleSheet("color: #b42318;")
            return

        self.html_original = html_result
        self.romaneio_calculado_text.setPlainText(html_result.replace('<br>', '\n'))
        self._show_page(1)
        self.label_info.setText(f"OK: {len(self.pedidos)} pedido(s) extraido(s) de {Path(arquivo).name}")
        self.label_info.setStyleSheet("color: #067647;")
        self._registrar_romaneio(arquivo)
        self._atualizar_dashboard()

    def _atualizar_estado_romaneio_colado(self):
        texto = (self.romaneio_colado_text.toPlainText() or "").strip()
        self.btn_quote_colado.setEnabled(bool(texto))

    def _iniciar_cotacao(self, modo: str):
        if self._is_shutting_down():
            return
        self._modo_cotacao = modo
        self._cep_origem_override = ""
        self.btn_quote_colado.setEnabled(False)
        self.btn_select.setEnabled(False)
        self.btn_cotar_fornecedor.setEnabled(False)
        self._cotacao_total = 0
        self._cotacao_concluidas = 0
        self.progress_bar.setVisible(True)
        self.progress_bar.start_anim()
        self.result_text.setPlainText("Iniciando cotações...\nAguardando primeiras respostas...")
        self._show_page(2)
        self.label_info.setText("Executando cotações de transportadoras...")
        self.label_info.setStyleSheet("color: #1f6feb;")
        self._run_async_cotacao()

    def _cotar_romaneio_colado(self):
        texto = (self.romaneio_colado_text.toPlainText() or "").strip()
        if not texto:
            QMessageBox.warning(self, "Aviso", "Cole um romaneio antes de cotar")
            return
        self._romaneio_colado = texto
        self._iniciar_cotacao("romaneio_colado")

    def _obter_cnpj_empresa(self) -> str:
        """Busca o CNPJ da empresa na configura\u00e7\u00e3o das transportadoras."""
        transp = self._sessao.config.get("transportadoras", {}) or {}
        cnpj = re.sub(r"\D", "", str((transp.get("braspress") or {}).get("cnpj", "") or ""))
        if len(cnpj) == 14:
            return cnpj

        agex_cfg = transp.get("agex") or {}
        for chave in ("cnpj_remetente", "cnpj"):
            cnpj = re.sub(r"\D", "", str(agex_cfg.get(chave, "") or ""))
            if len(cnpj) == 14:
                return cnpj

        for nome in ("bauer", "rodonaves"):
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
            erros.append("CNPJ da empresa n\u00e3o configurado (verifique Configura\u00e7\u00f5es > Credenciais)")
        if len(cep_empresa) != 8:
            erros.append("CEP da empresa n\u00e3o configurado (verifique Configura\u00e7\u00f5es > Empresa > CEP de Origem)")
        if len(cep_forn) != 8:
            erros.append("CEP do fornecedor inv\u00e1lido (deve ter 8 d\u00edgitos)")
        if qtd <= 0:
            erros.append("Quantidade de volumes deve ser maior que zero")
        if alt <= 0 or larg <= 0 or comp <= 0:
            erros.append("Dimens\u00f5es devem ser maiores que zero")
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
        try:
            romaneio_texto, cep_fornecedor = self._montar_romaneio_fornecedor()
        except (ValueError, Exception) as e:
            QMessageBox.warning(self, "Dados inv\u00e1lidos", str(e))
            return
        self._romaneio_colado = romaneio_texto
        self._cep_origem_override = cep_fornecedor
        self._cnpj_fornecedor = re.sub(r"\D", "", self._forn_cnpj.text())
        self._modo_cotacao = "fornecedor"
        self.btn_cotar_fornecedor.setEnabled(False)
        self.btn_quote_colado.setEnabled(False)
        self.btn_select.setEnabled(False)
        self.forn_progress_bar.setVisible(True)
        self.forn_progress_bar.start_anim()
        self.forn_result_text.setPlainText("Iniciando cota\u00e7\u00f5es...\nAguardando primeiras respostas...")
        self.label_info.setText("Cotando frete fornecedor...")
        self.label_info.setStyleSheet("color: #1f6feb;")
        self._run_async_cotacao()

    def _post_event_safe(self, event: QEvent) -> None:
        """Posta evento na fila da UI de forma segura (ignora se app já encerrou)."""
        if self._is_shutting_down():
            return
        try:
            inst = QApplication.instance()
            if inst is not None:
                inst.postEvent(self, event)
        except Exception:
            pass

    def _is_shutting_down(self) -> bool:
        return self._shutdown_started.is_set()

    def _start_daemon_worker(self, target) -> bool:
        if self._is_shutting_down():
            return False
        threading.Thread(target=target, daemon=True).start()
        return True

    def _track_async_future(self, future: concurrent.futures.Future) -> concurrent.futures.Future:
        with self._async_futures_lock:
            self._async_futures.add(future)
        future.add_done_callback(self._discard_async_future)
        return future

    def _discard_async_future(self, future: concurrent.futures.Future) -> None:
        with self._async_futures_lock:
            self._async_futures.discard(future)

    def _submit_async_future(
        self,
        coro_factory: Callable[[], Any],
    ) -> concurrent.futures.Future | None:
        if self._is_shutting_down():
            return None
        future = self._async_loop.submit(coro_factory)
        if future is None:
            return None
        return self._track_async_future(future)

    def _cancel_pending_async_futures(self) -> None:
        with self._async_futures_lock:
            futures = list(self._async_futures)
        for future in futures:
            try:
                future.cancel()
            except Exception:
                pass

    def _handle_async_worker_failure(
        self,
        *,
        exc: BaseException,
        context: str,
        log_label: str,
        ui_error_handler: Callable[[BaseException], None] | None = None,
    ) -> None:
        report_error(*sys.exc_info(), context=context)
        print(f"[fretio] {log_label}: {exc}", file=sys.stderr, flush=True)
        if ui_error_handler is not None and not self._is_shutting_down():
            ui_error_handler(exc)

    def _run_async_worker(
        self,
        coro_factory: Callable[[], Any],
        *,
        context: str,
        log_label: str,
        sync_lock: Any | None = None,
        ui_error_handler: Callable[[BaseException], None] | None = None,
        on_success: Callable[[Any], None] | None = None,
    ) -> bool:
        def _worker():
            future: concurrent.futures.Future | None = None
            try:
                context_manager = sync_lock if sync_lock is not None else nullcontext()
                with context_manager:
                    future = self._submit_async_future(coro_factory)
                    if future is None:
                        return
                    result = future.result()
                if on_success is not None and not self._is_shutting_down():
                    on_success(result)
            except concurrent.futures.CancelledError:
                return
            except Exception as exc:
                self._handle_async_worker_failure(
                    exc=exc,
                    context=context,
                    log_label=log_label,
                    ui_error_handler=ui_error_handler,
                )

        return self._start_daemon_worker(_worker)

    def _run_pre_login(self):
        """Faz pre-login de todas as transportadoras em background."""
        if self._is_shutting_down():
            return
        def _status_callback(msg):
            self._post_event_safe(StatusUpdateEvent(msg))
        def _login_status_callback(nome, status):
            self._post_event_safe(LoginStatusEvent(nome, status))
        self._run_async_worker(
            lambda: self._sessao.inicializar(
                callback=_status_callback,
                login_status_callback=_login_status_callback,
            ),
            context="pre_login",
            log_label="Erro no pre-login",
            sync_lock=self._session_task_lock,
        )

    def _run_async_cotacao(self):
        if self._is_shutting_down():
            return
        self._run_async_worker(
            self._cotar_transportadoras_async,
            context="run_async_cotacao",
            log_label="Erro na cotação",
            sync_lock=self._session_task_lock,
            ui_error_handler=lambda exc: (
                self._post_event_safe(UpdateResultEvent(f"Erro ao cotar: {exc}")),
                self._post_event_safe(UpdateFinishedEvent()),
            ),
        )

    async def _cotar_transportadoras_async(self):
        try:
            def _progresso_callback(payload: dict[str, Any]) -> None:
                self._post_event_safe(CotacaoProgressEvent(payload))

            if not self._sessao.pronto:
                self._post_event_safe(StatusUpdateEvent("Executando pre-login antes da cotação..."))
                await self._sessao.inicializar(
                    callback=lambda msg: self._post_event_safe(StatusUpdateEvent(msg)),
                    login_status_callback=lambda nome, status: self._post_event_safe(LoginStatusEvent(nome, status)),
                )
                self._pre_login_done = True

            _cotar_kwargs = dict(
                romaneio_colado=self._romaneio_colado,
                cep_origem=self._cep_origem_override,
                sessao=self._sessao,
                progresso_callback=_progresso_callback,
            )
            if getattr(self, "_modo_cotacao", "") == "fornecedor" and getattr(self, "_cnpj_fornecedor", ""):
                _cotar_kwargs["cnpj_remetente"] = self._cnpj_fornecedor
                _cotar_kwargs["tipo_frete"] = "2"  # FOB
            resultados = await cotar_transportadoras_romaneio_colado(**_cotar_kwargs)
            self._last_cotacao_results = resultados
            resumo = formatar_resultados_cotacao(resultados)

            # As atualizações da UI devem ser feitas na thread principal
            self._post_event_safe(UpdateResultEvent(resumo))

        except Exception as e:
            report_error(*sys.exc_info(), context="cotar_async")
            self._post_event_safe(UpdateResultEvent(f"Erro ao cotar transportadoras: {e}"))
        finally:
            self._post_event_safe(UpdateFinishedEvent())

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

    def customEvent(self, event):
        # --- Rastreio events ---
        if isinstance(event, RastreioResultEvent):
            self._on_rastreio_result(event.indice, event.total, event.resultado)
            return
        elif isinstance(event, RastreioFinishedEvent):
            self._on_rastreio_finished(event.resultados)
            return

        is_forn = self._modo_cotacao == "fornecedor"
        _result = self.forn_result_text if is_forn else self.result_text
        _progress = self.forn_progress_bar if is_forn else self.progress_bar

        if isinstance(event, UpdateResultEvent):
            _result.setPlainText(event.result)
            if not is_forn:
                self._show_page(2)
            self.label_info.setText("Cota\u00e7\u00f5es finalizadas")
            self.label_info.setStyleSheet("color: #067647;")
            self._verificar_erro_divergencia_uf(event.result)
        elif isinstance(event, CotacaoProgressEvent):
            payload = event.payload or {}
            total = int(payload.get("total", 0) or 0)
            concluidas = int(payload.get("concluidas", 0) or 0)
            resultado = payload.get("resultado")

            self._cotacao_total = total
            self._cotacao_concluidas = concluidas
            if total > 0:
                if concluidas < total:
                    self.label_info.setText(f"Cotando transportadoras... {concluidas}/{total}")
                    self.label_info.setStyleSheet("color: #1f6feb;")

            if isinstance(resultado, ResultadoCotacao):
                linha = self._formatar_linha_progresso(resultado)
                atual = _result.toPlainText().strip()
                if not atual:
                    _result.setPlainText(linha)
                else:
                    _result.appendPlainText(linha)
        elif isinstance(event, UpdateFinishedEvent):
            _progress.stop_anim()
            _progress.setVisible(False)
            self.btn_select.setEnabled(True)
            self.btn_cotar_fornecedor.setEnabled(True)
            self._atualizar_estado_romaneio_colado()
            self._atualizar_dashboard()
        elif isinstance(event, StatusUpdateEvent):
            self.label_info.setText(event.msg)
            self.label_info.setStyleSheet("color: #1f6feb;")
        elif isinstance(event, LoginStatusEvent):
            dot = self._login_status_dots.get(event.nome)
            if dot is not None:
                dot.set_status(event.status)
            # Atualiza também o card de status no dashboard
            pair = self._home_carrier_info.get(event.nome)
            if pair is not None:
                cr_dot, cr_tag = pair
                _color_map = {
                    "ok":      ("#3fb950", "online",     "TagGreen"),
                    "fail":    ("#f85149", "erro",       "TagRed"),
                    "pending": ("#e3b341", "aguardando", "TagAmber"),
                }
                color, text, tag_obj = _color_map.get(event.status, ("#768390", "—", "TagAmber"))
                cr_dot.setStyleSheet(f"border-radius:3px;background:{color};")
                cr_tag.setText(text)
                cr_tag.setObjectName(tag_obj)
                cr_tag.style().unpolish(cr_tag)
                cr_tag.style().polish(cr_tag)
    def _registrar_romaneio(self, arquivo: str) -> None:
        from datetime import date as _date
        destino = (self.pedidos[0].local_entrega or "—") if self.pedidos else "—"
        self._romaneios_processados.append({
            "data": _date.today().strftime("%d/%m"),
            "nome": Path(arquivo).name,
            "destino": destino,
            "volumes": len(self.pedidos),
        })

    def _atualizar_dashboard(self) -> None:
        if not hasattr(self, '_kpi_value_labels'):
            return

        # KPI 0: romaneios
        total_rom = len(self._romaneios_processados)
        self._kpi_value_labels[0].setText(str(total_rom))

        # KPI 1: volumes
        total_vol = sum(r["volumes"] for r in self._romaneios_processados)
        self._kpi_value_labels[1].setText(str(total_vol))

        # KPI 2 e 3: baseados na última cotação
        ok_results = [
            r for r in self._last_cotacao_results
            if r.status == "ok" and r.valor_frete is not None
        ]
        if ok_results:
            melhor = min(r.valor_frete for r in ok_results)
            self._kpi_value_labels[2].setText(f"R$ {melhor:.2f}")
        else:
            self._kpi_value_labels[2].setText("—")

        total_cot = len(self._last_cotacao_results)
        ok_cot = len(ok_results)
        if total_cot > 0:
            pct = round(ok_cot / total_cot * 100)
            self._kpi_value_labels[3].setText(f"{pct}%")
            self._kpi_sub_labels[3].setText(f"{ok_cot} de {total_cot} cotações")
        else:
            self._kpi_value_labels[3].setText("—")

        # Tabela recentes: limpar e reconstruir
        layout = self._recentes_body_layout
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not self._romaneios_processados:
            ph = QLabel("Nenhum romaneio processado nesta sessão")
            ph.setObjectName("KpiSub")
            ph.setContentsMargins(14, 12, 14, 12)
            layout.addWidget(ph)
            return

        rows = list(reversed(self._romaneios_processados))
        for i, r in enumerate(rows):
            row_w = QWidget()
            rw_layout = QHBoxLayout(row_w)
            rw_layout.setContentsMargins(14, 9, 14, 9)
            rw_layout.setSpacing(10)
            ld = QLabel(r["data"])
            ld.setObjectName("TableMono")
            ld.setFixedWidth(36)
            ln = QLabel(r["nome"])
            ln.setObjectName("TableMono2")
            lde = QLabel(r["destino"])
            lde.setObjectName("TableText")
            lv = QLabel(f'{r["volumes"]}v')
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

    def _formatar_linha_progresso(self, resultado: ResultadoCotacao) -> str:
        nome = (resultado.transportadora or "GERAL").strip().upper()
        if resultado.status == "ok" and resultado.valor_frete is not None:
            prazo = int(resultado.prazo_dias or 0)
            return f"- {nome} pronta: R$ {resultado.valor_frete:.2f} | {prazo} dia(s)"

        detalhe = (resultado.detalhes or resultado.status or "Sem detalhe")
        detalhe = re.sub(r"\s+", " ", str(detalhe)).strip()
        if len(detalhe) > 140:
            detalhe = detalhe[:137] + "..."
        return f"- {nome} falhou: {detalhe}"

    def closeEvent(self, event):
        """Limpa browsers ao fechar a janela (com timeout para não travar)."""
        if self._is_shutting_down():
            event.accept()
            return
        self._shutdown_started.set()
        try:
            from fretio.providers.base import request_browser_shutdown
            request_browser_shutdown()
        except Exception:
            pass
        event.accept()  # aceita logo para fechar a janela imediatamente
        self._cancel_pending_async_futures()

        def _cleanup_background():
            self._async_loop.shutdown(
                cleanup_coro_factory=lambda: asyncio.wait_for(self._sessao.cleanup(), timeout=2)
            )

        t = threading.Thread(target=_cleanup_background, name="RomaneioShutdownCleanup")
        t.start()
        # Não bloqueia o fechamento — cleanup roda em background e o processo encerra logo


    def _selecionar_nfe(self):
        """Abre dialogo para selecionar arquivos XML de NF-e (um ou varios)."""
        arquivos, _ = QFileDialog.getOpenFileNames(
            self,
            "Selecionar NF-e (XML)",
            "",
            "XML NF-e (*.xml);;Todos os arquivos (*.*)"
        )
        if not arquivos:
            return

        erros = []
        novas_notas = []
        card_offset = len(self._rastreio_card_widgets)
        for arq in arquivos:
            try:
                notas = extrair_nfe_arquivo(arq)
                if not notas:
                    erros.append(f"{Path(arq).name}: nenhuma NF-e encontrada")
                    continue
                for nf in notas:
                    if nf.chave_acesso and any(
                        n.chave_acesso == nf.chave_acesso for n in self._notas_rastreio
                    ):
                        continue
                    self._notas_rastreio.append(nf)
                    novas_notas.append(nf)
            except Exception as e:
                erros.append(f"{Path(arq).name}: {e}")

        if erros:
            QMessageBox.warning(
                self, "Aviso",
                "Alguns arquivos não puderam ser processados:\n\n" + "\n".join(erros)
            )

        if novas_notas:
            self._inserir_cards_novas_notas(novas_notas)
            self._rastreio_notas_subset = list(novas_notas)
            self._rastreio_card_offset = card_offset
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
        notas_a_rastrear = self._rastreio_notas_subset if self._rastreio_notas_subset else self._notas_rastreio
        if not notas_a_rastrear:
            QMessageBox.warning(self, "Aviso", "Nenhuma NF-e carregada para rastrear")
            return
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
        def _progress_callback(indice, total, resultado):
            self._post_event_safe(RastreioResultEvent(indice, total, resultado))
        resultados = await rastrear_multiplas(notas_para_rastrear, callback=_progress_callback)
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

    def _abrir_pasta_screenshots(self):
        """Abre a pasta de screenshots no explorador de arquivos."""
        appdata = os.getenv("APPDATA")
        if appdata:
            pasta = Path(appdata) / "Fretio" / "rastreamento"
        else:
            pasta = Path.cwd() / "Fretio_rastreamento"
        pasta.mkdir(parents=True, exist_ok=True)
        os.startfile(str(pasta))

    def _abrir_configuracoes(self):
        self._show_page(5)

    def _reiniciar_sessao(self):
        """Limpa sess\u00e3o atual e faz login novamente com a config atualizada."""
        if self._is_shutting_down():
            return
        self.label_info.setText("Reiniciando sess\u00f5es...")
        self.label_info.setStyleSheet("color: #1f6feb;")
        for dot in self._login_status_dots.values():
            dot.set_status("pending")
        for cr_dot, cr_tag in self._home_carrier_info.values():
            cr_dot.setStyleSheet("border-radius:3px;background:#e3b341;")
            cr_tag.setText("aguardando")
            cr_tag.setObjectName("TagAmber")
            cr_tag.style().unpolish(cr_tag)
            cr_tag.style().polish(cr_tag)

        def _do():
            future = None
            try:
                with self._session_task_lock:
                    future = self._submit_async_future(self._sessao.cleanup)
                    if future is None:
                        return
                    future.result()
            except concurrent.futures.CancelledError:
                return
            except Exception:
                pass
            if self._is_shutting_down():
                return
            self._sessao = TransportadoraSession(config_path=self._config_path)
            self._pre_login_done = False
            self._run_pre_login()

        self._start_daemon_worker(_do)

    def _validar_local_entrega(self, pedidos):
        if not pedidos:
            return True

        pedidos_sem_cep = []
        for pedido in pedidos:
            local_raw = pedido.local_entrega or ""
            if hasattr(self.extrator, 'obter_cep_local_entrega'):
                cep = self.extrator.obter_cep_local_entrega(local_raw)
            else:
                m_cep = re.search(r'\b(\d{5}-?\d{3})\b', local_raw or "")
                cep = m_cep.group(1) if m_cep else None
            if not cep:
                pedidos_sem_cep.append(pedido.numero)

        if pedidos_sem_cep:
            mensagem = "ATENCAO: CEP nao encontrado.\n\nPedidos: {}\n".format(", ".join(pedidos_sem_cep))
            QMessageBox.warning(self, "Validacao de Local de Entrega", mensagem)
            return False

        locais_diferentes = {}
        for pedido in pedidos:
            local_raw = pedido.local_entrega or ""
            if hasattr(self.extrator, 'normalizar_local_entrega'):
                local_norm = self.extrator.normalizar_local_entrega(local_raw) or ""
            else:
                local_norm = local_raw

            local_norm = local_norm.strip()
            if not local_norm or local_norm.upper() == "N/A":
                continue

            if hasattr(self.extrator, 'chave_local_entrega'):
                chave = self.extrator.chave_local_entrega(local_raw)
            else:
                chave = re.sub(r'\s+', ' ', local_norm).strip().upper()

            if not chave:
                continue

            entry = locais_diferentes.setdefault(chave, {'local': local_norm, 'pedidos': []})
            entry['pedidos'].append(pedido.numero)

        if len(locais_diferentes) <= 1:
            return True

        mensagem = "ATENCAO: Locais de entrega diferentes encontrados!\n\n"
        for _, info in locais_diferentes.items():
            local = info.get('local', '(sem local)')
            numeros_pedidos = info.get('pedidos', [])
            mensagem += "Local: {}\n".format(local.split('\n')[0])
            mensagem += "Pedidos: {}\n\n".format(", ".join(numeros_pedidos))

        QMessageBox.warning(self, "Validacao de Local de Entrega", mensagem)
        return False


def _instalar_thread_excepthook():
    """Captura exceções não tratadas em threads para evitar crash silencioso."""
    import traceback as _tb
    _original = threading.excepthook
    def _hook(args):
        msg = "".join(_tb.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        print(f"[fretio] Exceção em thread {args.thread}:\n{msg}",
              file=sys.stderr, flush=True)
        try:
            import logging
            logging.getLogger("thread").error("Exceção em thread %s:\n%s", args.thread, msg)
        except Exception:
            pass
        if _original:
            try:
                _original(args)
            except Exception:
                pass
    threading.excepthook = _hook


def _preparar_runtime_qt() -> None:
    """Ajusta ambiente Qt no app empacotado para evitar conflitos de plugin/GPU."""
    if not getattr(sys, "frozen", False):
        return

    # Evita falhas nativas de driver OpenGL em algumas máquinas Windows.
    os.environ.setdefault("QT_OPENGL", "software")
    os.environ.setdefault("QT_ANGLE_PLATFORM", "d3d11")

    # Remove caminhos herdados de outros softwares Qt (Conda/QGIS/etc).
    for var in ("QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "QML_IMPORT_PATH"):
        if var in os.environ:
            os.environ.pop(var, None)

    exe_dir = Path(sys.executable).resolve().parent
    meipass = Path(getattr(sys, "_MEIPASS", "") or "")
    candidatos_plugins: list[Path] = []

    if meipass:
        candidatos_plugins.extend([
            meipass / "PySide6" / "plugins",
            meipass / "plugins",
        ])

    candidatos_plugins.extend([
        exe_dir / "_internal" / "PySide6" / "plugins",
        exe_dir / "PySide6" / "plugins",
    ])

    for plugin_dir in candidatos_plugins:
        if not plugin_dir.exists():
            continue
        os.environ["QT_PLUGIN_PATH"] = str(plugin_dir)
        platform_dir = plugin_dir / "platforms"
        if platform_dir.exists():
            os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(platform_dir)
        break


_SINGLE_INSTANCE_MUTEX_HANDLE = None


def _executavel_instalacao_canonica() -> Path:
    local_appdata = os.getenv("LOCALAPPDATA", "").strip()
    if not local_appdata:
        return Path(sys.executable).resolve()
    exe_names = [Path(sys.executable).name, "Fretio.exe", "FreteBot.exe"]
    vistos: set[str] = set()
    candidatos_dirs = [
        Path(local_appdata) / "Programs" / "Fretio",
        Path(local_appdata) / "Programs" / "Romaneio Beta",
        Path(local_appdata) / "Programs" / "FreteBot",
    ]
    fallback = candidatos_dirs[0] / exe_names[0]
    for base_dir in candidatos_dirs:
        for exe_name in exe_names:
            exe_name_norm = exe_name.lower()
            if exe_name_norm in vistos:
                continue
            vistos.add(exe_name_norm)
            candidato = base_dir / exe_name
            if candidato.exists():
                return candidato
    return fallback


def _avisar_instalacao_paralela(caminho_canonico: Path) -> None:
    msg = (
        "Detectamos mais de uma cópia do Fretio neste computador.\n\n"
        f"A instalação oficial é:\n{caminho_canonico}\n\n"
        "Esta cópia será encerrada para evitar conflito."
    )
    try:
        import ctypes
        MB_OK = 0x00000000
        MB_ICONWARNING = 0x00000030
        ctypes.windll.user32.MessageBoxW(None, msg, "Fretio", MB_OK | MB_ICONWARNING)
    except Exception:
        print(f"[fretio] {msg}", file=sys.stderr, flush=True)


def _garantir_instalacao_unica() -> bool:
    """Força execução pela instalação canônica para evitar cópias paralelas."""
    if not getattr(sys, "frozen", False):
        return True
    if os.name != "nt":
        return True

    try:
        exe_atual = Path(sys.executable).resolve()
        exe_canonico = _executavel_instalacao_canonica().resolve()

        if exe_atual == exe_canonico:
            return True

        if exe_canonico.exists():
            try:
                os.startfile(str(exe_canonico))
            except Exception:
                pass
            _avisar_instalacao_paralela(exe_canonico)
            return False
    except Exception:
        return True

    return True


def _garantir_instancia_unica() -> bool:
    """Impede mais de uma instância do app por máquina/sessão de usuário."""
    global _SINGLE_INSTANCE_MUTEX_HANDLE
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        create_mutex.restype = wintypes.HANDLE

        _SINGLE_INSTANCE_MUTEX_HANDLE = create_mutex(None, False, "Local\\Fretio.Singleton.v1")
        if not _SINGLE_INSTANCE_MUTEX_HANDLE:
            return True

        ERROR_ALREADY_EXISTS = 183
        return ctypes.get_last_error() != ERROR_ALREADY_EXISTS
    except Exception:
        return True


def _avisar_instancia_ativa() -> None:
    msg = (
        "O Fretio já está em execução neste computador.\n\n"
        "Feche a janela atual antes de abrir outra instância."
    )
    try:
        import ctypes
        MB_OK = 0x00000000
        MB_ICONWARNING = 0x00000030
        ctypes.windll.user32.MessageBoxW(None, msg, "Fretio", MB_OK | MB_ICONWARNING)
    except Exception:
        print(f"[fretio] {msg}", file=sys.stderr, flush=True)


def main():
    # Migrar dados de Fretio → Fretio (uma vez, na primeira execução após renomeação)
    _migrate_appdata_fretebot_to_fretio()

    # Redireciona stderr para arquivo de crash ANTES de qualquer import falhar
    _crash_log = None
    try:
        _appdata = os.getenv("APPDATA")
        if _appdata:
            _log_dir = Path(_appdata) / "Fretio"
            _log_dir.mkdir(parents=True, exist_ok=True)
            _crash_log = _log_dir / "crash.log"
            # Rotaciona se > 2 MB
            try:
                if _crash_log.exists() and _crash_log.stat().st_size > 2 * 1024 * 1024:
                    _crash_log.write_text("", encoding="utf-8")
            except Exception:
                pass
            # Filtro que ignora warnings inofensivos do asyncio/Playwright
            # (EPIPE, closed pipe, DEP0169) para não assustar o usuário
            class _FilteredStderr:
                _IGNORE = (
                    "I/O operation on closed pipe",
                    "DEP0169",
                    "EPIPE: broken pipe",
                    "Exception ignored in:",
                    "_ProactorBasePipeTransport.__del__",
                    "BaseSubprocessTransport.__del__",
                )

                def __init__(self, stream):
                    self._stream = stream
                    self._buf = ""

                def write(self, s):
                    if not s:
                        return
                    self._buf += s
                    # Processa linhas completas
                    while "\n" in self._buf:
                        line, self._buf = self._buf.split("\n", 1)
                        if any(ign in line for ign in self._IGNORE):
                            continue
                        self._stream.write(line + "\n")

                def flush(self):
                    if self._buf and not any(ign in self._buf for ign in self._IGNORE):
                        self._stream.write(self._buf)
                    self._buf = ""
                    self._stream.flush()

                def __getattr__(self, name):
                    return getattr(self._stream, name)

            _raw_file = open(_crash_log, "a", encoding="utf-8")
            sys.stderr = _FilteredStderr(_raw_file)
    except Exception:
        pass

    if not _garantir_instalacao_unica():
        return

    if not _garantir_instancia_unica():
        _avisar_instancia_ativa()
        return

    import traceback as _tb
    try:
        _instalar_thread_excepthook()
        setup_global_exception_handler()
        install_global_hooks()  # DEPOIS dos outros hooks, para envolver todos

        # Configurar error reporter o mais cedo possível — antes de criar a janela,
        # para que exceptions durante o startup também sejam reportadas.
        try:
            _early_empresa = _ler_ultima_empresa()
            if _early_empresa:
                _er_configure(_empresa_config_path(_early_empresa))
        except Exception:
            pass

        _startup_logger = None

        # Log de inicialização (diagnóstico: versão, caminhos, etc.)
        try:
            from fretio.logging_conf import setup_logging, get_logger
            setup_logging()
            _startup_logger = get_logger("startup")
            _startup_logger.info("="*60)
            _startup_logger.info("Fretio iniciando")
            _startup_logger.info(f"Python: {sys.version}")
            _startup_logger.info(f"Frozen: {getattr(sys, 'frozen', False)}")
            _startup_logger.info(f"Exe: {sys.executable}")
            _startup_logger.info(f"CWD: {os.getcwd()}")
            _startup_logger.info(f"APPDATA: {os.getenv('APPDATA', '?')}")
            _startup_logger.info(f"LOCALAPPDATA: {os.getenv('LOCALAPPDATA', '?')}")
            _startup_logger.info(f"_MEIPASS: {getattr(sys, '_MEIPASS', '')}")
            try:
                _startup_logger.info(f"Exe dir: {Path(sys.executable).resolve().parent}")
            except Exception:
                pass
            try:
                _v = (Path(getattr(sys, '_MEIPASS', '')) / 'version.txt').read_text().strip()
            except Exception:
                try:
                    _v = (Path(__file__).parent / 'version.txt').read_text().strip()
                except Exception:
                    _v = '?'
            _startup_logger.info(f"Versão: {_v}")
        except Exception as _log_err:
            print(f"[fretio] Falha ao configurar logging: {_log_err}", file=sys.stderr, flush=True)

        _preparar_runtime_qt()
        QApplication.setAttribute(Qt.AA_UseSoftwareOpenGL, True)
        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
        app = QApplication(sys.argv)
        load_app_fonts()
        app.setQuitOnLastWindowClosed(True)
        if _startup_logger is not None:
            _startup_logger.info("QApplication criada com sucesso")

        # ── Verificação de licença ──
        try:
            _lic_key = get_saved_license()
            _machine = get_machine_id()

            if not _lic_key:
                if _startup_logger is not None:
                    _startup_logger.info("Sem licença local; exibindo diálogo de ativação")
                # Pedir chave de ativação
                while True:
                    _lic_key, _ok = _show_startup_text_input(
                        "Ativação — Fretio",
                        "Digite sua chave de licença:\n\n"
                        "Formato: FBOT-XXXX-XXXX-XXXX-XXXX",
                    )
                    if not _ok:
                        if _startup_logger is not None:
                            _startup_logger.warning("Ativação cancelada pelo usuário")
                        sys.exit(0)
                    _lic_key = _lic_key.strip().upper()
                    if not _lic_key:
                        continue
                    _lic_status = validate_license(_lic_key, _machine)
                    if _lic_status.valid:
                        save_license(_lic_key)
                        if _startup_logger is not None:
                            _startup_logger.info("Licença validada com sucesso")
                        break
                    else:
                        if _startup_logger is not None:
                            _startup_logger.warning("Licença inválida: %s", _lic_status.message or "chave não reconhecida")
                        _show_startup_message(
                            QMessageBox.Warning,
                            "Licença Inválida",
                            _lic_status.message or "Chave não reconhecida.",
                        )
            else:
                # Validar licença existente
                _lic_status = validate_license(_lic_key, _machine)
                if not _lic_status.valid:
                    if _startup_logger is not None:
                        _startup_logger.warning("Licença salva recusada: %s", _lic_status.message or "licença bloqueada")
                    _show_startup_message(
                        QMessageBox.Critical,
                        "Licença Bloqueada",
                        _lic_status.message or "Sua licença não é mais válida.",
                    )
                    # Dar chance de inserir outra chave
                    _lic_key2, _ok2 = _show_startup_text_input(
                        "Ativação — Fretio",
                        "Sua licença foi revogada.\n"
                        "Digite uma nova chave de licença:",
                    )
                    if _ok2 and _lic_key2.strip():
                        _lic_status2 = validate_license(_lic_key2.strip().upper(), _machine)
                        if _lic_status2.valid:
                            save_license(_lic_key2.strip().upper())
                            if _startup_logger is not None:
                                _startup_logger.info("Nova licença validada após revogação")
                        else:
                            if _startup_logger is not None:
                                _startup_logger.warning("Nova licença inválida após revogação: %s", _lic_status2.message or "chave não reconhecida")
                            _show_startup_message(
                                QMessageBox.Critical,
                                "Licença Inválida",
                                _lic_status2.message or "Chave não reconhecida.",
                            )
                            sys.exit(1)
                    else:
                        if _startup_logger is not None:
                            _startup_logger.warning("Reativação cancelada pelo usuário")
                        sys.exit(1)
        except SystemExit:
            raise
        except Exception as _lic_err:
            report_error(context="verificacao_licenca")
            print(f"[fretio] Verificação de licença falhou: {_lic_err}", file=sys.stderr, flush=True)

        # ── Verificação de atualização via GitHub ──
        try:
            _repo = get_repo_from_config()
            _cur_ver = _carregar_versao_app()
            if _repo:
                _update_info = check_for_update(_repo, _cur_ver)
                if _update_info:
                    _progress_dlg = QMessageBox(
                        QMessageBox.Information,
                        "Atualização Automática",
                        (
                            f"Nova versão encontrada: v{_update_info.version}.\n"
                            "Aplicando atualização automática..."
                        ),
                        QMessageBox.NoButton,
                    )
                    _progress_dlg.show()
                    QApplication.processEvents()

                    def _update_cb(msg: str):
                        _progress_dlg.setText(msg)
                        QApplication.processEvents()

                    _ok = apply_update(_update_info, callback=_update_cb)
                    _progress_dlg.close()

                    if _ok:
                        QMessageBox.information(
                            None,
                            "Atualização Concluída",
                            f"Fretio foi atualizado para v{_update_info.version}.\n"
                            "O aplicativo vai reiniciar automaticamente.",
                        )
                        restart_app()  # Lança o .bat e fecha o app
                    else:
                        QMessageBox.warning(
                            None,
                            "Atualização Falhou",
                            "Não foi possível aplicar a atualização automática.\n"
                            "O aplicativo continuará com a versão atual.",
                        )
        except Exception as _upd_err:
            report_error(context="verificacao_atualizacao")
            print(f"[fretio] Verificação de atualização falhou: {_upd_err}", file=sys.stderr, flush=True)

        _migrar_config_se_necessario()

        proxima_empresa: str | None = None

        while True:
            empresas_disponiveis = _listar_empresas()
            if not proxima_empresa or proxima_empresa not in empresas_disponiveis:
                selector = EmpresaSelectorDialog()
                if selector.exec() != QDialog.Accepted:
                    sys.exit(0)
                proxima_empresa = selector.empresa_selecionada

            window = RomaneioWindow(empresa_nome=proxima_empresa)
            window.show()
            app.exec()

            proxima_empresa = window._proxima_empresa
            if not proxima_empresa:
                break

        sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        crash_msg = _tb.format_exc()
        report_error(context="crash_fatal", wait=True)
        print(f"[fretio] CRASH FATAL:\n{crash_msg}", file=sys.stderr, flush=True)
        # Tenta mostrar msgbox para o usuário
        try:
            _show_startup_message(
                QMessageBox.Critical,
                "Fretio - Erro Fatal",
                f"O aplicativo encontrou um erro:\n\n{crash_msg[:800]}",
            )
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
