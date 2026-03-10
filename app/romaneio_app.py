#!/usr/bin/env python3
"""
Romaneio - Interface PySide6
"""

import os
import sys
import re
from pathlib import Path
from typing import Any
from time import monotonic

from PySide6.QtCore import Qt, QEvent, QTimer, QRectF
from PySide6.QtGui import QFont, QIcon, QColor, QPainter, QPen, QPixmap
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
)

import asyncio
import shutil
import threading

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
from error_reporter import install_global_hooks, report_error, report_error_message


# Eventos customizados para comunicação entre threads
class UdpateResultEvent(QEvent):
    """Evento para atualizar o resultado na UI."""
    EventType = QEvent.Type(QEvent.registerEventType())
    
    def __init__(self, result: str):
        super().__init__(self.EventType)
        self.result = result


class UpdateFinishedEvent(QEvent):
    """Evento para indicar que a cotação terminou."""
    EventType = QEvent.Type(QEvent.registerEventType())
    
    def __init__(self):
        super().__init__(self.EventType)


class StatusUpdateEvent(QEvent):
    """Evento para atualizar o status na UI."""
    EventType = QEvent.Type(QEvent.registerEventType())
    
    def __init__(self, msg: str):
        super().__init__(self.EventType)
        self.msg = msg


class CotacaoProgressEvent(QEvent):
    """Evento para atualizar progresso de cotações em tempo real."""
    EventType = QEvent.Type(QEvent.registerEventType())

    def __init__(self, payload: dict[str, Any]):
        super().__init__(self.EventType)
        self.payload = payload or {}


class LoginStatusEvent(QEvent):
    """Evento para atualizar status de login individual de transportadora."""
    EventType = QEvent.Type(QEvent.registerEventType())

    def __init__(self, nome: str, status: str):
        # status: "pending", "ok", "fail"
        super().__init__(self.EventType)
        self.nome = nome
        self.status = status


class LoginRetryPromptEvent(QEvent):
    """Evento para perguntar ao usuário se quer refazer login de uma transportadora."""
    EventType = QEvent.Type(QEvent.registerEventType())

    def __init__(self, nome: str):
        super().__init__(self.EventType)
        self.nome = nome


# ---------------------------------------------------------------------------
# Helpers de formatação automática para campos do formulário fornecedor
# ---------------------------------------------------------------------------

def _apply_cnpj_mask(line_edit: "QLineEdit") -> None:
    """Conecta textChanged para auto-formatar CNPJ (XX.XXX.XXX/XXXX-XX)."""
    def _fmt():
        d = re.sub(r"\D", "", line_edit.text())[:14]
        if len(d) > 12:
            t = f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
        elif len(d) > 8:
            t = f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:]}"
        elif len(d) > 5:
            t = f"{d[:2]}.{d[2:5]}.{d[5:]}"
        elif len(d) > 2:
            t = f"{d[:2]}.{d[2:]}"
        else:
            t = d
        if t != line_edit.text():
            line_edit.blockSignals(True)
            pos = line_edit.cursorPosition()
            old_len = len(line_edit.text())
            line_edit.setText(t)
            new_len = len(t)
            line_edit.setCursorPosition(min(pos + (new_len - old_len), new_len))
            line_edit.blockSignals(False)
    line_edit.setMaxLength(18)
    line_edit.textChanged.connect(_fmt)


def _apply_cep_mask(line_edit: "QLineEdit") -> None:
    """Conecta textChanged para auto-formatar CEP (XXXXX-XXX)."""
    def _fmt():
        d = re.sub(r"\D", "", line_edit.text())[:8]
        t = f"{d[:5]}-{d[5:]}" if len(d) > 5 else d
        if t != line_edit.text():
            line_edit.blockSignals(True)
            pos = line_edit.cursorPosition()
            old_len = len(line_edit.text())
            line_edit.setText(t)
            new_len = len(t)
            line_edit.setCursorPosition(min(pos + (new_len - old_len), new_len))
            line_edit.blockSignals(False)
    line_edit.setMaxLength(9)
    line_edit.textChanged.connect(_fmt)


def _apply_decimal_mask(line_edit: "QLineEdit", decimals: int = 2) -> None:
    """Conecta textChanged para auto-formatar decimal BR (vírgula, N casas)."""
    def _fmt():
        raw = line_edit.text().replace(".", "").replace(",", "")
        d = re.sub(r"\D", "", raw)
        if not d:
            if line_edit.text():
                line_edit.blockSignals(True)
                line_edit.setText("")
                line_edit.blockSignals(False)
            return
        d = d.lstrip("0") or "0"
        d = d.zfill(decimals + 1)
        inteiro = d[: len(d) - decimals]
        frac = d[len(d) - decimals :]
        t = f"{inteiro},{frac}"
        if t != line_edit.text():
            line_edit.blockSignals(True)
            line_edit.setText(t)
            line_edit.setCursorPosition(len(t))
            line_edit.blockSignals(False)
    line_edit.textChanged.connect(_fmt)


