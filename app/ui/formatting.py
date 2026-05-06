from __future__ import annotations

import re

from PySide6.QtWidgets import QLineEdit


def _apply_cnpj_mask(line_edit: QLineEdit) -> None:
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


def _apply_cep_mask(line_edit: QLineEdit) -> None:
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


def _apply_decimal_mask(line_edit: QLineEdit, decimals: int = 2) -> None:
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


def _apply_currency_mask(line_edit: QLineEdit) -> None:
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
