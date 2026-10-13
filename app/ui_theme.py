"""Shared semantic colors for the native light UI; no behavior or persistence."""
from dataclasses import dataclass


@dataclass(frozen=True)
class UiPalette:
    window: str = "#F2F5F3"
    surface: str = "#FFFFFF"
    text: str = "#182923"
    muted: str = "#64766E"
    divider: str = "#DCE5DF"
    primary: str = "#176B50"
    recommended: str = "#EDF6F0"
    candidate: str = "#F8FAF9"
    auxiliary: str = "#EEF2F7"
    sidebar: str = "#172C25"
    sidebar_text: str = "#DDE9E2"
    sidebar_active: str = "#29483B"
    warning: str = "#93611D"
    error: str = "#B44832"


PALETTE = UiPalette()


def configure_text_fonts(app):
    """Set text defaults before widget creation; leave icon renderers alone."""
    from PySide6.QtGui import QFontDatabase
    from qfluentwidgets.common.font import setFontFamilies
    available = set(QFontDatabase.families())
    families = [name for name in ("SimHei", "Microsoft YaHei") if name in available]
    families.append(QFontDatabase.systemFont(QFontDatabase.GeneralFont).family())
    families = list(dict.fromkeys(families))
    setFontFamilies(families, save=False)
    font = app.font()
    font.setFamilies(families)
    app.setFont(font)
