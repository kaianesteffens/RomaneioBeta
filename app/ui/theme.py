"""Camada de tema da UI (somente visual).

Centraliza paletas de cor, presets de cor de destaque (accent), presets de
cantos/botoes e o gerador da folha de estilo (QSS). Nao contem regra de
negocio: apenas aparencia. O `romaneio_app._apply_style()` consome este modulo.

Conceitos:
- modo claro/escuro -> define os neutros (fundo, paineis, texto).
- accent -> familia de cor de destaque escolhida pelo usuario.
- raio -> arredondamento de cards/botoes/inputs.
- botao -> estilo do botao primario (solido x suave).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass


def aplicar_cor_barra_titulo(window, *, dark: bool, caption_hex: str, text_hex: str) -> None:
    """Pinta a barra de titulo nativa do Windows para acompanhar o tema.

    Win10 1809+: alterna a barra entre clara/escura (immersive dark mode).
    Win11 22000+: define a cor exata da barra e do texto. Em versoes que nao
    suportam, a chamada falha silenciosamente sem afetar o resto.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(window.winId())
        dwm = ctypes.windll.dwmapi

        modo = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (20) / legado (19)
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(modo), ctypes.sizeof(modo)) == 0:
                break

        def _colorref(hex_str: str) -> int:
            h = hex_str.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return r | (g << 8) | (b << 16)

        cor = ctypes.c_int(_colorref(caption_hex))
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(cor), ctypes.sizeof(cor))  # DWMWA_CAPTION_COLOR
        txt = ctypes.c_int(_colorref(text_hex))
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(txt), ctypes.sizeof(txt))  # DWMWA_TEXT_COLOR
    except Exception:
        pass


# ── Cor de destaque (accent) ───────────────────────────────────────────────
# Identidade visual inspirada no Claude/Anthropic: terracota/coral quente.
# "accent" e a cor principal, "hover" o estado de hover, "dim" um fundo tenue
# e "border" a borda tenue. Variantes para tema escuro e claro.
ACCENTS: dict[str, dict[str, dict[str, str]]] = {
    "Claude": {
        "dark": {"accent": "#d97757", "hover": "#e08a63", "dim": "#3a261c", "border": "#5e3a28"},
        "light": {"accent": "#c15f3c", "hover": "#a94e2f", "dim": "#f7ebe3", "border": "#ecccbb"},
    },
}

DEFAULT_ACCENT = "Claude"


# ── Presets de cantos (raio) ───────────────────────────────────────────────
@dataclass(frozen=True)
class Radius:
    card: int
    btn: int
    input: int
    chip: int


RADII: dict[str, Radius] = {
    "Reto": Radius(card=6, btn=5, input=5, chip=3),
    "Suave": Radius(card=12, btn=9, input=8, chip=6),
    "Arredondado": Radius(card=16, btn=13, input=11, chip=9),
}

DEFAULT_RADIUS = "Suave"


# Estilo do botao primario.
BUTTON_STYLES = ("Solido", "Suave")
DEFAULT_BUTTON = "Solido"


@dataclass(frozen=True)
class Palette:
    dark: bool
    # neutros
    bg: str
    panel: str
    panel2: str
    panel3: str
    border: str
    border_soft: str
    ink: str
    ink2: str
    muted: str
    faint: str
    # accent
    accent: str
    accent_hover: str
    accent2: str
    accent_border: str
    # status
    green: str
    green_dim: str
    red: str
    red_dim: str
    amber: str
    amber_dim: str
    # forma
    radius: Radius
    button_style: str


def _neutros_dark() -> dict[str, str]:
    # Grafite quente (warm charcoal) com texto marfim, no espirito do Claude.
    return {
        "bg": "#1a1917",
        "panel": "#242220",
        "panel2": "#2c2a27",
        "panel3": "#36332f",
        "border": "#3a3733",
        "border_soft": "#2a2825",
        "ink": "#f0eee6",
        "ink2": "#cdc9bd",
        "muted": "#928d82",
        "faint": "#5c574f",
        "green": "#5fb87a",
        "green_dim": "#16301d",
        "red": "#e5645a",
        "red_dim": "#371613",
        "amber": "#e0a73a",
        "amber_dim": "#332610",
    }


