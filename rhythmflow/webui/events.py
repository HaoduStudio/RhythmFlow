from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class Emitter:
    def __init__(self) -> None:
        self._window: Any | None = None

    def bind(self, window: Any) -> None:
        self._window = window

    def emit(self, event: str, payload: Any = None) -> None:
        window = self._window
        if window is None:
            logger.debug("Dropping event %s because no window is bound yet", event)
            return
        message = json.dumps({"event": event, "payload": payload}, ensure_ascii=False)
        script = f"window.rhythmflowBridge && window.rhythmflowBridge.dispatch({message});"
        try:
            window.evaluate_js(script)
        except Exception:
            logger.debug("Could not emit event %s to the web view", event, exc_info=True)
