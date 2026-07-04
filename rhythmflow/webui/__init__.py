from __future__ import annotations

__all__ = ["run"]


def run() -> int:
    from .app_webview import run as _run

    return _run()
