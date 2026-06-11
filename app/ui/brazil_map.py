"""Mapa do Brasil clicavel (tile grid) para escolher UFs atendidas.

Cada estado e uma peca posicionada de forma aproximadamente geografica.
Clicar alterna a selecao; estados atendidos ficam pintados com o accent.
Sem dependencia de SVG externo — desenhado com QPainter.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget


# Posicao (linha, coluna) de cada UF num grid aproximadamente geografico.
_TILES: dict[str, tuple[int, int]] = {
    "RR": (1, 3), "AP": (1, 5),
    "AM": (2, 2), "PA": (2, 4), "MA": (2, 5), "CE": (2, 6), "RN": (2, 7),
    "AC": (3, 1), "RO": (3, 2), "MT": (3, 3), "TO": (3, 4), "PI": (3, 5), "PE": (3, 6), "PB": (3, 7),
    "MS": (4, 3), "GO": (4, 4), "BA": (4, 5), "SE": (4, 6), "AL": (4, 7),
    "DF": (5, 4), "MG": (5, 5), "ES": (5, 6),
    "SP": (6, 5), "RJ": (6, 6),
    "PR": (7, 4), "SC": (7, 5),
    "RS": (8, 4),
}
_ROWS = 8
_COLS = 7


class BrazilMap(QWidget):
    """Selecao de UFs num mapa do Brasil em tiles. Emite `changed` ao alterar."""

    changed = Signal()

    def __init__(self, selected=None, parent=None):
        super().__init__(parent)
        self._selected: set[str] = {str(u).upper() for u in (selected or [])}
        self._hover: str | None = None
        self._cell = 30
        self._gap = 4
        # cores padrao (tema claro Claude) — sobrescritas por set_theme
        self._sel_bg = "#c15f3c"
        self._sel_fg = "#ffffff"
        self._bg = "#f5f3ec"
        self._fg = "#43403a"
        self._border = "#e3dfd3"
        self._accent = "#c15f3c"
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        w = self._gap + _COLS * (self._cell + self._gap)
        h = self._gap + _ROWS * (self._cell + self._gap)
        self.setFixedSize(w, h)

    # --- API publica ---
    def get_selected(self) -> list[str]:
        return sorted(self._selected)

    def set_selected(self, ufs) -> None:
        self._selected = {str(u).upper() for u in (ufs or [])}
        self.update()

    def select_all(self) -> None:
        self._selected = set(_TILES)
        self.update()
        self.changed.emit()

    def clear_all(self) -> None:
        self._selected = set()
        self.update()
        self.changed.emit()

    def count(self) -> int:
        return len(self._selected)

    def set_theme(self, *, sel_bg: str, sel_fg: str, bg: str, fg: str, border: str, accent: str) -> None:
        self._sel_bg = sel_bg
        self._sel_fg = sel_fg
        self._bg = bg
        self._fg = fg
        self._border = border
        self._accent = accent
        self.update()

    # --- internos ---
    def _rect_for(self, uf: str) -> QRectF:
        r, c = _TILES[uf]
        x = self._gap + (c - 1) * (self._cell + self._gap)
        y = self._gap + (r - 1) * (self._cell + self._gap)
        return QRectF(x, y, self._cell, self._cell)

    def _uf_at(self, pos: QPointF) -> str | None:
        for uf in _TILES:
            if self._rect_for(uf).contains(pos):
                return uf
        return None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            uf = self._uf_at(event.position())
            if uf:
                if uf in self._selected:
                    self._selected.discard(uf)
                else:
                    self._selected.add(uf)
                self.update()
                self.changed.emit()

    def mouseMoveEvent(self, event) -> None:
        uf = self._uf_at(event.position())
        if uf != self._hover:
            self._hover = uf
            self.update()

    def leaveEvent(self, event) -> None:
        del event
        if self._hover is not None:
            self._hover = None
            self.update()

    def paintEvent(self, event) -> None:
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        font = QFont()
        font.setPointSize(7)
        font.setBold(True)
        p.setFont(font)
        for uf in _TILES:
            rect = self._rect_for(uf)
            selected = uf in self._selected
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(self._sel_bg if selected else self._bg))
            p.drawRoundedRect(rect, 5, 5)
            if uf == self._hover:
                pen = QPen(QColor(self._accent))
                pen.setWidth(2)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 5, 5)
            elif not selected:
                pen = QPen(QColor(self._border))
                pen.setWidth(1)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
            p.setPen(QColor(self._sel_fg if selected else self._fg))
            p.drawText(rect, Qt.AlignCenter, uf)
        p.end()
