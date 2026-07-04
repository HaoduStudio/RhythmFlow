from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from rhythmflow.config import SettingsKeys
from rhythmflow.webui import settings as settings_module


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("RHYTHMFLOW_CONFIG_DIR")
        os.environ["RHYTHMFLOW_CONFIG_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("RHYTHMFLOW_CONFIG_DIR", None)
        else:
            os.environ["RHYTHMFLOW_CONFIG_DIR"] = self._prev
        self._tmp.cleanup()

    def test_defaults_when_missing(self) -> None:
        loaded = settings_module.load_settings()
        self.assertEqual(loaded[SettingsKeys.LANGUAGE], "zh")
        self.assertEqual(loaded[SettingsKeys.CUT_MODE], "accurate")

    def test_save_then_load_round_trip(self) -> None:
        settings_module.save_settings(
            {
                SettingsKeys.LANGUAGE: "en",
                SettingsKeys.OUTPUT_PATTERN: "{index}_out.mp4",
                SettingsKeys.CUT_MODE: "fast",
            }
        )
        loaded = settings_module.load_settings()
        self.assertEqual(loaded[SettingsKeys.LANGUAGE], "en")
        self.assertEqual(loaded[SettingsKeys.OUTPUT_PATTERN], "{index}_out.mp4")
        self.assertEqual(loaded[SettingsKeys.CUT_MODE], "fast")
        self.assertTrue((Path(self._tmp.name) / "settings.json").is_file())

    def test_volume_clamped_and_mode_validated(self) -> None:
        saved = settings_module.save_settings(
            {
                SettingsKeys.ORIGINAL_VOLUME: 900,
                SettingsKeys.REFERENCE_VOLUME: -50,
                SettingsKeys.CUT_MODE: "nonsense",
            }
        )
        self.assertEqual(saved[SettingsKeys.ORIGINAL_VOLUME], 200)
        self.assertEqual(saved[SettingsKeys.REFERENCE_VOLUME], 0)
        self.assertEqual(saved[SettingsKeys.CUT_MODE], "accurate")

    def test_corrupt_file_falls_back_to_defaults(self) -> None:
        (Path(self._tmp.name) / "settings.json").write_text("{not json", encoding="utf-8")
        loaded = settings_module.load_settings()
        self.assertEqual(loaded[SettingsKeys.LANGUAGE], "zh")


if __name__ == "__main__":
    unittest.main()
