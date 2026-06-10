#!/usr/bin/env python3
"""
Romaneio - Interface PySide6
"""

import os
import sys
import re
import concurrent.futures
import queue
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QEvent, QUrl
from PySide6.QtGui import (
    QDesktopServices,
    QFont,
    QIcon,
    QColor,
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
    QComboBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
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
    _garantir_defaults_empresa,
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
    CHROME_DOWNLOAD_URL,
    CHROME_MISSING_USER_MESSAGE,
    carrier_login_indicator_from_progress_payload,
    cotar_transportadoras_romaneio_colado,
    formatar_resultados_cotacao,
    setup_global_exception_handler,
    TransportadoraSession,
    ResultadoCotacao,
)
from updater import check_for_update, apply_update, get_repo_from_config, needs_restart, restart_app
from license import get_saved_license, save_license, validate_license, get_machine_id, LicenseStatus
from remote_config import fetch_remote_config, get_last_fetch_status
from remote_permissions import ensure_feature_allowed, feature_allowed_or_default
from version_policy import evaluate_minimum_version, parse_semantic_version
from error_reporter import install_global_hooks, report_error, report_error_message, configure as _er_configure
from usage_reporter import (
    configure as _usage_configure,
    report_app_started,
    report_license_validated,
    report_remote_config_fetched,
    report_nfe_imported,
    report_romaneio_processed,
    report_tracking_finished,
    report_tracking_started,
)
from quotation_jobs_client import configure as _quotation_jobs_configure
from quotation_normalization_client import configure as _quotation_normalization_configure
from async_worker import AsyncWorkerLoop
from extrator_nfe import extrair_arquivo as extrair_nfe_arquivo, NotaFiscal, identificar_transportadora, formatar_nota_resumo, parsear_info_complementar
from rastreamento import rastrear_multiplas, ResultadoRastreio, obter_link_rastreio
from fretio.providers.base import find_chrome
from fretio.providers.factory import validate_provider_minimum_config
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
    NfeImportedEvent,
    PdfProcessedEvent,
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
from startup import (
    MandatoryUpdateDeclined,
    _resource_path,
    _fetch_remote_config_sync,
    _run_startup_update_flow,
    _enforce_minimum_version_policy,
    _carregar_versao_app,
    _show_startup_text_input,
    _show_startup_message,
    _migrate_appdata_fretebot_to_fretio,
)
from ui.dialogs import EmpresaSelectorDialog, ConfiguracoesDialog
from ui.dialogs.configuracoes import CAMPOS_CREDENCIAIS, TRANSPORTADORAS_CONFIGURAVEIS


