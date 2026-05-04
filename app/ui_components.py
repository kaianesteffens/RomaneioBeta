from pathlib import Path

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QWidget,
)


NAV_ICONS: dict[str, str] = {
    "radar": '<circle cx="12" cy="12" r="2"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3m-3.6-6.4-2.1 2.1M7.7 16.3l-2.1 2.1M19.1 19.1l-2.1-2.1M7.7 7.7 5.6 5.6"/>',
    "doc": '<path d="M14 2H6a2 2 0 0 0-2 2v16c0 1.1.9 2 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M9 13h6M9 17h4"/>',
    "money": '<path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
    "box": '<path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
    "truck": '<rect x="1" y="3" width="15" height="13" rx="1"/><path d="M16 8h4l3 3v5h-7V8z"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/>',
    "cog": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41m11.32-11.32 1.41-1.41"/>',
    "moon": '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
}


def svg_icon(svg_body: str, size: int = 16, color: str = "#768390") -> QPixmap:
    from PySide6.QtSvg import QSvgRenderer

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}"'
        f' viewBox="0 0 24 24" fill="none" stroke="{color}"'
        f' stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        f"{svg_body}</svg>"
    )
    renderer = QSvgRenderer(svg.encode())
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return pixmap


class ToggleWidget(QWidget):
    toggled = Signal(bool)

    def __init__(self, checked: bool = True, parent=None):
        super().__init__(parent)
        self._checked = checked
        self._accent = "#00b4d8"
        self._track_off = "#444c56"
        self.setFixedSize(28, 16)
        self.setCursor(Qt.PointingHandCursor)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, val: bool) -> None:
        self._checked = val
        self.update()

    def refresh_theme(self, accent: str, faint: str) -> None:
        self._accent = accent
        self._track_off = faint
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._checked = not self._checked
            self.toggled.emit(self._checked)
            self.update()

    def paintEvent(self, event) -> None:
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self._accent if self._checked else self._track_off))
        p.drawRoundedRect(0, 3, 28, 10, 5, 5)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(15 if self._checked else 1, 0, 13, 16)


class NavItem(QWidget):
    clicked = Signal()

    def __init__(self, icon_name: str, label: str, kbd: str, parent=None):
        super().__init__(parent)
        self._icon_name = icon_name
        self._active = False
        self._hovered = False
        self._accent = "#00b4d8"
        self._muted = "#768390"
        self._panel2 = "#1c232c"
        self._panel3 = "#21282f"
        self._border = "#262f3a"
        self._faint = "#444c56"
        self._accent_dim = "#0a2030"
        self.setFixedHeight(36)
        self.setCursor(Qt.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 8, 0)
        layout.setSpacing(8)
        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedSize(16, 16)
        layout.addWidget(self._icon_lbl)
        self._text_lbl = QLabel(label)
        layout.addWidget(self._text_lbl, 1)
        self._kbd_lbl = QLabel(kbd)
        if kbd:
            layout.addWidget(self._kbd_lbl)
        self._update_labels()

    def refresh_theme(
        self,
        accent: str,
        muted: str,
        panel2: str,
        panel3: str,
        border: str,
        faint: str,
        accentDim: str,
    ) -> None:
        self._accent = accent
        self._muted = muted
        self._panel2 = panel2
        self._panel3 = panel3
        self._border = border
        self._faint = faint
        self._accent_dim = accentDim
        self._update_labels()
        self.update()

    def _update_labels(self) -> None:
        color = self._accent if self._active else self._muted
        weight = "600" if self._active else "400"
        self._icon_lbl.setPixmap(svg_icon(NAV_ICONS.get(self._icon_name, ""), 16, color))
        self._text_lbl.setStyleSheet(f"color:{color};font-size:13px;font-weight:{weight};")
        self._kbd_lbl.setStyleSheet(
            f"font-family:'JetBrains Mono';font-size:10px;padding:1px 5px;"
            f"background:{self._panel3};border:1px solid {self._border};"
            f"border-radius:3px;color:{self._faint};"
        )

    def set_active(self, active: bool) -> None:
        self._active = active
        self._update_labels()
        self.update()

    def enterEvent(self, event) -> None:
        del event
        self._hovered = True
        self.update()

    def leaveEvent(self, event) -> None:
        del event
        self._hovered = False
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event) -> None:
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(Qt.NoPen)
        if self._active:
            p.setBrush(QColor(self._accent_dim))
            p.drawRoundedRect(QRectF(3, 2, w - 5, h - 4), 6, 6)
            p.setBrush(QColor(self._accent))
            p.drawRoundedRect(QRectF(0, 5, 3, h - 10), 1.5, 1.5)
        elif self._hovered:
            p.setBrush(QColor(self._panel2))
            p.drawRoundedRect(QRectF(3, 2, w - 5, h - 4), 6, 6)


class CarrierDot(QWidget):
    _COLORS = {
        "ok": ("#3fb950", True),
        "fail": ("#f85149", False),
        "pending": ("#e3b341", False),
    }

    def __init__(self, nome: str, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self._dot = QFrame()
        self._dot.setFixedSize(6, 6)
        layout.addWidget(self._dot, 0, Qt.AlignVCenter)
        self._lbl = QLabel(nome.upper())
        layout.addWidget(self._lbl)
        self._apply_status("pending")

    def set_status(self, status: str) -> None:
        self._apply_status(status)

    def _apply_status(self, status: str) -> None:
        color, glow = self._COLORS.get(status, ("#768390", False))
        self._dot.setStyleSheet(f"border-radius:3px;background:{color};")
        self._lbl.setStyleSheet(
            f"font-family:'JetBrains Mono';font-size:11px;font-weight:600;color:{color};"
        )
        if glow:
            eff = QGraphicsDropShadowEffect(self._dot)
            eff.setColor(QColor(color))
            eff.setBlurRadius(7)
            eff.setOffset(0, 0)
            self._dot.setGraphicsEffect(eff)
        else:
            self._dot.setGraphicsEffect(None)


def load_app_fonts() -> None:
    families: list[str] = []
    app_dir = Path(__file__).resolve().parent
    for rel in (
        "assets/fonts/InterVariable.ttf",
        "assets/fonts/JetBrainsMono-Regular.ttf",
        "assets/fonts/JetBrainsMono-Medium.ttf",
        "assets/fonts/JetBrainsMono-Bold.ttf",
    ):
        font_path = app_dir / rel
        if not font_path.exists():
            continue
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id >= 0:
            families.extend(QFontDatabase.applicationFontFamilies(font_id))
    preferred_sans = next((f for f in families if "Inter" in f), "Segoe UI")
    QApplication.setFont(QFont(preferred_sans, 10))
