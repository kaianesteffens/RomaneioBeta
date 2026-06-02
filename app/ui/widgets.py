from __future__ import annotations

from time import monotonic

from PySide6.QtCore import QTimer, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class IndeterminateBar(QWidget):
    """Barra de carregamento indeterminada com animação suave (~60 FPS)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ProgressBar")
        self.setMinimumHeight(18)
        self.setMaximumHeight(18)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._last_t = monotonic()
        self._offset_px = 0.0
        self._speed_px_s = 360.0
        self._track_color = "#e9eef7"
        self._border_color = "#cfd8ea"
        self._chunk_color = "#1f6feb"

    def set_theme(self, track: str, border: str, chunk: str) -> None:
        """Atualiza as cores da barra para acompanhar o tema ativo."""
        self._track_color = track
        self._border_color = border
        self._chunk_color = chunk
        self.update()

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
        painter.setPen(QPen(QColor(self._border_color), 1.0))
        painter.setBrush(QColor(self._track_color))
        painter.drawRoundedRect(track_rect, radius, radius)

        if w <= 2 or h <= 2:
            return

        chunk_w = self._chunk_width()
        span = w + chunk_w
        x = (self._offset_px % span) - chunk_w

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self._chunk_color))
        for shift in (0.0, span):
            xr = x + shift
            chunk_rect = QRectF(xr, 1.0, chunk_w, max(0.0, h - 2.0))
            if chunk_rect.right() < 0 or chunk_rect.left() > w:
                continue
            painter.drawRoundedRect(chunk_rect, radius, radius)
