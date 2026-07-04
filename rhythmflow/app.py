from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def main() -> int:
    from rhythmflow.webui.app_webview import run

    return run()
