from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from rhythmflow.config import APP_NAME
from rhythmflow.logging_setup import capture_exception, configure_logging, flush_telemetry
from rhythmflow.webui.api import Api
from rhythmflow.webui.events import Emitter
from rhythmflow.webui.media_server import MediaServer

logger = logging.getLogger(__name__)

DEV_SERVER_URL = "http://localhost:5173"


def run() -> int:
    log_path = configure_logging()
    logger.info("Starting %s (pywebview UI)", APP_NAME)

    import webview

    dev_mode = os.getenv("RHYTHMFLOW_DEV", "").strip().lower() in {"1", "true", "yes", "on"}
    frontend_dir = _frontend_dir()
    if frontend_dir is None and not dev_mode:
        raise RuntimeError(
            "Front-end build not found. Run `npm install && npm run build` in "
            "rhythmflow/webui/frontend before launching."
        )

    media_server = MediaServer(frontend_dir)
    media_server.start()

    emitter = Emitter()
    api = Api(emitter, media_server)

    url = DEV_SERVER_URL if dev_mode else f"{media_server.base_url}/"
    logger.info("%s UI window url=%s; log file: %s", APP_NAME, url, log_path)

    window = webview.create_window(
        APP_NAME,
        url,
        js_api=api,
        width=1180,
        height=800,
        min_size=(960, 660),
        background_color="#0f172a",
    )
    api.set_window(window)

    try:
        webview.start(debug=dev_mode)
        return 0
    except Exception as exc:
        logger.exception("Application crashed")
        capture_exception(exc)
        raise
    finally:
        logger.info("%s shutting down", APP_NAME)
        media_server.stop()
        flush_telemetry()


def _frontend_dir() -> Path | None:
    package_dir = Path(__file__).resolve().parent
    bundle_base = Path(getattr(sys, "_MEIPASS", package_dir))
    candidates = [
        package_dir / "frontend_dist",
        bundle_base / "rhythmflow" / "webui" / "frontend_dist",
        bundle_base / "frontend_dist",
    ]
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None
