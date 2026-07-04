from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from rhythmflow.config import (
    APP_NAME,
    DEFAULT_LANGUAGE,
    DEFAULT_ORIGINAL_VOLUME,
    DEFAULT_OUTPUT_PATTERN,
    DEFAULT_REFERENCE_VOLUME,
    ORG_NAME,
    SettingsKeys,
)

logger = logging.getLogger(__name__)


def _config_dir() -> Path:
    override = os.getenv("RHYTHMFLOW_CONFIG_DIR", "").strip()
    if override:
        return Path(override).expanduser()

    base = (
        os.getenv("LOCALAPPDATA")
        or os.getenv("APPDATA")
        or os.getenv("XDG_CONFIG_HOME")
        or os.getenv("XDG_STATE_HOME")
    )
    if base:
        return Path(base) / ORG_NAME / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def _settings_path() -> Path:
    return _config_dir() / "settings.json"


def default_settings() -> dict[str, Any]:
    return {
        SettingsKeys.LANGUAGE: DEFAULT_LANGUAGE,
        SettingsKeys.OUTPUT_DIR: str(Path.cwd() / "output"),
        SettingsKeys.OUTPUT_PATTERN: DEFAULT_OUTPUT_PATTERN,
        SettingsKeys.ORIGINAL_VOLUME: DEFAULT_ORIGINAL_VOLUME,
        SettingsKeys.REFERENCE_VOLUME: DEFAULT_REFERENCE_VOLUME,
        SettingsKeys.CUT_MODE: "accurate",
    }


def load_settings() -> dict[str, Any]:
    settings = default_settings()
    path = _settings_path()
    if not path.exists():
        return settings
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Could not read settings file at %s; using defaults", path, exc_info=True)
        return settings
    if isinstance(stored, dict):
        for key in settings:
            if key in stored and stored[key] is not None:
                settings[key] = stored[key]
    return _coerce(settings)


def save_settings(values: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    for key in default_settings():
        if key in values and values[key] is not None:
            settings[key] = values[key]
    settings = _coerce(settings)
    path = _settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Saved settings to %s", path)
    except OSError:
        logger.warning("Could not write settings file at %s", path, exc_info=True)
    return settings


def _coerce(settings: dict[str, Any]) -> dict[str, Any]:
    settings[SettingsKeys.LANGUAGE] = str(settings[SettingsKeys.LANGUAGE] or DEFAULT_LANGUAGE)
    settings[SettingsKeys.OUTPUT_DIR] = str(settings[SettingsKeys.OUTPUT_DIR] or "")
    settings[SettingsKeys.OUTPUT_PATTERN] = (
        str(settings[SettingsKeys.OUTPUT_PATTERN] or "") or DEFAULT_OUTPUT_PATTERN
    )
    settings[SettingsKeys.ORIGINAL_VOLUME] = _clamp_volume(
        settings[SettingsKeys.ORIGINAL_VOLUME], DEFAULT_ORIGINAL_VOLUME
    )
    settings[SettingsKeys.REFERENCE_VOLUME] = _clamp_volume(
        settings[SettingsKeys.REFERENCE_VOLUME], DEFAULT_REFERENCE_VOLUME
    )
    mode = str(settings[SettingsKeys.CUT_MODE] or "accurate")
    settings[SettingsKeys.CUT_MODE] = mode if mode in {"accurate", "fast"} else "accurate"
    return settings


def _clamp_volume(value: Any, fallback: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return fallback
    return max(0, min(200, number))
