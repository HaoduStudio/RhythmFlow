from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from rhythmflow.webui import updater
from rhythmflow.webui.updater import UpdateError


class UpdaterTests(unittest.TestCase):
    def test_release_name_matches_current_version(self) -> None:
        data = {
            "name": "v0.2.1",
            "tag_name": "v0.2.1",
            "html_url": "https://github.com/HaoduStudio/RhythmFlow/releases/tag/v0.2.1",
            "assets": [],
        }

        plan = updater.build_update_plan("0.2.1", platform_key="windows", fetch_json=lambda _url: data)

        self.assertFalse(plan.update_available)
        self.assertEqual(plan.current_name, "v0.2.1")
        self.assertEqual(plan.latest_name, "v0.2.1")
        self.assertIsNone(plan.asset)

    def test_windows_update_selects_windows_x64_asset(self) -> None:
        data = {
            "name": "v0.2.2",
            "tag_name": "v0.2.2",
            "html_url": "https://github.com/HaoduStudio/RhythmFlow/releases/tag/v0.2.2",
            "assets": [
                {
                    "name": "RhythmFlow-macos.zip",
                    "browser_download_url": "https://example.invalid/mac.zip",
                    "size": 12,
                },
                {
                    "name": "RhythmFlow-windows-x64.zip",
                    "browser_download_url": "https://example.invalid/win.zip",
                    "size": 34,
                },
            ],
        }

        plan = updater.build_update_plan("0.2.1", platform_key="windows", fetch_json=lambda _url: data)

        self.assertTrue(plan.update_available)
        self.assertIsNotNone(plan.asset)
        self.assertEqual(plan.asset.name, "RhythmFlow-windows-x64.zip")

    def test_macos_update_selects_macos_asset(self) -> None:
        data = {
            "name": "v0.2.2",
            "tag_name": "v0.2.2",
            "html_url": "https://github.com/HaoduStudio/RhythmFlow/releases/tag/v0.2.2",
            "assets": [
                {
                    "name": "RhythmFlow-windows-x64.zip",
                    "browser_download_url": "https://example.invalid/win.zip",
                    "size": 34,
                },
                {
                    "name": "RhythmFlow-macos.zip",
                    "browser_download_url": "https://example.invalid/mac.zip",
                    "size": 12,
                },
            ],
        }

        plan = updater.build_update_plan("0.2.1", platform_key="macos", fetch_json=lambda _url: data)

        self.assertTrue(plan.update_available)
        self.assertIsNotNone(plan.asset)
        self.assertEqual(plan.asset.name, "RhythmFlow-macos.zip")

    def test_archive_validation_rejects_unsafe_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../RhythmFlow.exe", b"bad")

            with self.assertRaises(UpdateError) as raised:
                updater.validate_update_archive(archive_path, "windows", "RhythmFlow.exe")

        self.assertEqual(raised.exception.key, "update_bad_archive")

    def test_archive_validation_accepts_windows_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "good.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("RhythmFlow/RhythmFlow.exe", b"ok")
                archive.writestr("RhythmFlow/_internal/library.dll", b"ok")

            updater.validate_update_archive(archive_path, "windows", "RhythmFlow.exe")


if __name__ == "__main__":
    unittest.main()
