from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

from rhythmflow.config import APP_NAME, ORG_NAME, SENTRY_DSN


DEFAULT_LOG_LEVEL = "INFO"
LOG_FILE_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5

_CONFIGURED = False
_LOG_PATH: Path | None = None
_ORIGINAL_EXCEPTHOOK = sys.excepthook
_ORIGINAL_THREADING_EXCEPTHOOK = threading.excepthook


def configure_logging() -> Path:
    global _CONFIGURED, _LOG_PATH
    if _CONFIGURED and _LOG_PATH is not None:
        return _LOG_PATH

    log_dir = _log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "rhythmflow.log"
    _LOG_PATH = log_path

    root_logger = logging.getLogger()
    root_logger.setLevel(_configured_level())

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if not _has_handler(root_logger, RotatingFileHandler, log_path):
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=LOG_FILE_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    if _stderr_logging_enabled() and not _has_stream_handler(root_logger):
        console_handler = logging.StreamHandler()
        console_handler.setLevel(_configured_level())
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    _install_exception_hooks()
    _init_sentry()
    _CONFIGURED = True

    logger = logging.getLogger(__name__)
    logger.info("Logging initialized at %s", log_path)
    record_metric("rhythmflow.app.start", 1)
    return log_path


def flush_telemetry(timeout: float = 2.0) -> None:
    try:
        import sentry_sdk
    except ImportError:
        return

    try:
        sentry_sdk.flush(timeout=timeout)
    except Exception:
        logging.getLogger(__name__).debug("Sentry flush failed", exc_info=True)


def capture_exception(exc: BaseException) -> None:
    try:
        import sentry_sdk
    except ImportError:
        return

    try:
        sentry_sdk.capture_exception(exc)
    except Exception:
        logging.getLogger(__name__).debug("Sentry exception capture failed", exc_info=True)


def record_metric(name: str, value: int | float = 1, *, metric_type: str = "count") -> None:
    try:
        from sentry_sdk import metrics
    except (ImportError, AttributeError):
        return

    try:
        if metric_type == "gauge":
            metrics.gauge(name, value)
        elif metric_type == "distribution":
            metrics.distribution(name, value)
        else:
            metrics.count(name, int(value))
    except Exception:
        logging.getLogger(__name__).debug("Sentry metric emit failed: %s", name, exc_info=True)


def _init_sentry() -> None:
    dsn = os.getenv("RHYTHMFLOW_SENTRY_DSN", SENTRY_DSN).strip()
    if not dsn:
        logging.getLogger(__name__).info("Sentry disabled because DSN is empty")
        return

    try:
        import sentry_sdk
    except ImportError:
        logging.getLogger(__name__).warning(
            "sentry-sdk is not installed; telemetry will be local-only"
        )
        return

    try:
        sentry_sdk.init(
            dsn=dsn,
            send_default_pii=True,
            enable_logs=True,
            release=os.getenv("RHYTHMFLOW_RELEASE"),
            environment=os.getenv("RHYTHMFLOW_ENVIRONMENT", "production"),
        )
        logging.getLogger(__name__).info("Sentry initialized")
    except Exception:
        logging.getLogger(__name__).exception("Sentry initialization failed")


def _install_exception_hooks() -> None:
    sys.excepthook = _handle_uncaught_exception
    threading.excepthook = _handle_thread_exception


def _handle_uncaught_exception(
    exc_type: type[BaseException],
    exc: BaseException,
    traceback: TracebackType | None,
) -> None:
    logging.getLogger(__name__).critical("Uncaught exception", exc_info=(exc_type, exc, traceback))
    capture_exception(exc)
    flush_telemetry()
    _ORIGINAL_EXCEPTHOOK(exc_type, exc, traceback)


def _handle_thread_exception(args: threading.ExceptHookArgs) -> None:
    logging.getLogger(__name__).critical(
        "Uncaught thread exception",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )
    if args.exc_value is not None:
        capture_exception(args.exc_value)
    flush_telemetry()
    _ORIGINAL_THREADING_EXCEPTHOOK(args)


def _configured_level() -> int:
    level_name = os.getenv("RHYTHMFLOW_LOG_LEVEL", DEFAULT_LOG_LEVEL).upper()
    return getattr(logging, level_name, logging.INFO)


def _stderr_logging_enabled() -> bool:
    return os.getenv("RHYTHMFLOW_LOG_STDERR", "").strip().lower() in {"1", "true", "yes", "on"}


def _log_dir() -> Path:
    override = os.getenv("RHYTHMFLOW_LOG_DIR", "").strip()
    if override:
        return Path(override).expanduser()

    base = (
        os.getenv("LOCALAPPDATA")
        or os.getenv("APPDATA")
        or os.getenv("XDG_STATE_HOME")
        or os.getenv("XDG_CACHE_HOME")
    )
    if base:
        return Path(base) / ORG_NAME / APP_NAME / "logs"
    return Path.home() / f".{APP_NAME.lower()}" / "logs"


def _has_handler(
    logger: logging.Logger,
    handler_type: type[logging.Handler],
    log_path: Path | None = None,
) -> bool:
    resolved = log_path.resolve() if log_path is not None else None
    for handler in logger.handlers:
        if not isinstance(handler, handler_type):
            continue
        if resolved is None:
            return True
        filename = getattr(handler, "baseFilename", None)
        if filename and Path(filename).resolve() == resolved:
            return True
    return False


def _has_stream_handler(logger: logging.Logger) -> bool:
    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler) and not hasattr(handler, "baseFilename"):
            return True
    return False
