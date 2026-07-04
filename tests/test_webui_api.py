from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from rhythmflow.webui.api import Api
from rhythmflow.webui.events import Emitter
from rhythmflow.webui.lxns import LxnsError
from rhythmflow.webui.media_server import MediaServer


class ApiReferenceAudioTests(unittest.TestCase):
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

    def test_analyze_rejects_invalid_lxns_cache_before_ffmpeg(self) -> None:
        cache_file = Path(self._tmp.name) / "reference_audio_cache" / "maimai_1485_bad.mp3"
        cache_file.parent.mkdir(parents=True)
        cache_file.write_text("<script>challenge</script>", encoding="utf-8")
        api = Api(Emitter(), MediaServer(None))
        api.get_settings()

        result = api.analyze(["handcam.mp4"], str(cache_file), {})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "warn_reference_cache_invalid")

    def test_download_reference_audio_returns_error_payload(self) -> None:
        api = Api(Emitter(), MediaServer(None))
        api.get_settings()
        api.lxns.download_audio = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            LxnsError("asset challenge failed")
        )

        result = api.download_reference_audio("maimai", "1485", "song", False)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "asset challenge failed")


if __name__ == "__main__":
    unittest.main()
