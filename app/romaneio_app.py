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
from ui import theme as ui_theme
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
from ui.mixins import ConfigMixin, CotacaoMixin, RastreioMixin, DashboardMixin, WorkerMixin


class RomaneioWindow(ConfigMixin, CotacaoMixin, RastreioMixin, DashboardMixin, WorkerMixin, QMainWindow):
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
        _fb_ui = self._sessao.config.get("fretio", {}) or {}
        self._theme_mode = str(_fb_ui.get("ui_tema", "sistema")).lower()
        if self._theme_mode not in ("sistema", "claro", "escuro"):
            self._theme_mode = "sistema"
        self._theme_accent = ui_theme.normalize_accent(_fb_ui.get("ui_accent"))
        self._theme_radius = ui_theme.normalize_radius(_fb_ui.get("ui_raio"))
        self._theme_button = ui_theme.normalize_button(_fb_ui.get("ui_botao"))

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
        for nome in ("braspress", "trd", "agex", "eucatur", "rodonaves", "alfa", "coopex", "translovato"):
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
        for nome in ("braspress", "trd", "agex", "eucatur", "rodonaves", "alfa", "coopex", "translovato"):
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
        lbl_resultado = QLabel("Resultado da cotação")
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
        # Mantidos no codigo para o fluxo de progresso, porem ocultos na tela.
        self.cotacao_summary_label = QLabel("Nenhuma cotação iniciada nesta tela.")
        self.cotacao_summary_label.setObjectName("CotacaoSummaryLabel")
        self.cotacao_summary_label.setWordWrap(True)
        self.cotacao_summary_label.setVisible(False)
        self.cotacao_status_table = self._criar_tabela_status_cotacao()
        self.cotacao_status_table.setVisible(False)
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
        # Mantida no codigo para o fluxo de progresso, porem oculta na tela.
        self.forn_cotacao_status_table = self._criar_tabela_status_cotacao()
        self.forn_cotacao_status_table.setVisible(False)
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

    def _apply_style(self):
        dark = self._usar_tema_escuro()
        palette = ui_theme.build_palette(
            dark,
            accent_name=getattr(self, "_theme_accent", None),
            radius_name=getattr(self, "_theme_radius", None),
            button_style=getattr(self, "_theme_button", None),
        )
        c_panel2 = palette.panel2
        c_border = palette.border
        c_accent = palette.accent
        c_muted = palette.muted
        c_panel3 = palette.panel3
        c_faint = palette.faint
        c_accent2 = palette.accent2
        self._c_info = c_accent

        ui_theme.aplicar_cor_barra_titulo(self, dark=dark, caption_hex=palette.panel, text_hex=palette.ink)
        self.setStyleSheet(ui_theme.build_stylesheet(palette))

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

        # Refresh CmdK search icon
        if hasattr(self, '_cmd_icon_lbl'):
            self._cmd_icon_lbl.setPixmap(svg_icon(NAV_ICONS['search'], 13, c_muted))



    def _wrap_page_with_back(self, title_text: str, content_widget: QWidget) -> QWidget:
        # O titulo da pagina ja aparece na barra de topo; aqui mantemos apenas
        # o botao Voltar para evitar cabecalho duplicado.
        del title_text
        page = QWidget()
        page.setObjectName("PageContent")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        page_header = QFrame()
        page_header.setObjectName("PageHeader")
        header_layout = QHBoxLayout(page_header)
        header_layout.setContentsMargins(12, 8, 12, 8)
        header_layout.setSpacing(10)
        btn_back = QPushButton("\u2190 Voltar")
        btn_back.setObjectName("BackButton")
        btn_back.setCursor(Qt.PointingHandCursor)
        btn_back.clicked.connect(lambda: self._show_page(0))
        header_layout.addWidget(btn_back)
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
                    self.label_info.setStyleSheet(f"color: {getattr(self, '_c_info', '#d97757')};")

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
            self.label_info.setStyleSheet(f"color: {getattr(self, '_c_info', '#d97757')};")
        elif isinstance(event, LoginStatusEvent):
            self._set_carrier_login_status(event.nome, event.status)

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
        self.label_info.setStyleSheet(f"color: {getattr(self, '_c_info', '#d97757')};")
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