def _apply_currency_mask(line_edit: "QLineEdit") -> None:
    """Conecta textChanged para auto-formatar moeda BR (R$ X,XX)."""
    def _fmt():
        raw = line_edit.text().replace("R$", "").replace(".", "").replace(",", "").replace(" ", "")
        d = re.sub(r"\D", "", raw)
        if not d:
            if line_edit.text():
                line_edit.blockSignals(True)
                line_edit.setText("")
                line_edit.blockSignals(False)
            return
        d = d.lstrip("0") or "0"
        d = d.zfill(3)
        inteiro = d[: len(d) - 2]
        centavos = d[len(d) - 2 :]
        # Adicionar pontos de milhar
        inteiro_fmt = ""
        for i, ch in enumerate(reversed(inteiro)):
            if i > 0 and i % 3 == 0:
                inteiro_fmt = "." + inteiro_fmt
            inteiro_fmt = ch + inteiro_fmt
        t = f"R$ {inteiro_fmt},{centavos}"
        if t != line_edit.text():
            line_edit.blockSignals(True)
            line_edit.setText(t)
            line_edit.setCursorPosition(len(t))
            line_edit.blockSignals(False)
    line_edit.textChanged.connect(_fmt)


class IndeterminateBar(QWidget):
    """Barra de carregamento indeterminada com animação suave (~60 FPS)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ProgressBar")
        self.setMinimumHeight(18)
        self.setMaximumHeight(18)
        self._timer = QTimer(self)
        self._timer.setInterval(16)  # ~60 FPS
        self._timer.timeout.connect(self._tick)
        self._last_t = monotonic()
        self._offset_px = 0.0
        self._speed_px_s = 360.0

    def start_anim(self) -> None:
        self._last_t = monotonic()
        self._offset_px = 0.0
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def stop_anim(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
        self._offset_px = 0.0
        self.update()

    def _chunk_width(self) -> float:
        return max(42.0, min(96.0, self.width() * 0.2))

    def _tick(self) -> None:
        now = monotonic()
        dt = now - self._last_t
        self._last_t = now
        if dt <= 0:
            return
        # Limita delta para evitar saltos quando a janela fica em background.
        dt = min(dt, 0.05)
        self._offset_px += self._speed_px_s * dt
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        h = float(self.height())
        w = float(self.width())
        radius = 5.0

        track_rect = QRectF(0.5, 0.5, max(0.0, w - 1.0), max(0.0, h - 1.0))
        painter.setPen(QPen(QColor("#cfd8ea"), 1.0))
        painter.setBrush(QColor("#e9eef7"))
        painter.drawRoundedRect(track_rect, radius, radius)

        if w <= 2 or h <= 2:
            return

        chunk_w = self._chunk_width()
        span = w + chunk_w
        x = (self._offset_px % span) - chunk_w

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#1f6feb"))
        for shift in (0.0, span):
            xr = x + shift
            chunk_rect = QRectF(xr, 1.0, chunk_w, max(0.0, h - 2.0))
            if chunk_rect.right() < 0 or chunk_rect.left() > w:
                continue
            painter.drawRoundedRect(chunk_rect, radius, radius)


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


# ---------------------------------------------------------------------------
#  Constantes e utilitários — gestão de empresas e configurações
# ---------------------------------------------------------------------------

TODAS_UFS = [
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA",
    "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN",
    "RO", "RR", "RS", "SC", "SE", "SP", "TO",
]

CAMPOS_CREDENCIAIS: dict[str, list[tuple[str, str, bool]]] = {
    "braspress": [("cnpj", "CNPJ", False), ("senha", "Senha", True)],
    "bauer": [
        ("cnpj_pagador", "CNPJ Pagador", False),
        ("cnpj_remetente", "CNPJ Remetente", False),
    ],
    "trd": [("email", "Email", False), ("senha", "Senha", True)],
    "agex": [
        ("cnpj", "CNPJ", False),
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


def _fretebot_appdata_dir() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        d = Path(appdata) / "FreteBot"
    else:
        d = Path.cwd() / "FreteBot_data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _empresas_dir() -> Path:
    d = _fretebot_appdata_dir() / "empresas"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _empresa_config_path(nome: str) -> Path:
    return _empresas_dir() / nome / "CONFIG.toml"


def _listar_empresas() -> list[str]:
    d = _empresas_dir()
    if not d.exists():
        return []
    return sorted(
        [p.name for p in d.iterdir() if p.is_dir() and (p / "CONFIG.toml").exists()],
        key=str.lower,
    )


def _ultima_empresa_path() -> Path:
    return _fretebot_appdata_dir() / "ultima_empresa.txt"


def _ler_ultima_empresa() -> str:
    p = _ultima_empresa_path()
    if p.exists():
        try:
            return p.read_text(encoding="utf-8").strip()
        except Exception:
            return ""
    return ""


def _salvar_ultima_empresa(nome: str) -> None:
    try:
        _ultima_empresa_path().write_text(nome, encoding="utf-8")
    except Exception:
        pass


def _toml_valor(v: Any) -> str:
    """Converte um valor Python em representação TOML."""
    if v is None:
        return '""'
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return f"{v}"
    if isinstance(v, list):
        items = ", ".join(_toml_valor(x) for x in v)
        return f"[{items}]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _escrever_config_toml(config: dict[str, Any], path: Path) -> None:
    """Serializa o dict de config e grava como TOML."""
    lines: list[str] = []
    # Chaves escalares no topo (antes de qualquer seção)
    for key, val in config.items():
        if not isinstance(val, dict):
            lines.append(f"{key} = {_toml_valor(val)}")
    if any(not isinstance(v, dict) for v in config.values()):
        lines.append("")
    # Seções simples (não-transportadoras)
    for key, val in config.items():
        if key == "transportadoras" or not isinstance(val, dict):
            continue
        lines.append(f"[{key}]")
        for k, v in val.items():
            if not isinstance(v, dict):
                lines.append(f"{k} = {_toml_valor(v)}")
        lines.append("")
    # Transportadoras
    transportadoras = config.get("transportadoras", {})
    if isinstance(transportadoras, dict):
        for nome, tcfg in transportadoras.items():
            if isinstance(tcfg, dict):
                lines.append(f"[transportadoras.{nome}]")
                for k, v in tcfg.items():
                    if not isinstance(v, dict):
                        lines.append(f"{k} = {_toml_valor(v)}")
                lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _criar_config_empresa_vazia(nome: str) -> None:
    """Cria CONFIG.toml com todas as transportadoras desabilitadas."""
    config: dict[str, Any] = {
        "fretebot": {"fator_cubagem": 6000, "cache_dir": "cache", "github_repo": "", "license_url": ""},
        "romaneio": {"cep_origem": ""},
        "transportadoras": {
            "braspress": {"habilitado": False, "cnpj": "", "senha": "",
                          "ufs_atendidas": list(TODAS_UFS)},
            "bauer": {"habilitado": False, "cotacao_url": "", "cnpj_pagador": "",
                      "cnpj_remetente": "", "cnpj_destinatario": "", "headless": True,
                      "quantidade": 1, "ufs_atendidas": ["PR", "RS", "SC"]},
            "trd": {"habilitado": False, "email": "", "senha": "", "headless": True,
                    "ufs_atendidas": ["RS", "SC", "PR", "SP", "MG", "ES", "RJ"]},
            "agex": {"habilitado": False, "cnpj": "", "senha": "", "cnpj_remetente": "",
                     "cnpj_destinatario": "", "headless": True,
                     "ufs_atendidas": ["PR", "SP", "GO", "DF", "TO", "PA", "MT", "MS"]},
            "eucatur": {"habilitado": False, "dominio": "", "usuario": "", "senha": "",
                        "ufs_atendidas": ["RR", "AM", "AC", "RO", "MT", "MS"]},
            "rodonaves": {"habilitado": False, "dominio": "RTE", "usuario": "", "senha": "",
                          "cnpj_pagador": "", "login_url": "", "cotacao_url": "",
                          "headless": True, "ufs_atendidas": list(TODAS_UFS)},
            "alfa": {"habilitado": False, "login": "", "senha": "", "cnpj_remetente": "",
                     "login_url": "", "cotacao_url": "", "headless": False,
                     "ufs_atendidas": list(TODAS_UFS)},
            "coopex": {"habilitado": False, "dominio": "", "usuario": "", "senha": "",
                       "ufs_atendidas": []},
        },
    }
    _escrever_config_toml(config, _empresa_config_path(nome))


def _migrar_config_se_necessario() -> None:
    """Se não existem empresas, migra CONFIG.toml existente como 'darlu'."""
    if _listar_empresas():
        return
    base = Path(__file__).resolve().parent
    candidatos = [
        base / "CONFIG.toml",
        base / "fretebot" / "CONFIG.toml",
    ]
    appdata = os.getenv("APPDATA")
    if appdata:
        candidatos.append(Path(appdata) / "FreteBot" / "CONFIG.toml")
    for c in candidatos:
        if c.exists():
            destino = _empresa_config_path("darlu")
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(c), str(destino))
            _salvar_ultima_empresa("darlu")
            return
    _criar_config_empresa_vazia("default")
    _salvar_ultima_empresa("default")


def _renomear_pasta_empresa(nome_atual: str, nome_novo: str) -> bool:
    """Renomeia a pasta da empresa (e atualiza ultima_empresa se necessário)."""
    nome_novo = re.sub(r'[<>:"/\\|?*]', '_', nome_novo.strip())
    if not nome_novo or nome_novo == nome_atual:
        return False
    pasta_atual = _empresas_dir() / nome_atual
    pasta_nova = _empresas_dir() / nome_novo
    # Windows é case-insensitive: se só mudou maiúsculas/minúsculas, usa nome temporário
    apenas_case = nome_atual.lower() == nome_novo.lower()
    if pasta_nova.exists() and not apenas_case:
        return False
    try:
        if apenas_case:
            tmp = pasta_atual.with_name(nome_atual + "_tmp_rename")
            pasta_atual.rename(tmp)
            tmp.rename(pasta_nova)
        else:
            pasta_atual.rename(pasta_nova)
        if _ler_ultima_empresa().lower() == nome_atual.lower():
            _salvar_ultima_empresa(nome_novo)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
#  Diálogo: Seleção de Empresa
# ---------------------------------------------------------------------------

class EmpresaSelectorDialog(QDialog):
    """Tela inicial para escolher com qual empresa operar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FreteBot — Selecionar Empresa")
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
        self.setStyleSheet(
            """
            QDialog { background: #f3f6fb; }
            #TitleLabel { font-size: 20px; font-weight: 700; color: #16213d; }
            #SubtitleLabel { font-size: 12px; color: #5a6b8a; }
            QLabel { color: #1f2a44; }
            QListWidget { background: #fff; color: #1f2a44; border: 1px solid #dde3f0; border-radius: 8px;
                          padding: 6px; font-size: 13px; }
            QListWidget::item { padding: 8px; border-radius: 6px; }
            QListWidget::item:selected { background: #1f6feb; color: #fff; }
            QPushButton { background: #1f6feb; color: #fff; border: none; border-radius: 8px;
                          padding: 10px 18px; font-weight: 600; }
            QPushButton:hover { background: #1a5ed6; }
            QPushButton#SecondaryButton { background: #e9eef7; color: #1f2a44; }
            QPushButton#SecondaryButton:hover { background: #dde6f5; }
            """
        )


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
        tabs.addTab(self._tab_empresa(), "Empresa")
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
                le.setText(str(tcfg.get(chave, "") or ""))
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

    # --- aba Empresa ---
    def _tab_empresa(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        lbl = QLabel(f"Empresa atual:  {self.empresa_nome}")
        lbl.setObjectName("TitleLabel")
        layout.addWidget(lbl)
        rename_row = QHBoxLayout()
        rename_row.setSpacing(8)
        self._edit_nome_empresa = QLineEdit()
        self._edit_nome_empresa.setText(self.empresa_nome)
        self._edit_nome_empresa.setObjectName("CredField")
        self._edit_nome_empresa.setMaximumWidth(300)
        btn_renomear = QPushButton("Renomear")
        btn_renomear.setObjectName("SecondaryButton")
        btn_renomear.clicked.connect(self._renomear_empresa)
        lbl_nome = QLabel("Nome da empresa:")
        rename_row.addWidget(lbl_nome)
        rename_row.addWidget(self._edit_nome_empresa)
        rename_row.addWidget(btn_renomear)
        rename_row.addStretch(1)
        layout.addLayout(rename_row)
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.HLine)
        layout.addWidget(sep1)
        btn_trocar = QPushButton("Trocar de Empresa")
        btn_trocar.setObjectName("SecondaryButton")
        btn_trocar.clicked.connect(self._trocar_empresa)
        layout.addWidget(btn_trocar, 0, Qt.AlignLeft)
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        layout.addWidget(sep)
        rom_cfg = self.config.get("romaneio", {}) or {}
        fb_cfg = self.config.get("fretebot", {}) or {}
        form = QFormLayout()
        self._cep_origem = QLineEdit()
        self._cep_origem.setText(str(rom_cfg.get("cep_origem", "") or ""))
        self._cep_origem.setObjectName("CredField")
        self._cep_origem.setMaximumWidth(200)
        form.addRow("CEP de Origem:", self._cep_origem)
        self._paralelo_val = max(1, min(7, int(fb_cfg.get("max_paralelo", 3) or 3)))
        paralelo_row = QHBoxLayout()
        paralelo_row.setSpacing(6)
        self._lbl_paralelo = QLabel(str(self._paralelo_val))
        self._lbl_paralelo.setFixedWidth(28)
        self._lbl_paralelo.setAlignment(Qt.AlignCenter)
        self._lbl_paralelo.setObjectName("CredField")
        btn_menos = QPushButton("−")
        btn_menos.setObjectName("MiniButton")
        btn_menos.setFixedSize(28, 28)
        btn_mais = QPushButton("+")
        btn_mais.setObjectName("MiniButton")
        btn_mais.setFixedSize(28, 28)
        btn_menos.clicked.connect(lambda: self._ajustar_paralelo(-1))
        btn_mais.clicked.connect(lambda: self._ajustar_paralelo(1))
        paralelo_row.addWidget(btn_menos)
        paralelo_row.addWidget(self._lbl_paralelo)
        paralelo_row.addWidget(btn_mais)
        paralelo_row.addStretch(1)
        paralelo_widget = QWidget()
        paralelo_widget.setLayout(paralelo_row)
        form.addRow("Cotações em paralelo:", paralelo_widget)
        layout.addLayout(form)
        layout.addStretch(1)
        return tab

    def _ajustar_paralelo(self, delta: int):
        self._paralelo_val = max(1, min(7, self._paralelo_val + delta))
        self._lbl_paralelo.setText(str(self._paralelo_val))

    def _renomear_empresa(self):
        novo = self._edit_nome_empresa.text().strip()
        novo = re.sub(r'[<>:"/\\|?*]', '_', novo)
        if not novo or novo == self.empresa_nome:
            return
        if _renomear_pasta_empresa(self.empresa_nome, novo):
            QMessageBox.information(self, "Sucesso", f"Empresa renomeada para '{novo}'")
            self.empresa_trocada = novo
            self.accept()
        else:
            QMessageBox.warning(self, "Erro",
                                f"Não foi possível renomear para '{novo}'.\n"
                                "Verifique se o nome já existe.")

    def _trocar_empresa(self):
        dlg = EmpresaSelectorDialog(self)
        if dlg.exec() == QDialog.Accepted and dlg.empresa_selecionada:
            self.empresa_trocada = dlg.empresa_selecionada
            self.accept()

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
        # CEP origem
        rom_cfg = self.config.setdefault("romaneio", {})
        rom_cfg["cep_origem"] = self._cep_origem.text().strip()
        # Cotações em paralelo
        fb_cfg = self.config.setdefault("fretebot", {})
        fb_cfg["max_paralelo"] = self._paralelo_val
        _escrever_config_toml(self.config, self.config_path)
        self._credenciais_mudaram = cred_changed
        QMessageBox.information(self, "Sucesso", "Configurações salvas!")
        self.accept()

    def _apply_style(self):
        self.setStyleSheet(
            """
            QDialog { background: #f3f6fb; }
            QLabel { color: #1f2a44; }
            #TitleLabel { font-size: 18px; font-weight: 700; color: #16213d; }
            #SettingsGroup { border: 1px solid #dde3f0; border-radius: 8px;
                             padding: 12px 10px 10px 10px; margin-top: 6px; background: #fff; }
            #TranspTitle { font-size: 17px; font-weight: 700; color: #16213d;
                           padding: 6px 0 8px 0; }
            #CredField { border: 1px solid #cfd8ea; border-radius: 6px; padding: 5px 8px;
                         background: #fff; color: #1f2a44; }
            QTabWidget#MainTabs::pane { border: 1px solid #dde3f0; border-radius: 10px; background: #fff; }
            QTabBar::tab { background: #e9eef7; color: #1f2a44; border: 1px solid #dde3f0;
                           padding: 7px 12px; margin-right: 4px; border-top-left-radius: 8px;
                           border-top-right-radius: 8px; }
            QTabBar::tab:selected { background: #fff; border-bottom-color: #fff; }
            QPushButton { background: #1f6feb; color: #fff; border: none; border-radius: 8px;
                          padding: 9px 16px; font-weight: 600; }
            QPushButton:hover { background: #1a5ed6; }
            QPushButton#SecondaryButton { background: #e9eef7; color: #1f2a44; }
            QPushButton#SecondaryButton:hover { background: #dde6f5; }
            QPushButton#MiniButton { background: #e9eef7; color: #1f2a44; border: 1px solid #cfd8ea;
                                     border-radius: 4px; padding: 2px 8px; font-size: 11px; }
            QPushButton#MiniButton:hover { background: #dde6f5; }
            QCheckBox { color: #1f2a44; spacing: 4px; }
            QLineEdit { color: #1f2a44; background: #fff; }
            QScrollArea { background: transparent; border: none; }
            """
        )