def _neutros_light() -> dict[str, str]:
    # Creme/marfim (ivory) com texto grafite quente, no espirito do Claude.
    return {
        "bg": "#f0eee6",
        "panel": "#fbfaf6",
        "panel2": "#f5f3ec",
        "panel3": "#ece9df",
        "border": "#e3dfd3",
        "border_soft": "#ece9e0",
        "ink": "#1f1e1d",
        "ink2": "#43403a",
        "muted": "#76726a",
        "faint": "#a8a39a",
        "green": "#3f9656",
        "green_dim": "#dcefe0",
        "red": "#c4453c",
        "red_dim": "#f7e3e1",
        "amber": "#b9802a",
        "amber_dim": "#f6ecd4",
    }


def normalize_accent(name: str | None) -> str:
    if name and name in ACCENTS:
        return name
    return DEFAULT_ACCENT


def normalize_radius(name: str | None) -> str:
    if name and name in RADII:
        return name
    return DEFAULT_RADIUS


def normalize_button(name: str | None) -> str:
    if name and name in BUTTON_STYLES:
        return name
    return DEFAULT_BUTTON


def build_palette(
    dark: bool,
    accent_name: str | None = None,
    radius_name: str | None = None,
    button_style: str | None = None,
) -> Palette:
    neutros = _neutros_dark() if dark else _neutros_light()
    acc = ACCENTS[normalize_accent(accent_name)]["dark" if dark else "light"]
    return Palette(
        dark=dark,
        bg=neutros["bg"],
        panel=neutros["panel"],
        panel2=neutros["panel2"],
        panel3=neutros["panel3"],
        border=neutros["border"],
        border_soft=neutros["border_soft"],
        ink=neutros["ink"],
        ink2=neutros["ink2"],
        muted=neutros["muted"],
        faint=neutros["faint"],
        accent=acc["accent"],
        accent_hover=acc["hover"],
        accent2=acc["dim"],
        accent_border=acc["border"],
        green=neutros["green"],
        green_dim=neutros["green_dim"],
        red=neutros["red"],
        red_dim=neutros["red_dim"],
        amber=neutros["amber"],
        amber_dim=neutros["amber_dim"],
        radius=RADII[normalize_radius(radius_name)],
        button_style=normalize_button(button_style),
    )


