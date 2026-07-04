from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .config import APP_NAME, DEFAULT_FONT_FAMILY, DEFAULT_THEME, ORG_NAME
from .logging_setup import capture_exception, configure_logging, flush_telemetry

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QApplication


logger = logging.getLogger(__name__)


def main() -> int:
    log_path = configure_logging()
    logger.info("Starting %s", APP_NAME)
    from PyQt6.QtWidgets import QApplication
    from qt_material import apply_stylesheet

    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)

    configure_app_font(app)
    apply_stylesheet(app, theme=DEFAULT_THEME, extra={"font_family": DEFAULT_FONT_FAMILY})
    window = MainWindow()
    window.show()

    logger.info("%s UI initialized; log file: %s", APP_NAME, log_path)
    try:
        return app.exec()
    except Exception as exc:
        logger.exception("Application crashed")
        capture_exception(exc)
        raise
    finally:
        logger.info("%s shutting down", APP_NAME)
        flush_telemetry()


def configure_app_font(app: QApplication) -> None:
    from PyQt6.QtGui import QFont, QFontDatabase

    for font_path in (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/msyhbd.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
    ):
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
            logger.debug("Loaded application font from %s", font_path)
            break
    app.setFont(QFont(DEFAULT_FONT_FAMILY, 10))