class RomaneioWindow(QMainWindow):
    def __init__(self, empresa_nome: str = "default"):
        super().__init__()
        self.empresa_nome = empresa_nome
        self._config_path = _empresa_config_path(empresa_nome)
        self._proxima_empresa: str | None = None
        self.extrator = ExtratorPedidos()
        self.pedidos = []
        self.html_original = ''
        self._romaneio_colado = ""
        self._modo_cotacao = "pdf"
        self._sessao = TransportadoraSession(config_path=self._config_path)
        self._loop = asyncio.new_event_loop()
        self._loop_lock = threading.Lock()
        self._cotacao_total = 0
        self._cotacao_concluidas = 0
        self._cep_origem_override = ""
        self.app_version = _carregar_versao_app()
        self.app_name = f"Romaneio Beta {self.app_version} \u2014 {empresa_nome}"

        self.setWindowTitle(self.app_name)
        icon_path = _resource_path("assets/romaneio.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.setMinimumSize(980, 620)
        self._build_ui()
        # Pre-login em background
        threading.Thread(target=self._run_pre_login, daemon=True).start()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        header = QFrame()
        header.setObjectName("Card")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(18, 16, 18, 16)
        header_layout.setSpacing(6)

        header_top = QHBoxLayout()
        header_top.setSpacing(10)
        self.btn_config = QPushButton("⚙ Configurações")
        self.btn_config.setObjectName("ConfigButton")
        self.btn_config.clicked.connect(self._abrir_configuracoes)
        title = QLabel(self.empresa_nome)
        title.setObjectName("TitleLabel")
        header_top.addWidget(title)
        header_top.addStretch(1)
        header_top.addWidget(self.btn_config)
        header_layout.addLayout(header_top)
        self.label_info = QLabel("Nenhum arquivo carregado")
        self.label_info.setObjectName("StatusLabel")
        header_layout.addWidget(self.label_info)

        # --- Painel de status de login das transportadoras ---
        login_status_row = QHBoxLayout()
        login_status_row.setSpacing(12)
        self._login_status_labels: dict[str, QLabel] = {}
        config = self._sessao.config if hasattr(self._sessao, 'config') else {}
        transp_cfg = config.get("transportadoras", {}) if isinstance(config, dict) else {}
        for nome in ("braspress", "bauer", "trd", "agex", "eucatur", "rodonaves", "alfa", "coopex"):
            tcfg = transp_cfg.get(nome, {}) if isinstance(transp_cfg, dict) else {}
            if not tcfg.get("habilitado", False):
                continue
            lbl = QLabel(f"⏳ {nome.upper()}")
            lbl.setStyleSheet("color: #8896ab; font-size: 11px; font-weight: 600;")
            login_status_row.addWidget(lbl)
            self._login_status_labels[nome] = lbl
        login_status_row.addStretch(1)
        self.btn_relogin = QPushButton("🔄 Refazer Login")
        self.btn_relogin.setObjectName("SecondaryButton")
        self.btn_relogin.setFixedHeight(28)
        self.btn_relogin.setStyleSheet(
            "font-size: 11px; padding: 4px 10px; background: #e9eef7; color: #1f2a44; "
            "border: 1px solid #b0bdd0; border-radius: 6px; font-weight: 600;"
        )
        self.btn_relogin.clicked.connect(self._relogin_manual)
        login_status_row.addWidget(self.btn_relogin)
        header_layout.addLayout(login_status_row)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("MainTabs")

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
        lbl_resultado = QLabel("Resultado da cotação:")
        lbl_resultado.setObjectName("SubtitleLabel")
        self.result_text = QPlainTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setObjectName("ResultText")
        self.result_text.setPlainText("")
        right_layout.addWidget(lbl_resultado)
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
        lbl_forn_result = QLabel("Resultado da cota\u00e7\u00e3o:")
        lbl_forn_result.setObjectName("SubtitleLabel")
        self.forn_result_text = QPlainTextEdit()
        self.forn_result_text.setReadOnly(True)
        self.forn_result_text.setObjectName("ResultText")
        forn_right_layout.addWidget(lbl_forn_result)
        forn_right_layout.addWidget(self.forn_result_text, 1)

        tab_forn_layout.addWidget(forn_left, 1)
        tab_forn_layout.addWidget(forn_right, 1)

        self.tabs.addTab(tab_pdf, "ROMANEIO")
        self.tabs.addTab(tab_colado, "CALCULAR FRETE")
        self.tabs.addTab(tab_fornecedor, "FRETE FORNECEDORES")

        footer = QHBoxLayout()
        footer.setSpacing(10)
        lbl_app_name = QLabel(f"Romaneio Beta {self.app_version}")
        lbl_app_name.setObjectName("FooterLabel")
        footer.addStretch(1)
        footer.addWidget(lbl_app_name)

        root.addWidget(header)
        root.addWidget(self.tabs)
        root.addLayout(footer)

        self._apply_style()

    def _apply_style(self):
        self.setStyleSheet(
            """
            QMainWindow { background: #f3f6fb; }
            #Card { background: #ffffff; border: 1px solid #dde3f0; border-radius: 12px; }
            #TitleLabel { font-size: 22px; font-weight: 700; color: #16213d; }
            #SubtitleLabel { font-size: 13px; color: #5a6b8a; }
            #StatusLabel { color: #6b7a96; }
            #FooterLabel { font-size: 11px; color: #8896ab; }
            #InputText { background: #ffffff; color: #1f2a44; border: 1px solid #cfd8ea; border-radius: 8px; padding: 8px; font-family: Consolas; font-size: 10.5pt; }
            #ResultText { background: #0f172a; color: #e2e8f0; border: 1px solid #1f2a44; border-radius: 10px; padding: 10px; font-family: Consolas; font-size: 11pt; }
            QTabWidget#MainTabs::pane { border: 1px solid #dde3f0; border-radius: 10px; background: #ffffff; }
            QTabBar::tab { background: #e9eef7; color: #1f2a44; border: 1px solid #dde3f0; padding: 8px 12px; margin-right: 4px; border-top-left-radius: 8px; border-top-right-radius: 8px; }
            QTabBar::tab:selected { background: #ffffff; border-bottom-color: #ffffff; }
            QPushButton { background: #1f6feb; color: #ffffff; border: none; border-radius: 8px; padding: 10px 14px; font-weight: 600; }
            QPushButton:hover { background: #1a5ed6; }
            QPushButton#SecondaryButton { background: #e9eef7; color: #1f2a44; }
            QPushButton#SecondaryButton:hover { background: #dde6f5; }
            QPushButton#ConfigButton { background: #e9eef7; color: #1f2a44; border: 1px solid #b0bdd0; border-radius: 8px; padding: 6px 12px; font-size: 12px; font-weight: 600; }
            QPushButton#ConfigButton:hover { background: #dde6f5; color: #16213d; }
            #InputField { background: #ffffff; color: #1f2a44; border: 1px solid #cfd8ea; border-radius: 6px; padding: 6px 8px; }
            #FornLabel { font-size: 13px; font-weight: 600; color: #1f2a44; padding-right: 6px; }
            #FornUnit { font-size: 12px; color: #5a6b8a; }
            """
        )

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
        if self.tabs.currentIndex() == 0:
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
        self.tabs.setCurrentIndex(0)
        self.label_info.setText(f"OK: {len(self.pedidos)} pedido(s) extraido(s) de {Path(arquivo).name}")
        self.label_info.setStyleSheet("color: #067647;")

    def _atualizar_estado_romaneio_colado(self):
        texto = (self.romaneio_colado_text.toPlainText() or "").strip()
        self.btn_quote_colado.setEnabled(bool(texto))

    def _iniciar_cotacao(self, modo: str):
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
        self.tabs.setCurrentIndex(1)
        self.label_info.setText("Executando cotações de transportadoras...")
        self.label_info.setStyleSheet("color: #1f6feb;")
        threading.Thread(target=self._run_async_cotacao, daemon=True).start()

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
        for nome in ("braspress", "agex"):
            cnpj = re.sub(r"\D", "", str((transp.get(nome) or {}).get("cnpj", "") or ""))
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
        threading.Thread(target=self._run_async_cotacao, daemon=True).start()

    def _post_event_safe(self, event: QEvent) -> None:
        """Posta evento na fila da UI de forma segura (ignora se app já encerrou)."""
        try:
            inst = QApplication.instance()
            if inst is not None:
                inst.postEvent(self, event)
        except Exception:
            pass

    def _run_pre_login(self):
        """Faz pre-login de todas as transportadoras em background."""
        def _status_callback(msg):
            self._post_event_safe(StatusUpdateEvent(msg))
        def _login_status_callback(nome, status):
            self._post_event_safe(LoginStatusEvent(nome, status))
        def _login_retry_callback(nome):
            self._post_event_safe(LoginRetryPromptEvent(nome))
        try:
            with self._loop_lock:
                asyncio.set_event_loop(self._loop)
                self._loop.run_until_complete(self._sessao.inicializar(
                    callback=_status_callback,
                    login_status_callback=_login_status_callback,
                    login_retry_callback=_login_retry_callback,
                ))
        except Exception as exc:
            print(f"[FreteBot] Erro no pre-login: {exc}", file=sys.stderr, flush=True)

    def _run_async_cotacao(self):
        try:
            with self._loop_lock:
                asyncio.set_event_loop(self._loop)
                self._loop.run_until_complete(self._cotar_transportadoras_async())
        except Exception as exc:
            print(f"[FreteBot] Erro na cotação: {exc}", file=sys.stderr, flush=True)
            self._post_event_safe(UdpateResultEvent(f"Erro ao cotar: {exc}"))
            self._post_event_safe(UpdateFinishedEvent())

    async def _cotar_transportadoras_async(self):
        try:
            def _progresso_callback(payload: dict[str, Any]) -> None:
                self._post_event_safe(CotacaoProgressEvent(payload))

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
            resumo = formatar_resultados_cotacao(resultados)
            
            # As atualizações da UI devem ser feitas na thread principal
            self._post_event_safe(UdpateResultEvent(resumo))

        except Exception as e:
            self._post_event_safe(UdpateResultEvent(f"Erro ao cotar transportadoras: {e}"))
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
        is_forn = self._modo_cotacao == "fornecedor"
        _result = self.forn_result_text if is_forn else self.result_text
        _progress = self.forn_progress_bar if is_forn else self.progress_bar

        if isinstance(event, UdpateResultEvent):
            _result.setPlainText(event.result)
            if not is_forn:
                self.tabs.setCurrentIndex(1)
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
        elif isinstance(event, StatusUpdateEvent):
            self.label_info.setText(event.msg)
            self.label_info.setStyleSheet("color: #1f6feb;")
        elif isinstance(event, LoginStatusEvent):
            lbl = self._login_status_labels.get(event.nome)
            if lbl is not None:
                nome_upper = event.nome.upper()
                if event.status == "ok":
                    lbl.setText(f"✅ {nome_upper}")
                    lbl.setStyleSheet("color: #067647; font-size: 11px; font-weight: 600;")
                elif event.status == "fail":
                    lbl.setText(f"❌ {nome_upper}")
                    lbl.setStyleSheet("color: #b42318; font-size: 11px; font-weight: 600;")
                else:
                    lbl.setText(f"⏳ {nome_upper}")
                    lbl.setStyleSheet("color: #8896ab; font-size: 11px; font-weight: 600;")
        elif isinstance(event, LoginRetryPromptEvent):
            self._perguntar_relogin(event.nome)

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
        event.accept()  # aceita logo para fechar a janela imediatamente

        def _cleanup_background():
            if self._loop_lock.acquire(timeout=3):
                try:
                    asyncio.set_event_loop(self._loop)
                    self._loop.run_until_complete(
                        asyncio.wait_for(self._sessao.cleanup(), timeout=5)
                    )
                except Exception:
                    pass
                finally:
                    # Fecha o event loop corretamente para evitar warnings
                    # de "I/O operation on closed pipe" no Windows
                    try:
                        self._loop.run_until_complete(self._loop.shutdown_asyncgens())
                    except Exception:
                        pass
                    try:
                        self._loop.close()
                    except Exception:
                        pass
                    self._loop_lock.release()
            # Força encerramento de Chromes órfãos restantes
            try:
                from cotacao_transportadoras import _kill_orphan_fretebot_chromes
                _kill_orphan_fretebot_chromes()
            except Exception:
                pass

        t = threading.Thread(target=_cleanup_background, daemon=True)
        t.start()

    def _abrir_configuracoes(self):
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

    def _reiniciar_sessao(self):
        """Limpa sess\u00e3o atual e faz login novamente com a config atualizada."""
        self.label_info.setText("Reiniciando sess\u00f5es...")
        self.label_info.setStyleSheet("color: #1f6feb;")
        for lbl in self._login_status_labels.values():
            raw = lbl.text()
            nome_upper = raw.split(" ", 1)[-1] if " " in raw else raw
            lbl.setText(f"\u23f3 {nome_upper}")
            lbl.setStyleSheet("color: #8896ab; font-size: 11px; font-weight: 600;")

        def _do():
            with self._loop_lock:
                asyncio.set_event_loop(self._loop)
                try:
                    self._loop.run_until_complete(self._sessao.cleanup())
                except Exception:
                    pass
            self._sessao = TransportadoraSession(config_path=self._config_path)
            self._run_pre_login()

        threading.Thread(target=_do, daemon=True).start()

    def _relogin_manual(self):
        """Botão de relogin manual — reinicia sessões de todas as transportadoras."""
        self._reiniciar_sessao()

    def _perguntar_relogin(self, nome: str):
        """Mostra prompt perguntando se o usuário quer refazer login de uma transportadora."""
        nome_upper = nome.upper()
        resposta = QMessageBox.question(
            self,
            f"Login falhou: {nome_upper}",
            f"O login da transportadora {nome_upper} falhou.\n\n"
            f"Deseja tentar refazer o login agora?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resposta == QMessageBox.Yes:
            self._relogin_transportadora(nome)

    def _relogin_transportadora(self, nome: str):
        """Refaz login de uma transportadora específica em background."""
        nome_upper = nome.upper()
        lbl = self._login_status_labels.get(nome)
        if lbl is not None:
            lbl.setText(f"⏳ {nome_upper}")
            lbl.setStyleSheet("color: #8896ab; font-size: 11px; font-weight: 600;")

        def _login_status_cb(n, status):
            self._post_event_safe(LoginStatusEvent(n, status))

        def _do():
            with self._loop_lock:
                asyncio.set_event_loop(self._loop)
                try:
                    self._loop.run_until_complete(
                        self._sessao.relogin_one(nome, login_status_callback=_login_status_cb)
                    )
                except Exception as exc:
                    print(f"[FreteBot] Erro no relogin de {nome}: {exc}", file=sys.stderr, flush=True)
                    self._post_event_safe(LoginStatusEvent(nome, "fail"))

        threading.Thread(target=_do, daemon=True).start()

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
        print(f"[FreteBot] Exceção em thread {args.thread}:\n{msg}",
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


def main():
    # Redireciona stderr para arquivo de crash ANTES de qualquer import falhar
    _crash_log = None
    try:
        _appdata = os.getenv("APPDATA")
        if _appdata:
            _log_dir = Path(_appdata) / "FreteBot"
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

    import traceback as _tb
    try:
        _instalar_thread_excepthook()
        setup_global_exception_handler()
        install_global_hooks()  # DEPOIS dos outros hooks, para envolver todos

        # Log de inicialização (diagnóstico: versão, caminhos, etc.)
        try:
            from fretebot.logging_conf import setup_logging, get_logger
            setup_logging()
            _startup_logger = get_logger("startup")
            _startup_logger.info("="*60)
            _startup_logger.info("FreteBot iniciando")
            _startup_logger.info(f"Python: {sys.version}")
            _startup_logger.info(f"Frozen: {getattr(sys, 'frozen', False)}")
            _startup_logger.info(f"Exe: {sys.executable}")
            _startup_logger.info(f"CWD: {os.getcwd()}")
            _startup_logger.info(f"APPDATA: {os.getenv('APPDATA', '?')}")
            try:
                _v = (Path(getattr(sys, '_MEIPASS', '')) / 'version.txt').read_text().strip()
            except Exception:
                try:
                    _v = (Path(__file__).parent / 'version.txt').read_text().strip()
                except Exception:
                    _v = '?'
            _startup_logger.info(f"Versão: {_v}")
        except Exception as _log_err:
            print(f"[FreteBot] Falha ao configurar logging: {_log_err}", file=sys.stderr, flush=True)

        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(True)

        # ── Verificação de licença ──
        try:
            _lic_key = get_saved_license()
            _machine = get_machine_id()

            if not _lic_key:
                # Pedir chave de ativação
                while True:
                    _lic_key, _ok = QInputDialog.getText(
                        None,
                        "Ativação — FreteBot",
                        "Digite sua chave de licença:\n\n"
                        "Formato: FBOT-XXXX-XXXX-XXXX-XXXX",
                    )
                    if not _ok:
                        sys.exit(0)
                    _lic_key = _lic_key.strip().upper()
                    if not _lic_key:
                        continue
                    _lic_status = validate_license(_lic_key, _machine)
                    if _lic_status.valid:
                        save_license(_lic_key)
                        break
                    else:
                        QMessageBox.warning(
                            None, "Licença Inválida",
                            _lic_status.message or "Chave não reconhecida.",
                        )
            else:
                # Validar licença existente
                _lic_status = validate_license(_lic_key, _machine)
                if not _lic_status.valid:
                    QMessageBox.critical(
                        None, "Licença Bloqueada",
                        _lic_status.message or "Sua licença não é mais válida.",
                    )
                    # Dar chance de inserir outra chave
                    _lic_key2, _ok2 = QInputDialog.getText(
                        None,
                        "Ativação — FreteBot",
                        "Sua licença foi revogada.\n"
                        "Digite uma nova chave de licença:",
                    )
                    if _ok2 and _lic_key2.strip():
                        _lic_status2 = validate_license(_lic_key2.strip().upper(), _machine)
                        if _lic_status2.valid:
                            save_license(_lic_key2.strip().upper())
                        else:
                            QMessageBox.critical(
                                None, "Licença Inválida",
                                _lic_status2.message or "Chave não reconhecida.",
                            )
                            sys.exit(1)
                    else:
                        sys.exit(1)
        except SystemExit:
            raise
        except Exception as _lic_err:
            report_error(context="verificacao_licenca")
            print(f"[FreteBot] Verificação de licença falhou: {_lic_err}", file=sys.stderr, flush=True)

        # ── Verificação de atualização via GitHub ──
        try:
            _repo = get_repo_from_config()
            _cur_ver = _carregar_versao_app()
            if _repo:
                _update_info = check_for_update(_repo, _cur_ver)
                if _update_info:
                    _resp = QMessageBox.question(
                        None,
                        "Atualização Disponível",
                        f"Nova versão disponível: v{_update_info.version}\n"
                        f"Versão atual: v{_cur_ver}\n\n"
                        f"{(_update_info.release_notes or 'Sem notas de versão.')[:500]}\n\n"
                        f"Deseja atualizar agora?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.Yes,
                    )
                    if _resp == QMessageBox.Yes:
                        _progress_dlg = QMessageBox(
                            QMessageBox.Information,
                            "Atualizando...",
                            "Baixando atualização...",
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
                                f"FreteBot será atualizado para v{_update_info.version}!\n"
                                "O aplicativo vai fechar e reabrir automaticamente.",
                            )
                            restart_app()  # Lança o .bat e fecha o app
                        else:
                            QMessageBox.warning(
                                None,
                                "Atualização Falhou",
                                "Não foi possível aplicar a atualização.\n"
                                "O aplicativo continuará com a versão atual.",
                            )
        except Exception as _upd_err:
            report_error(context="verificacao_atualizacao")
            print(f"[FreteBot] Verificação de atualização falhou: {_upd_err}", file=sys.stderr, flush=True)

        _migrar_config_se_necessario()

        proxima_empresa: str | None = _ler_ultima_empresa() or None

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
        print(f"[FreteBot] CRASH FATAL:\n{crash_msg}", file=sys.stderr, flush=True)
        # Tenta mostrar msgbox para o usuário
        try:
            QMessageBox.critical(None, "FreteBot - Erro Fatal", f"O aplicativo encontrou um erro:\n\n{crash_msg[:800]}")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