def build_stylesheet(p: Palette) -> str:
    c_bg, c_panel, c_panel2, c_panel3 = p.bg, p.panel, p.panel2, p.panel3
    c_border, c_border_soft = p.border, p.border_soft
    c_ink, c_muted, c_ink2, c_faint = p.ink, p.muted, p.ink2, p.faint
    c_accent, c_accent_hover, c_accent2, c_accent_border = (
        p.accent, p.accent_hover, p.accent2, p.accent_border,
    )
    c_green, c_green_dim = p.green, p.green_dim
    c_red, c_red_dim = p.red, p.red_dim
    c_amber, c_amber_dim = p.amber, p.amber_dim
    dark = p.dark
    r_card, r_btn, r_input, r_chip = (
        p.radius.card, p.radius.btn, p.radius.input, p.radius.chip,
    )

    if p.button_style == "Suave":
        primary_btn = (
            f"QPushButton {{ background: {c_accent2}; color: {c_accent};"
            f" border: 1px solid {c_accent_border}; border-radius: {r_btn}px;"
            f" padding: 10px 14px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {c_accent}; color: #ffffff; border-color: {c_accent}; }}"
        )
    else:
        primary_btn = (
            f"QPushButton {{ background: {c_accent}; color: #ffffff; border: none;"
            f" border-radius: {r_btn}px; padding: 10px 14px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {c_accent_hover}; }}"
        )

    return f"""
        QMainWindow {{ background: {c_bg}; color: {c_ink}; }}
        QDialog {{ background: {c_bg}; color: {c_ink}; }}
        QWidget {{ color: {c_ink}; }}
        QToolTip {{ background: {c_panel3}; color: {c_ink}; border: 1px solid {c_border}; border-radius: {r_chip}px; padding: 4px 7px; }}
        #Sidebar {{ background: {c_panel}; border-right: 1px solid {c_border}; min-width: 200px; max-width: 200px; }}
        #BrandLabel {{ font-size: 18px; font-weight: 700; letter-spacing: -0.5px; color: {c_ink}; }}
        #SidebarSep {{ background: {c_border}; border: none; max-height: 1px; }}
        #ChipWrap {{ background: transparent; }}
        #EmpresaChip {{ background: {c_panel2}; border: 1px solid {c_border}; border-radius: {r_input}px; }}
        #EmpresaAvatar {{ background: {c_accent}; color: #fff; font-size: 11px; font-weight: 700; border-radius: {r_chip}px; }}
        #EmpresaName {{ font-size: 12px; font-weight: 500; color: {c_ink2}; }}
        #ToggleRow {{ border-radius: {r_input}px; }}
        #ToggleLabel {{ font-size: 13px; color: {c_muted}; }}
        #TopBar {{ background: {c_panel}; border-bottom: 1px solid {c_border}; }}
        #TopBarTitle {{ font-size: 14px; font-weight: 600; color: {c_ink}; }}
        #CmdKBtn {{ background: {c_panel2}; border: 1px solid {c_border}; border-radius: {r_input}px; }}
        #CmdKBtn:hover {{ background: {c_panel3}; border-color: {c_accent_border}; }}
        #CmdKText {{ font-size: 12px; color: {c_muted}; }}
        #CmdKKbd {{ font-family: 'JetBrains Mono'; font-size: 10px; padding: 1px 5px;
                    background: {c_panel3}; border: 1px solid {c_border}; border-radius: {r_chip}px; color: {c_faint}; }}
        #StatusLabel {{ color: {c_muted}; font-size: 12px; }}
        #ChromeWarningFrame {{ background: {c_amber_dim}; border: 1px solid {c_amber}; border-radius: {r_input}px; }}
        #ChromeWarningLabel {{ color: {c_ink}; font-size: 12px; font-weight: 600; }}
        #FooterLabel {{ font-size: 11px; color: {c_muted}; }}
        #Card {{ background: {c_panel}; border: 1px solid {c_border}; border-radius: {r_card}px; }}
        #SettingsHero {{ background: {c_panel}; border-bottom: 1px solid {c_border}; }}
        #SettingsGear {{ background: {c_accent}; border-radius: {r_chip}px; }}
        #SettingsTitle {{ font-size: 22px; font-weight: 800; letter-spacing: 0.08em; color: {c_ink}; }}
        #SettingsSubtitle {{ font-size: 12px; color: {c_muted}; }}
        #SettingsSurface {{ background: {c_bg}; }}
        #SettingsNav {{ background: {c_bg}; border-bottom: 1px solid {c_border_soft}; }}
        #SettingsCard {{ background: {c_panel}; border: 1px solid {c_border}; border-radius: {r_card}px; }}
        #SettingsRowCard {{ background: {c_panel2}; border: 1px solid {c_border_soft}; border-radius: {r_input}px; }}
        #SettingsCardTitle {{ font-size: 12px; font-weight: 800; letter-spacing: 0.12em; color: {c_ink}; }}
        #SettingsCardSubtitle {{ font-size: 11px; color: {c_muted}; }}
        #SettingsFieldLabel {{ font-size: 11px; font-weight: 700; color: {c_muted}; }}
        #SettingsCarrierName {{ font-size: 11px; font-weight: 800; letter-spacing: 0.08em; color: {c_ink2}; }}
        #SettingsTableHeader {{ font-size: 10px; font-weight: 800; letter-spacing: 0.07em; color: {c_muted}; padding-bottom: 4px; }}
        #SettingsMutedText {{ font-size: 11px; color: {c_muted}; }}
        #SettingsMiniLabel {{ font-size: 10px; color: {c_faint}; }}
        #SwatchButton {{ border: 2px solid {c_border}; border-radius: {r_chip}px; min-width: 26px; min-height: 26px; max-width: 26px; max-height: 26px; }}
        #SwatchButtonActive {{ border: 2px solid {c_ink}; border-radius: {r_chip}px; min-width: 26px; min-height: 26px; max-width: 26px; max-height: 26px; }}
        QPushButton#ThemeOption, QPushButton#ThemeOptionActive {{ border: 1px solid {c_border}; border-radius: {r_btn}px; padding: 8px 12px; font-weight: 700; }}
        QPushButton#ThemeOption {{ background: {c_panel2}; color: {c_ink2}; }}
        QPushButton#ThemeOption:hover {{ background: {c_panel3}; border-color: {c_accent_border}; }}
        QPushButton#ThemeOptionActive {{ background: {c_accent2}; color: {c_accent}; border-color: {c_accent_border}; }}
        QComboBox#SettingsCombo {{ background: {c_panel2}; color: {c_ink}; border: 1px solid {c_border}; border-radius: {r_input}px; padding: 6px 8px; }}
        QComboBox#SettingsCombo::drop-down {{ border: none; width: 22px; }}
        QCheckBox#UfChip {{ font-size: 10px; color: {c_ink2}; spacing: 2px; }}
        QPushButton#UfChipBtn {{ background: {c_panel2}; color: {c_ink2}; border: 1px solid {c_border}; border-radius: {r_chip}px; padding: 5px 0px; font-size: 11px; font-weight: 600; min-width: 34px; }}
        QPushButton#UfChipBtn:hover {{ border-color: {c_accent}; }}
        QPushButton#UfChipBtn:checked {{ background: {c_accent}; color: #ffffff; border-color: {c_accent}; }}
        #SubtitleLabel {{ font-size: 12px; color: {c_muted}; }}
        #CotacaoCardTitle {{ font-size: 15px; font-weight: 700; color: {c_ink}; }}
        #CotacaoHintLabel {{ background: {c_panel2}; color: {c_muted}; border: 1px solid {c_border}; border-radius: {r_input}px; padding: 7px 9px; font-size: 12px; }}
        #CotacaoStatusLabel {{ color: {c_muted}; font-size: 12px; font-weight: 600; }}
        #CotacaoSummaryLabel {{ background: {c_accent2}; color: {c_ink2}; border: 1px solid {c_accent_border}; border-radius: {r_input}px; padding: 8px 10px; font-size: 12px; font-weight: 600; }}
        #KpiLabel {{ font-size: 10px; font-weight: 700; letter-spacing: 0.1em; color: {c_muted}; }}
        #KpiValue {{ font-size: 28px; font-weight: 700; color: {c_ink}; letter-spacing: -0.03em; }}
        #KpiValueAccent {{ font-size: 28px; font-weight: 700; color: {c_accent}; letter-spacing: -0.03em; }}
        #KpiValueGreen {{ font-size: 28px; font-weight: 700; color: {c_green}; letter-spacing: -0.03em; }}
        #KpiValueAmber {{ font-size: 28px; font-weight: 700; color: {c_amber}; letter-spacing: -0.03em; }}
        #KpiSub {{ font-size: 11px; color: {c_muted}; }}
        #SectionLabel {{ font-size: 10px; font-weight: 700; letter-spacing: 0.1em; color: {c_muted}; }}
        #SectionHint {{ font-size: 11px; color: {c_faint}; }}
        #DashboardEmpty {{ background: {c_panel2}; border: 1px dashed {c_border}; border-radius: {r_card}px; }}
        #DashboardEmptyTitle {{ font-size: 12px; font-weight: 700; color: {c_ink2}; }}
        #DashboardEmptyText {{ font-size: 11px; color: {c_muted}; }}
        #LinkLabel {{ font-size: 11px; font-weight: 500; color: {c_accent}; }}
        #SoftSep {{ background: {c_border_soft}; border: none; max-height: 1px; }}
        #TableMono {{ font-family: 'JetBrains Mono'; font-size: 11px; color: {c_faint}; }}
        #TableMono2 {{ font-family: 'JetBrains Mono'; font-size: 11px; color: {c_ink2}; }}
        #TableText {{ font-size: 12px; color: {c_muted}; }}
        #TableMonoBold {{ font-family: 'JetBrains Mono'; font-size: 13px; font-weight: 600; color: {c_ink}; }}
        #CotacaoStatusTable {{ background: {c_panel2}; color: {c_ink}; border: 1px solid {c_border}; border-radius: {r_card}px; gridline-color: {c_border_soft}; font-size: 12px; }}
        #CotacaoStatusTable::item {{ padding: 5px; }}
        #CotacaoStatusTable::item:selected {{ background: {c_accent2}; color: {c_ink}; }}
        #CotacaoStatusTable QHeaderView::section {{ background: {c_panel3}; color: {c_muted}; border: none; border-bottom: 1px solid {c_border}; padding: 5px 7px; font-size: 10px; font-weight: 700; }}
        #CarrierRowName {{ font-family: 'JetBrains Mono'; font-size: 12px; color: {c_ink2}; }}
        #TagGreen {{ background: {c_green_dim}; color: {c_green}; font-size: 11px; font-weight: 600; padding: 2px 7px; border-radius: {r_chip}px; }}
        #TagRed {{ background: {c_red_dim}; color: {c_red}; font-size: 11px; font-weight: 600; padding: 2px 7px; border-radius: {r_chip}px; }}
        #TagAmber {{ background: {c_amber_dim}; color: {c_amber}; font-size: 11px; font-weight: 600; padding: 2px 7px; border-radius: {r_chip}px; }}
        #InputText {{ background: {c_panel2}; color: {c_ink}; border: 1px solid {c_border}; border-radius: {r_input}px; padding: 8px; font-family: "JetBrains Mono"; font-size: 10.5pt; }}
        #ResultText {{ background: {c_panel2}; color: {c_ink}; border: 1px solid {c_border}; border-radius: {r_card}px; padding: 10px; font-family: "JetBrains Mono"; font-size: 11pt; }}
        #MainStack {{ background: transparent; }}
        #PageHeader {{ background: transparent; border: none; }}
        #BackButton {{ background: {c_panel2}; color: {c_ink2}; border: 1px solid {c_border}; border-radius: {r_btn}px; padding: 6px 14px; font-size: 13px; font-weight: 600; }}
        #BackButton:hover {{ background: {c_panel2}; color: {c_ink}; }}
        #PageTitleLabel {{ font-size: 18px; font-weight: 700; color: {c_ink}; }}
        #PageContent {{ background: {c_bg}; border: none; border-radius: 0px; }}
        #RastreioScroll {{ background: transparent; border: none; }}
        #RastreioScroll QWidget {{ background: transparent; }}
        #RastreioCard {{ background: {c_panel}; border: 1px solid {c_border}; border-radius: {r_card}px; }}
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
        {"#RastreioBlueBlock  { background: " + c_accent2 + "; border: 1px solid " + c_accent_border + "; border-radius: " + str(r_input) + "px; }"}
        {"#RastreioGreenBlock { background: #0d2010; border: 1px solid #1a3a20; border-radius: " + str(r_input) + "px; }" if dark else "#RastreioGreenBlock { background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: " + str(r_input) + "px; }"}
        {"#RastreioSlateBlock { background: " + c_panel2 + "; border: 1px solid " + c_border + "; border-radius: " + str(r_input) + "px; }"}
        {primary_btn}
        QPushButton:disabled {{ background: {c_panel3}; color: {c_faint}; }}
        QPushButton#SecondaryButton {{ background: {c_panel2}; color: {c_ink2}; border: 1px solid {c_border}; }}
        QPushButton#SecondaryButton:hover {{ background: {c_panel3}; color: {c_ink}; border-color: {c_accent_border}; }}
        #InputField {{ background: {c_panel2}; color: {c_ink}; border: 1px solid {c_border}; border-radius: {r_input}px; padding: 6px 8px; selection-background-color: {c_accent}; }}
        #InputField:focus {{ background: {c_panel}; border: 1px solid {c_accent}; }}
        QLineEdit {{ background: {c_panel2}; color: {c_ink}; border: 1px solid {c_border}; border-radius: {r_input}px; padding: 6px 8px; selection-background-color: {c_accent}; }}
        QLineEdit:focus {{ background: {c_panel}; border: 1px solid {c_accent}; }}
        QPlainTextEdit {{ selection-background-color: {c_accent}; }}
        QListWidget {{ background: {c_panel2}; color: {c_ink}; border: 1px solid {c_border}; border-radius: {r_card}px; padding: 4px; }}
        QListWidget::item {{ padding: 7px 8px; border-radius: {r_chip}px; }}
        QListWidget::item:selected {{ background: {c_accent2}; color: {c_ink}; }}
        QListWidget::item:hover {{ background: {c_panel3}; }}
        #FornLabel {{ font-size: 13px; font-weight: 600; color: {c_ink}; padding-right: 6px; }}
        #FornUnit {{ font-size: 12px; color: {c_muted}; }}
        QTabWidget#MainTabs::pane {{ border: 1px solid {c_border}; border-radius: {r_card}px; background: {c_panel}; }}
        QTabBar::tab {{ background: {c_panel2}; color: {c_muted}; border: 1px solid {c_border};
                       padding: 7px 12px; margin-right: 4px; border-top-left-radius: {r_btn}px;
                       border-top-right-radius: {r_btn}px; }}
        QTabBar::tab:selected {{ background: {c_panel}; color: {c_ink}; border-bottom-color: {c_panel}; }}
        QTabBar::tab:hover {{ background: {c_panel3}; color: {c_ink}; }}
        #SettingsGroup {{ border: 1px solid {c_border}; border-radius: {r_card}px;
                         padding: 12px 10px 10px 10px; margin-top: 6px; background: {c_panel}; }}
        QGroupBox#SettingsGroup {{ border: 1px solid {c_border}; background: {c_panel}; border-radius: {r_card}px; margin-top: 0px; }}
        QGroupBox#SettingsGroup::title {{ subcontrol-origin: margin; height: 0px; width: 0px; padding: 0px; color: transparent; }}
        #TranspTitle {{ font-size: 17px; font-weight: 700; color: {c_ink}; padding: 6px 0 8px 0; }}
        #ConfigWarning {{ color: {c_amber}; background: {c_amber_dim};
                          border: 1px solid {c_amber}; border-radius: {r_input}px; padding: 6px 8px; }}
        #CredField {{ border: 1px solid {c_border}; border-radius: {r_input}px; padding: 5px 8px;
                     background: {c_panel2}; color: {c_ink}; }}
        QPushButton#MiniButton {{ background: {c_panel2}; color: {c_ink2};
                                 border: 1px solid {c_border}; border-radius: {r_chip}px;
                                 padding: 2px 8px; font-size: 11px; }}
        QPushButton#MiniButton:hover {{ background: {c_panel3}; border-color: {c_accent_border}; }}
        QCheckBox {{ color: {c_ink}; spacing: 4px; }}
        QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {c_border}; border-radius: 4px; background: {c_panel}; }}
        QCheckBox::indicator:hover {{ border-color: {c_accent_border}; }}
        QCheckBox::indicator:checked {{ background: {c_accent}; border-color: {c_accent}; }}
        QCheckBox::indicator:checked:hover {{ background: {c_accent_hover}; border-color: {c_accent_hover}; }}
        QScrollArea {{ background: transparent; border: none; }}
        QScrollBar:vertical {{ background: {c_panel2}; width: 10px; margin: 0; border-radius: 5px; }}
        QScrollBar::handle:vertical {{ background: {c_faint}; min-height: 28px; border-radius: 5px; }}
        QScrollBar::handle:vertical:hover {{ background: {c_muted}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        QScrollBar:horizontal {{ background: {c_panel2}; height: 10px; margin: 0; border-radius: 5px; }}
        QScrollBar::handle:horizontal {{ background: {c_faint}; min-width: 28px; border-radius: 5px; }}
        QScrollBar::handle:horizontal:hover {{ background: {c_muted}; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
    """