class RomaneioWindow(QMainWindow):
    def __init__(self, empresa_nome: str = "default"):
        super().__init__()
        self.empresa_nome = empresa_nome
        self._config_path = _empresa_config_path(empresa_nome)
        _er_configure(self._config_path)
        _usage_configure(self._config_path)
        _quotation_jobs_configure(self._config_path)
        _quotation_normalization_configure(self._config_path)
        self._proxima_empresa: str | None = None
        self.extrator = ExtratorPedidos()
        self.pedidos = []
        self.html_original = ''
        self._romaneio_colado = ""
        self._modo_cotacao = "pdf"
        self._sessao = TransportadoraSession(config_path=self._config_path)
        if isinstance(self._sessao.config, dict):
            defaults_changed = _garantir_defaults_fretio(self._sessao.config)
            defaults_changed = _garantir_defaults_empresa(self._sessao.config) or defaults_changed
            if defaults_changed:
                _escrever_config_toml(self._sessao.config, self._config_path)
        self._async_loop = AsyncWorkerLoop(name="RomaneioAsyncLoop")
        self._session_task_lock = threading.Lock()
        self._async_futures: set[concurrent.futures.Future] = set()
        self._async_futures_lock = threading.Lock()
        self._shutdown_started = threading.Event()
        self._cotacao_total = 0
        self._cotacao_concluidas = 0
        self._cotacao_status_rows: dict[str, int] = {}
        self._forn_cotacao_status_rows: dict[str, int] = {}
        self._cep_origem_override = ""
        self._romaneios_processados: list[dict] = []
        self._last_cotacao_results: list = []
        self._tracking_started_at: float | None = None
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

        self._chrome_warning_frame = QFrame()
        self._chrome_warning_frame.setObjectName("ChromeWarningFrame")
        chrome_warning_layout = QHBoxLayout(self._chrome_warning_frame)
        chrome_warning_layout.setContentsMargins(10, 8, 10, 8)
        chrome_warning_layout.setSpacing(10)
        self._chrome_warning_label = QLabel(CHROME_MISSING_USER_MESSAGE)
        self._chrome_warning_label.setObjectName("ChromeWarningLabel")
        self._chrome_warning_label.setWordWrap(True)
        chrome_warning_layout.addWidget(self._chrome_warning_label, 1)
        self.btn_instalar_chrome = QPushButton("Instalar Google Chrome")
        self.btn_instalar_chrome.setObjectName("SecondaryButton")
        self.btn_instalar_chrome.clicked.connect(self._abrir_instalacao_chrome)
        chrome_warning_layout.addWidget(self.btn_instalar_chrome, 0, Qt.AlignRight)
        self._chrome_warning_frame.setVisible(False)
        header_layout.addWidget(self._chrome_warning_frame)

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
            ("ROMANEIOS", "0", "processados nesta sessão", "KpiValueAccent"),
            ("VOLUMES", "0", "volumes processados", "KpiValue"),
            ("MELHOR FRETE", "—", "inicie uma cotação", "KpiValueGreen"),
            ("SUCESSO COTAÇÃO", "—", "aguardando retorno", "KpiValueAmber"),
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
        rh_context = QLabel("sessão atual")
        rh_context.setObjectName("SectionHint")
        rh_layout.addWidget(rh_lbl)
        rh_layout.addStretch(1)
        rh_layout.addWidget(rh_context)
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
        carr_lbl = QLabel("STATUS DAS TRANSPORTADORAS")
        carr_lbl.setObjectName("SectionLabel")
        carr_vlayout.addWidget(carr_lbl)
        carr_hint = QLabel("Mostra apenas transportadoras habilitadas para cotação.")
        carr_hint.setObjectName("SectionHint")
        carr_hint.setWordWrap(True)
        carr_vlayout.addWidget(carr_hint)

        self._home_carrier_info: dict[str, tuple[QFrame, QLabel]] = {}
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

        if not self._home_carrier_info:
            carr_vlayout.addWidget(
                self._criar_estado_vazio_dashboard(
                    "Nenhuma transportadora habilitada",
                    "Abra Configurações e habilite as transportadoras que serão usadas nas cotações.",
                )
            )

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

        self.btn_quote_colado = QPushButton("Iniciar cotação")
        self.btn_quote_colado.setObjectName("PrimaryButton")
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
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(10)
        lbl_colado = QLabel("1. Cole ou revise o romaneio")
        lbl_colado.setObjectName("CotacaoCardTitle")
        lbl_colado_desc = QLabel("Use o texto processado do PDF ou cole o romaneio completo antes de iniciar.")
        lbl_colado_desc.setObjectName("SubtitleLabel")
        lbl_colado_desc.setWordWrap(True)
        self.cotacao_input_hint = QLabel("Aguardando romaneio para liberar a cotação.")
        self.cotacao_input_hint.setObjectName("CotacaoHintLabel")
        self.cotacao_input_hint.setWordWrap(True)
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
        left_layout.addWidget(lbl_colado_desc)
        left_layout.addWidget(self.romaneio_colado_text, 1)
        left_layout.addWidget(self.cotacao_input_hint)
        left_layout.addWidget(self.btn_quote_colado, 0, Qt.AlignLeft)
        self.cotacao_run_status = QLabel("Pronto para cotar assim que houver romaneio.")
        self.cotacao_run_status.setObjectName("CotacaoStatusLabel")
        self.cotacao_run_status.setWordWrap(True)
        left_layout.addWidget(self.cotacao_run_status)
        self.progress_bar = IndeterminateBar()
        self.progress_bar.setVisible(False)
        left_layout.addWidget(self.progress_bar)

        # Coluna direita — resultado da cotação
        right_card = QFrame()
        right_card.setObjectName("Card")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(14, 14, 14, 14)
        right_layout.setSpacing(10)
        result_header = QHBoxLayout()
        lbl_resultado = QLabel("2. Acompanhe e leia o resultado")
        lbl_resultado.setObjectName("CotacaoCardTitle")
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
        self.result_text.setPlaceholderText("O resultado calculado pelas transportadoras aparecerá aqui.")
        self.cotacao_summary_label = QLabel("Nenhuma cotação iniciada nesta tela.")
        self.cotacao_summary_label.setObjectName("CotacaoSummaryLabel")
        self.cotacao_summary_label.setWordWrap(True)
        self.cotacao_status_table = self._criar_tabela_status_cotacao()
        right_layout.addLayout(result_header)
        right_layout.addWidget(self.cotacao_summary_label)
        right_layout.addWidget(self.cotacao_status_table, 0)
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
        self.forn_cotacao_status_table = self._criar_tabela_status_cotacao()
        forn_right_layout.addLayout(forn_result_header)
        forn_right_layout.addWidget(self.forn_cotacao_status_table, 0)
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
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._cfg_ufs_cbs: dict = {}
        self._cfg_hab_checks: dict = {}
        self._cfg_cred_fields: dict = {}
        self._cfg_cred_warnings: dict = {}

        header = QFrame()
        header.setObjectName("SettingsHero")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 18, 20, 16)
        header_layout.setSpacing(14)

        btn_voltar = QPushButton("← Voltar")
        btn_voltar.setObjectName("BackButton")
        btn_voltar.clicked.connect(lambda: self._show_page(0))
        header_layout.addWidget(btn_voltar, 0, Qt.AlignTop)

        gear = QLabel()
        gear.setObjectName("SettingsGear")
        gear.setPixmap(svg_icon(NAV_ICONS["cog"], 24, "#ffffff"))
        gear.setFixedSize(44, 44)
        gear.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(gear, 0, Qt.AlignTop)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        title = QLabel("CONFIGURAÇÕES")
        title.setObjectName("SettingsTitle")
        subtitle = QLabel("Gerencie empresa, aparência, transportadoras e credenciais operacionais.")
        subtitle.setObjectName("SettingsSubtitle")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        header_layout.addLayout(title_col, 1)
        layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("SettingsScroll")
        content = QWidget()
        content.setObjectName("SettingsSurface")
        grid = QGridLayout(content)
        grid.setContentsMargins(20, 18, 20, 20)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)

        grid.addWidget(self._build_card_empresa_inline(), 0, 0)
        grid.addWidget(self._build_card_aparencia_inline(), 0, 1)
        grid.addWidget(self._build_card_transportadoras_inline(), 1, 0)
        grid.addWidget(self._build_card_credenciais_inline(), 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(1, 1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        return page

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

    def _provider_user_key(self, nome: str) -> str | None:
        for chave, label, eh_senha in CAMPOS_CREDENCIAIS.get(nome, []):
            label_l = str(label).lower()
            if not eh_senha and ("usu" in label_l or "email" in label_l or chave in ("usuario", "email", "cnpj")):
                return chave
        campos = CAMPOS_CREDENCIAIS.get(nome, [])
        return campos[0][0] if campos else None

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
            "Ajustes visuais locais. A alternância claro/escuro continua disponível na sidebar.",
        )
        cfg = self._sessao.config if isinstance(self._sessao.config, dict) else {}
        fb = cfg.get("fretio", {}) or {}

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

        controls = QGridLayout()
        controls.setHorizontalSpacing(10)
        controls.setVerticalSpacing(9)
        self._cfg_densidade = QComboBox()
        self._cfg_densidade.setObjectName("SettingsCombo")
        self._cfg_densidade.addItems(["Confortável", "Compacta"])
        self._cfg_densidade.setCurrentText(str(fb.get("ui_densidade", "Confortável") or "Confortável"))
        self._cfg_idioma = QComboBox()
        self._cfg_idioma.setObjectName("SettingsCombo")
        self._cfg_idioma.addItems(["Português (Brasil)"])
        self._cfg_idioma.setCurrentText(str(fb.get("ui_idioma", "Português (Brasil)") or "Português (Brasil)"))
        self._cfg_dicas_toggle = ToggleWidget(checked=bool(fb.get("ui_dicas", True)))
        controls.addWidget(self._setting_field_label("Densidade"), 0, 0)
        controls.addWidget(self._cfg_densidade, 0, 1)
        controls.addWidget(self._setting_field_label("Idioma"), 1, 0)
        controls.addWidget(self._cfg_idioma, 1, 1)
        controls.addWidget(self._setting_field_label("Mostrar dicas"), 2, 0)
        controls.addWidget(self._cfg_dicas_toggle, 2, 1, Qt.AlignLeft)
        controls.setColumnStretch(1, 1)
        layout.addLayout(controls)

        btn_salvar = QPushButton("Salvar aparência")
        btn_salvar.clicked.connect(self._salvar_aparencia_embutido)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(btn_salvar)
        layout.addLayout(footer)
        return card

    def _build_card_transportadoras_inline(self) -> QFrame:
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
        card, layout = self._settings_card(
            "CREDENCIAIS",
            "Tabela de acessos por transportadora, sem expor senhas na interface.",
        )
        cfg = self._sessao.config if isinstance(self._sessao.config, dict) else {}
        transp_cfg = cfg.get("transportadoras", {}) or {}
        table = QGridLayout()
        table.setHorizontalSpacing(8)
        table.setVerticalSpacing(7)
        headers = ["Transportadora", "Usuário", "Status", "Última verificação"]
        for col, header in enumerate(headers):
            lbl = QLabel(header)
            lbl.setObjectName("SettingsTableHeader")
            table.addWidget(lbl, 0, col)
        for row_idx, nome in enumerate(sorted(CAMPOS_CREDENCIAIS), start=1):
            campos = CAMPOS_CREDENCIAIS[nome]
            tcfg = transp_cfg.get(nome, {}) or {}
            name = QLabel(nome.upper())
            name.setObjectName("SettingsCarrierName")
            table.addWidget(name, row_idx * 2 - 1, 0)
            fields: dict = {}
            user_key = self._provider_user_key(nome)
            user_edit = None
            extra = QHBoxLayout()
            extra.setSpacing(6)
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
                fields[chave] = le
                if chave == user_key:
                    user_edit = le
                else:
                    mini_wrap = QVBoxLayout()
                    mini_wrap.setSpacing(2)
                    mini_label = QLabel(label)
                    mini_label.setObjectName("SettingsMiniLabel")
                    mini_wrap.addWidget(mini_label)
                    mini_wrap.addWidget(le)
                    extra.addLayout(mini_wrap)
            if user_edit is None:
                user_edit = QLineEdit()
                user_edit.setObjectName("CredField")
            table.addWidget(user_edit, row_idx * 2 - 1, 1)
            self._cfg_cred_fields[nome] = fields
            warning = QLabel("")
            warning.setObjectName("ConfigWarning")
            warning.setWordWrap(True)
            self._cfg_cred_warnings[nome] = warning
            status_text, status_obj = self._transportadora_status_text(nome, tcfg)
            status = QLabel(status_text)
            status.setObjectName(status_obj)
            table.addWidget(status, row_idx * 2 - 1, 2)
            ultima = QLabel(str(tcfg.get("ultima_verificacao", "Nunca") or "Nunca"))
            ultima.setObjectName("SettingsMutedText")
            table.addWidget(ultima, row_idx * 2 - 1, 3)
            if extra.count():
                table.addLayout(extra, row_idx * 2, 1, 1, 3)
            table.addWidget(warning, row_idx * 2, 0, 1, 4)
            cb_hab = self._cfg_hab_checks.get(nome)
            if cb_hab is not None:
                cb_hab.toggled.connect(lambda _checked, n=nome: self._atualizar_aviso_credencial_embutido(n))
            for le in fields.values():
                le.textChanged.connect(lambda _text, n=nome: self._atualizar_aviso_credencial_embutido(n))
            self._atualizar_aviso_credencial_embutido(nome)
        table.setColumnStretch(1, 1)
        layout.addLayout(table)
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

    def _salvar_aparencia_embutido(self):
        cfg = self._sessao.config if isinstance(self._sessao.config, dict) else {}
        fb = cfg.setdefault("fretio", {})
        fb["ui_tema"] = self._theme_mode
        fb["ui_densidade"] = self._cfg_densidade.currentText()
        fb["ui_idioma"] = self._cfg_idioma.currentText()
        fb["ui_dicas"] = self._cfg_dicas_toggle.isChecked()
        _escrever_config_toml(cfg, self._config_path)
        self.label_info.setText("Aparência salva.")

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
        # Aviso pós-save: informa quais transportadoras habilitadas estão com config incompleta
        erros = self._validar_credenciais_embutidas_antes_de_salvar()
        if erros:
            QMessageBox.warning(
                self,
                "Configuração incompleta",
                "As credenciais foram salvas, mas as transportadoras abaixo estão habilitadas "
                "com campos obrigatórios vazios e não serão cotadas:\n\n"
                + "\n".join(erros)
                + "\n\nPreencha os campos indicados para que a cotação funcione corretamente.",
            )

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

    def _apply_style(self):
        dark = self._usar_tema_escuro()
        if dark:
            c_bg = "#0d1117"; c_panel = "#161b22"; c_panel2 = "#1c232c"; c_panel3 = "#21282f"
            c_border = "#262f3a"; c_border_soft = "#1d2530"
            c_ink = "#e6edf3"; c_muted = "#768390"; c_ink2 = "#adbac7"; c_faint = "#444c56"
            c_accent = "#00b4d8"; c_accent_hover = "#0098b8"; c_accent2 = "#0a2030"; c_accent_border = "#0d3d55"
            c_green = "#3fb950"; c_green_dim = "#0d2b16"
            c_red = "#f85149"; c_red_dim = "#2b0e0e"
            c_amber = "#e3b341"; c_amber_dim = "#2b2008"
        else:
            c_bg = "#f0f4f8"; c_panel = "#ffffff"; c_panel2 = "#f8fafc"; c_panel3 = "#f1f5f9"
            c_border = "#e2e8f0"; c_border_soft = "#edf2f7"
            c_ink = "#0f172a"; c_muted = "#64748b"; c_ink2 = "#334155"; c_faint = "#94a3b8"
            c_accent = "#0077b6"; c_accent_hover = "#0369a1"; c_accent2 = "#e0f2fe"; c_accent_border = "#bae6fd"
            c_green = "#16a34a"; c_green_dim = "#dcfce7"
            c_red = "#dc2626"; c_red_dim = "#fee2e2"
            c_amber = "#d97706"; c_amber_dim = "#fef3c7"

        self.setStyleSheet(f"""
            QMainWindow {{ background: {c_bg}; color: {c_ink}; }}
            QDialog {{ background: {c_bg}; color: {c_ink}; }}
            QWidget {{ color: {c_ink}; }}
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
            #CmdKBtn:hover {{ background: {c_panel3}; border-color: {c_accent_border}; }}
            #CmdKText {{ font-size: 12px; color: {c_muted}; }}
            #CmdKKbd {{ font-family: 'JetBrains Mono'; font-size: 10px; padding: 1px 5px;
                        background: {c_panel3}; border: 1px solid {c_border}; border-radius: 3px; color: {c_faint}; }}
            #StatusLabel {{ color: {c_muted}; font-size: 12px; }}
            #ChromeWarningFrame {{ background: {c_amber_dim}; border: 1px solid {c_amber}; border-radius: 8px; }}
            #ChromeWarningLabel {{ color: {c_ink}; font-size: 12px; font-weight: 600; }}
            #FooterLabel {{ font-size: 11px; color: {c_muted}; }}
            #Card {{ background: {c_panel}; border: 1px solid {c_border}; border-radius: 8px; }}
            #SettingsHero {{ background: {c_panel}; border-bottom: 1px solid {c_border}; }}
            #SettingsGear {{ background: {c_accent}; border-radius: 12px; }}
            #SettingsTitle {{ font-size: 22px; font-weight: 800; letter-spacing: 0.08em; color: {c_ink}; }}
            #SettingsSubtitle {{ font-size: 12px; color: {c_muted}; }}
            #SettingsSurface {{ background: {c_bg}; }}
            #SettingsCard {{ background: {c_panel}; border: 1px solid {c_border}; border-radius: 14px; }}
            #SettingsRowCard {{ background: {c_panel2}; border: 1px solid {c_border_soft}; border-radius: 10px; }}
            #SettingsCardTitle {{ font-size: 12px; font-weight: 800; letter-spacing: 0.12em; color: {c_ink}; }}
            #SettingsCardSubtitle {{ font-size: 11px; color: {c_muted}; }}
            #SettingsFieldLabel {{ font-size: 11px; font-weight: 700; color: {c_muted}; }}
            #SettingsCarrierName {{ font-size: 11px; font-weight: 800; letter-spacing: 0.08em; color: {c_ink2}; }}
            #SettingsTableHeader {{ font-size: 10px; font-weight: 800; letter-spacing: 0.07em; color: {c_muted}; padding-bottom: 4px; }}
            #SettingsMutedText {{ font-size: 11px; color: {c_muted}; }}
            #SettingsMiniLabel {{ font-size: 10px; color: {c_faint}; }}
            QPushButton#ThemeOption, QPushButton#ThemeOptionActive {{ border: 1px solid {c_border}; border-radius: 9px; padding: 8px 12px; font-weight: 700; }}
            QPushButton#ThemeOption {{ background: {c_panel2}; color: {c_ink2}; }}
            QPushButton#ThemeOption:hover {{ background: {c_panel3}; border-color: {c_accent_border}; }}
            QPushButton#ThemeOptionActive {{ background: {c_accent2}; color: {c_accent}; border-color: {c_accent_border}; }}
            QComboBox#SettingsCombo {{ background: {c_panel2}; color: {c_ink}; border: 1px solid {c_border}; border-radius: 6px; padding: 6px 8px; }}
            QComboBox#SettingsCombo::drop-down {{ border: none; width: 22px; }}
            QCheckBox#UfChip {{ font-size: 10px; color: {c_ink2}; spacing: 2px; }}
            #SubtitleLabel {{ font-size: 12px; color: {c_muted}; }}
            #CotacaoCardTitle {{ font-size: 15px; font-weight: 700; color: {c_ink}; }}
            #CotacaoHintLabel {{ background: {c_panel2}; color: {c_muted}; border: 1px solid {c_border}; border-radius: 7px; padding: 7px 9px; font-size: 12px; }}
            #CotacaoStatusLabel {{ color: {c_muted}; font-size: 12px; font-weight: 600; }}
            #CotacaoSummaryLabel {{ background: {c_accent2}; color: {c_ink2}; border: 1px solid {c_accent_border}; border-radius: 8px; padding: 8px 10px; font-size: 12px; font-weight: 600; }}
            #KpiLabel {{ font-size: 10px; font-weight: 700; letter-spacing: 0.1em; color: {c_muted}; }}
            #KpiValue {{ font-size: 28px; font-weight: 700; color: {c_ink}; letter-spacing: -0.03em; }}
            #KpiValueAccent {{ font-size: 28px; font-weight: 700; color: {c_accent}; letter-spacing: -0.03em; }}
            #KpiValueGreen {{ font-size: 28px; font-weight: 700; color: {c_green}; letter-spacing: -0.03em; }}
            #KpiValueAmber {{ font-size: 28px; font-weight: 700; color: {c_amber}; letter-spacing: -0.03em; }}
            #KpiSub {{ font-size: 11px; color: {c_muted}; }}
            #SectionLabel {{ font-size: 10px; font-weight: 700; letter-spacing: 0.1em; color: {c_muted}; }}
            #SectionHint {{ font-size: 11px; color: {c_faint}; }}
            #DashboardEmpty {{ background: {c_panel2}; border: 1px dashed {c_border}; border-radius: 8px; }}
            #DashboardEmptyTitle {{ font-size: 12px; font-weight: 700; color: {c_ink2}; }}
            #DashboardEmptyText {{ font-size: 11px; color: {c_muted}; }}
            #LinkLabel {{ font-size: 11px; font-weight: 500; color: {c_accent}; }}
            #SoftSep {{ background: {c_border_soft}; border: none; max-height: 1px; }}
            #TableMono {{ font-family: 'JetBrains Mono'; font-size: 11px; color: {c_faint}; }}
            #TableMono2 {{ font-family: 'JetBrains Mono'; font-size: 11px; color: {c_ink2}; }}
            #TableText {{ font-size: 12px; color: {c_muted}; }}
            #TableMonoBold {{ font-family: 'JetBrains Mono'; font-size: 13px; font-weight: 600; color: {c_ink}; }}
            #CotacaoStatusTable {{ background: {c_panel2}; color: {c_ink}; border: 1px solid {c_border}; border-radius: 8px; gridline-color: {c_border_soft}; font-size: 12px; }}
            #CotacaoStatusTable::item {{ padding: 5px; }}
            #CotacaoStatusTable::item:selected {{ background: {c_accent2}; color: {c_ink}; }}
            #CotacaoStatusTable QHeaderView::section {{ background: {c_panel3}; color: {c_muted}; border: none; border-bottom: 1px solid {c_border}; padding: 5px 7px; font-size: 10px; font-weight: 700; }}
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
            QPushButton:hover {{ background: {c_accent_hover}; }}
            QPushButton:disabled {{ background: {c_panel3}; color: {c_faint}; }}
            QPushButton#SecondaryButton {{ background: {c_panel2}; color: {c_ink2}; border: 1px solid {c_border}; }}
            QPushButton#SecondaryButton:hover {{ background: {c_panel3}; color: {c_ink}; border-color: {c_accent_border}; }}
            #InputField {{ background: {c_panel2}; color: {c_ink}; border: 1px solid {c_border}; border-radius: 6px; padding: 6px 8px; selection-background-color: {c_accent}; }}
            #InputField:focus {{ background: {c_panel}; border: 1px solid {c_accent}; }}
            QLineEdit {{ background: {c_panel2}; color: {c_ink}; border: 1px solid {c_border}; border-radius: 6px; padding: 6px 8px; selection-background-color: {c_accent}; }}
            QLineEdit:focus {{ background: {c_panel}; border: 1px solid {c_accent}; }}
            QPlainTextEdit {{ selection-background-color: {c_accent}; }}
            QListWidget {{ background: {c_panel2}; color: {c_ink}; border: 1px solid {c_border}; border-radius: 8px; padding: 4px; }}
            QListWidget::item {{ padding: 7px 8px; border-radius: 5px; }}
            QListWidget::item:selected {{ background: {c_accent2}; color: {c_ink}; }}
            QListWidget::item:hover {{ background: {c_panel3}; }}
            #FornLabel {{ font-size: 13px; font-weight: 600; color: {c_ink}; padding-right: 6px; }}
            #FornUnit {{ font-size: 12px; color: {c_muted}; }}
            QTabWidget#MainTabs::pane {{ border: 1px solid {c_border}; border-radius: 10px; background: {c_panel}; }}
            QTabBar::tab {{ background: {c_panel2}; color: {c_muted}; border: 1px solid {c_border};
                           padding: 7px 12px; margin-right: 4px; border-top-left-radius: 8px;
                           border-top-right-radius: 8px; }}
            QTabBar::tab:selected {{ background: {c_panel}; color: {c_ink}; border-bottom-color: {c_panel}; }}
            QTabBar::tab:hover {{ background: {c_panel3}; color: {c_ink}; }}
            #SettingsGroup {{ border: 1px solid {c_border}; border-radius: 8px;
                             padding: 12px 10px 10px 10px; margin-top: 6px; background: {c_panel}; }}
            QGroupBox#SettingsGroup {{ border: 1px solid {c_border}; background: {c_panel}; border-radius: 8px; margin-top: 0px; }}
            QGroupBox#SettingsGroup::title {{ subcontrol-origin: margin; height: 0px; width: 0px; padding: 0px; color: transparent; }}
            #TranspTitle {{ font-size: 17px; font-weight: 700; color: {c_ink}; padding: 6px 0 8px 0; }}
            #ConfigWarning {{ color: {c_amber}; background: {c_amber_dim};
                              border: 1px solid {c_amber}; border-radius: 6px; padding: 6px 8px; }}
            #CredField {{ border: 1px solid {c_border}; border-radius: 6px; padding: 5px 8px;
                         background: {c_panel2}; color: {c_ink}; }}
            QPushButton#MiniButton {{ background: {c_panel2}; color: {c_ink2};
                                     border: 1px solid {c_border}; border-radius: 4px;
                                     padding: 2px 8px; font-size: 11px; }}
            QPushButton#MiniButton:hover {{ background: {c_panel3}; border-color: {c_accent_border}; }}
            QCheckBox {{ color: {c_ink}; spacing: 4px; }}
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: {c_panel2}; width: 10px; margin: 0; border-radius: 5px; }}
            QScrollBar::handle:vertical {{ background: {c_faint}; min-height: 28px; border-radius: 5px; }}
            QScrollBar::handle:vertical:hover {{ background: {c_muted}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            QScrollBar:horizontal {{ background: {c_panel2}; height: 10px; margin: 0; border-radius: 5px; }}
            QScrollBar::handle:horizontal {{ background: {c_faint}; min-width: 28px; border-radius: 5px; }}
            QScrollBar::handle:horizontal:hover {{ background: {c_muted}; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
        """)

        for bar_name in ("progress_bar", "forn_progress_bar", "rastreio_progress_bar"):
            bar = getattr(self, bar_name, None)
            if hasattr(bar, "set_theme"):
                bar.set_theme(c_panel2, c_border, c_accent)

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
        if hasattr(self, '_cfg_dicas_toggle'):
            self._cfg_dicas_toggle.refresh_theme(c_accent, c_faint)

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
        if (
            index in (2, 3)
            and feature_allowed_or_default("cotacao")
            and not self._pre_login_done
            and not self._is_shutting_down()
        ):
            self._run_pre_login()
            self._pre_login_done = True

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
        self.label_info.setStyleSheet("color: #1f6feb;")

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
        table.setHorizontalHeaderLabels(["Transportadora", "Situação", "Etapa", "Mensagem", "Tempo"])
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
        if not ensure_feature_allowed("cotacao", self):
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
        self._resetar_tabela_status_cotacao(fornecedor=True)
        self.forn_progress_bar.setVisible(True)
        self.forn_progress_bar.start_anim()
        self.forn_result_text.setPlainText("Cotação em andamento. As respostas serão listadas conforme cada transportadora finalizar.")
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

    def _post_status(self, msg: str) -> None:
        self._post_event_safe(StatusUpdateEvent(str(msg or "")))

    def _post_login_status(self, nome: str, status: str) -> None:
        self._post_event_safe(LoginStatusEvent(str(nome or ""), str(status or "")))

    def _post_cotacao_progress(self, payload: dict[str, Any]) -> None:
        self._post_event_safe(CotacaoProgressEvent(payload or {}))

    def _post_rastreio_progress(self, indice: int, total: int, resultado: Any) -> None:
        self._post_event_safe(RastreioResultEvent(indice, total, resultado))

    def _mostrar_chrome_ausente(self) -> None:
        self._chrome_warning_label.setText(CHROME_MISSING_USER_MESSAGE)
        self._chrome_warning_frame.setVisible(True)
        self.label_info.setText(CHROME_MISSING_USER_MESSAGE)
        self.label_info.setStyleSheet("color: #b42318;")
        for dot in getattr(self, "_login_status_dots", {}).values():
            dot.set_status("fail")

    def _set_carrier_login_status(self, nome: str, status: str) -> None:
        dot = self._login_status_dots.get(nome)
        if dot is not None:
            dot.set_status(status)
        pair = self._home_carrier_info.get(nome)
        if pair is None:
            return
        cr_dot, cr_tag = pair
        color_map = {
            "ok": ("#3fb950", "online", "TagGreen"),
            "fail": ("#f85149", "erro", "TagRed"),
            "pending": ("#e3b341", "aguardando", "TagAmber"),
        }
        color, text, tag_obj = color_map.get(status, ("#768390", "—", "TagAmber"))
        cr_dot.setStyleSheet(f"border-radius:3px;background:{color};")
        cr_tag.setText(text)
        cr_tag.setObjectName(tag_obj)
        cr_tag.style().unpolish(cr_tag)
        cr_tag.style().polish(cr_tag)

    def _abrir_instalacao_chrome(self) -> None:
        QDesktopServices.openUrl(QUrl(CHROME_DOWNLOAD_URL))

    def _is_shutting_down(self) -> bool:
        return self._shutdown_started.is_set()

    def _start_daemon_worker(self, target) -> bool:
        if self._is_shutting_down():
            return False
        threading.Thread(target=target, daemon=True).start()
        return True

    def _run_sync_worker(
        self,
        target: Callable[[], Any],
        *,
        context: str,
        log_label: str,
        on_success: Callable[[Any], None] | None = None,
        ui_error_handler: Callable[[BaseException], None] | None = None,
    ) -> bool:
        def _worker():
            try:
                result = target()
                if on_success is not None and not self._is_shutting_down():
                    on_success(result)
            except Exception as exc:
                self._handle_async_worker_failure(
                    exc=exc,
                    context=context,
                    log_label=log_label,
                    ui_error_handler=ui_error_handler,
                )

        return self._start_daemon_worker(_worker)

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
        print(
            (
                "[fretio] worker failed "
                f"context={context} label={log_label} "
                f"thread={threading.current_thread().name} "
                f"exc={type(exc).__name__}: {exc}"
            ),
            file=sys.stderr,
            flush=True,
        )
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
        self._run_async_worker(
            lambda: self._sessao.inicializar(
                callback=self._post_status,
                login_status_callback=self._post_login_status,
            ),
            context="pre_login",
            log_label="Erro no pre-login",
            sync_lock=self._session_task_lock,
            on_success=lambda _result: self._post_event_safe(StatusUpdateEvent(CHROME_MISSING_USER_MESSAGE))
            if self._sessao.chrome_missing else None,
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
                self._post_event_safe(UpdateResultEvent("Erro ao cotar transportadoras. Tente novamente em alguns minutos.")),
                self._post_event_safe(UpdateFinishedEvent()),
            ),
        )

    async def _cotar_transportadoras_async(self):
        try:
            if not self._sessao.pronto:
                self._post_status("Executando pre-login antes da cotação...")
                await self._sessao.inicializar(
                    callback=self._post_status,
                    login_status_callback=self._post_login_status,
                )
                self._pre_login_done = True
                if self._sessao.chrome_missing:
                    self._post_event_safe(UpdateResultEvent(CHROME_MISSING_USER_MESSAGE))
                    return

            _cotar_kwargs = dict(
                romaneio_colado=self._romaneio_colado,
                cep_origem=self._cep_origem_override,
                sessao=self._sessao,
                progresso_callback=self._post_cotacao_progress,
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
            self._post_event_safe(UpdateResultEvent("Erro ao cotar transportadoras. Tente novamente em alguns minutos."))
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
        if isinstance(event, PdfProcessedEvent):
            self._on_pdf_processed(event)
            return
        if isinstance(event, NfeImportedEvent):
            self._on_nfe_imported(event)
            return
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
                if hasattr(self, "cotacao_summary_label"):
                    total = len(getattr(self, "_last_cotacao_results", []) or [])
                    ok_count = len([
                        r for r in getattr(self, "_last_cotacao_results", []) or []
                        if getattr(r, "status", "") == "ok" and getattr(r, "valor_frete", None) is not None
                    ])
                    if total > 0:
                        self.cotacao_summary_label.setText(
                            f"Resultado final: {ok_count} de {total} transportadora(s) com cotação válida."
                        )
                    else:
                        self.cotacao_summary_label.setText("Cotação finalizada sem retorno de transportadoras.")
                if hasattr(self, "cotacao_run_status"):
                    self.cotacao_run_status.setText("Cotação finalizada. Confira o resultado ao lado.")
            if event.result == CHROME_MISSING_USER_MESSAGE:
                self._mostrar_chrome_ausente()
            else:
                self.label_info.setText("Cotações finalizadas")
                self.label_info.setStyleSheet("color: #067647;")
            self._verificar_erro_divergencia_uf(event.result)
        elif isinstance(event, CotacaoProgressEvent):
            payload = event.payload or {}
            total = int(payload.get("total", 0) or 0)
            concluidas = int(payload.get("concluidas", 0) or 0)
            resultado = payload.get("resultado")
            self._atualizar_tabela_status_cotacao(payload, fornecedor=is_forn)
            promoted_login_status = carrier_login_indicator_from_progress_payload(payload)
            if promoted_login_status is not None:
                self._set_carrier_login_status(*promoted_login_status)

            if total > 0:
                self._cotacao_total = total
                self._cotacao_concluidas = concluidas
            if total > 0:
                resumo_progresso = self._resumir_progresso_cotacao(total, concluidas, resultado)
                if not is_forn and hasattr(self, "cotacao_summary_label"):
                    self.cotacao_summary_label.setText(resumo_progresso)
                if not is_forn and hasattr(self, "cotacao_run_status"):
                    self.cotacao_run_status.setText(resumo_progresso)
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
            if event.msg == CHROME_MISSING_USER_MESSAGE:
                self._mostrar_chrome_ausente()
                return
            self.label_info.setText(event.msg)
            self.label_info.setStyleSheet("color: #1f6feb;")
        elif isinstance(event, LoginStatusEvent):
            self._set_carrier_login_status(event.nome, event.status)
    def _registrar_romaneio(self, arquivo: str) -> None:
        from datetime import date as _date
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
                cleanup_coro_factory=lambda: asyncio.wait_for(self._sessao.cleanup(), timeout=2),
                cancel_first=True,
            )

        t = threading.Thread(target=_cleanup_background, name="RomaneioShutdownCleanup")
        t.start()
        # Não bloqueia o fechamento — cleanup roda em background e o processo encerra logo


    def _selecionar_nfe(self):
        """Abre dialogo para selecionar arquivos XML de NF-e (um ou varios)."""
        if not ensure_feature_allowed("nfe", self):
            return
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
        report_app_started()
        _license_validated = False

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
                        report_license_validated("ok")
                        _license_validated = True
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
                            report_license_validated("ok")
                            _license_validated = True
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
                else:
                    report_license_validated("ok")
                    _license_validated = True
        except SystemExit:
            raise
        except Exception as _lic_err:
            report_error(context="verificacao_licenca")
            print(f"[fretio] Verificação de licença falhou: {_lic_err}", file=sys.stderr, flush=True)

        _repo = get_repo_from_config()
        _cur_ver = _carregar_versao_app()

        # ── Política de versão mínima via remote config ──
        if _license_validated:
            _remote_payload = _fetch_remote_config_sync(_startup_logger)
            if not _enforce_minimum_version_policy(
                remote_payload=_remote_payload,
                current_version=_cur_ver,
                repo=_repo,
                startup_logger=_startup_logger,
            ):
                sys.exit(0)

        # ── Verificação de atualização via servidor/GitHub ──
        try:
            _run_startup_update_flow(
                _repo,
                _cur_ver,
                startup_logger=_startup_logger,
                show_verification_error=False,
            )
        except MandatoryUpdateDeclined:
            sys.exit(0)
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
